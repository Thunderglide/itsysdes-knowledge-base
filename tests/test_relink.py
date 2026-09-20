import json
from pathlib import Path

from kb_pipeline.cli import main
from kb_pipeline.config import Config
from kb_pipeline.curate import apply_merge_actions, apply_reparent_actions
from kb_pipeline.models import Article, Folder, Hierarchy, MessageRef
from kb_pipeline.persist import load_hierarchy, save_hierarchy
from kb_pipeline.relink import (
    aliases_from_plan,
    apply_relink_plan,
    propose_relinks,
    rewrite_markdown,
    slug_map,
)
from tests.dbutil import write_config


def _config(tmp_path: Path) -> Config:
    config = Config(
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
    )
    config.config_path = tmp_path / "config.yaml"
    return config


def _hierarchy() -> Hierarchy:
    return Hierarchy(
        folders={
            "Методологии и процессы": Folder(
                articles={
                    "source-page": Article(title="Источник"),
                }
            ),
            "Данные и модели": Folder(
                articles={
                    "alive-target": Article(title="Цель"),
                }
            ),
        }
    )


def _write_article(root: Path, folder: str, slug: str, body: str) -> Path:
    directory = root / "articles" / folder
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_rewrite_unique_slug_and_percent_encoding():
    slugs = {"alive-target": "Данные и модели/alive-target.md"}
    text = (
        "See [Цель](Методологии%20и%20процессы/alive-target.md#sec) "
        "and [same](./Методологии и процессы/alive-target.md)."
    )
    updated, changes = rewrite_markdown(text, slugs, {})
    assert "Данные и модели/alive-target.md#sec" in updated
    assert "%20" not in updated
    assert all(item["to"] for item in changes)


def test_rewrite_skips_files_id_and_http():
    slugs = {"alive-target": "Данные и модели/alive-target.md"}
    text = (
        "[img](files/photo.jpg) "
        "[cite](id:chat:topic:msg) "
        "[web](https://example.com/x.md) "
        "[ok](Old/alive-target.md)"
    )
    updated, changes = rewrite_markdown(text, slugs, {})
    assert "[img](files/photo.jpg)" in updated
    assert "[cite](id:chat:topic:msg)" in updated
    assert "[web](https://example.com/x.md)" in updated
    assert "[ok](Данные и модели/alive-target.md)" in updated
    assert [item["from"] for item in changes] == ["Old/alive-target.md"]


def test_rewrite_orphan_uses_merge_alias():
    slugs = {"alive-target": "Данные и модели/alive-target.md"}
    aliases = {"merged-away": "alive-target"}
    updated, changes = rewrite_markdown(
        "[Gone](Старая/merged-away.md)", slugs, aliases
    )
    assert updated == "[Gone](Данные и модели/alive-target.md)"
    assert changes[0]["via"] == "alias"


def test_rewrite_unknown_stays():
    updated, changes = rewrite_markdown(
        "[Nope](Ghost/no-such.md)", {"alive-target": "Данные и модели/alive-target.md"}, {}
    )
    assert updated == "[Nope](Ghost/no-such.md)"
    assert changes[0]["via"] == "unresolved"


def test_aliases_from_plan_use_slugs_not_old_folders():
    mapping = aliases_from_plan(
        {
            "actions": [
                {
                    "sources": ["Методологии и процессы/merged-away"],
                    "target": "Старая/alive-target",
                }
            ]
        }
    )
    assert mapping == {"merged-away": "alive-target"}


def test_propose_and_apply_relink(tmp_path: Path):
    config = _config(tmp_path)
    hierarchy = _hierarchy()
    save_hierarchy(config, hierarchy)
    root = tmp_path / "kb"
    _write_article(
        root,
        "Методологии и процессы",
        "source-page",
        (
            "---\ntitle: Источник\nfolder: Методологии и процессы\ntags: []\n---\n\n"
            "[Цель](Методологии%20и%20процессы/alive-target.md)\n"
            "[img](files/scan.png)\n"
            "[cite](id:1:2:3)\n"
            "[Gone](Old/merged-away.md)\n"
            "[Nope](Ghost/missing.md)\n"
        ),
    )
    _write_article(root, "Данные и модели", "alive-target", "# Цель\n")
    (root / "curate").mkdir(parents=True)
    (root / "curate" / "merge-plan.json").write_text(
        json.dumps(
            {
                "kind": "merge",
                "actions": [
                    {
                        "sources": ["Old/merged-away"],
                        "target": "Методологии и процессы/alive-target",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plan = propose_relinks(config, hierarchy)
    assert plan["kind"] == "relink"
    tos = {item["to"] for item in plan["rewrites"]}
    assert "Данные и модели/alive-target.md" in tos
    hrefs = {item["href"] for item in plan["unresolved"]}
    assert "Ghost/missing.md" in hrefs
    assert all("files/" not in item["from"] for item in plan["rewrites"])
    assert all(not str(item["from"]).startswith("id:") for item in plan["rewrites"])
    apply_relink_plan(config, hierarchy, plan)
    text = (root / "articles" / "Методологии и процессы" / "source-page.md").read_text(
        encoding="utf-8"
    )
    assert "Данные и модели/alive-target.md" in text
    assert "%20" not in text
    assert "[img](files/scan.png)" in text
    assert "[cite](id:1:2:3)" in text
    assert "[Nope](Ghost/missing.md)" in text
    assert text.startswith("---\n")
    assert "title: Источник" in text


def test_apply_relink_leaves_drafts(tmp_path: Path):
    config = _config(tmp_path)
    hierarchy = _hierarchy()
    save_hierarchy(config, hierarchy)
    root = tmp_path / "kb"
    folder = root / "articles" / "Методологии и процессы"
    folder.mkdir(parents=True)
    (folder / "source-page.md").write_text("# Источник\n", encoding="utf-8")
    (folder / "source-page.draft.md").write_text(
        "[Цель](Wrong/alive-target.md)\n", encoding="utf-8"
    )
    _write_article(root, "Данные и модели", "alive-target", "# Цель\n")
    plan = propose_relinks(config, hierarchy)
    apply_relink_plan(config, hierarchy, plan)
    draft = (folder / "source-page.draft.md").read_text(encoding="utf-8")
    assert draft == "[Цель](Wrong/alive-target.md)\n"


def test_merge_persists_alias_and_rewrites_other_articles(tmp_path: Path):
    config = _config(tmp_path)
    hierarchy = Hierarchy(
        folders={
            "Тема": Folder(
                articles={
                    "parent": Article(
                        title="Родитель",
                        messages=[MessageRef(id="1:1:1")],
                    ),
                    "stub": Article(
                        title="Заглушка",
                        messages=[MessageRef(id="1:1:2")],
                    ),
                    "other": Article(title="Другая"),
                }
            )
        }
    )
    save_hierarchy(config, hierarchy)
    articles = tmp_path / "kb" / "articles" / "Тема"
    articles.mkdir(parents=True)
    (articles / "parent.md").write_text("# Родитель\n", encoding="utf-8")
    (articles / "stub.md").write_text("# Заглушка\n", encoding="utf-8")
    (articles / "other.md").write_text("[x](Тема/stub.md)\n", encoding="utf-8")
    apply_merge_actions(
        config,
        hierarchy,
        [{"sources": ["Тема/stub"], "target": "Тема/parent"}],
    )
    aliases = json.loads(
        (tmp_path / "kb" / "curate" / "link-aliases.json").read_text(encoding="utf-8")
    )
    assert aliases["aliases"]["stub"] == "parent"
    other = (articles / "other.md").read_text(encoding="utf-8")
    assert "[x](Тема/parent.md)" in other


def test_reparent_rewrites_links_in_other_finals(tmp_path: Path):
    config = _config(tmp_path)
    hierarchy = Hierarchy(
        folders={
            "Методологии и процессы": Folder(
                articles={
                    "sql-hub": Article(title="SQL"),
                    "index": Article(title="Индекс"),
                }
            )
        }
    )
    save_hierarchy(config, hierarchy)
    src = tmp_path / "kb" / "articles" / "Методологии и процессы"
    src.mkdir(parents=True)
    (src / "sql-hub.md").write_text("# SQL\n", encoding="utf-8")
    (src / "index.md").write_text(
        "[SQL](Методологии и процессы/sql-hub.md)\n", encoding="utf-8"
    )
    dest_folder = "Данные, SQL и хранилища/SQL"
    apply_reparent_actions(
        config,
        hierarchy,
        [{"id": "Методологии и процессы/sql-hub", "folder": dest_folder}],
    )
    index = (src / "index.md").read_text(encoding="utf-8")
    assert f"[SQL]({dest_folder}/sql-hub.md)" in index
    assert (tmp_path / "kb" / "articles" / dest_folder / "sql-hub.md").exists()


def test_cli_relink_dry_run_and_apply(tmp_path: Path, capsys):
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
    )
    cfg = Config(
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
    )
    cfg.config_path = config_path
    hierarchy = _hierarchy()
    save_hierarchy(cfg, hierarchy)
    root = tmp_path / "kb"
    _write_article(
        root,
        "Методологии и процессы",
        "source-page",
        "[Цель](Методологии и процессы/alive-target.md)\n",
    )
    _write_article(root, "Данные и модели", "alive-target", "# Цель\n")
    plan_path = root / "curate" / "relink-plan.json"
    code = main(
        [
            "--config",
            str(config_path),
            "curate",
            "relink",
            "--dry-run",
            "-o",
            str(plan_path),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "замен" in out
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["kind"] == "relink"
    assert plan["rewrites"]
    code = main(
        ["--config", str(config_path), "curate", "relink", "--apply", str(plan_path)]
    )
    assert code == 0
    text = (root / "articles" / "Методологии и процессы" / "source-page.md").read_text(
        encoding="utf-8"
    )
    assert "[Цель](Данные и модели/alive-target.md)" in text
    assert load_hierarchy(cfg).find_by_id("Методологии и процессы/source-page")
    assert slug_map(hierarchy)["alive-target"] == "Данные и модели/alive-target.md"
    backups = list((root / "curate").glob("backup-*"))
    assert backups
