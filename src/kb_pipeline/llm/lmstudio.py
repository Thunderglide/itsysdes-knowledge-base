from __future__ import annotations

from openai import OpenAI

from kb_pipeline.config import Config


class LMStudioBackend:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
    ) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key or "lm-studio")
        self._model = model
        self._base_url = base_url

    @classmethod
    def from_config(cls, config: Config, model: str | None = None) -> "LMStudioBackend":
        chosen = (model or config.lmstudio.model or "").strip()
        if not chosen:
            chosen = _first_lmstudio_model(config.lmstudio.base_url, config.lmstudio.api_key)
        return cls(
            base_url=config.lmstudio.base_url,
            api_key=config.lmstudio.api_key,
            model=chosen,
        )

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
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
        except Exception:
            if json_mode:
                kwargs.pop("response_format", None)
                response = self._client.chat.completions.create(**kwargs)
            else:
                raise
        choice = response.choices[0].message.content
        return choice or ""


def _first_lmstudio_model(base_url: str, api_key: str) -> str:
    client = OpenAI(base_url=base_url, api_key=api_key or "lm-studio")
    listing = client.models.list()
    models = list(listing.data or [])
    if not models:
        raise RuntimeError(
            f"LM Studio at {base_url} returned no models. Load a model in the Server tab."
        )
    return models[0].id
