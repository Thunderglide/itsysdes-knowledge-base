from __future__ import annotations

import json

from kb_pipeline.agents.common import complete_json, messages_payload
from kb_pipeline.config import Config
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import Hierarchy, Message, coerce_hierarchy, merge_hierarchy
from kb_pipeline.prompts import load_prompt


def run_structure(
    backend: LLMBackend,
    config: Config,
    *,
    hierarchy: Hierarchy,
    messages: list[Message],
    subchat_id: str,
    part_file: str,
) -> tuple[Hierarchy, list[str]]:
    system = load_prompt("structure")
    user = json.dumps(
        {
            "subchat_id": subchat_id,
            "part_file": part_file,
            "hierarchy": hierarchy.model_dump(),
            "messages": messages_payload(messages, config.max_message_chars),
        },
        ensure_ascii=False,
    )
    data = complete_json(backend, system, user)
    incoming = coerce_hierarchy(data.get("hierarchy", data))
    current_ids = {item.id for item in messages}
    return merge_hierarchy(
        hierarchy,
        incoming,
        current_message_ids=current_ids,
        current_part=part_file,
        current_subchat=subchat_id,
    )
