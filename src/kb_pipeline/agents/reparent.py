from __future__ import annotations

import json
from typing import Any

from kb_pipeline.agents.common import complete_json
from kb_pipeline.config import Config
from kb_pipeline.content_stitch import first_heading
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import Hierarchy
from kb_pipeline.persist import load_article_record
from kb_pipeline.prompts import load_prompt
from kb_pipeline.taxonomy import load_taxonomy

_BATCH = 80


def propose_reparents(
    backend: LLMBackend,
    config: Config,
    hierarchy: Hierarchy,
) -> dict[str, Any]:
    taxonomy = load_taxonomy(config)
    allowed = set(taxonomy.folder_paths())
    listing = hierarchy.to_listing()
    by_l1: dict[str, list[dict[str, Any]]] = {}
    for row in listing:
        folder = str(row.get("folder") or "")
        l1 = folder.split("/", 1)[0] or folder
        by_l1.setdefault(l1, []).append(_compact_row(config, hierarchy, row))
    actions: list[dict[str, Any]] = []
    proposed_folders: list[dict[str, Any]] = []
    used: set[str] = set()
    system = load_prompt("reparent")
    for _l1, rows in by_l1.items():
        for batch in (rows[index : index + _BATCH] for index in range(0, len(rows), _BATCH)):
            user = json.dumps(
                {
                    "taxonomy": taxonomy.listing_payload(),
                    "articles": batch,
                },
                ensure_ascii=False,
            )
            data = complete_json(backend, system, user)
            moves, extra = validate_reparent_actions(
                data.get("actions") or data.get("moves") or [],
                hierarchy,
                allowed,
            )
            for item in moves:
                if item["id"] in used or item["folder"] == item.get("from_folder"):
                    continue
                actions.append(item)
                used.add(item["id"])
            proposed_folders.extend(extra)
            for item in data.get("proposed_folders") or []:
                if isinstance(item, dict) and item.get("folder"):
                    proposed_folders.append(
                        {
                            "folder": str(item.get("folder") or "").strip(),
                            "reason": str(item.get("reason") or "").strip(),
                        }
                    )
    return {
        "kind": "reparent",
        "actions": actions,
        "proposed_folders": _unique_folders(proposed_folders),
    }


def validate_reparent_actions(
    raw_actions: Any,
    hierarchy: Hierarchy,
    allowed_folders: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    proposed: list[dict[str, Any]] = []
    if not isinstance(raw_actions, list):
        return kept, proposed
    seen: set[str] = set()
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        article_id = str(item.get("id") or item.get("source") or "").strip()
        folder = str(item.get("folder") or "").strip().strip("/")
        if not article_id or not folder or article_id in seen:
            continue
        found = hierarchy.find_by_id(article_id)
        if found is None:
            continue
        current_folder, _slug, _article = found
        if folder == current_folder:
            continue
        if folder not in allowed_folders:
            proposed.append(
                {
                    "folder": folder,
                    "reason": str(item.get("reason") or "unknown folder"),
                    "id": article_id,
                }
            )
            continue
        seen.add(article_id)
        kept.append(
            {
                "id": article_id,
                "folder": folder,
                "from_folder": current_folder,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return kept, proposed


def _compact_row(
    config: Config, hierarchy: Hierarchy, row: dict[str, Any]
) -> dict[str, Any]:
    found = hierarchy.find_by_id(str(row["id"]))
    heading = ""
    if found:
        folder, slug, article = found
        record = load_article_record(
            config, str(row["id"]), article.title, folder, slug, tags=article.tags
        )
        heading = first_heading(record.final or record.draft)
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "folder": row.get("folder") or "",
        "message_count": row.get("message_count") or 0,
        "heading": heading,
    }


def _unique_folders(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("folder") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
