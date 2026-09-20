from __future__ import annotations

import json
import logging
import os
from typing import NoReturn

from openai import OpenAI

from kb_pipeline.config import Config
from kb_pipeline.source import estimate_tokens

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT_SEC = 600.0
DEFAULT_MAX_TOKENS = 131_072
_JSON_MAX_TOKENS = 8_192
_JSON_LARGE_MAX_TOKENS = 32_768
_JSON_LARGE_USER_CHARS = 80_000
_TEXT_MIN_TOKENS = 4_096
_TEXT_DEFAULT_TOKENS = 16_384


class DeepSeekError(RuntimeError):
    pass


class DeepSeekBackend:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        max_retries: int = 0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._timeout_sec = timeout_sec
        self._max_tokens = max(1, max_tokens)
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=_openai_timeout(timeout_sec),
            max_retries=max(0, max_retries),
        )

    @classmethod
    def from_config(cls, config: Config, model: str | None = None) -> "DeepSeekBackend":
        env_name = config.deepseek.api_key_env
        api_key = os.environ.get(env_name, "").strip()
        if not api_key:
            raise RuntimeError(
                f"DeepSeek backend requires {env_name} (see .env.example)."
            )
        chosen = (model or config.deepseek.model or DEFAULT_MODEL).strip()
        return cls(
            api_key=api_key,
            model=chosen,
            base_url=config.deepseek.base_url,
            timeout_sec=config.deepseek.timeout_sec,
            max_retries=config.deepseek.max_retries,
            max_tokens=config.deepseek.max_tokens,
        )

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": _completion_budget(
                user, json_mode=json_mode, ceiling=self._max_tokens
            ),
        }
        # V4 Flash thinking is on by default and often returns empty choices
        # on long polish/generate completions. It also streams a long
        # reasoning trace: HTTP 200 arrives, then the client blocks on body.
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        logger.info(
            "DeepSeek request model=%s max_tokens=%s json_mode=%s user_chars=%s",
            self._model,
            kwargs["max_tokens"],
            json_mode,
            len(user),
        )
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            retried = False
            if json_mode and _is_response_format_error(exc):
                kwargs.pop("response_format", None)
                retried = True
            if _is_thinking_param_error(exc):
                kwargs.pop("extra_body", None)
                retried = True
            if retried:
                try:
                    response = self._client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    _reraise_deepseek(retry_exc, self._base_url, self._model)
            else:
                _reraise_deepseek(exc, self._base_url, self._model)
        return _choice_text(response, self._model)


def _completion_budget(user: str, *, json_mode: bool, ceiling: int) -> int:
    ceiling = max(1, ceiling)
    if json_mode:
        if len(user) >= _JSON_LARGE_USER_CHARS:
            return min(ceiling, _JSON_LARGE_MAX_TOKENS)
        return min(ceiling, _JSON_MAX_TOKENS)
    draft = _draft_from_user(user)
    if draft:
        need = int(estimate_tokens(draft) * 1.4) + 2048
        return max(_TEXT_MIN_TOKENS, min(ceiling, need))
    return min(ceiling, _TEXT_DEFAULT_TOKENS)


def _draft_from_user(user: str) -> str:
    try:
        payload = json.loads(user)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("draft", "existing_draft"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _openai_timeout(timeout_sec: float):
    if timeout_sec and timeout_sec > 0:
        return timeout_sec
    return None


def _choice_text(response: object, model: str) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        logger.warning("Empty DeepSeek response model=%s (no choices)", model)
        return ""
    choice = choices[0]
    message = getattr(choice, "message", None)
    text = getattr(message, "content", None) if message is not None else None
    if isinstance(text, str) and text.strip():
        logger.info(
            "DeepSeek response model=%s finish_reason=%s chars=%s",
            model,
            getattr(choice, "finish_reason", None),
            len(text),
        )
        return text
    finish_reason = getattr(choice, "finish_reason", None)
    logger.warning(
        "Empty DeepSeek content model=%s finish_reason=%s",
        model,
        finish_reason,
    )
    return text if isinstance(text, str) else ""


def _is_response_format_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("response_format", "json_object", "json_mode", "json schema")
    )


def _is_thinking_param_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "thinking" in text and any(
        token in text for token in ("unknown", "invalid", "unexpected", "extra_body")
    )


def _reraise_deepseek(exc: BaseException, base_url: str, model: str) -> NoReturn:
    raise DeepSeekError(
        f"DeepSeek API на {base_url} (модель {model!r}) вернул ошибку: {exc}"
    ) from exc
