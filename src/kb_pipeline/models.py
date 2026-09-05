from __future__ import annotations

import re
from typing import Any, Iterable, Iterator

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    path: str
    description: str = ""


class Message(BaseModel):
    id: str
    author: str
    date: str
    text: str
    attachments: list[Attachment] = Field(default_factory=list)
    part_file: str = ""
    subchat_id: str = ""


class MessageRef(BaseModel):
    id: str
    context: str = ""
    part: str = ""


class Article(BaseModel):
    title: str
    source_subchat: str = ""
    messages: list[MessageRef] = Field(default_factory=list)

    def message_ids(self) -> set[str]:
        return {item.id for item in self.messages}


class Folder(BaseModel):
    articles: dict[str, Article] = Field(default_factory=dict)
    folders: dict[str, Folder] = Field(default_factory=dict)


class Hierarchy(BaseModel):
    folders: dict[str, Folder] = Field(default_factory=dict)

    def iter_articles(self) -> Iterator[tuple[str, str, Article]]:
        """Yield (folder_path, slug, article). folder_path uses '/'."""
        yield from _walk_articles(self.folders, prefix="")

    def find_by_id(self, article_id: str) -> tuple[str, str, Article] | None:
        folder_path, _, slug = article_id.rpartition("/")
        for path, found_slug, article in self.iter_articles():
            if path == folder_path and found_slug == slug:
                return path, found_slug, article
        return None

    def article_id(self, folder_path: str, slug: str) -> str:
        return f"{folder_path}/{slug}" if folder_path else slug

    def to_listing(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for folder_path, slug, article in self.iter_articles():
            rows.append(
                {
                    "id": self.article_id(folder_path, slug),
                    "title": article.title,
                    "folder": folder_path,
                    "path": f"{folder_path}/{slug}.md" if folder_path else f"{slug}.md",
                }
            )
        return rows


class CriticReport(BaseModel):
    remarks: list[dict[str, str]] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class ArticleRecord(BaseModel):
    article_id: str
    title: str
    folder: str
    slug: str
    draft: str = ""
    critic_comments: dict[str, Any] = Field(default_factory=dict)
    final: str = ""


class WorkItem(BaseModel):
    subchat_id: str
    folder_name: str
    part_file: str
    part_index: int
    chat_db_id: int
    telegram_id: int
    thread_id: int | None = None
    first_message_id: int
    last_message_id: int


def _walk_articles(
    folders: dict[str, Folder], prefix: str
) -> Iterator[tuple[str, str, Article]]:
    for name, folder in folders.items():
        path = f"{prefix}/{name}" if prefix else name
        for slug, article in folder.articles.items():
            yield path, slug, article
        yield from _walk_articles(folder.folders, path)


_SLUG_RE = re.compile(r"[^\w\-а-яё]+", re.IGNORECASE)


def coerce_hierarchy(data: dict[str, Any]) -> Hierarchy:
    if "folders" in data:
        return Hierarchy.model_validate(data)
    folders: dict[str, Folder] = {}
    for key, value in data.items():
        if isinstance(value, list):
            articles: dict[str, Article] = {}
            for item in value:
                if isinstance(item, str):
                    articles[slugify(item)] = Article(title=item)
                elif isinstance(item, dict) and "title" in item:
                    title = str(item["title"])
                    slug = str(item.get("slug") or slugify(title))
                    articles[slug] = Article.model_validate(
                        {**item, "title": title}
                    )
            folders[key] = Folder(articles=articles)
        elif isinstance(value, dict):
            folders[key] = Folder.model_validate(value)
    return Hierarchy(folders=folders)


def slugify(title: str) -> str:
    slug = title.strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = _SLUG_RE.sub("", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:80] or "untitled"


def merge_hierarchy(
    existing: Hierarchy,
    incoming: Hierarchy,
    *,
    current_message_ids: set[str],
    current_part: str,
    current_subchat: str,
) -> tuple[Hierarchy, list[str]]:
    """Upsert incoming into existing. Never delete unseen articles. Return touched ids."""
    merged = existing.model_copy(deep=True)
    touched: list[str] = []
    _merge_folders(
        merged.folders,
        incoming.folders,
        prefix="",
        touched=touched,
        current_message_ids=current_message_ids,
        current_part=current_part,
        current_subchat=current_subchat,
    )
    # unique, stable
    seen: set[str] = set()
    unique: list[str] = []
    for item in touched:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return merged, unique


def _merge_folders(
    dest: dict[str, Folder],
    src: dict[str, Folder],
    *,
    prefix: str,
    touched: list[str],
    current_message_ids: set[str],
    current_part: str,
    current_subchat: str,
) -> None:
    for name, src_folder in src.items():
        dest_folder = dest.setdefault(name, Folder())
        path = f"{prefix}/{name}" if prefix else name
        for slug, src_article in src_folder.articles.items():
            resolved_slug, article, is_new = _upsert_article(
                dest_folder, slug, src_article, current_subchat
            )
            added = _merge_message_refs(
                article, src_article.messages, current_part, current_message_ids
            )
            article_id = f"{path}/{resolved_slug}"
            if is_new or added or (article.message_ids() & current_message_ids):
                touched.append(article_id)
        _merge_folders(
            dest_folder.folders,
            src_folder.folders,
            prefix=path,
            touched=touched,
            current_message_ids=current_message_ids,
            current_part=current_part,
            current_subchat=current_subchat,
        )


def _upsert_article(
    folder: Folder, slug: str, incoming: Article, current_subchat: str
) -> tuple[str, Article, bool]:
    slug = slug or slugify(incoming.title)
    title_key = incoming.title.strip().casefold()
    for existing_slug, existing in folder.articles.items():
        if existing_slug == slug or existing.title.strip().casefold() == title_key:
            if incoming.title:
                existing.title = incoming.title
            if incoming.source_subchat:
                existing.source_subchat = incoming.source_subchat
            elif not existing.source_subchat:
                existing.source_subchat = current_subchat
            return existing_slug, existing, False
    article = Article(
        title=incoming.title or slug,
        source_subchat=incoming.source_subchat or current_subchat,
        messages=[],
    )
    folder.articles[slug] = article
    return slug, article, True


def _merge_message_refs(
    article: Article,
    incoming: Iterable[MessageRef],
    current_part: str,
    current_message_ids: set[str],
) -> bool:
    by_id = {item.id: item for item in article.messages}
    added = False
    for ref in incoming:
        if not ref.id:
            continue
        if ref.id in by_id:
            existing = by_id[ref.id]
            if ref.context and not existing.context:
                existing.context = ref.context
            if current_part and not existing.part:
                existing.part = current_part
            continue
        part = ref.part or (current_part if ref.id in current_message_ids else "")
        by_id[ref.id] = MessageRef(id=ref.id, context=ref.context, part=part)
        added = True
    article.messages = list(by_id.values())
    return added
