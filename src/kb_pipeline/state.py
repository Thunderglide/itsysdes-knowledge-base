from __future__ import annotations

from typing import Any, Literal, TypedDict


UntilStage = Literal["ingest", "structure", "generate", "critic", "polish"]

STAGE_ORDER = ["ingest", "structure", "generate", "critic", "polish"]


class PipelineState(TypedDict, total=False):
    hierarchy: dict[str, Any]
    articles: dict[str, dict[str, Any]]
    work_items: list[dict[str, Any]]
    work_index: int
    current_subchat_id: str
    current_part_file: str
    current_messages: list[dict[str, Any]]
    touched_article_ids: list[str]
    until: str
    done: bool
    status: str
