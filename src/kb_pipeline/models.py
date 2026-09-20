from __future__ import annotations

import re
from typing import Any, Iterable, Iterator

from pydantic import BaseModel, Field, field_validator


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


_CONTEXT_MAX = 240


class MessageRef(BaseModel):
    id: str
    context: str = ""
    part: str = ""

    @field_validator("context", mode="before")
    @classmethod
    def _short_context(cls, value: Any) -> str:
        text = "" if value is None else str(value).replace("\n", " ").strip()
        if len(text) > _CONTEXT_MAX:
            return text[:_CONTEXT_MAX].rstrip() + "…"
        return text


class Article(BaseModel):
    title: str
    source_subchat: str = ""
    messages: list[MessageRef] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        tags: list[str] = []
        for item in value:
            tag = str(item or "").strip().lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
        return tags

    @field_validator("messages", mode="before")
    @classmethod
    def _drop_blank_message_ids(cls, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        kept: list[Any] = []
        for item in value:
            if isinstance(item, dict) and not item.get("id"):
                continue
            kept.append(item)
        return kept

    def message_ids(self) -> set[str]:
        return {item.id for item in self.messages if item.id}


class Folder(BaseModel):
    articles: dict[str, Article] = Field(default_factory=dict)
    folders: dict[str, Folder] = Field(default_factory=dict)

    @field_validator("articles", mode="before")
    @classmethod
    def _drop_null_articles(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items() if item is not None}

    @field_validator("folders", mode="before")
    @classmethod
    def _drop_null_folders(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items() if item is not None}


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

    def to_listing(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for folder_path, slug, article in self.iter_articles():
            rows.append(
                {
                    "id": self.article_id(folder_path, slug),
                    "title": article.title,
                    "folder": folder_path,
                    "path": f"{folder_path}/{slug}.md" if folder_path else f"{slug}.md",
                    "message_count": len(article.messages),
                    "tags": list(article.tags),
                }
            )
        return rows


class CriticReport(BaseModel):
    remarks: list[dict[str, str]] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)

    @field_validator("questions", mode="before")
    @classmethod
    def _coerce_questions(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        questions: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                raw = item.get("question") or item.get("text") or item.get("q")
                text = str(raw).strip() if raw else ""
            else:
                text = str(item).strip()
            if text:
                questions.append(text)
        return questions

    @field_validator("remarks", mode="before")
    @classmethod
    def _coerce_remarks(cls, value: Any) -> list[dict[str, str]]:
        if not value:
            return []
        if not isinstance(value, list):
            value = [value]
        remarks: list[dict[str, str]] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    remarks.append({"section": "", "kind": "note", "text": text})
                continue
            if not isinstance(item, dict):
                continue
            coerced = {str(key): str(val) for key, val in item.items() if val is not None}
            if coerced:
                remarks.append(coerced)
        return remarks


class ArticleRecord(BaseModel):
    article_id: str
    title: str
    folder: str
    slug: str
    draft: str = ""
    critic_comments: dict[str, Any] = Field(default_factory=dict)
    final: str = ""
    tags: list[str] = Field(default_factory=list)


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


def compact_message_contexts(hierarchy: Hierarchy) -> Hierarchy:
    """Keep message ids, drop stored contexts so checkpoints stay small."""
    copied = hierarchy.model_copy(deep=True)
    for _folder, _slug, article in copied.iter_articles():
        for ref in article.messages:
            ref.context = ""
    return copied


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
            resolved_slug, article, _is_new = _upsert_article(
                dest_folder, slug, src_article, current_subchat
            )
            added = _merge_message_refs(
                article, src_article.messages, current_part, current_message_ids
            )
            article_id = f"{path}/{resolved_slug}"
            if added or (article.message_ids() & current_message_ids):
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
            if incoming.tags:
                seen = set(existing.tags)
                for tag in incoming.tags:
                    if tag not in seen:
                        existing.tags.append(tag)
                        seen.add(tag)
            return existing_slug, existing, False
    article = Article(
        title=incoming.title or slug,
        source_subchat=incoming.source_subchat or current_subchat,
        messages=[],
        tags=list(incoming.tags),
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
