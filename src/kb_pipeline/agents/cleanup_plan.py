from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from kb_pipeline.config import Config
from kb_pipeline.models import Hierarchy
from kb_pipeline.taxonomy import Taxonomy, load_taxonomy

_TYPOS = {
    "маркерплейсом": "маркетплейсом",
    "маркерплейса": "маркетплейса",
    "маркерплейс": "маркетплейс",
}


def normalize_title(title: str) -> str:
    text = (title or "").strip().casefold().replace("ё", "е")
    text = re.sub(r"[«»\"'`]", "", text)
    text = re.sub(r"\s+", " ", text)
    for src, dst in _TYPOS.items():
        text = text.replace(src, dst)
    return text


def propose_cleanup(config: Config, hierarchy: Hierarchy) -> dict[str, Any]:
    taxonomy = load_taxonomy(config)
    listing = {
        row["id"]: row
        for row in hierarchy.to_listing()
    }
    used: set[str] = set()
    actions: list[dict[str, Any]] = []
    for article_id, row in listing.items():
        if int(row.get("message_count") or 0) == 0:
            actions.append(
                {"type": "delete", "id": article_id, "reason": "empty"}
            )
            used.add(article_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for article_id, row in listing.items():
        if article_id in used:
            continue
        title_key = normalize_title(str(row.get("title") or ""))
        slug = _slug_from_id(article_id)
        if title_key:
            groups[f"title:{title_key}"].append(article_id)
        if slug:
            groups[f"slug:{slug}"].append(article_id)
    for key, ids in groups.items():
        unique_ids = [item for item in _unique(ids) if item not in used]
        folders = {listing[item]["folder"] for item in unique_ids}
        if len(unique_ids) < 2 or len(folders) < 2:
            continue
        target = _pick_duplicate_target(unique_ids, listing, taxonomy)
        sources = [item for item in unique_ids if item != target]
        if not sources:
            continue
        actions.append(
            {
                "type": "merge",
                "sources": sources,
                "target": target,
                "reason": "duplicate title" if key.startswith("title:") else "duplicate slug",
            }
        )
        used.add(target)
        used.update(sources)
    return {"kind": "cleanup", "actions": actions}


def _pick_duplicate_target(
    ids: list[str],
    listing: dict[str, dict[str, Any]],
    taxonomy: Taxonomy,
) -> str:
    def rank(article_id: str) -> tuple[int, int, str]:
        row = listing[article_id]
        count = int(row.get("message_count") or 0)
        return (count, -taxonomy.duplicate_rank(str(row.get("folder") or "")), article_id)

    return max(ids, key=rank)


def _slug_from_id(article_id: str) -> str:
    _folder, _, slug = article_id.rpartition("/")
    return slug or article_id


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out
