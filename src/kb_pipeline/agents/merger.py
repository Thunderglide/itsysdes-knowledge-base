from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from kb_pipeline.agents.cleanup_plan import normalize_title
from kb_pipeline.agents.common import complete_json
from kb_pipeline.config import Config, CurateSettings
from kb_pipeline.content_stitch import first_heading
from kb_pipeline.llm.base import LLMBackend
from kb_pipeline.models import Hierarchy, slugify
from kb_pipeline.persist import load_article_record
from kb_pipeline.prompts import load_prompt
from kb_pipeline.taxonomy import load_taxonomy

_SQL_RE = re.compile(
    r"^sql[-–—\s]*запросы?\s+для\s+работы\s+с(?:о)?\s+(.+)$",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)
_PARENT_CANDIDATES = 20
_LLM_STUB_BATCH = 40


def propose_merges(
    backend: LLMBackend,
    config: Config,
    hierarchy: Hierarchy,
) -> dict[str, Any]:
    settings = config.curate
    listing = hierarchy.to_listing()
    by_id = {row["id"]: row for row in listing}
    stubs = [
        _stub_row(config, row)
        for row in listing
        if 0 < int(row.get("message_count") or 0) <= settings.merge_max_messages
    ]
    used: set[str] = set()
    actions: list[dict[str, Any]] = []
    for cluster in _deterministic_clusters(stubs, by_id, config):
        action = _action_from_cluster(cluster, by_id, settings, config)
        if action is None:
            continue
        validated = validate_merge_actions([action], hierarchy, settings)
        if not validated:
            continue
        item = validated[0]
        if any(source in used for source in item["sources"]):
            continue
        actions.append(item)
        used.update(item["sources"])
        used.add(item["target"])
    leftover = [row for row in stubs if row["id"] not in used]
    if leftover:
        for batch in _batches(leftover, _LLM_STUB_BATCH):
            payload_actions = _llm_merge_batch(
                backend, config, hierarchy, listing, batch, by_id, used
            )
            for item in payload_actions:
                if any(source in used for source in item["sources"]):
                    continue
                actions.append(item)
                used.update(item["sources"])
                used.add(item["target"])
    return {"kind": "merge", "actions": actions}


def validate_merge_actions(
    raw_actions: Any,
    hierarchy: Hierarchy,
    settings: CurateSettings,
    *,
    allow_large_sources: bool = False,
) -> list[dict[str, Any]]:
    by_id = {row["id"]: row for row in hierarchy.to_listing()}
    kept: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    if not isinstance(raw_actions, list):
        return kept
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        sources, create, target, title = _resolve_target(item, hierarchy, by_id)
        if not target or not sources:
            continue
        if any(source in used_sources or source == target for source in sources):
            continue
        valid_sources: list[str] = []
        extra_msgs = 0
        for source_id in sources:
            if source_id not in by_id:
                continue
            count = int(by_id[source_id].get("message_count") or 0)
            if count <= 0:
                continue
            if not allow_large_sources and count > settings.merge_max_messages:
                continue
            valid_sources.append(source_id)
            extra_msgs += count
        if not valid_sources:
            continue
        target_count = int(by_id.get(target, {}).get("message_count") or 0)
        if not create and target not in by_id:
            continue
        if not create and target_count >= settings.merge_target_max:
            continue
        if target_count + extra_msgs > settings.merge_target_max:
            continue
        action = {
            "sources": valid_sources,
            "target": target,
            "reason": str(item.get("reason") or "").strip(),
            "create": create,
        }
        if create:
            action["title"] = title
        kept.append(action)
        used_sources.update(valid_sources)
    return kept


def _resolve_target(
    item: dict[str, Any],
    hierarchy: Hierarchy,
    by_id: dict[str, dict[str, Any]],
) -> tuple[list[str], bool, str, str]:
    sources_raw = item.get("sources") or []
    if isinstance(sources_raw, str):
        sources_raw = [sources_raw]
    sources = [str(source).strip() for source in sources_raw if str(source).strip()]
    new_spec = item.get("new") if isinstance(item.get("new"), dict) else {}
    raw_target = str(item.get("target") or "").strip()
    title = str(new_spec.get("title") or item.get("title") or "").strip()
    folder = str(new_spec.get("folder") or item.get("folder") or "").strip()
    slug = str(new_spec.get("slug") or item.get("slug") or "").strip()
    create = bool(item.get("create")) or raw_target == "new"
    if create:
        if not title:
            title = str(by_id.get(sources[0], {}).get("title") or "").strip() if sources else ""
        if not folder and sources:
            folder = str(by_id.get(sources[0], {}).get("folder") or "")
        slug = slug or slugify(title)
        if not folder or not slug:
            return [], False, "", ""
        target = hierarchy.article_id(folder, slug)
        if target in by_id:
            return sources, False, target, str(by_id[target].get("title") or title)
        title = title or slug
        return sources, True, target, title
    if raw_target in by_id:
        return sources, False, raw_target, str(by_id[raw_target].get("title") or "")
    if folder and (slug or title):
        slug = slug or slugify(title)
        target = hierarchy.article_id(folder, slug)
        if target not in by_id:
            return sources, True, target, title or slug
        return sources, False, target, str(by_id[target].get("title") or title)
    return sources, False, raw_target, title


def _deterministic_clusters(
    stubs: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    config: Config,
) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stubs:
        key = _cluster_key(str(row.get("title") or ""))
        if key:
            grouped[key].append(row)
            continue
        grouped[f"title:{normalize_title(str(row.get('title') or ''))}"].append(row)
    clusters: list[list[dict[str, Any]]] = []
    settings = config.curate
    for _key, rows in grouped.items():
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if row["id"] in seen or row["id"] not in by_id:
                continue
            seen.add(row["id"])
            unique.append(row)
        if len(unique) < 2:
            continue
        clusters.extend(_chunk_by_messages(unique, settings.merge_target_max))
    return clusters


def _action_from_cluster(
    cluster: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    settings: CurateSettings,
    config: Config,
) -> dict[str, Any] | None:
    if not cluster:
        return None
    taxonomy = load_taxonomy(config)
    sources = [row["id"] for row in cluster]
    key = _cluster_key(str(cluster[0].get("title") or ""))
    kind = key.split(":", 1)[0] if key else ""
    if not kind:
        target_row = max(cluster, key=lambda row: int(row.get("message_count") or 0))
        rest = [row["id"] for row in cluster if row["id"] != target_row["id"]]
        if not rest:
            return None
        return {
            "sources": rest,
            "target": target_row["id"],
            "reason": "same-title stubs",
        }
    parent = _best_parent(cluster, by_id, settings)
    if parent:
        return {
            "sources": sources,
            "target": parent["id"],
            "reason": "cluster merge into parent",
        }
    title = _new_cluster_title(cluster)
    folder = taxonomy.cluster_folder(kind, str(cluster[0].get("folder") or ""))
    return {
        "sources": sources,
        "target": "new",
        "new": {
            "title": title,
            "slug": slugify(title),
            "folder": folder,
        },
        "reason": "cluster merge into new article",
        "create": True,
        "title": title,
        "folder": folder,
    }


def _best_parent(
    cluster: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    settings: CurateSettings,
) -> dict[str, Any] | None:
    cluster_ids = {row["id"] for row in cluster}
    cluster_msgs = sum(int(row.get("message_count") or 0) for row in cluster)
    tokens = _tokens(" ".join(str(row.get("title") or "") for row in cluster))
    folders = {str(row.get("folder") or "") for row in cluster}
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in by_id.values():
        if row["id"] in cluster_ids:
            continue
        count = int(row.get("message_count") or 0)
        if count < 6 or count >= settings.merge_target_max:
            continue
        if count + cluster_msgs > settings.merge_target_max:
            continue
        score = len(tokens & _tokens(str(row.get("title") or "")))
        if str(row.get("folder") or "") in folders:
            score += 1
        if score >= 2:
            scored.append((score, row))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], int(item[1].get("message_count") or 0)))
    return scored[0][1]


def _new_cluster_title(cluster: list[dict[str, Any]]) -> str:
    title = str(cluster[0].get("title") or "").strip()
    key = _cluster_key(title)
    if key and key.startswith("sql:"):
        topic = key.split(":", 1)[1]
        return f"SQL: {topic}"
    if key and ":" in key:
        kind, rest = key.split(":", 1)
        if rest:
            return f"{kind.upper()}: {rest}" if kind != "plantuml" else f"PlantUML: {rest}"
    return title or "Объединённая статья"


def _cluster_key(title: str) -> str:
    norm = normalize_title(title)
    match = _SQL_RE.match(norm)
    if match:
        return "sql:" + match.group(1).strip(" .,:;")
    if norm.startswith("plantuml"):
        rest = norm[len("plantuml") :].strip(" :—-")
        return "plantuml:" + (rest or "general")
    if norm.startswith("bpmn"):
        rest = norm[4:].strip(" :—-")
        return "bpmn:" + (rest or "general")
    return ""


def _chunk_by_messages(
    rows: list[dict[str, Any]], max_messages: int
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        count = int(row.get("message_count") or 0)
        if current and total + count > max_messages:
            chunks.append(current)
            current = []
            total = 0
        current.append(row)
        total += count
    if current:
        chunks.append(current)
    return chunks


def _llm_merge_batch(
    backend: LLMBackend,
    config: Config,
    hierarchy: Hierarchy,
    listing: list[dict[str, Any]],
    stubs: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    used: set[str],
) -> list[dict[str, Any]]:
    parents = _parent_candidates(stubs, by_id, config.curate, used)
    system = load_prompt("merger")
    user = json.dumps(
        {
            "stubs": stubs,
            "parent_candidates": parents,
            "articles": parents + stubs,
            "rules": {
                "merge_max_messages": config.curate.merge_max_messages,
                "merge_target_max": config.curate.merge_target_max,
            },
        },
        ensure_ascii=False,
    )
    data = complete_json(backend, system, user)
    return validate_merge_actions(data.get("actions") or [], hierarchy, config.curate)


def _parent_candidates(
    stubs: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    settings: CurateSettings,
    used: set[str],
) -> list[dict[str, Any]]:
    folders = {str(row.get("folder") or "") for row in stubs}
    tokens = _tokens(" ".join(str(row.get("title") or "") for row in stubs))
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in by_id.values():
        if row["id"] in used:
            continue
        count = int(row.get("message_count") or 0)
        if count <= settings.merge_max_messages or count >= settings.merge_target_max:
            continue
        score = len(tokens & _tokens(str(row.get("title") or "")))
        if str(row.get("folder") or "") in folders:
            score += 2
        scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [row for _score, row in scored[:_PARENT_CANDIDATES]]


def _stub_row(config: Config, row: dict[str, Any]) -> dict[str, Any]:
    record = load_article_record(
        config,
        str(row["id"]),
        str(row.get("title") or ""),
        str(row.get("folder") or ""),
        _slug_from_id(str(row["id"])),
        tags=row.get("tags") or [],
    )
    heading = first_heading(record.final or record.draft) or str(row.get("title") or "")
    return {**row, "heading": heading}


def _slug_from_id(article_id: str) -> str:
    _, _, slug = article_id.rpartition("/")
    return slug or article_id


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(text or "")}


def _batches(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
