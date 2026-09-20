from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kb_pipeline.agents.cleanup_plan import propose_cleanup
from kb_pipeline.agents.critic import run_critic
from kb_pipeline.agents.curate_polish import run_curate_polish
from kb_pipeline.agents.generator import run_generator
from kb_pipeline.agents.merger import propose_merges, validate_merge_actions
from kb_pipeline.agents.polisher import run_polisher
from kb_pipeline.agents.reparent import propose_reparents, validate_reparent_actions
from kb_pipeline.agents.splitter import propose_splits, resolve_split_actions, split_candidates
from kb_pipeline.agents.tagger import propose_tags, validate_tag_actions
from kb_pipeline.cleanup import session_is_complete
from kb_pipeline.config import Config
from kb_pipeline.content_stitch import extract_part_markdown, remainder_markdown, stitch_markdown
from kb_pipeline.llm.factory import get_backend
from kb_pipeline.models import Article, Folder, Hierarchy, MessageRef, slugify
from kb_pipeline.persist import (
    article_artifact_paths,
    checkpoint_db_path,
    delete_article_artifacts,
    last_run_path,
    load_article_record,
    load_hierarchy,
    move_article_artifacts,
    save_article_artifacts,
    save_hierarchy,
)
from kb_pipeline.relink import apply_relink_plan, propose_relinks, rewrite_all_finals
from kb_pipeline.taxonomy import filter_tags, load_taxonomy, union_tags

logger = logging.getLogger(__name__)

_DEFAULT_POLISH_KINDS = frozenset({"merge", "split"})


class CurateError(RuntimeError):
    pass


def cmd_curate(args: Any) -> int:
    from kb_pipeline.config import load_config

    config = load_config(args.config)
    fake = bool(getattr(args, "fake", False))
    kind = args.curate_command
    polish = getattr(args, "polish", None)
    if polish is None:
        polish = kind in _DEFAULT_POLISH_KINDS
    try:
        if args.apply:
            return apply_plan(
                config,
                kind,
                Path(args.apply),
                fake=fake,
                polish=bool(polish),
            )
        plan = dry_run(config, kind, fake=fake)
        output = Path(args.output) if args.output else _default_plan_path(config, kind)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if kind == "relink":
            print(
                f"План {kind}: {output} "
                f"({len(plan.get('rewrites') or [])} замен, "
                f"{len(plan.get('unresolved') or [])} неразрешённых)"
            )
        else:
            count = len(plan.get("actions") or [])
            print(f"План {kind}: {output} ({count} действий)")
        return 0
    except CurateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def dry_run(config: Config, kind: str, *, fake: bool) -> dict[str, Any]:
    hierarchy = load_hierarchy(config)
    if kind == "cleanup":
        return propose_cleanup(config, hierarchy)
    if kind == "relink":
        return propose_relinks(config, hierarchy)
    backend = get_backend(kind, config, fake=fake)
    if kind == "merge":
        return propose_merges(backend, config, hierarchy)
    if kind == "split":
        return propose_splits(backend, config, hierarchy)
    if kind == "reparent":
        return propose_reparents(backend, config, hierarchy)
    if kind == "tag":
        return propose_tags(backend, config, hierarchy)
    raise CurateError(f"Неизвестная команда curate: {kind}")


def apply_plan(
    config: Config,
    kind: str,
    plan_path: Path,
    *,
    fake: bool,
    polish: bool = False,
) -> int:
    blocked = apply_blocked_reason(config)
    if blocked:
        raise CurateError(blocked)
    plan = _load_plan(plan_path)
    plan_kind = str(plan.get("kind") or kind)
    if plan_kind != kind:
        raise CurateError(f"План kind={plan_kind!r} не совпадает с командой {kind}")
    hierarchy = load_hierarchy(config)
    taxonomy = load_taxonomy(config)
    if kind == "cleanup":
        actions = plan.get("actions") or []
        if not actions:
            print("Нет допустимых действий cleanup.")
            return 0
        backup = _backup(config, _cleanup_article_ids(actions, hierarchy))
        hierarchy, touched = apply_cleanup_actions(config, hierarchy, actions)
    elif kind == "merge":
        actions = validate_merge_actions(
            plan.get("actions") or [], hierarchy, config.curate
        )
        if not actions:
            print("Нет допустимых действий merge.")
            return 0
        backup = _backup(config, _merge_article_ids(actions, hierarchy))
        hierarchy, touched = apply_merge_actions(config, hierarchy, actions)
    elif kind == "split":
        candidates = split_candidates(config, hierarchy, config.curate)
        actions = resolve_split_actions(plan.get("actions") or [], candidates, hierarchy)
        if not actions:
            print("Нет допустимых действий split.")
            return 0
        backup = _backup(config, _split_article_ids(actions))
        hierarchy, touched = apply_split_actions(config, hierarchy, actions)
    elif kind == "reparent":
        actions, _proposed = validate_reparent_actions(
            plan.get("actions") or plan.get("moves") or [],
            hierarchy,
            set(taxonomy.folder_paths()),
        )
        if not actions:
            print("Нет допустимых действий reparent.")
            return 0
        backup = _backup(config, [str(item.get("id") or "") for item in actions])
        hierarchy, touched = apply_reparent_actions(config, hierarchy, actions)
    elif kind == "tag":
        actions = validate_tag_actions(plan.get("actions") or [], hierarchy, config)
        if not actions:
            print("Нет допустимых действий tag.")
            return 0
        backup = _backup(config, [str(item.get("id") or "") for item in actions])
        hierarchy, touched = apply_tag_actions(config, hierarchy, actions)
    elif kind == "relink":
        rewrites = plan.get("rewrites") or []
        if not rewrites:
            print("Нет допустимых действий relink.")
            return 0
        backup = _backup(config, list(plan.get("files") or []))
        touched = apply_relink_plan(config, hierarchy, plan)
        save_hierarchy(config, hierarchy)
        print(f"Backup: {backup}")
        print(f"Обновлено статей: {len(touched)}")
        return 0
    else:
        raise CurateError(f"Неизвестная команда curate: {kind}")
    save_hierarchy(config, hierarchy)
    if polish:
        polish_articles(config, hierarchy, touched, fake=fake)
    print(f"Backup: {backup}")
    print(f"Обновлено статей: {len(touched)}")
    return 0


def apply_blocked_reason(config: Config) -> str | None:
    last_path = last_run_path(config)
    if not last_path.exists():
        return None
    try:
        meta = json.loads(last_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "Нельзя применить curate: last_run.json не читается. Дождитесь завершения пайплайна."
    thread_id = str((meta or {}).get("thread_id") or "")
    if not thread_id:
        return "Нельзя применить curate: в last_run.json нет thread_id."
    if not checkpoint_db_path(config).exists():
        return None
    if session_is_complete(config, thread_id):
        return None
    return (
        "Нельзя применить curate, пока kb-pipeline resume не завершит прогон "
        "(иначе чекпоинт перезапишет hierarchy.json)."
    )


def apply_cleanup_actions(
    config: Config,
    hierarchy: Hierarchy,
    actions: list[dict[str, Any]],
) -> tuple[Hierarchy, list[str]]:
    copied = hierarchy.model_copy(deep=True)
    touched: list[str] = []
    merges: list[dict[str, Any]] = []
    for action in actions:
        if str(action.get("type") or "") == "delete":
            article_id = str(action.get("id") or "")
            found = copied.find_by_id(article_id)
            if found is None:
                continue
            folder, slug, _article = found
            _remove_article(copied, article_id)
            delete_article_artifacts(config, folder, slug)
            continue
        if str(action.get("type") or "") == "merge" or action.get("sources"):
            merges.append(action)
    if merges:
        copied, merge_touched = apply_merge_actions(config, copied, merges)
        touched.extend(merge_touched)
    _prune_empty_folders(copied)
    return copied, _unique(touched)


def apply_merge_actions(
    config: Config,
    hierarchy: Hierarchy,
    actions: list[dict[str, Any]],
) -> tuple[Hierarchy, list[str]]:
    copied = hierarchy.model_copy(deep=True)
    taxonomy = load_taxonomy(config)
    touched: list[str] = []
    extra_aliases: dict[str, str] = {}
    for action in actions:
        source_ids = [str(item) for item in action.get("sources") or []]
        source_rows: list[tuple[str, str, str, Article]] = []
        for source_id in source_ids:
            found = copied.find_by_id(source_id)
            if found is None:
                continue
            source_rows.append((source_id, found[0], found[1], found[2]))
        if not source_rows:
            continue
        target_id, target_article = _resolve_merge_target(copied, action, source_rows[0][3])
        if target_article is None:
            continue
        folder, slug, _ = copied.find_by_id(target_id) or ("", "", target_article)
        target_record = load_article_record(
            config, target_id, target_article.title, folder, slug, tags=target_article.tags
        )
        parts: list[tuple[str, str]] = []
        existing = target_record.final or target_record.draft
        if existing.strip():
            parts.append((target_article.title, existing))
        for source_id, src_folder, src_slug, source_article in source_rows:
            source_record = load_article_record(
                config,
                source_id,
                source_article.title,
                src_folder,
                src_slug,
                tags=source_article.tags,
            )
            parts.append((source_article.title, source_record.final or source_record.draft))
            _merge_message_refs(target_article, source_article.messages)
            target_article.tags = union_tags(
                target_article.tags, source_article.tags, taxonomy=taxonomy
            )
            _remove_article(copied, source_id)
            delete_article_artifacts(config, src_folder, src_slug)
        stitched = stitch_markdown(target_article.title, parts)
        _write_final(config, copied, target_id, stitched)
        touched.append(target_id)
        for _source_id, _src_folder, src_slug, _source_article in source_rows:
            if src_slug and slug and src_slug != slug:
                extra_aliases[src_slug] = slug
    touched.extend(rewrite_all_finals(config, copied, extra_aliases=extra_aliases))
    return copied, _unique(touched)


def apply_split_actions(
    config: Config,
    hierarchy: Hierarchy,
    actions: list[dict[str, Any]],
) -> tuple[Hierarchy, list[str]]:
    copied = hierarchy.model_copy(deep=True)
    touched: list[str] = []
    for action in actions:
        source_id = str(action["source"])
        found = copied.find_by_id(source_id)
        if found is None:
            continue
        folder, slug, source_article = found
        source_record = load_article_record(
            config, source_id, source_article.title, folder, slug, tags=source_article.tags
        )
        source_text = source_record.final or source_record.draft
        by_id = {ref.id: ref for ref in source_article.messages if ref.id}
        moved: set[str] = set()
        used_headings: list[str] = []
        used_message_ids: list[str] = []
        for part in action.get("parts") or []:
            part_folder = str(part.get("folder") or folder)
            part_slug = str(part.get("slug") or slugify(str(part.get("title") or "")))
            part_title = str(part.get("title") or part_slug)
            article_id = copied.article_id(part_folder, part_slug)
            if article_id == source_id:
                continue
            refs = [
                by_id[mid]
                for mid in part.get("message_ids") or []
                if mid in by_id and mid not in moved
            ]
            if not refs:
                continue
            dest = _ensure_article(
                copied,
                part_folder,
                part_slug,
                title=part_title,
                source_subchat=source_article.source_subchat,
                tags=[],
            )
            _merge_message_refs(dest, refs)
            moved.update(ref.id for ref in refs)
            headings = [str(item) for item in part.get("headings") or [] if str(item)]
            message_ids = [ref.id for ref in refs]
            used_headings.extend(headings)
            used_message_ids.extend(message_ids)
            part_body = extract_part_markdown(
                source_text,
                title=part_title,
                headings=headings,
                message_ids=message_ids,
            )
            _write_final(config, copied, article_id, part_body)
            touched.append(article_id)
        source_article.messages = [
            ref for ref in source_article.messages if not ref.id or ref.id not in moved
        ]
        remainder = remainder_markdown(
            source_text,
            headings=used_headings,
            message_ids=[] if used_headings else used_message_ids,
        )
        _write_final(
            config,
            copied,
            source_id,
            remainder or f"# {source_article.title}\n",
        )
        touched.append(source_id)
    return copied, _unique(touched)


def apply_reparent_actions(
    config: Config,
    hierarchy: Hierarchy,
    actions: list[dict[str, Any]],
) -> tuple[Hierarchy, list[str]]:
    copied = hierarchy.model_copy(deep=True)
    touched: list[str] = []
    for action in actions:
        article_id = str(action.get("id") or "")
        dest_folder = str(action.get("folder") or "").strip().strip("/")
        found = copied.find_by_id(article_id)
        if found is None or not dest_folder:
            continue
        src_folder, slug, article = found
        if dest_folder == src_folder:
            continue
        dest_holder = _ensure_folder(copied, dest_folder)
        if slug in dest_holder.articles:
            logger.warning("Reparent skip collision %s -> %s/%s", article_id, dest_folder, slug)
            continue
        moved = article.model_copy(deep=True)
        _remove_article(copied, article_id)
        dest_holder.articles[slug] = moved
        move_article_artifacts(config, src_folder, slug, dest_folder, slug)
        new_id = copied.article_id(dest_folder, slug)
        record = load_article_record(
            config, new_id, moved.title, dest_folder, slug, tags=moved.tags
        )
        _write_final(config, copied, new_id, record.final or record.draft)
        touched.append(new_id)
    _prune_empty_folders(copied)
    touched.extend(rewrite_all_finals(config, copied))
    return copied, _unique(touched)


def apply_tag_actions(
    config: Config,
    hierarchy: Hierarchy,
    actions: list[dict[str, Any]],
) -> tuple[Hierarchy, list[str]]:
    copied = hierarchy.model_copy(deep=True)
    touched: list[str] = []
    for action in actions:
        article_id = str(action.get("id") or "")
        found = copied.find_by_id(article_id)
        if found is None:
            continue
        folder, slug, article = found
        tags, _proposed = filter_tags(
            action.get("tags") or [],
            load_taxonomy(config),
            max_tags=config.curate.tag_max,
        )
        article.tags = tags
        record = load_article_record(
            config, article_id, article.title, folder, slug, tags=article.tags
        )
        _write_final(config, copied, article_id, record.final or record.draft)
        touched.append(article_id)
    return copied, _unique(touched)


def polish_articles(
    config: Config,
    hierarchy: Hierarchy,
    article_ids: list[str],
    *,
    fake: bool,
) -> None:
    if not article_ids:
        return
    backend = get_backend("polisher", config, fake=fake)
    for article_id in article_ids:
        found = hierarchy.find_by_id(article_id)
        if found is None:
            continue
        folder, slug, article = found
        record = load_article_record(
            config, article_id, article.title, folder, slug, tags=article.tags
        )
        text = record.final or record.draft
        if not text.strip():
            continue
        polished = run_curate_polish(backend, title=article.title, text=text)
        _write_final(config, hierarchy, article_id, polished)


def regenerate_articles(
    config: Config,
    hierarchy: Hierarchy,
    article_ids: list[str],
    *,
    fake: bool,
) -> None:
    if not article_ids:
        return
    listing = hierarchy.to_listing()
    gen = get_backend("generator", config, fake=fake)
    critic_backend = get_backend("critic", config, fake=fake)
    polish_backend = get_backend("polisher", config, fake=fake)
    conn = connect(config)
    try:
        for article_id in article_ids:
            found = hierarchy.find_by_id(article_id)
            if not found:
                continue
            folder, slug, article = found
            ids = [ref.id for ref in article.messages if ref.id]
            messages = load_messages_by_ids(conn, ids)
            record = load_article_record(
                config, article_id, article.title, folder, slug, tags=article.tags
            )
            record.title = article.title
            record.folder = folder
            record.slug = slug
            logger.info("Curate regenerate %s (%s msgs)", article_id, len(messages))
            draft = ""
            batches = pack_batches(
                messages,
                max_tokens=config.max_batch_tokens,
                max_chars=config.max_message_chars,
            ) or [[]]
            for batch in batches:
                draft = run_generator(
                    gen,
                    config,
                    title=record.title,
                    folder=folder,
                    slug=slug,
                    existing_draft=draft,
                    messages=batch,
                    subchat_id=article.source_subchat,
                )
            record.draft = draft
            critic_messages = messages[-80:] if len(messages) > 80 else messages
            report = run_critic(
                critic_backend,
                config,
                draft=record.draft,
                messages=critic_messages,
            )
            record.critic_comments = report.model_dump()
            record.final = run_polisher(
                polish_backend,
                config,
                draft=record.draft,
                critic=report,
                messages=critic_messages,
                article_listing=listing,
                title=record.title,
                folder=folder,
            )
            record.tags = list(article.tags)
            save_article_artifacts(config, record, write_final=True)
    finally:
        conn.close()


def _resolve_merge_target(
    hierarchy: Hierarchy,
    action: dict[str, Any],
    sample_source: Article,
) -> tuple[str, Article | None]:
    target_id = str(action.get("target") or "")
    create = bool(action.get("create"))
    found = hierarchy.find_by_id(target_id)
    if found is not None:
        return target_id, found[2]
    if not create and not target_id:
        return target_id, None
    folder, _, slug = target_id.rpartition("/")
    if not slug:
        return target_id, None
    title = str(action.get("title") or slug)
    article = _ensure_article(
        hierarchy,
        folder,
        slug,
        title=title,
        source_subchat=sample_source.source_subchat,
        tags=list(sample_source.tags),
    )
    return hierarchy.article_id(folder, slug), article


def _write_final(config: Config, hierarchy: Hierarchy, article_id: str, body: str) -> None:
    found = hierarchy.find_by_id(article_id)
    if found is None:
        return
    folder, slug, article = found
    record = load_article_record(
        config, article_id, article.title, folder, slug, tags=article.tags
    )
    record.title = article.title
    record.folder = folder
    record.slug = slug
    record.tags = list(article.tags)
    record.final = body
    save_article_artifacts(config, record, write_final=True)


def _default_plan_path(config: Config, kind: str) -> Path:
    return config.output_dir_resolved / "curate" / f"{kind}-plan.json"


def _load_plan(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CurateError(f"План не найден: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CurateError(f"Нельзя прочитать план {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CurateError("План должен быть JSON-объектом")
    return data


def _backup(config: Config, article_ids: list[str]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = config.output_dir_resolved / "curate" / f"backup-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    hierarchy_src = config.output_dir_resolved / "hierarchy.json"
    if hierarchy_src.exists():
        shutil.copy2(hierarchy_src, root / "hierarchy.json")
    articles_root = config.output_dir_resolved / "articles"
    for article_id in article_ids:
        folder, _, slug = article_id.rpartition("/")
        if not slug:
            slug = folder
            folder = ""
        for path in article_artifact_paths(config, folder, slug):
            if not path.exists():
                continue
            try:
                rel = path.relative_to(articles_root)
            except ValueError:
                rel = Path(path.name)
            dest = root / "articles" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    return root


def _merge_article_ids(actions: list[dict[str, Any]], hierarchy: Hierarchy) -> list[str]:
    ids: list[str] = []
    for action in actions:
        ids.append(str(action.get("target") or ""))
        ids.extend(str(item) for item in action.get("sources") or [])
    return [item for item in _unique(ids) if item and hierarchy.find_by_id(item)]


def _split_article_ids(actions: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for action in actions:
        ids.append(str(action.get("source") or ""))
        for part in action.get("parts") or []:
            folder = str(part.get("folder") or "")
            slug = str(part.get("slug") or "")
            if slug:
                ids.append(f"{folder}/{slug}" if folder else slug)
    return _unique([item for item in ids if item])


def _cleanup_article_ids(actions: list[dict[str, Any]], hierarchy: Hierarchy) -> list[str]:
    ids: list[str] = []
    for action in actions:
        if action.get("id"):
            ids.append(str(action["id"]))
        ids.extend(_merge_article_ids([action], hierarchy))
    return _unique([item for item in ids if item])


def _folder_at(hierarchy: Hierarchy, folder_path: str) -> Folder | None:
    current: dict[str, Folder] | None = hierarchy.folders
    folder: Folder | None = None
    for name in [part for part in folder_path.split("/") if part]:
        if current is None:
            return None
        folder = current.get(name)
        if folder is None:
            return None
        current = folder.folders
    return folder


def _ensure_folder(hierarchy: Hierarchy, folder_path: str) -> Folder:
    parts = [part for part in folder_path.split("/") if part]
    if not parts:
        raise CurateError("Путь папки пуст")
    current = hierarchy.folders
    folder: Folder | None = None
    for name in parts:
        folder = current.setdefault(name, Folder())
        current = folder.folders
    assert folder is not None
    return folder


def _ensure_article(
    hierarchy: Hierarchy,
    folder_path: str,
    slug: str,
    *,
    title: str,
    source_subchat: str,
    tags: list[str] | None = None,
) -> Article:
    folder = _ensure_folder(hierarchy, folder_path)
    existing = folder.articles.get(slug)
    if existing is not None:
        if title:
            existing.title = title
        if tags:
            existing.tags = union_tags(existing.tags, tags)
        return existing
    article = Article(
        title=title or slug,
        source_subchat=source_subchat,
        messages=[],
        tags=list(tags or []),
    )
    folder.articles[slug] = article
    return article


def _remove_article(hierarchy: Hierarchy, article_id: str) -> None:
    found = hierarchy.find_by_id(article_id)
    if found is None:
        return
    folder_path, slug, _article = found
    folder = _folder_at(hierarchy, folder_path)
    if folder is not None:
        folder.articles.pop(slug, None)


def _prune_empty_folders(hierarchy: Hierarchy) -> None:
    def prune(folders: dict[str, Folder]) -> None:
        for name, folder in list(folders.items()):
            prune(folder.folders)
            if not folder.articles and not folder.folders:
                del folders[name]

    prune(hierarchy.folders)


def _merge_message_refs(dest: Article, incoming: list[MessageRef]) -> None:
    by_id = {item.id: item for item in dest.messages if item.id}
    for ref in incoming:
        if not ref.id or ref.id in by_id:
            continue
        by_id[ref.id] = MessageRef(id=ref.id, context=ref.context, part=ref.part)
    dest.messages = list(by_id.values())


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique
