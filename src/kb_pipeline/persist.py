from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kb_pipeline.config import Config
from kb_pipeline.models import ArticleRecord, Hierarchy


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


def save_article_artifacts(config: Config, record: ArticleRecord) -> None:
    folder = article_dir(config, record.folder)
    folder.mkdir(parents=True, exist_ok=True)
    if record.draft:
        (folder / f"{record.slug}.draft.md").write_text(record.draft, encoding="utf-8")
    if record.critic_comments:
        (folder / f"{record.slug}.critic.json").write_text(
            json.dumps(record.critic_comments, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if record.final:
        (folder / f"{record.slug}.md").write_text(record.final, encoding="utf-8")


def load_article_record(
    config: Config, article_id: str, title: str, folder: str, slug: str
) -> ArticleRecord:
    folder_path = article_dir(config, folder)
    draft = _read_text(folder_path / f"{slug}.draft.md")
    final = _read_text(folder_path / f"{slug}.md")
    critic_path = folder_path / f"{slug}.critic.json"
    critic: dict[str, Any] = {}
    if critic_path.exists():
        critic = json.loads(critic_path.read_text(encoding="utf-8"))
    return ArticleRecord(
        article_id=article_id,
        title=title,
        folder=folder,
        slug=slug,
        draft=draft,
        critic_comments=critic,
        final=final,
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
