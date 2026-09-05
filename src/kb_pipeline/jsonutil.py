from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

T = TypeVar("T", bound=BaseModel)


def extract_json_object(text: str) -> dict[str, Any]:
    payload = _extract_payload(text)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def extract_json_model(text: str, model: type[T]) -> T:
    return model.model_validate(extract_json_object(text))


def _extract_payload(text: str) -> str:
    stripped = text.strip()
    fenced = FENCE_RE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object in LLM output: {stripped[:200]!r}")
    return stripped[start : end + 1]
