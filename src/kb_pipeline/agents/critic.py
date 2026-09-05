from __future__ import annotations

import json

from kb_pipeline.agents.common import complete_json, messages_payload
from kb_pipeline.config import Config
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import CriticReport, Message
from kb_pipeline.prompts import load_prompt


def run_critic(
    backend: LLMBackend,
    config: Config,
    *,
    draft: str,
    messages: list[Message],
) -> CriticReport:
    system = load_prompt("critic")
    user = json.dumps(
        {
            "draft": draft,
            "messages": messages_payload(messages, config.max_message_chars),
        },
        ensure_ascii=False,
    )
    data = complete_json(backend, system, user)
    return CriticReport.model_validate(data)
