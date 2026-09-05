from __future__ import annotations

import json

from kb_pipeline.agents.common import complete_text, messages_payload
from kb_pipeline.config import Config
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import CriticReport, Message
from kb_pipeline.prompts import load_prompt


def run_polisher(
    backend: LLMBackend,
    config: Config,
    *,
    draft: str,
    critic: CriticReport,
    messages: list[Message],
    article_listing: list[dict[str, str]],
) -> str:
    system = load_prompt("polisher")
    user = json.dumps(
        {
            "draft": draft,
            "critic": critic.model_dump(),
            "messages": messages_payload(messages, config.max_message_chars),
            "articles": article_listing,
        },
        ensure_ascii=False,
    )
    return complete_text(backend, system, user)
