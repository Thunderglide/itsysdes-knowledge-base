from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id BIGINT UNIQUE NOT NULL,
    title TEXT,
    type TEXT,
    username TEXT,
    last_loaded_id BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_id BIGINT NOT NULL,
    thread_id BIGINT,
    author_id BIGINT,
    author_name TEXT,
    date TIMESTAMP,
    text TEXT,
    reply_to_msg_id BIGINT,
    processed BOOLEAN DEFAULT FALSE,
    raw_data JSON,
    reactions JSON,
    UNIQUE(chat_id, message_id)
);
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_name TEXT,
    file_size INTEGER,
    mime_type TEXT,
    telegram_file_id TEXT
);
"""


def create_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def add_chat(
    conn: sqlite3.Connection,
    *,
    telegram_id: int = 1099595414,
    title: str = "Системный анализ",
    chat_type: str = "supergroup",
    username: str = "itsysdes",
) -> int:
    cur = conn.execute(
        "INSERT INTO chats (telegram_id, title, type, username) VALUES (?, ?, ?, ?)",
        (telegram_id, title, chat_type, username),
    )
    return int(cur.lastrowid)


def add_message(
    conn: sqlite3.Connection,
    chat_id: int,
    message_id: int,
    *,
    thread_id: int | None = None,
    author_name: str = "alice",
    date: str = "2023-03-14T10:12:22+00:00",
    text: str = "hello",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO messages (chat_id, message_id, thread_id, author_name, date, text)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (chat_id, message_id, thread_id, author_name, date, text),
    )
    return int(cur.lastrowid)


def add_attachment(
    conn: sqlite3.Connection,
    message_pk: int,
    *,
    file_path: str,
    file_name: str | None = None,
    mime_type: str = "image/jpeg",
) -> int:
    name = file_name or Path(file_path).name
    cur = conn.execute(
        """
        INSERT INTO attachments (message_id, file_path, file_name, mime_type)
        VALUES (?, ?, ?, ?)
        """,
        (message_pk, file_path, name, mime_type),
    )
    return int(cur.lastrowid)


def write_config(
    path: Path,
    *,
    sqlite_path: Path,
    files_root: Path,
    output_dir: Path,
    chats: list[str] | None = None,
    max_batch_tokens: int = 120000,
    max_message_chars: int = 6000,
    fake_agents: bool = True,
) -> Path:
    lines = [
        f"sqlite_path: {sqlite_path}",
        f"files_root: {files_root}",
        f"output_dir: {output_dir}",
        f"max_batch_tokens: {max_batch_tokens}",
        f"max_message_chars: {max_message_chars}",
    ]
    if chats:
        lines.append("chats:")
        for item in chats:
            lines.append(f"  - {item}")
    else:
        lines.append("chats: []")
    if fake_agents:
        lines.extend(
            [
                "agents:",
                "  structure: {backend: fake, model: x}",
                "  generator: {backend: fake, model: x}",
                "  critic: {backend: fake, model: x}",
                "  polisher: {backend: fake, model: x}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
