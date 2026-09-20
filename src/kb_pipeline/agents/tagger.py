from __future__ import annotations

import json
from typing import Any

from kb_pipeline.agents.common import complete_json
from kb_pipeline.config import Config
from kb_pipeline.content_stitch import first_heading, parse_sections, strip_frontmatter
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import Hierarchy
from kb_pipeline.persist import load_article_record
from kb_pipeline.prompts import load_prompt
from kb_pipeline.taxonomy import filter_tags, load_taxonomy

_BATCH = 40
_PREVIEW_LINES = 80


def propose_tags(
    backend: LLMBackend,
    config: Config,
    hierarchy: Hierarchy,
) -> dict[str, Any]:
    taxonomy = load_taxonomy(config)
    by_folder: dict[str, list[dict[str, Any]]] = {}
    for row in hierarchy.to_listing():
        folder = str(row.get("folder") or "")
        by_folder.setdefault(folder, []).append(
            _tag_row(config, hierarchy, row)
        )
    system = load_prompt("tagger")
    actions: list[dict[str, Any]] = []
    for _folder, rows in by_folder.items():
        for batch in (rows[index : index + _BATCH] for index in range(0, len(rows), _BATCH)):
            user = json.dumps(
                {
                    "allowed": taxonomy.listing_payload(),
                    "tag_max": config.curate.tag_max,
                    "articles": batch,
                },
                ensure_ascii=False,
            )
            data = complete_json(backend, system, user)
            actions.extend(
                validate_tag_actions(
                    data.get("actions") or [],
                    hierarchy,
                    config,
                )
            )
    return {"kind": "tag", "actions": actions}


def validate_tag_actions(
    raw_actions: Any,
    hierarchy: Hierarchy,
    config: Config,
) -> list[dict[str, Any]]:
    taxonomy = load_taxonomy(config)
    kept: list[dict[str, Any]] = []
    if not isinstance(raw_actions, list):
        return kept
    seen: set[str] = set()
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        article_id = str(item.get("id") or "").strip()
        if not article_id or article_id in seen or hierarchy.find_by_id(article_id) is None:
            continue
        raw_tags = item.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        extra = item.get("proposed_tags") or []
        if isinstance(extra, str):
            extra = [extra]
        tags, proposed = filter_tags(
            list(raw_tags) + list(extra),
            taxonomy,
            max_tags=config.curate.tag_max,
        )
        seen.add(article_id)
        kept.append(
            {
                "id": article_id,
                "tags": tags,
                "proposed_tags": proposed,
            }
        )
    return kept


def _tag_row(config: Config, hierarchy: Hierarchy, row: dict[str, Any]) -> dict[str, Any]:
    found = hierarchy.find_by_id(str(row["id"]))
    preview = ""
    headings: list[str] = []
    if found:
        folder, slug, article = found
        record = load_article_record(
            config, str(row["id"]), article.title, folder, slug, tags=article.tags
        )
        text = strip_frontmatter(record.final or record.draft)
        preview = "\n".join(text.splitlines()[:_PREVIEW_LINES])
        headings = [
            section.heading
            for section in parse_sections(text)
            if section.heading
        ]
        heading = first_heading(text)
    else:
        heading = ""
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "folder": row.get("folder") or "",
        "heading": heading,
        "headings": headings[:20],
        "preview": preview,
        "current_tags": row.get("tags") or [],
    }
