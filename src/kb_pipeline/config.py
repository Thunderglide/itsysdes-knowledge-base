from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class LMStudioSettings(BaseModel):
    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = "lm-studio"
    model: str = ""
    # Seconds to wait for one completion. 0 = no read timeout.
    timeout_sec: float = 3600
    # OpenAI SDK retries. Keep 0: a timeout retry starts a second generation.
    max_retries: int = 0


class DeepSeekSettings(BaseModel):
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    model: str = "deepseek-flash"
    timeout_sec: float = 600
    max_retries: int = 0
    # Hard ceiling against Flash's 384k default. Per-request budget is
    # smaller: JSON 8k, text ~1.4× draft (or 16k if there is no draft).
    max_tokens: int = 131_072


class AgentLLMConfig(BaseModel):
    backend: Literal["lmstudio", "deepseek", "fake"] = "lmstudio"
    model: str = ""


class CurateSettings(BaseModel):
    merge_max_messages: int = 5
    merge_target_max: int = 80
    split_min_messages: int = 120
    split_min_chars: int = 40_000
    taxonomy_path: Path | None = None
    tag_max: int = 8

    @field_validator("taxonomy_path", mode="before")
    @classmethod
    def _empty_taxonomy_path(cls, value: Any) -> Any:
        if value in ("", None):
            return None
        return value


class Config(BaseModel):
    sqlite_path: Path
    files_root: Path
    output_dir: Path = Path("kb")
    chats: list[str] = Field(default_factory=list)
    lmstudio: LMStudioSettings = Field(default_factory=LMStudioSettings)
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    max_message_chars: int = 6000
    max_batch_tokens: int = 120_000
    agents: dict[str, AgentLLMConfig] = Field(default_factory=dict)
    curate: CurateSettings = Field(default_factory=CurateSettings)
    config_path: Path | None = None

    @field_validator("chats", mode="before")
    @classmethod
    def _chats_as_str(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return [str(item) for item in value]

    def agent(self, name: str) -> AgentLLMConfig:
        if name in self.agents:
            return self.agents[name]
        if name in {"merge", "split", "reparent", "tag"}:
            for fallback in ("structure", "generator", "polisher"):
                if fallback in self.agents:
                    return self.agents[fallback]
        raise KeyError(f"Agent {name!r} is not configured")

    def resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        root = self.config_path.parent if self.config_path else Path.cwd()
        return (root / path).resolve()

    @property
    def output_dir_resolved(self) -> Path:
        return self.resolve_path(self.output_dir)

    @property
    def sqlite_path_resolved(self) -> Path:
        return self.resolve_path(self.sqlite_path)

    @property
    def files_root_resolved(self) -> Path:
        return self.resolve_path(self.files_root)


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path) if path else Path("config.yaml")
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(raw)
    cfg.config_path = config_path
    return cfg
