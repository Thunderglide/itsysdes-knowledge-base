from __future__ import annotations

import json
from typing import Any

from kb_pipeline.agents.common import complete_json
from kb_pipeline.config import Config, CurateSettings
from kb_pipeline.content_stitch import parse_sections
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import Hierarchy, slugify
from kb_pipeline.persist import load_article_record
from kb_pipeline.prompts import load_prompt


def propose_splits(
    backend: LLMBackend,
    config: Config,
    hierarchy: Hierarchy,
) -> dict[str, Any]:
    settings = config.curate
    candidates = split_candidates(config, hierarchy, settings)
    system = load_prompt("splitter")
    user = json.dumps(
        {
            "candidates": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "folder": item["folder"],
                    "message_count": item["message_count"],
                    "char_count": item["char_count"],
                    "needs_outline": item.get("needs_outline", False),
                    "sections": [
                        {
                            "heading": section["heading"],
                            "citation_count": len(section["message_ids"]),
                        }
                        for section in item["sections"]
                    ],
                }
                for item in candidates
            ],
            "rules": {
                "split_min_messages": settings.split_min_messages,
                "split_min_chars": settings.split_min_chars,
            },
        },
        ensure_ascii=False,
    )
    data = complete_json(backend, system, user)
    actions = resolve_split_actions(data.get("actions") or [], candidates, hierarchy)
    return {"kind": "split", "actions": actions}


def parse_markdown_sections(text: str) -> list[dict[str, Any]]:
    return [section.as_parser_dict() for section in parse_sections(text)]


def resolve_split_actions(
    raw_actions: Any,
    candidates: list[dict[str, Any]],
    hierarchy: Hierarchy,
) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in candidates}
    kept: list[dict[str, Any]] = []
    if not isinstance(raw_actions, list):
        return kept
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        candidate = by_id.get(source)
        if candidate is None:
            found = hierarchy.find_by_id(source)
            if found is None:
                continue
            folder, slug, article = found
            candidate = {
                "id": source,
                "title": article.title,
                "folder": folder,
                "slug": slug,
                "sections": [],
            }
        heading_ids = _heading_to_ids(candidate["sections"])
        owned: set[str] = set()
        parts: list[dict[str, Any]] = []
        for part in item.get("parts") or []:
            if not isinstance(part, dict):
                continue
            title = str(part.get("title") or "").strip()
            folder = str(part.get("folder") or candidate["folder"] or "").strip()
            slug = str(part.get("slug") or "").strip() or slugify(title)
            if not title or not slug:
                continue
            article_id = f"{folder}/{slug}" if folder else slug
            if article_id == source:
                continue
            headings = [
                str(heading).strip()
                for heading in (part.get("headings") or [])
                if str(heading).strip()
            ]
            message_ids: list[str] = []
            explicit = part.get("message_ids")
            if isinstance(explicit, list) and explicit:
                for mid in explicit:
                    text = str(mid).strip()
                    if text and text not in owned:
                        message_ids.append(text)
                        owned.add(text)
            else:
                for heading in headings:
                    for mid in heading_ids.get(_norm_heading(heading), []):
                        if mid not in owned:
                            message_ids.append(mid)
                            owned.add(mid)
            if not message_ids:
                continue
            parts.append(
                {
                    "title": title,
                    "slug": slug,
                    "folder": folder,
                    "headings": headings,
                    "message_ids": message_ids,
                }
            )
        if not parts:
            continue
        found = hierarchy.find_by_id(source)
        remainder: list[str] = []
        if found:
            remainder = [
                ref.id
                for ref in found[2].messages
                if ref.id and ref.id not in owned
            ]
        kept.append(
            {
                "source": source,
                "parts": parts,
                "remainder_ids": remainder,
            }
        )
    return kept


def split_candidates(
    config: Config,
    hierarchy: Hierarchy,
    settings: CurateSettings,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in hierarchy.to_listing():
        found = hierarchy.find_by_id(str(row["id"]))
        if not found:
            continue
        folder, slug, article = found
        record = load_article_record(
            config, str(row["id"]), article.title, folder, slug, tags=article.tags
        )
        text = record.final or record.draft
        count = len(article.messages)
        chars = len(text)
        if count < settings.split_min_messages and chars < settings.split_min_chars:
            continue
        sections = parse_markdown_sections(text)
        heading_sections = [item for item in sections if item.get("heading")]
        needs_outline = len(heading_sections) < 2
        if needs_outline and count < settings.split_min_messages:
            continue
        rows.append(
            {
                "id": row["id"],
                "title": article.title,
                "folder": folder,
                "slug": slug,
                "message_count": count,
                "char_count": chars,
                "needs_outline": needs_outline,
                "sections": sections,
            }
        )
    return rows


def _heading_to_ids(sections: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for section in sections:
        key = _norm_heading(str(section.get("heading") or ""))
        mapping.setdefault(key, [])
        for mid in section.get("message_ids") or []:
            if mid not in mapping[key]:
                mapping[key].append(mid)
    return mapping


def _norm_heading(value: str) -> str:
    return " ".join(value.strip().lower().split())
