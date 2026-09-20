from __future__ import annotations

import json
from typing import Any

from kb_pipeline.agents.common import complete_text, messages_payload
from kb_pipeline.config import Config
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import CriticReport, Message
from kb_pipeline.prompts import load_prompt
from kb_pipeline.structure_guard import compact_polish_listing

_POLISH_MESSAGE_CHARS = 2000
_POLISH_MAX_MESSAGES = 60


def run_polisher(
    backend: LLMBackend,
    config: Config,
    *,
    draft: str,
    critic: CriticReport,
    messages: list[Message],
    article_listing: list[dict[str, Any]],
    title: str = "",
    folder: str = "",
) -> str:
    system = load_prompt("polisher")
    cap = min(config.max_message_chars or _POLISH_MESSAGE_CHARS, _POLISH_MESSAGE_CHARS)
    listing = compact_polish_listing(article_listing, title=title, folder=folder)
    user = json.dumps(
        {
            "draft": draft,
            "critic": critic.model_dump(),
            "messages": messages_payload(messages[:_POLISH_MAX_MESSAGES], cap),
            "articles": [
                {"title": item.get("title") or "", "path": item.get("path") or ""}
                for item in listing
            ],
        },
        ensure_ascii=False,
    )
    return complete_text(backend, system, user, fallback=_fallback_final(draft, critic))


def _fallback_final(draft: str, critic: CriticReport) -> str:
    body = (draft or "").rstrip() or "# Статья"
    lines = [body, "", "## Открытые вопросы", ""]
    if critic.questions:
        lines.extend(f"- {item}" for item in critic.questions)
    else:
        lines.append("- Уточнить границы темы при следующем файле сообщений.")
    return "\n".join(lines) + "\n"
