from __future__ import annotations

import json

from kb_pipeline.agents.common import complete_text, messages_payload
from kb_pipeline.config import Config
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import Message
from kb_pipeline.persist import relative_media_path
from kb_pipeline.prompts import load_prompt


def run_generator(
    backend: LLMBackend,
    config: Config,
    *,
    title: str,
    folder: str,
    slug: str,
    existing_draft: str,
    messages: list[Message],
    subchat_id: str,
) -> str:
    system = load_prompt("generator")
    payload_messages = messages_payload(messages, config.max_message_chars)
    for item in payload_messages:
        for attachment in item.get("attachments") or []:
            attachment["path"] = relative_media_path(config, attachment.get("path") or "")
    user = json.dumps(
        {
            "title": title,
            "folder": folder,
            "slug": slug,
            "chat_id": subchat_id,
            "existing_draft": existing_draft,
            "messages": payload_messages,
        },
        ensure_ascii=False,
    )
    return complete_text(
        backend, system, user, fallback=existing_draft.strip() or None
    )
