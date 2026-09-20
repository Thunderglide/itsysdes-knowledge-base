from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable, TextIO

from kb_pipeline.config import Config
from kb_pipeline.models import Hierarchy, WorkItem
from kb_pipeline.persist import (
    KEEP_OUTPUT_NAMES,
    article_dir,
    checkpoint_db_path,
    checkpoint_dir,
    ensure_output_dirs,
    last_run_path,
    load_hierarchy,
)

logger = logging.getLogger(__name__)


def clean_session(
    config: Config,
    *,
    yes: bool,
    stdout: TextIO,
    stderr: TextIO,
    stdin: TextIO | None = None,
) -> int:
    last_path = last_run_path(config)
    if not last_path.exists():
        print("Нет last_run.json — нечего очищать.", file=stderr)
        return 1

    meta = _read_last_run(last_path)
    thread_id = str((meta or {}).get("thread_id") or "")
    if _session_is_complete(config, thread_id):
        print("Последний прогон уже завершён — сессия не удалена.", file=stdout)
        return 0

    targets = collect_session_targets(config, meta)
    if not targets:
        print("Нет файлов незавершённой сессии.", file=stdout)
        return 0
    if not _confirm(targets, config.output_dir_resolved, yes=yes, stdout=stdout, stdin=stdin):
        print("Отменено.", file=stderr)
        return 1
    deleted = _delete_paths(targets)
    _prune_empty_dirs(config.output_dir_resolved)
    ensure_output_dirs(config)
    print(f"Удалено файлов сессии: {deleted}", file=stdout)
    return 0


def clean_kb(
    config: Config,
    *,
    yes: bool,
    stdout: TextIO,
    stderr: TextIO,
    stdin: TextIO | None = None,
) -> int:
    ensure_output_dirs(config)
    targets = collect_kb_targets(config)
    if not targets:
        print("База знаний уже пуста.", file=stdout)
        return 0
    if not _confirm(targets, config.output_dir_resolved, yes=yes, stdout=stdout, stdin=stdin):
        print("Отменено.", file=stderr)
        return 1
    deleted = _delete_paths(targets)
    _prune_empty_dirs(config.output_dir_resolved)
    ensure_output_dirs(config)
    print(f"Удалено файлов базы знаний: {deleted}", file=stdout)
    return 0


def clean_checkpoints(
    config: Config,
    *,
    yes: bool,
    stdout: TextIO,
    stderr: TextIO,
    stdin: TextIO | None = None,
) -> int:
    targets = collect_checkpoint_targets(config)
    if not targets:
        print("Нет файлов чекпоинта.", file=stdout)
        return 0
    if not _confirm(targets, config.output_dir_resolved, yes=yes, stdout=stdout, stdin=stdin):
        print("Отменено.", file=stderr)
        return 1
    deleted = _delete_paths(targets)
    ensure_output_dirs(config)
    print(f"Удалено файлов чекпоинта: {deleted}", file=stdout)
    return 0


def collect_session_targets(config: Config, meta: dict[str, Any] | None) -> list[Path]:
    output = config.output_dir_resolved
    targets: list[Path] = []
    last_path = last_run_path(config)
    if last_path.exists():
        targets.append(last_path)

    cp_dir = checkpoint_dir(config)
    if cp_dir.exists():
        targets.extend(path for path in cp_dir.rglob("*") if path.is_file() or path.is_symlink())

    work_items = _work_items(meta)
    for item in work_items:
        cache_path = output / "cache" / item.subchat_id / f"{item.part_file}.jsonl"
        if cache_path.exists():
            targets.append(cache_path)

    extra_ids = _touched_article_ids(config, str((meta or {}).get("thread_id") or ""))
    hierarchy = load_hierarchy(config)
    for folder, slug, _article in _session_articles(hierarchy, work_items, extra_ids):
        folder_path = article_dir(config, folder)
        for name in (f"{slug}.draft.md", f"{slug}.critic.json"):
            artifact = folder_path / name
            if artifact.exists():
                targets.append(artifact)

    return _unique_existing(targets)


def collect_checkpoint_targets(config: Config) -> list[Path]:
    cp_dir = checkpoint_dir(config)
    if not cp_dir.exists():
        return []
    return _unique_existing(
        path for path in cp_dir.rglob("*") if path.is_file() or path.is_symlink()
    )


def collect_kb_targets(config: Config) -> list[Path]:
    output = config.output_dir_resolved
    if not output.exists():
        return []
    targets: list[Path] = []
    for path in output.rglob("*"):
        if path.name in KEEP_OUTPUT_NAMES:
            continue
        if path.is_file() or path.is_symlink():
            targets.append(path)
    return _unique_existing(targets)


def _read_last_run(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Cannot parse %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _work_items(meta: dict[str, Any] | None) -> list[WorkItem]:
    if not meta:
        return []
    items: list[WorkItem] = []
    for raw in meta.get("work_items") or []:
        try:
            items.append(WorkItem.model_validate(raw))
        except Exception as exc:
            logger.warning("Skip invalid work item: %s", exc)
    return items


def session_is_complete(config: Config, thread_id: str) -> bool:
    return _session_is_complete(config, thread_id)


def _session_is_complete(config: Config, thread_id: str) -> bool:
    if not thread_id or not checkpoint_db_path(config).exists():
        return False
    try:
        from kb_pipeline.graph import PipelineRuntime, compiled_graph

        runtime = PipelineRuntime(config=config, fake=True)
        graph_config = {
            "configurable": {
                "thread_id": thread_id,
                "pipeline_runtime": runtime,
            }
        }
        with compiled_graph(checkpoint_db_path(config)) as app:
            snapshot = app.get_state(graph_config)
    except Exception as exc:
        logger.info("Checkpoint unreadable (%s); treat session as incomplete", exc)
        return False
    values = getattr(snapshot, "values", None) or {}
    return bool(values.get("done"))


def _touched_article_ids(config: Config, thread_id: str) -> list[str]:
    if not thread_id or not checkpoint_db_path(config).exists():
        return []
    try:
        from kb_pipeline.graph import PipelineRuntime, compiled_graph

        runtime = PipelineRuntime(config=config, fake=True)
        graph_config = {
            "configurable": {
                "thread_id": thread_id,
                "pipeline_runtime": runtime,
            }
        }
        with compiled_graph(checkpoint_db_path(config)) as app:
            snapshot = app.get_state(graph_config)
    except Exception as exc:
        logger.info("Cannot read touched articles from checkpoint: %s", exc)
        return []
    values = getattr(snapshot, "values", None) or {}
    ids = values.get("touched_article_ids") or []
    return [str(item) for item in ids]


def _session_articles(
    hierarchy: Hierarchy,
    work_items: list[WorkItem],
    extra_ids: Iterable[str],
) -> list[tuple[str, str, Any]]:
    wanted = {item for item in extra_ids if item}
    parts_by_subchat: dict[str, set[str]] = {}
    subchats: set[str] = set()
    for item in work_items:
        subchats.add(item.subchat_id)
        parts_by_subchat.setdefault(item.subchat_id, set()).add(item.part_file)

    matched: list[tuple[str, str, Any]] = []
    seen: set[str] = set()
    for folder, slug, article in hierarchy.iter_articles():
        article_id = hierarchy.article_id(folder, slug)
        if article_id in seen:
            continue
        if article_id in wanted or _article_matches_session(
            article, subchats, parts_by_subchat
        ):
            seen.add(article_id)
            matched.append((folder, slug, article))
    return matched


def _article_matches_session(
    article: Any,
    subchats: set[str],
    parts_by_subchat: dict[str, set[str]],
) -> bool:
    if article.source_subchat not in subchats:
        return False
    parts = parts_by_subchat.get(article.source_subchat) or set()
    refs = list(article.messages or [])
    if not refs:
        return True
    if any(ref.part in parts for ref in refs):
        return True
    return all(not ref.part for ref in refs)


def _confirm(
    paths: list[Path],
    root: Path,
    *,
    yes: bool,
    stdout: TextIO,
    stdin: TextIO | None,
) -> bool:
    print("Будут удалены:", file=stdout)
    for path in paths:
        print(f"  {_display_path(path, root)}", file=stdout)
    if yes:
        return True
    stream = stdin
    if stream is None:
        try:
            answer = input("Удалить эти файлы? [y/N] ").strip().lower()
        except EOFError:
            return False
    else:
        print("Удалить эти файлы? [y/N] ", file=stdout, end="")
        answer = (stream.readline() or "").strip().lower()
    return answer in {"y", "yes", "д", "да"}


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _unique_existing(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _delete_paths(paths: list[Path]) -> int:
    deleted = 0
    for path in paths:
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                deleted += 1
        except OSError as exc:
            logger.warning("Cannot delete %s: %s", path, exc)
    return deleted


def _prune_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    dirs = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in dirs:
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
        except OSError:
            continue
