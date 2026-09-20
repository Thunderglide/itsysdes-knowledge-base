from __future__ import annotations

from typing import NoReturn

from openai import OpenAI

from kb_pipeline.config import Config

DEFAULT_TIMEOUT_SEC = 3600.0


class LMStudioError(RuntimeError):
    pass


class LMStudioBackend:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        max_retries: int = 0,
    ) -> None:
        self._timeout_sec = timeout_sec
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "lm-studio",
            timeout=_openai_timeout(timeout_sec),
            max_retries=max(0, max_retries),
        )
        self._model = model
        self._base_url = base_url

    @classmethod
    def from_config(cls, config: Config, model: str | None = None) -> "LMStudioBackend":
        chosen = (model or config.lmstudio.model or "").strip()
        if not chosen:
            chosen = _first_lmstudio_model(
                config.lmstudio.base_url,
                config.lmstudio.api_key,
                timeout_sec=config.lmstudio.timeout_sec,
                max_retries=config.lmstudio.max_retries,
            )
        return cls(
            base_url=config.lmstudio.base_url,
            api_key=config.lmstudio.api_key,
            model=chosen,
            timeout_sec=config.lmstudio.timeout_sec,
            max_retries=config.lmstudio.max_retries,
        )

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        self._ensure_model_ready()
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if json_mode and _is_response_format_error(exc):
                kwargs.pop("response_format", None)
                try:
                    response = self._client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    _reraise_lmstudio(
                        retry_exc, self._base_url, self._model, self._timeout_sec
                    )
            else:
                _reraise_lmstudio(exc, self._base_url, self._model, self._timeout_sec)
        choice = response.choices[0].message.content
        return choice or ""

    def _ensure_model_ready(self) -> None:
        try:
            ids = _list_model_ids(self._client)
        except Exception:
            return
        if not ids:
            raise LMStudioError(no_models_message(self._base_url))
        if self._model and self._model not in ids:
            raise LMStudioError(unknown_model_message(self._base_url, self._model, ids))


def _openai_timeout(timeout_sec: float):
    if timeout_sec and timeout_sec > 0:
        return timeout_sec
    return None


def _first_lmstudio_model(
    base_url: str,
    api_key: str,
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    max_retries: int = 0,
) -> str:
    client = OpenAI(
        base_url=base_url,
        api_key=api_key or "lm-studio",
        timeout=_openai_timeout(timeout_sec),
        max_retries=max(0, max_retries),
    )
    try:
        models = _list_model_ids(client)
    except Exception as exc:
        raise LMStudioError(
            f"Не удалось получить список моделей LM Studio на {base_url}: {exc}"
        ) from exc
    if not models:
        raise LMStudioError(no_models_message(base_url))
    return models[0]


def _list_model_ids(client: OpenAI) -> list[str]:
    listing = client.models.list()
    return [item.id for item in (listing.data or []) if getattr(item, "id", None)]


def _is_response_format_error(exc: BaseException) -> bool:
    if _is_no_models_error(exc) or _is_timeout_error(exc):
        return False
    text = str(exc).lower()
    return any(
        token in text
        for token in ("response_format", "json_object", "json_mode", "json schema")
    )


def _is_no_models_error(exc: BaseException) -> bool:
    return "no models loaded" in str(exc).lower()


def _is_timeout_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return "timeout" in name or "timed out" in text or "timeout" in text


def _reraise_lmstudio(
    exc: BaseException, base_url: str, model: str, timeout_sec: float
) -> NoReturn:
    if _is_no_models_error(exc):
        raise LMStudioError(no_models_message(base_url)) from exc
    if _is_timeout_error(exc):
        raise LMStudioError(timeout_message(base_url, timeout_sec)) from exc
    text = str(exc).lower()
    if model and ("model" in text) and any(
        token in text for token in ("not found", "does not exist", "unknown", "invalid")
    ):
        raise LMStudioError(
            unknown_model_message(base_url, model, []) + f"\nИсходная ошибка: {exc}"
        ) from exc
    raise exc


def no_models_message(base_url: str) -> str:
    return (
        f"LM Studio на {base_url} не загрузил ни одной модели.\n"
        "Скачанная или видимая через LM Link модель ещё не в памяти сервера "
        "(downloaded ≠ loaded).\n"
        "Что сделать:\n"
        "  1. В LM Studio откройте Developer и загрузите модель, либо: lms load <id>\n"
        "  2. Для LM Link: lms link status, затем lms load <id> "
        "(при необходимости lms link set-preferred-device)\n"
        "  3. Проверьте: lms ps  и  curl http://127.0.0.1:1234/v1/models\n"
        "  4. Либо включите Just-in-Time loading в настройках сервера.\n"
        "Пайплайн сам модель не загружает."
    )


def timeout_message(base_url: str, timeout_sec: float) -> str:
    limit = (
        "без ограничения по времени"
        if not timeout_sec or timeout_sec <= 0
        else f"{timeout_sec:g} с"
    )
    return (
        f"LM Studio на {base_url} не ответил за {limit}.\n"
        "Повторный запрос не отправлялся — иначе на сервере пошла бы вторая генерация.\n"
        "Дождитесь окончания текущей генерации в LM Studio или увеличьте "
        "lmstudio.timeout_sec в config.yaml (0 = ждать сколько угодно)."
    )


def unknown_model_message(base_url: str, requested: str, available: list[str]) -> str:
    listed = ", ".join(available) if available else "(список пуст или недоступен)"
    return (
        f"Модель {requested!r} недоступна на {base_url}.\n"
        f"Доступные id: {listed}\n"
        "Проверьте опечатку в config.yaml (agents.*.model или lmstudio.model)."
    )
