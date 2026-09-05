from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from kb_pipeline.models import Attachment, Message

HEADER_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.+):\s*$"
)
ATTACHMENT_RE = re.compile(r"^📎\s*Вложение:\s*(.+)\s*$")


def parse_messages_file(
    path: Path | str,
    *,
    subchat_id: str = "",
    part_file: str = "",
) -> list[Message]:
    """Parse a single messages_part_*.txt export file."""
    file_path = Path(path)
    part_file = part_file or file_path.name
    text = file_path.read_text(encoding="utf-8")
    return parse_messages_text(text, subchat_id=subchat_id, part_file=part_file)


def parse_messages_text(
    text: str,
    *,
    subchat_id: str = "",
    part_file: str = "",
) -> list[Message]:
    lines = text.splitlines()
    headers: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = HEADER_RE.match(line)
        if match:
            headers.append((index, match.group(1), match.group(2)))

    counts: dict[str, int] = defaultdict(int)
    messages: list[Message] = []
    for position, (start, date, author) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        body_lines = lines[start + 1 : end]
        text_body, attachments = _split_body(body_lines)
        base_id = f"{date}|{author}"
        counts[base_id] += 1
        message_id = base_id if counts[base_id] == 1 else f"{base_id}|#{counts[base_id]}"
        messages.append(
            Message(
                id=message_id,
                author=author,
                date=date,
                text=text_body,
                attachments=attachments,
                part_file=part_file,
                subchat_id=subchat_id,
            )
        )
    return messages


def _split_body(body_lines: list[str]) -> tuple[str, list[Attachment]]:
    attachments: list[Attachment] = []
    text_lines: list[str] = []
    for raw in body_lines:
        attach = ATTACHMENT_RE.match(raw)
        if attach:
            path = attach.group(1).strip()
            attachments.append(
                Attachment(path=path, description=_attachment_description(path))
            )
            continue
        text_lines.append(raw)
    text = "\n".join(text_lines).strip()
    if text == "(без текста)":
        text = ""
    return text, attachments


def _attachment_description(path: str) -> str:
    name = Path(path).name
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix:
        return f"{suffix}: {name}"
    return name


def messages_to_jsonl(messages: list[Message]) -> str:
    return "".join(json.dumps(m.model_dump(), ensure_ascii=False) + "\n" for m in messages)


def messages_from_jsonl(text: str) -> list[Message]:
    items: list[Message] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        items.append(Message.model_validate(json.loads(line)))
    return items
