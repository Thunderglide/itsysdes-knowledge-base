import json
import sqlite3
from pathlib import Path

from kb_pipeline.agents.cleanup_plan import propose_cleanup
from kb_pipeline.agents.merger import propose_merges, validate_merge_actions
from kb_pipeline.agents.splitter import parse_markdown_sections, resolve_split_actions
from kb_pipeline.agents.tagger import validate_tag_actions
from kb_pipeline.cli import main
from kb_pipeline.config import Config, CurateSettings, load_config
from kb_pipeline.curate import (
    apply_blocked_reason,
    apply_cleanup_actions,
    apply_merge_actions,
    apply_reparent_actions,
    apply_split_actions,
    apply_tag_actions,
)
from kb_pipeline.content_stitch import split_frontmatter
from kb_pipeline.llm.fake import FakeBackend
from kb_pipeline.models import Article, Folder, Hierarchy, MessageRef
from kb_pipeline.persist import load_hierarchy, save_hierarchy
from kb_pipeline.source import load_messages_by_ids, parse_source_id, source_message_id
from tests.dbutil import add_chat, add_message, create_db, write_config


def _hierarchy() -> Hierarchy:
    return Hierarchy(
        folders={
            "Тема": Folder(
                articles={
                    "parent": Article(
                        title="Родитель",
                        source_subchat="topic_1",
                        messages=[
                            MessageRef(id="1099595414:1:10"),
                            MessageRef(id="1099595414:1:11"),
                            MessageRef(id="1099595414:1:12"),
                            MessageRef(id="1099595414:1:13"),
                            MessageRef(id="1099595414:1:14"),
                            MessageRef(id="1099595414:1:15"),
                        ],
                    ),
                    "stub": Article(
                        title="Пример",
                        source_subchat="topic_1",
                        messages=[MessageRef(id="1099595414:1:20")],
                    ),
                    "dump": Article(
                        title="Большая",
                        source_subchat="topic_1",
                        messages=[
                            MessageRef(id="1099595414:1:30"),
                            MessageRef(id="1099595414:1:31"),
                            MessageRef(id="1099595414:1:32"),
                            MessageRef(id="1099595414:1:33"),
                        ],
                    ),
                }
            )
        }
    )


def test_parse_source_and_load_messages(tmp_path: Path):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    add_message(conn, chat_id, 10, thread_id=1, text="alpha")
    add_message(conn, chat_id, 11, thread_id=1, text="beta")
    conn.row_factory = sqlite3.Row
    conn.commit()
    telegram_id, thread_id, message_id = parse_source_id("1099595414:1:10")
    assert telegram_id == 1099595414
    assert thread_id == 1
    assert message_id == 10
    loaded = load_messages_by_ids(
        conn, ["1099595414:1:11", "1099595414:1:10", "missing"]
    )
    conn.close()
    assert [item.id for item in loaded] == [
        source_message_id(1099595414, 1, 11),
        source_message_id(1099595414, 1, 10),
    ]
    assert loaded[0].text == "beta"


def test_validate_merge_skips_dump_and_non_stubs():
    hierarchy = _hierarchy()
    settings = CurateSettings(merge_max_messages=5, merge_target_max=5)
    actions = validate_merge_actions(
        [
            {
                "sources": ["Тема/stub", "Тема/dump"],
                "target": "Тема/parent",
                "reason": "no",
            },
            {"sources": ["Тема/stub"], "target": "missing", "reason": "no"},
        ],
        hierarchy,
        settings,
    )
    assert actions == []
    settings = CurateSettings(merge_max_messages=5, merge_target_max=80)
    actions = validate_merge_actions(
        [{"sources": ["Тема/stub"], "target": "Тема/parent", "reason": "ok"}],
        hierarchy,
        settings,
    )
    assert actions[0]["sources"] == ["Тема/stub"]


def test_apply_merge_moves_refs_and_deletes_files(tmp_path: Path):
    config = Config(
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
    )
    config.config_path = tmp_path / "config.yaml"
    hierarchy = _hierarchy()
    save_hierarchy(config, hierarchy)
    articles = tmp_path / "kb" / "articles" / "Тема"
    articles.mkdir(parents=True)
    (articles / "stub.md").write_text("stub", encoding="utf-8")
    (articles / "stub.draft.md").write_text("draft", encoding="utf-8")
    (articles / "parent.md").write_text("parent", encoding="utf-8")
    updated, touched = apply_merge_actions(
        config,
        hierarchy,
        [{"sources": ["Тема/stub"], "target": "Тема/parent", "reason": "x"}],
    )
    assert "stub" not in updated.folders["Тема"].articles
    ids = updated.folders["Тема"].articles["parent"].message_ids()
    assert "1099595414:1:20" in ids
    assert not (articles / "stub.md").exists()
    assert touched == ["Тема/parent"]


def test_parse_markdown_sections_collects_citations():
    text = """# Title
intro [x](id:1:1:1)

## Kafka
text [a](id:1099595414:1:30)

## Rabbit
text [b](id:1099595414:1:31)
"""
    sections = parse_markdown_sections(text)
    headings = [item["heading"] for item in sections]
    assert "Kafka" in headings
    kafka = next(item for item in sections if item["heading"] == "Kafka")
    assert kafka["message_ids"] == ["1099595414:1:30"]


def test_apply_split_keeps_remainder(tmp_path: Path):
    config = Config(
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
    )
    hierarchy = _hierarchy()
    updated, touched = apply_split_actions(
        config,
        hierarchy,
        [
            {
                "source": "Тема/dump",
                "parts": [
                    {
                        "title": "Kafka",
                        "slug": "kafka",
                        "folder": "Тема",
                        "message_ids": ["1099595414:1:30", "1099595414:1:31"],
                    }
                ],
                "remainder_ids": ["1099595414:1:32", "1099595414:1:33"],
            }
        ],
    )
    dump = updated.folders["Тема"].articles["dump"]
    kafka = updated.folders["Тема"].articles["kafka"]
    assert dump.message_ids() == {"1099595414:1:32", "1099595414:1:33"}
    assert kafka.message_ids() == {"1099595414:1:30", "1099595414:1:31"}
    assert kafka.title == "Kafka"
    assert "Тема/kafka" in touched
    assert "Тема/dump" in touched


def test_resolve_split_uses_explicit_ids():
    hierarchy = _hierarchy()
    actions = resolve_split_actions(
        [
            {
                "source": "Тема/dump",
                "parts": [
                    {
                        "title": "Часть",
                        "slug": "chast",
                        "folder": "Тема",
                        "message_ids": ["1099595414:1:30"],
                    }
                ],
            }
        ],
        [],
        hierarchy,
    )
    assert actions[0]["parts"][0]["message_ids"] == ["1099595414:1:30"]
    assert "1099595414:1:31" in actions[0]["remainder_ids"]


def test_apply_blocked_when_session_incomplete(tmp_path: Path, capsys):
    output = tmp_path / "kb"
    output.mkdir()
    (output / "last_run.json").write_text(
        json.dumps({"thread_id": "kb-open", "fake": True}) + "\n",
        encoding="utf-8",
    )
    (output / ".checkpoints").mkdir()
    (output / ".checkpoints" / "lg.sqlite").write_text("not-a-checkpoint", encoding="utf-8")
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=output,
    )
    plan = output / "merge-plan.json"
    plan.write_text(
        json.dumps({"kind": "merge", "actions": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    code = main(
        ["--config", str(config_path), "curate", "merge", "--apply", str(plan), "--fake"]
    )
    assert code == 1
    assert "пока kb-pipeline resume не завершит" in capsys.readouterr().err
    cfg = load_config(config_path)
    assert apply_blocked_reason(cfg)


def test_apply_allowed_when_checkpoint_missing(tmp_path: Path):
    output = tmp_path / "kb"
    output.mkdir()
    (output / "last_run.json").write_text(
        json.dumps({"thread_id": "kb-open", "fake": True}) + "\n",
        encoding="utf-8",
    )
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=output,
    )
    cfg = load_config(config_path)
    assert apply_blocked_reason(cfg) is None


def test_cli_merge_dry_run_and_apply(tmp_path: Path, capsys):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    for msg_id, text in (
        (10, "parent-a"),
        (11, "parent-b"),
        (12, "parent-c"),
        (13, "parent-d"),
        (14, "parent-e"),
        (15, "parent-f"),
        (20, "stub-one"),
    ):
        add_message(conn, chat_id, msg_id, thread_id=1, text=text)
    conn.commit()
    conn.close()
    output = tmp_path / "kb"
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=db,
        files_root=tmp_path / "data",
        output_dir=output,
    )
    cfg = load_config(config_path)
    hierarchy = _hierarchy()
    save_hierarchy(cfg, hierarchy)
    articles = output / "articles" / "Тема"
    articles.mkdir(parents=True)
    (articles / "parent.md").write_text("# Родитель\n", encoding="utf-8")
    (articles / "stub.md").write_text("# Пример\n", encoding="utf-8")
    plan_path = output / "curate" / "merge-plan.json"
    code = main(
        [
            "--config",
            str(config_path),
            "curate",
            "merge",
            "--dry-run",
            "-o",
            str(plan_path),
            "--fake",
        ]
    )
    assert code == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["kind"] == "merge"
    assert plan["actions"]
    code = main(
        [
            "--config",
            str(config_path),
            "curate",
            "merge",
            "--apply",
            str(plan_path),
            "--fake",
        ]
    )
    assert code == 0
    updated = load_hierarchy(cfg)
    assert "stub" not in updated.folders["Тема"].articles
    assert "1099595414:1:20" in updated.folders["Тема"].articles["parent"].message_ids()
    assert not (articles / "stub.md").exists()
    assert (articles / "parent.md").exists()
    backups = list((output / "curate").glob("backup-*"))
    assert backups


def test_cli_split_apply_regenerates_parts(tmp_path: Path):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    for msg_id, text in (
        (30, "kafka-1"),
        (31, "kafka-2"),
        (32, "rabbit-1"),
        (33, "rabbit-2"),
    ):
        add_message(conn, chat_id, msg_id, thread_id=1, text=text)
    conn.commit()
    conn.close()
    output = tmp_path / "kb"
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=db,
        files_root=tmp_path / "data",
        output_dir=output,
    )
    cfg = load_config(config_path)
    hierarchy = _hierarchy()
    save_hierarchy(cfg, hierarchy)
    articles = output / "articles" / "Тема"
    articles.mkdir(parents=True)
    (articles / "dump.md").write_text(
        "# Большая\n\n## Kafka\n[a](id:1099595414:1:30)\n\n## Rabbit\n[b](id:1099595414:1:32)\n",
        encoding="utf-8",
    )
    plan_path = output / "split-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "kind": "split",
                "actions": [
                    {
                        "source": "Тема/dump",
                        "parts": [
                            {
                                "title": "Kafka",
                                "slug": "kafka",
                                "folder": "Тема",
                                "message_ids": ["1099595414:1:30", "1099595414:1:31"],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    code = main(
        [
            "--config",
            str(config_path),
            "curate",
            "split",
            "--apply",
            str(plan_path),
            "--fake",
        ]
    )
    assert code == 0
    updated = load_hierarchy(cfg)
    assert "kafka" in updated.folders["Тема"].articles
    assert updated.folders["Тема"].articles["kafka"].message_ids() == {
        "1099595414:1:30",
        "1099595414:1:31",
    }
    assert updated.folders["Тема"].articles["dump"].message_ids() == {
        "1099595414:1:32",
        "1099595414:1:33",
    }
    assert (articles / "kafka.md").exists()
    assert (articles / "dump.md").exists()
    kafka_text = (articles / "kafka.md").read_text(encoding="utf-8")
    assert "id:1099595414:1:30" in kafka_text
    assert "Основная часть" not in kafka_text
    dump_text = (articles / "dump.md").read_text(encoding="utf-8")
    assert "Rabbit" in dump_text


def test_agent_fallback_uses_structure():
    cfg = Config(
        sqlite_path="db.sqlite",
        files_root="files",
        agents={
            "structure": {"backend": "fake", "model": "x"},
            "generator": {"backend": "fake", "model": "x"},
        },
    )
    assert cfg.agent("merge").backend == "fake"
    assert cfg.agent("split").backend == "fake"
    assert cfg.agent("reparent").backend == "fake"
    assert cfg.agent("tag").backend == "fake"


def test_cleanup_deletes_empty_and_merges_cross_folder_dupes(tmp_path: Path):
    config = Config(
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
    )
    config.config_path = tmp_path / "config.yaml"
    hierarchy = Hierarchy(
        folders={
            "Методологии и процессы": Folder(
                articles={
                    "dup": Article(
                        title="Терминология",
                        messages=[MessageRef(id="1099595414:1:1")],
                    ),
                    "empty": Article(title="Пустая", messages=[]),
                }
            ),
            "Требования и документация": Folder(
                articles={
                    "dup": Article(
                        title="Терминология",
                        messages=[
                            MessageRef(id="1099595414:1:2"),
                            MessageRef(id="1099595414:1:3"),
                        ],
                    ),
                }
            ),
        }
    )
    save_hierarchy(config, hierarchy)
    articles_a = tmp_path / "kb" / "articles" / "Методологии и процессы"
    articles_b = tmp_path / "kb" / "articles" / "Требования и документация"
    articles_a.mkdir(parents=True)
    articles_b.mkdir(parents=True)
    (articles_a / "dup.md").write_text("# Терминология\nA [x](id:1099595414:1:1)\n", encoding="utf-8")
    (articles_a / "empty.md").write_text("# Пустая\n", encoding="utf-8")
    (articles_b / "dup.md").write_text("# Терминология\nB [y](id:1099595414:1:2)\n", encoding="utf-8")
    plan = propose_cleanup(config, hierarchy)
    kinds = {item["type"] for item in plan["actions"]}
    assert "delete" in kinds
    assert "merge" in kinds
    updated, _touched = apply_cleanup_actions(config, hierarchy, plan["actions"])
    leftover = updated.folders.get("Методологии и процессы")
    assert leftover is None or "empty" not in leftover.articles
    assert leftover is None or "dup" not in leftover.articles
    kept = updated.folders["Требования и документация"].articles["dup"]
    assert kept.message_ids() == {"1099595414:1:1", "1099595414:1:2", "1099595414:1:3"}
    assert not (articles_a / "empty.md").exists()
    meta, body = split_frontmatter((articles_b / "dup.md").read_text(encoding="utf-8"))
    assert "id:1099595414:1:1" in body
    assert meta["folder"] == "Требования и документация"


def test_merge_cluster_creates_new_sql_target(tmp_path: Path):
    config = Config(
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
        agents={"merge": {"backend": "fake", "model": "x"}},
    )
    config.config_path = tmp_path / "config.yaml"
    hierarchy = Hierarchy(
        folders={
            "Методологии и процессы": Folder(
                articles={
                    "sql-a": Article(
                        title="SQL-запросы для работы с пагинацией",
                        messages=[MessageRef(id="1099595414:1:1")],
                    ),
                    "sql-b": Article(
                        title="SQL-запросы для работы с пагинацией",
                        messages=[MessageRef(id="1099595414:1:2")],
                    ),
                }
            )
        }
    )
    save_hierarchy(config, hierarchy)
    folder = tmp_path / "kb" / "articles" / "Методологии и процессы"
    folder.mkdir(parents=True)
    (folder / "sql-a.md").write_text("# A\n[a](id:1099595414:1:1)\n", encoding="utf-8")
    (folder / "sql-b.md").write_text("# B\n[b](id:1099595414:1:2)\n", encoding="utf-8")
    plan = propose_merges(FakeBackend(), config, hierarchy)
    created = [item for item in plan["actions"] if item.get("create")]
    assert created
    updated, touched = apply_merge_actions(config, hierarchy, plan["actions"])
    assert "sql-a" not in updated.folders["Методологии и процессы"].articles
    assert touched
    found = updated.find_by_id(touched[0])
    assert found is not None
    _folder, _slug, article = found
    assert article.message_ids() == {"1099595414:1:1", "1099595414:1:2"}


def test_reparent_and_tag_apply(tmp_path: Path):
    config = Config(
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
    )
    config.config_path = tmp_path / "config.yaml"
    hierarchy = Hierarchy(
        folders={
            "Методологии и процессы": Folder(
                articles={
                    "sql-hub": Article(
                        title="SQL и пагинация",
                        messages=[MessageRef(id="1099595414:1:1")],
                        tags=[],
                    )
                }
            )
        }
    )
    save_hierarchy(config, hierarchy)
    src = tmp_path / "kb" / "articles" / "Методологии и процессы"
    src.mkdir(parents=True)
    (src / "sql-hub.md").write_text("# SQL и пагинация\nselect 1\n", encoding="utf-8")
    dest_folder = "Данные, SQL и хранилища/SQL"
    updated, touched = apply_reparent_actions(
        config,
        hierarchy,
        [{"id": "Методологии и процессы/sql-hub", "folder": dest_folder}],
    )
    leftover = updated.folders.get("Методологии и процессы")
    assert leftover is None or "sql-hub" not in leftover.articles
    article = updated.folders["Данные, SQL и хранилища"].folders["SQL"].articles["sql-hub"]
    assert article.title == "SQL и пагинация"
    dest = tmp_path / "kb" / "articles" / "Данные, SQL и хранилища" / "SQL" / "sql-hub.md"
    assert dest.exists()
    assert not (src / "sql-hub.md").exists()
    tagged, _ = apply_tag_actions(
        config,
        updated,
        [{"id": f"{dest_folder}/sql-hub", "tags": ["sql", "howto", "bogus"]}],
    )
    assert tagged.find_by_id(f"{dest_folder}/sql-hub")[2].tags == ["sql", "howto"]
    validated = validate_tag_actions(
        [{"id": f"{dest_folder}/sql-hub", "tags": ["sql", "howto", "bogus"]}],
        tagged,
        config,
    )
    assert validated[0]["tags"] == ["sql", "howto"]
    assert "bogus" in validated[0]["proposed_tags"]
