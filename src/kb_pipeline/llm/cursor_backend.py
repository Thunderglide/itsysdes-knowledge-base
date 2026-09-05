from __future__ import annotations

import os
from pathlib import Path

from kb_pipeline.config import Config


class CursorBackend:
    def __init__(self, *, api_key: str, model: str, scratch_dir: Path) -> None:
        self._api_key = api_key
        self._model = model
        self._scratch_dir = scratch_dir
        self._scratch_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(cls, config: Config, model: str | None = None) -> "CursorBackend":
        env_name = config.cursor.api_key_env
        api_key = os.environ.get(env_name, "").strip()
        if not api_key:
            raise RuntimeError(
                f"Cursor backend requires {env_name} (see .env.example)."
            )
        chosen = (model or config.cursor.model or "composer-2.5").strip()
        return cls(
            api_key=api_key,
            model=chosen,
            scratch_dir=config.cursor_scratch_resolved,
        )

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        try:
            from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
        except ImportError as exc:
            raise RuntimeError(
                "cursor-sdk is not installed. Run: pip install cursor-sdk"
            ) from exc

        format_note = (
            "Верни ТОЛЬКО валидный JSON, без markdown и без пояснений."
            if json_mode
            else "Верни только итоговый текст. Не вызывай инструменты и не меняй файлы."
        )
        prompt = (
            f"{system}\n\n{user}\n\n"
            f"{format_note}\n"
            "Не используй инструменты, не читай и не редактируй файлы проекта."
        )
        options = AgentOptions(
            api_key=self._api_key,
            model=self._model,
            local=LocalAgentOptions(cwd=str(self._scratch_dir)),
            tools=[],
        )
        result = Agent.prompt(prompt, options)
        return _cursor_result_text(result)


def _cursor_result_text(result: object) -> str:
    if hasattr(result, "text") and callable(getattr(result, "text")):
        try:
            text = result.text()
            if isinstance(text, str) and text.strip():
                return text
        except TypeError:
            pass
    status = getattr(result, "status", None)
    payload = getattr(result, "result", None)
    if isinstance(payload, str) and payload.strip():
        return payload
    if payload is not None:
        for attr in ("text", "content", "message"):
            value = getattr(payload, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        if isinstance(payload, dict):
            for key in ("text", "content", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    if status == "error":
        raise RuntimeError(f"Cursor agent run failed: {result!r}")
    return str(result)
