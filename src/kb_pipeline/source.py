from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from kb_pipeline.config import Config
from kb_pipeline.models import Attachment, Message, WorkItem

_ENCODING = None


@dataclass(frozen=True)
class ChatInfo:
    id: int
    telegram_id: int
    title: str
    type: str
    username: str


@dataclass(frozen=True)
class SubchatInfo:
    chat: ChatInfo
    thread_id: int | None
    subchat_id: str
    title: str
    message_count: int


@dataclass
class _RawMessage:
    pk: int
    message_id: int
    author_name: str
    date: str
    text: str
    attachments: list[Attachment] = field(default_factory=list)


def connect(config: Config) -> sqlite3.Connection:
    path = config.sqlite_path_resolved
    if not path.exists():
        raise FileNotFoundError(f"SQLite не найден: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def subchat_id_for(*, thread_id: int | None, telegram_id: int) -> str:
    if thread_id is None:
        return f"chat_{telegram_id}"
    return f"topic_{thread_id}"


def encoding():
    global _ENCODING
    if _ENCODING is None:
        import tiktoken

        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(encoding().encode(text))


def message_token_count(message: Message, max_chars: int) -> int:
    return estimate_tokens(_payload_json(message, max_chars))


def chat_matches(chat: ChatInfo, filters: Iterable[str]) -> bool:
    tokens = [item.strip() for item in filters if str(item).strip()]
    if not tokens:
        return True
    username = (chat.username or "").lstrip("@")
    candidates = {
        str(chat.id),
        str(chat.telegram_id),
        chat.title or "",
        chat.username or "",
        username,
    }
    return any(token.lstrip("@") in candidates or token in candidates for token in tokens)


def subchat_matches(info: SubchatInfo, token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    names = {
        info.subchat_id,
        info.subchat_id.removeprefix("topic_"),
        info.subchat_id.removeprefix("chat_"),
    }
    if info.thread_id is not None:
        names.add(str(info.thread_id))
    return token in names


def list_chats(conn: sqlite3.Connection, filters: list[str] | None = None) -> list[ChatInfo]:
    rows = conn.execute(
        "SELECT id, telegram_id, title, type, username FROM chats ORDER BY id"
    ).fetchall()
    chats = [
        ChatInfo(
            id=int(row["id"]),
            telegram_id=int(row["telegram_id"]),
            title=row["title"] or "",
            type=row["type"] or "",
            username=row["username"] or "",
        )
        for row in rows
    ]
    return [chat for chat in chats if chat_matches(chat, filters or [])]


def list_subchats(conn: sqlite3.Connection, chats: list[ChatInfo]) -> list[SubchatInfo]:
    result: list[SubchatInfo] = []
    for chat in chats:
        rows = conn.execute(
            """
            SELECT thread_id, COUNT(*) AS n
            FROM messages
            WHERE chat_id = ?
            GROUP BY thread_id
            ORDER BY (thread_id IS NULL), thread_id
            """,
            (chat.id,),
        ).fetchall()
        for row in rows:
            thread_id = row["thread_id"]
            thread_id_int = int(thread_id) if thread_id is not None else None
            result.append(
                SubchatInfo(
                    chat=chat,
                    thread_id=thread_id_int,
                    subchat_id=subchat_id_for(
                        thread_id=thread_id_int, telegram_id=chat.telegram_id
                    ),
                    title=_topic_title(conn, chat, thread_id_int),
                    message_count=int(row["n"] or 0),
                )
            )
    return result


def load_thread_messages(
    conn: sqlite3.Connection,
    *,
    chat: ChatInfo,
    thread_id: int | None,
    part_file: str = "",
    subchat_id: str = "",
) -> list[Message]:
    raw = _fetch_raw_messages(conn, chat.id, thread_id, first=None, last=None)
    return [
        mapped
        for mapped in (
            _to_message(
                item,
                telegram_id=chat.telegram_id,
                part_file=part_file,
                subchat_id=subchat_id,
            )
            for item in raw
        )
        if mapped is not None
    ]


def load_work_item_messages(conn: sqlite3.Connection, item: WorkItem) -> list[Message]:
    raw = _fetch_raw_messages(
        conn,
        item.chat_db_id,
        item.thread_id,
        first=item.first_message_id,
        last=item.last_message_id,
    )
    messages: list[Message] = []
    for item_raw in raw:
        mapped = _to_message(
            item_raw,
            telegram_id=item.telegram_id,
            part_file=item.part_file,
            subchat_id=item.subchat_id,
        )
        if mapped is not None:
            messages.append(mapped)
    return messages


def pack_batches(
    messages: list[Message], *, max_tokens: int, max_chars: int
) -> list[list[Message]]:
    if max_tokens <= 0:
        raise ValueError("max_batch_tokens должен быть > 0")
    batches: list[list[Message]] = []
    current: list[Message] = []
    current_tokens = 0
    for message in messages:
        cost = message_token_count(message, max_chars)
        if current and current_tokens + cost > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(message)
        current_tokens += cost
        if len(current) == 1 and current_tokens > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
    if current:
        batches.append(current)
    return batches


def strip_attachment_paths(text: str, paths: Iterable[str]) -> str:
    if not text:
        return ""
    path_set = {path.replace("\\", "/").strip() for path in paths if path}
    kept: list[str] = []
    for line in text.splitlines():
        if line.strip().replace("\\", "/") in path_set:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def normalize_date(value: str | None) -> str:
    if not value:
        return ""
    raw = value.strip()
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return raw.replace("T", " ")[:19]


def _topic_title(conn: sqlite3.Connection, chat: ChatInfo, thread_id: int | None) -> str:
    if thread_id is None:
        return chat.title or chat.username or str(chat.telegram_id)
    row = conn.execute(
        "SELECT text FROM messages WHERE chat_id = ? AND message_id = ?",
        (chat.id, thread_id),
    ).fetchone()
    if row:
        text = (row["text"] or "").strip()
        if text:
            return text.splitlines()[0][:120]
    return f"topic_{thread_id}"


def _fetch_raw_messages(
    conn: sqlite3.Connection,
    chat_db_id: int,
    thread_id: int | None,
    *,
    first: int | None,
    last: int | None,
) -> list[_RawMessage]:
    sql = [
        "SELECT id, message_id, author_name, date, text",
        "FROM messages",
        "WHERE chat_id = ?",
    ]
    params: list[Any] = [chat_db_id]
    if thread_id is None:
        sql.append("AND thread_id IS NULL")
    else:
        sql.append("AND thread_id = ?")
        params.append(thread_id)
    if first is not None:
        sql.append("AND message_id >= ?")
        params.append(first)
    if last is not None:
        sql.append("AND message_id <= ?")
        params.append(last)
    sql.append("ORDER BY date ASC, message_id ASC")
    rows = conn.execute(" ".join(sql), params).fetchall()
    by_pk: dict[int, _RawMessage] = {}
    ordered: list[_RawMessage] = []
    for row in rows:
        item = _RawMessage(
            pk=int(row["id"]),
            message_id=int(row["message_id"]),
            author_name=row["author_name"] or "",
            date=row["date"] or "",
            text=row["text"] or "",
        )
        by_pk[item.pk] = item
        ordered.append(item)
    _attach_files(conn, by_pk)
    return ordered


def _attach_files(conn: sqlite3.Connection, by_pk: dict[int, _RawMessage]) -> None:
    if not by_pk:
        return
    pks = list(by_pk)
    chunk = 500
    for start in range(0, len(pks), chunk):
        part = pks[start : start + chunk]
        placeholders = ",".join("?" * len(part))
        rows = conn.execute(
            f"""
            SELECT message_id, file_path, file_name, mime_type
            FROM attachments
            WHERE message_id IN ({placeholders})
            ORDER BY id
            """,
            part,
        ).fetchall()
        for row in rows:
            host = by_pk.get(int(row["message_id"]))
            if host is None:
                continue
            path = (row["file_path"] or "").replace("\\", "/")
            if not path:
                continue
            host.attachments.append(
                Attachment(
                    path=path,
                    description=_attachment_description(
                        path, row["file_name"], row["mime_type"]
                    ),
                )
            )


def _to_message(
    raw: _RawMessage,
    *,
    telegram_id: int,
    part_file: str,
    subchat_id: str,
) -> Message | None:
    paths = [item.path for item in raw.attachments]
    text = strip_attachment_paths(raw.text, paths)
    if not text and not raw.attachments:
        return None
    return Message(
        id=f"{telegram_id}:{raw.message_id}",
        author=raw.author_name,
        date=normalize_date(raw.date),
        text=text,
        attachments=list(raw.attachments),
        part_file=part_file,
        subchat_id=subchat_id,
    )


def _payload_json(message: Message, max_chars: int) -> str:
    text = message.text
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n…[обрезано]"
    payload = {
        "id": message.id,
        "author": message.author,
        "date": message.date,
        "text": text,
        "attachments": [item.model_dump() for item in message.attachments],
    }
    return json.dumps(payload, ensure_ascii=False)


def _attachment_description(path: str, file_name: str | None, mime_type: str | None) -> str:
    name = file_name or Path(path).name
    if mime_type:
        short = mime_type.split("/")[-1]
        return f"{short}: {name}"
    suffix = Path(name).suffix.lower().lstrip(".")
    if suffix:
        return f"{suffix}: {name}"
    return name
