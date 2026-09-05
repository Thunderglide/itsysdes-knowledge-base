from __future__ import annotations

import os

from kb_pipeline.config import AgentLLMConfig, Config
from kb_pipeline.llm.cursor_backend import CursorBackend
from kb_pipeline.llm.fake import FakeBackend
from kb_pipeline.llm.lmstudio import LMStudioBackend


def get_backend(agent_name: str, config: Config, *, fake: bool = False):
    if fake:
        return FakeBackend()
    spec: AgentLLMConfig = config.agent(agent_name)
    model = spec.model
    if spec.backend == "lmstudio":
        env_model = os.environ.get("LMSTUDIO_MODEL", "").strip()
        return LMStudioBackend.from_config(config, model or env_model)
    if spec.backend == "cursor":
        return CursorBackend.from_config(config, model)
    if spec.backend == "fake":
        return FakeBackend()
    raise ValueError(f"Unknown backend {spec.backend!r} for agent {agent_name}")
