from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel

FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def extract_json_object(text: str) -> dict[str, Any]:
    payload = _extract_payload(text)
    last_error: Exception | None = None
    for candidate in _payload_candidates(payload):
        try:
            return _as_object(json.loads(candidate))
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
    for candidate in reversed(_payload_candidates(payload)):
        try:
            data = _as_object(json.loads(_close_truncated_json(candidate)))
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
        logger.warning("Repaired truncated or incomplete LLM JSON")
        return data
    if last_error:
        raise last_error
    raise ValueError(f"No JSON object in LLM output: {payload[:200]!r}")


def extract_json_model(text: str, model: type[T]) -> T:
    return model.model_validate(extract_json_object(text))


def _extract_payload(text: str) -> str:
    stripped = text.strip()
    fenced = FENCE_RE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    start = stripped.find("{")
    if start == -1:
        raise ValueError(f"No JSON object in LLM output: {stripped[:200]!r}")
    return stripped[start:]


def _payload_candidates(payload: str) -> list[str]:
    end = payload.rfind("}")
    if end == -1:
        return [payload]
    sliced = payload[: end + 1]
    if sliced == payload:
        return [payload]
    return [sliced, payload]


def _as_object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def _close_truncated_json(payload: str) -> str:
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in payload:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
    if escape:
        payload += "\\"
    if in_string:
        payload += '"'
    payload = payload.rstrip()
    if payload.endswith(","):
        payload = payload[:-1].rstrip()
    payload = _strip_incomplete_key(payload)
    while stack:
        payload += stack.pop()
    return payload


_INCOMPLETE_KEY_RE = re.compile(r',?\s*"[^"\\]*(?:\\.[^"\\]*)*"\s*:\s*$')


def _strip_incomplete_key(payload: str) -> str:
    stripped = payload.rstrip()
    updated = _INCOMPLETE_KEY_RE.sub("", stripped).rstrip()
    if updated.endswith(","):
        updated = updated[:-1].rstrip()
    return updated
