from __future__ import annotations

import json

from kb_pipeline.agents.common import complete_text
from kb_pipeline.content_stitch import citations_lost
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.prompts import load_prompt


def run_curate_polish(backend: LLMBackend, *, title: str, text: str) -> str:
    original = text or ""
    system = load_prompt("curate_polish")
    user = json.dumps({"title": title, "text": original}, ensure_ascii=False)
    result = complete_text(backend, system, user, fallback=original)
    polished = (result or "").strip()
    if not polished or citations_lost(original, polished):
        return original
    return polished if polished.endswith("\n") else polished + "\n"
