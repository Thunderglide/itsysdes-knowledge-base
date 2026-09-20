from __future__ import annotations

import re
from typing import Any, Iterable

from kb_pipeline.models import Article, Folder, Hierarchy, Message, MessageRef, slugify

_STRUCTURE_LISTING_LIMIT = 40
_POLISH_LISTING_MAX = 80
_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{4,}", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "для",
        "работы",
        "работа",
        "запросы",
        "запрос",
        "sql",
        "the",
        "and",
        "with",
        "from",
        "that",
        "this",
        "или",
        "при",
        "как",
        "что",
        "этот",
        "этой",
        "также",
    }
)


def tokenize(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(text or "")} - _STOPWORDS


def overlap_score(left: set[str], right: set[str]) -> int:
    return len(left & right)


def listing_for_structure(
    hierarchy: Hierarchy,
    messages: Iterable[Message],
    *,
    limit: int = _STRUCTURE_LISTING_LIMIT,
) -> list[dict[str, Any]]:
    rows = hierarchy.to_listing()
    if not rows:
        return []
    current_ids = {item.id for item in messages if item.id}
    batch_tokens = _batch_tokens(messages)
    related: list[dict[str, Any]] = []
    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for folder_path, slug, article in hierarchy.iter_articles():
        row = _listing_row(hierarchy, folder_path, slug, article)
        if article.message_ids() & current_ids:
            related.append(row)
            continue
        title_tokens = tokenize(f"{article.title} {slug.replace('-', ' ')}")
        score = overlap_score(batch_tokens, title_tokens)
        scored.append((score, _size_penalty(len(article.messages)), row["id"], row))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    picked = list(related)
    seen = {item["id"] for item in picked}
    for _score, _penalty, _aid, row in scored:
        if row["id"] in seen:
            continue
        picked.append(row)
        seen.add(row["id"])
        if len(picked) >= len(related) + limit:
            break
    return picked


def compact_polish_listing(
    listing: list[dict[str, Any]],
    *,
    title: str = "",
    folder: str = "",
) -> list[dict[str, Any]]:
    if len(listing) <= _POLISH_LISTING_MAX:
        return listing
    title_tokens = tokenize(title)
    ranked = sorted(
        listing,
        key=lambda row: (
            1 if (row.get("folder") or "") == folder else 0,
            overlap_score(title_tokens, tokenize(str(row.get("title") or ""))),
        ),
        reverse=True,
    )
    return ranked[:_POLISH_LISTING_MAX]


def guard_incoming(
    existing: Hierarchy,
    incoming: Hierarchy,
    messages: Iterable[Message],
) -> Hierarchy:
    """Drop empty/fan-out stubs so merge_hierarchy only sees grounded articles."""
    current_ids = [item.id for item in messages if item.id]
    current_set = set(current_ids)
    batch_tokens = _batch_tokens(messages)
    claimed: set[str] = set()
    kept: list[tuple[str, str, Article]] = []
    leftover_refs: dict[str, MessageRef] = {}

    existing_items: list[tuple[str, str, Article]] = []
    new_items: list[tuple[str, str, Article]] = []
    for folder_path, slug, article in incoming.iter_articles():
        refs = [ref for ref in article.messages if ref.id in current_set]
        if not refs:
            continue
        trimmed = article.model_copy(deep=True)
        trimmed.messages = refs
        if _is_existing(existing, folder_path, slug, trimmed.title):
            existing_items.append((folder_path, slug, trimmed))
        else:
            new_items.append((folder_path, slug, trimmed))

    for folder_path, slug, article in existing_items:
        unique = _claim_refs(article.messages, claimed, leftover_refs)
        if unique:
            kept.append((folder_path, slug, article.model_copy(update={"messages": unique})))

    best_existing = _best_existing(existing, batch_tokens)
    allow_new = best_existing is None
    if allow_new:
        chosen = _pick_new_article(new_items, claimed)
        chosen_key = (chosen[0], chosen[1]) if chosen is not None else None
        for folder_path, slug, article in new_items:
            if chosen_key == (folder_path, slug):
                unique = _claim_refs(article.messages, claimed, leftover_refs)
                if unique:
                    kept.append(
                        (
                            folder_path,
                            slug,
                            article.model_copy(update={"messages": unique}),
                        )
                    )
            else:
                for ref in article.messages:
                    leftover_refs.setdefault(ref.id, ref)
    else:
        for _folder, _slug, article in new_items:
            for ref in article.messages:
                leftover_refs.setdefault(ref.id, ref)

    unclaimed = [mid for mid in current_ids if mid not in claimed]
    if unclaimed:
        sink = _sink_for_leftovers(kept, best_existing)
        if sink is not None:
            folder_path, slug, article = sink
            extra = [
                leftover_refs.get(mid) or MessageRef(id=mid) for mid in unclaimed
            ]
            article.messages = _merge_ref_lists(article.messages, extra)
            claimed.update(unclaimed)
            replaced = False
            next_kept: list[tuple[str, str, Article]] = []
            for item in kept:
                if item[0] == folder_path and item[1] == slug:
                    next_kept.append((folder_path, slug, article))
                    replaced = True
                else:
                    next_kept.append(item)
            if not replaced:
                next_kept.append((folder_path, slug, article))
            kept = next_kept

    return _hierarchy_from_items(kept)


def _batch_tokens(messages: Iterable[Message]) -> set[str]:
    return tokenize(" ".join(item.text for item in messages))


def _listing_row(
    hierarchy: Hierarchy, folder_path: str, slug: str, article: Article
) -> dict[str, Any]:
    return {
        "id": hierarchy.article_id(folder_path, slug),
        "title": article.title,
        "folder": folder_path,
        "path": f"{folder_path}/{slug}.md" if folder_path else f"{slug}.md",
        "message_count": len(article.messages),
        "tags": list(article.tags),
    }


def _size_penalty(count: int) -> int:
    if 8 <= count <= 80:
        return 0
    if count < 8:
        return 8 - count
    return count - 80


def _is_existing(hierarchy: Hierarchy, folder_path: str, slug: str, title: str) -> bool:
    article_id = f"{folder_path}/{slug}" if folder_path else slug
    if hierarchy.find_by_id(article_id):
        return True
    folder = _folder_at(hierarchy, folder_path)
    if folder is None:
        return False
    title_key = title.strip().casefold()
    for existing_slug, existing in folder.articles.items():
        if existing_slug == slug or existing.title.strip().casefold() == title_key:
            return True
    return False


def _folder_at(hierarchy: Hierarchy, folder_path: str) -> Folder | None:
    folder: Folder | None = None
    current = hierarchy.folders
    for name in [part for part in folder_path.split("/") if part]:
        folder = current.get(name)
        if folder is None:
            return None
        current = folder.folders
    return folder


def _claim_refs(
    refs: list[MessageRef],
    claimed: set[str],
    leftover_refs: dict[str, MessageRef],
) -> list[MessageRef]:
    unique: list[MessageRef] = []
    for ref in refs:
        if not ref.id:
            continue
        leftover_refs.setdefault(ref.id, ref)
        if ref.id in claimed:
            continue
        claimed.add(ref.id)
        unique.append(ref)
    return unique


def _pick_new_article(
    items: list[tuple[str, str, Article]],
    claimed: set[str],
) -> tuple[str, str, Article] | None:
    ranked: list[tuple[int, str, tuple[str, str, Article]]] = []
    for folder_path, slug, article in items:
        free = sum(1 for ref in article.messages if ref.id and ref.id not in claimed)
        if free <= 0:
            continue
        ranked.append((-free, f"{folder_path}/{slug}", (folder_path, slug, article)))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][2]


def _best_existing(
    hierarchy: Hierarchy, batch_tokens: set[str]
) -> tuple[str, str, Article] | None:
    best: tuple[str, str, Article] | None = None
    best_key: tuple[int, int, str] | None = None
    for folder_path, slug, article in hierarchy.iter_articles():
        title_tokens = tokenize(f"{article.title} {slug.replace('-', ' ')}")
        score = overlap_score(batch_tokens, title_tokens)
        if score <= 0:
            continue
        key = (-score, _size_penalty(len(article.messages)), f"{folder_path}/{slug}")
        if best_key is None or key < best_key:
            best_key = key
            best = (folder_path, slug, article)
    return best


def _sink_for_leftovers(
    kept: list[tuple[str, str, Article]],
    best_existing: tuple[str, str, Article] | None,
) -> tuple[str, str, Article] | None:
    if best_existing is not None:
        folder_path, slug, source = best_existing
        for item in kept:
            if item[0] == folder_path and item[1] == slug:
                return item
        copied = source.model_copy(deep=True)
        copied.messages = []
        return folder_path, slug, copied
    if kept:
        return kept[0]
    return None


def _merge_ref_lists(
    current: list[MessageRef], extra: list[MessageRef]
) -> list[MessageRef]:
    by_id = {item.id: item for item in current if item.id}
    for ref in extra:
        if ref.id and ref.id not in by_id:
            by_id[ref.id] = ref
    return list(by_id.values())


def _hierarchy_from_items(items: list[tuple[str, str, Article]]) -> Hierarchy:
    hierarchy = Hierarchy()
    for folder_path, slug, article in items:
        folder = _ensure_folder(hierarchy, folder_path)
        if folder is None:
            continue
        folder.articles[slug or slugify(article.title)] = article
    return hierarchy


def _ensure_folder(hierarchy: Hierarchy, folder_path: str) -> Folder | None:
    parts = [part for part in folder_path.split("/") if part]
    if not parts:
        return None
    current = hierarchy.folders
    folder: Folder | None = None
    for name in parts:
        folder = current.setdefault(name, Folder())
        current = folder.folders
    return folder
