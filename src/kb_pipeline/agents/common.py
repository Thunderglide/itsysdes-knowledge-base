from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from kb_pipeline.jsonutil import extract_json_object
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import Message

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def complete_text(backend: LLMBackend, system: str, user: str) -> str:
    text = backend.complete(system, user, json_mode=False)
    if not text or not str(text).strip():
        raise LLMError("Empty LLM response")
    return str(text).strip()


def complete_json(backend: LLMBackend, system: str, user: str) -> dict[str, Any]:
    raw = backend.complete(system, user, json_mode=True)
    try:
        return extract_json_object(raw)
    except (ValueError, json.JSONDecodeError) as first:
        retry_user = (
            user
            + "\n\nПредыдущий ответ нельзя разобрать как JSON: "
            + str(first)
            + "\nВерни только валидный JSON-объект."
        )
        raw = backend.complete(system, retry_user, json_mode=True)
        try:
            return extract_json_object(raw)
        except (ValueError, json.JSONDecodeError) as second:
            raise LLMError(f"LLM did not return JSON: {second}") from second


def messages_payload(messages: list[Message], max_chars: int) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        text = message.text
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "\n…[обрезано]"
        payload.append(
            {
                "id": message.id,
                "author": message.author,
                "date": message.date,
                "text": text,
                "attachments": [item.model_dump() for item in message.attachments],
            }
        )
    return payload
