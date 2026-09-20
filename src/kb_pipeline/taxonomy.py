from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

from kb_pipeline.config import Config

_PACKAGE_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Taxonomy:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        raw = data or {}
        self.folders: list[dict[str, Any]] = list(raw.get("folders") or [])
        self.duplicate_priority: list[str] = [
            str(item) for item in (raw.get("duplicate_priority") or [])
        ]
        self.folder_aliases: dict[str, str] = {
            str(key): str(value)
            for key, value in (raw.get("folder_aliases") or {}).items()
        }
        self.cluster_folders: dict[str, str] = {
            str(key): str(value)
            for key, value in (raw.get("cluster_folders") or {}).items()
        }
        self.topics: list[str] = _as_ids(raw.get("topics"))
        self.tech: list[str] = _as_ids(raw.get("tech"))
        self.formats: list[str] = _as_ids(raw.get("formats"))

    def folder_paths(self) -> list[str]:
        paths: list[str] = []
        for item in self.folders:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            paths.append(name)
            for child in item.get("children") or []:
                child_name = str(child).strip()
                if child_name:
                    paths.append(f"{name}/{child_name}")
        return paths

    def allowed_folder_set(self) -> set[str]:
        allowed = set(self.folder_paths())
        allowed.update(self.folder_aliases.keys())
        allowed.update(self.folder_aliases.values())
        return allowed

    def allowed_tags(self) -> set[str]:
        return set(self.topics) | set(self.tech) | set(self.formats)

    def is_allowed_folder(self, folder: str) -> bool:
        text = str(folder or "").strip()
        if not text:
            return False
        if text in self.allowed_folder_set():
            return True
        l1 = text.split("/", 1)[0]
        return l1 in self.allowed_folder_set()

    def duplicate_rank(self, folder: str) -> int:
        l1 = (folder or "").split("/", 1)[0]
        try:
            return self.duplicate_priority.index(l1)
        except ValueError:
            return len(self.duplicate_priority) + 1

    def cluster_folder(self, kind: str, fallback: str = "") -> str:
        return self.cluster_folders.get(kind) or fallback

    def listing_payload(self) -> dict[str, Any]:
        return {
            "folders": self.folder_paths(),
            "topics": self.topics,
            "tech": self.tech,
            "formats": self.formats,
        }


def taxonomy_path(config: Config | None = None) -> Path | None:
    candidates: list[Path] = []
    if config is not None:
        raw = config.curate.taxonomy_path
        if raw:
            path = Path(raw)
            candidates.append(config.resolve_path(path) if not path.is_absolute() else path)
        if config.config_path is not None:
            candidates.append(config.config_path.parent / "taxonomy.yaml")
    candidates.extend(
        [
            Path.cwd() / "taxonomy.yaml",
            _REPO_ROOT / "taxonomy.yaml",
            _PACKAGE_ROOT / "taxonomy.yaml",
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return None


@lru_cache(maxsize=4)
def _load_from_path(path: str) -> Taxonomy:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        data = {}
    return Taxonomy(data)


def load_taxonomy(config: Config | None = None) -> Taxonomy:
    path = taxonomy_path(config)
    if path is None:
        return Taxonomy()
    return _load_from_path(str(path))


def filter_tags(
    tags: Iterable[str],
    taxonomy: Taxonomy | None = None,
    *,
    max_tags: int = 8,
) -> tuple[list[str], list[str]]:
    allowed = taxonomy.allowed_tags() if taxonomy is not None else set()
    kept: list[str] = []
    proposed: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = str(raw or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        if allowed and tag not in allowed:
            proposed.append(tag)
            continue
        kept.append(tag)
        if len(kept) >= max_tags:
            break
    return kept, proposed


def union_tags(*groups: Iterable[str], taxonomy: Taxonomy | None = None) -> list[str]:
    merged: list[str] = []
    for group in groups:
        merged.extend(group)
    kept, _proposed = filter_tags(merged, taxonomy)
    return kept


def _as_ids(value: Any) -> list[str]:
    if not value:
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("id") or item.get("name") or "").strip().lower()
        else:
            text = str(item or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        ids.append(text)
    return ids
