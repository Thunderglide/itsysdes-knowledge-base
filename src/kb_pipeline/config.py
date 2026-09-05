from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class LMStudioSettings(BaseModel):
    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = "lm-studio"
    model: str = ""


class CursorSettings(BaseModel):
    api_key_env: str = "CURSOR_API_KEY"
    scratch_dir: Path = Path(".cursor-scratch")
    model: str = "composer-2.5"


class AgentLLMConfig(BaseModel):
    backend: Literal["lmstudio", "cursor", "fake"] = "lmstudio"
    model: str = ""


class Config(BaseModel):
    sqlite_path: Path
    files_root: Path
    output_dir: Path = Path("kb")
    chats: list[str] = Field(default_factory=list)
    lmstudio: LMStudioSettings = Field(default_factory=LMStudioSettings)
    cursor: CursorSettings = Field(default_factory=CursorSettings)
    max_message_chars: int = 6000
    max_batch_tokens: int = 120_000
    agents: dict[str, AgentLLMConfig] = Field(default_factory=dict)
    config_path: Path | None = None

    @field_validator("chats", mode="before")
    @classmethod
    def _chats_as_str(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return [str(item) for item in value]

    def agent(self, name: str) -> AgentLLMConfig:
        if name not in self.agents:
            raise KeyError(f"Agent {name!r} is not configured")
        return self.agents[name]

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

    @property
    def cursor_scratch_resolved(self) -> Path:
        return self.resolve_path(self.cursor.scratch_dir)


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
