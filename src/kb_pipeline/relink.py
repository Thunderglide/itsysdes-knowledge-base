from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from kb_pipeline.config import Config
from kb_pipeline.models import Hierarchy
from kb_pipeline.persist import load_article_record, save_article_artifacts

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_ALIASES_NAME = "link-aliases.json"
_PLAN_ALIAS_FILES = ("merge-plan.json", "cleanup-plan.json")


def article_rel_path(folder: str, slug: str) -> str:
    return f"{folder}/{slug}.md" if folder else f"{slug}.md"


def slug_map(hierarchy: Hierarchy) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for folder, slug, _article in hierarchy.iter_articles():
        mapping[slug] = article_rel_path(folder, slug)
    return mapping


def slug_from_article_id(article_id: str) -> str:
    _folder, _, slug = article_id.rpartition("/")
    return slug or article_id


def aliases_from_plan(plan: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for action in plan.get("actions") or []:
        target = str(action.get("target") or "").strip()
        if not target:
            continue
        target_slug = slug_from_article_id(target)
        for source in action.get("sources") or []:
            source_slug = slug_from_article_id(str(source))
            if source_slug and target_slug and source_slug != target_slug:
                mapping[source_slug] = target_slug
    return mapping


def aliases_path(config: Config) -> Path:
    return config.output_dir_resolved / "curate" / _ALIASES_NAME


def load_stored_aliases(config: Config) -> dict[str, str]:
    path = aliases_path(config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("aliases") if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if key and value}


def persist_aliases(config: Config, extra: dict[str, str]) -> None:
    if not extra:
        return
    path = aliases_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = load_stored_aliases(config)
    merged.update(extra)
    path.write_text(
        json.dumps({"aliases": merged}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_plan_aliases(config: Config) -> dict[str, str]:
    mapping = load_stored_aliases(config)
    curate_dir = config.output_dir_resolved / "curate"
    for name in _PLAN_ALIAS_FILES:
        path = curate_dir / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            mapping.update(aliases_from_plan(data))
    return mapping


def should_skip_href(href: str) -> bool:
    text = (href or "").strip()
    if not text:
        return True
    lower = text.lower()
    if lower.startswith(("http://", "https://", "mailto:", "id:")):
        return True
    path = href_path(text)
    return path.startswith("files/") or "/files/" in f"/{path}"


def href_path(href: str) -> str:
    path, _, _fragment = (href or "").strip().partition("#")
    path = unquote(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def href_fragment(href: str) -> str:
    if "#" in (href or ""):
        return "#" + href.split("#", 1)[1]
    return ""


def resolve_href(
    href: str,
    slugs: dict[str, str],
    aliases: dict[str, str],
) -> tuple[str | None, str]:
    if should_skip_href(href):
        return None, "skip"
    path = href_path(href)
    if not path.lower().endswith(".md"):
        return None, "skip"
    slug = Path(path).stem
    target = slugs.get(slug)
    via = "slug"
    if target is None:
        alias = aliases.get(slug)
        if alias:
            target = slugs.get(alias)
            via = "alias"
    if target is None:
        return None, "unresolved"
    if path == target:
        return None, "ok"
    return target + href_fragment(href), via


def rewrite_markdown(
    text: str,
    slugs: dict[str, str],
    aliases: dict[str, str],
) -> tuple[str, list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []

    def replace(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        new_href, status = resolve_href(href, slugs, aliases)
        if status in {"slug", "alias"} and new_href:
            changes.append(
                {"from": href, "to": new_href, "label": label, "via": status}
            )
            return f"[{label}]({new_href})"
        if status == "unresolved":
            changes.append(
                {"from": href, "to": None, "label": label, "via": "unresolved"}
            )
        return match.group(0)

    return _LINK_RE.sub(replace, text or ""), changes


def propose_relinks(config: Config, hierarchy: Hierarchy) -> dict[str, Any]:
    slugs = slug_map(hierarchy)
    aliases = load_plan_aliases(config)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    seen_unresolved: set[tuple[str, str]] = set()
    files: list[str] = []
    for folder, slug, article in hierarchy.iter_articles():
        article_id = hierarchy.article_id(folder, slug)
        record = load_article_record(
            config, article_id, article.title, folder, slug, tags=article.tags
        )
        _new_text, changes = rewrite_markdown(record.final, slugs, aliases)
        rewrote = False
        for change in changes:
            if change["to"]:
                key = (change["from"], change["to"])
                row = grouped.setdefault(
                    key,
                    {
                        "from": change["from"],
                        "to": change["to"],
                        "via": change["via"],
                        "files": set(),
                    },
                )
                row["files"].add(article_id)
                rewrote = True
                continue
            marker = (change["from"], change["label"])
            if marker in seen_unresolved:
                continue
            seen_unresolved.add(marker)
            unresolved.append(
                {
                    "href": change["from"],
                    "label": change["label"],
                    "reason": "missing article",
                    "file": article_id,
                }
            )
        if rewrote:
            files.append(article_id)
    rewrites = [
        {
            "from": item["from"],
            "to": item["to"],
            "via": item["via"],
            "files_count": len(item["files"]),
        }
        for item in sorted(
            grouped.values(),
            key=lambda row: (-len(row["files"]), str(row["from"])),
        )
    ]
    return {
        "kind": "relink",
        "rewrites": rewrites,
        "unresolved": unresolved,
        "files": files,
    }


def apply_relink_plan(
    config: Config,
    hierarchy: Hierarchy,
    plan: dict[str, Any],
) -> list[str]:
    mapping = _rewrite_mapping(plan.get("rewrites") or [])
    if not mapping:
        return []
    touched: list[str] = []
    for folder, slug, article in hierarchy.iter_articles():
        article_id = hierarchy.article_id(folder, slug)
        record = load_article_record(
            config, article_id, article.title, folder, slug, tags=article.tags
        )
        if not record.final:
            continue
        updated = _apply_mapping(record.final, mapping)
        if updated == record.final:
            continue
        record.final = updated
        save_article_artifacts(config, record, write_final=True)
        touched.append(article_id)
    return touched


def rewrite_all_finals(
    config: Config,
    hierarchy: Hierarchy,
    extra_aliases: dict[str, str] | None = None,
) -> list[str]:
    if extra_aliases:
        persist_aliases(config, extra_aliases)
    slugs = slug_map(hierarchy)
    aliases = load_plan_aliases(config)
    if extra_aliases:
        aliases = {**aliases, **extra_aliases}
    touched: list[str] = []
    for folder, slug, article in hierarchy.iter_articles():
        article_id = hierarchy.article_id(folder, slug)
        record = load_article_record(
            config, article_id, article.title, folder, slug, tags=article.tags
        )
        if not record.final:
            continue
        updated, changes = rewrite_markdown(record.final, slugs, aliases)
        if updated == record.final or not any(item.get("to") for item in changes):
            continue
        record.final = updated
        save_article_artifacts(config, record, write_final=True)
        touched.append(article_id)
    return touched


def _rewrite_mapping(rewrites: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in rewrites:
        source = str(item.get("from") or "").strip()
        dest = str(item.get("to") or "").strip()
        if not source or not dest:
            continue
        mapping[source] = dest
        decoded = unquote(source)
        mapping.setdefault(decoded, dest)
        mapping.setdefault(href_path(source), dest)
    return mapping


def _apply_mapping(text: str, mapping: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        if should_skip_href(href):
            return match.group(0)
        if href in mapping:
            return f"[{label}]({mapping[href]})"
        decoded = unquote(href)
        if decoded in mapping:
            return f"[{label}]({mapping[decoded]})"
        path = href_path(href)
        if path in mapping:
            dest = href_path(mapping[path]) + href_fragment(href)
            return f"[{label}]({dest})"
        return match.group(0)

    return _LINK_RE.sub(replace, text)
