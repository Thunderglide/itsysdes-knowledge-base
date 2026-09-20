from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from kb_pipeline.config import Config
from kb_pipeline.content_stitch import split_frontmatter, strip_frontmatter, with_frontmatter
from kb_pipeline.models import ArticleRecord, Hierarchy

LAST_RUN_FILENAME = "last_run.json"
CHECKPOINT_DIRNAME = ".checkpoints"
CHECKPOINT_DB = "lg.sqlite"
KEEP_OUTPUT_NAMES = {".gitkeep"}


def last_run_path(config: Config) -> Path:
    return config.output_dir_resolved / LAST_RUN_FILENAME


def checkpoint_dir(config: Config) -> Path:
    return config.output_dir_resolved / CHECKPOINT_DIRNAME


def checkpoint_db_path(config: Config) -> Path:
    return checkpoint_dir(config) / CHECKPOINT_DB


def ensure_output_dirs(config: Config) -> Path:
    output = config.output_dir_resolved
    (output / "articles").mkdir(parents=True, exist_ok=True)
    (output / "cache").mkdir(parents=True, exist_ok=True)
    (output / ".checkpoints").mkdir(parents=True, exist_ok=True)
    return output


def hierarchy_path(config: Config) -> Path:
    return config.output_dir_resolved / "hierarchy.json"


def load_hierarchy(config: Config) -> Hierarchy:
    path = hierarchy_path(config)
    if not path.exists():
        return Hierarchy()
    return Hierarchy.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_hierarchy(config: Config, hierarchy: Hierarchy) -> Path:
    ensure_output_dirs(config)
    path = hierarchy_path(config)
    path.write_text(
        json.dumps(hierarchy.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def article_dir(config: Config, folder: str) -> Path:
    base = config.output_dir_resolved / "articles"
    if folder:
        return base.joinpath(*Path(folder).parts)
    return base


def save_article_artifacts(
    config: Config, record: ArticleRecord, *, write_final: bool = False
) -> None:
    folder = article_dir(config, record.folder)
    folder.mkdir(parents=True, exist_ok=True)
    if record.draft:
        (folder / f"{record.slug}.draft.md").write_text(record.draft, encoding="utf-8")
    if record.critic_comments:
        (folder / f"{record.slug}.critic.json").write_text(
            json.dumps(record.critic_comments, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if write_final and record.final:
        (folder / f"{record.slug}.md").write_text(
            with_frontmatter(
                record.final,
                title=record.title,
                folder=record.folder,
                tags=record.tags,
            ),
            encoding="utf-8",
        )


def article_artifact_paths(config: Config, folder: str, slug: str) -> list[Path]:
    directory = article_dir(config, folder)
    return [
        directory / f"{slug}.md",
        directory / f"{slug}.draft.md",
        directory / f"{slug}.critic.json",
    ]


def delete_article_artifacts(config: Config, folder: str, slug: str) -> None:
    for path in article_artifact_paths(config, folder, slug):
        if path.exists() or path.is_symlink():
            path.unlink()


def article_final_is_current(config: Config, record: ArticleRecord) -> bool:
    folder = article_dir(config, record.folder)
    final_path = folder / f"{record.slug}.md"
    draft_path = folder / f"{record.slug}.draft.md"
    critic_path = folder / f"{record.slug}.critic.json"
    if not final_path.exists() or final_path.stat().st_size == 0:
        return False
    final_mtime = final_path.stat().st_mtime
    if draft_path.exists() and final_mtime < draft_path.stat().st_mtime:
        return False
    if critic_path.exists() and final_mtime < critic_path.stat().st_mtime:
        return False
    return True


def move_article_artifacts(
    config: Config,
    src_folder: str,
    src_slug: str,
    dest_folder: str,
    dest_slug: str,
) -> None:
    if src_folder == dest_folder and src_slug == dest_slug:
        return
    dest_dir = article_dir(config, dest_folder)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src, dest in zip(
        article_artifact_paths(config, src_folder, src_slug),
        article_artifact_paths(config, dest_folder, dest_slug),
        strict=True,
    ):
        if not src.exists() and not src.is_symlink():
            continue
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        shutil.move(str(src), str(dest))


def load_article_record(
    config: Config,
    article_id: str,
    title: str,
    folder: str,
    slug: str,
    *,
    tags: Iterable[str] | None = None,
) -> ArticleRecord:
    folder_path = article_dir(config, folder)
    draft = _read_text(folder_path / f"{slug}.draft.md")
    raw_final = _read_text(folder_path / f"{slug}.md")
    meta, final = split_frontmatter(raw_final)
    critic_path = folder_path / f"{slug}.critic.json"
    critic: dict[str, Any] = {}
    if critic_path.exists():
        critic = json.loads(critic_path.read_text(encoding="utf-8"))
    file_tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    resolved_tags = [str(item) for item in (tags if tags is not None else file_tags)]
    return ArticleRecord(
        article_id=article_id,
        title=str(meta.get("title") or title),
        folder=folder,
        slug=slug,
        draft=strip_frontmatter(draft),
        critic_comments=critic,
        final=final,
        tags=resolved_tags,
    )


def relative_media_path(config: Config, attachment_path: str) -> str:
    """Keep exporter-relative paths so Markdown links stay portable."""
    raw = attachment_path.replace("\\", "/")
    files_root = config.files_root_resolved
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            return str(candidate.relative_to(files_root))
        except ValueError:
            try:
                return str(candidate.relative_to(files_root.parent))
            except ValueError:
                return raw
    return raw


def _read_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""
