import json
from pathlib import Path

from kb_pipeline.cli import main
from tests.dbutil import add_chat, add_message, create_db, write_config


def test_cli_run_without_subchat_lists_topics(tmp_path: Path, capsys):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    add_message(conn, chat_id, 1, thread_id=107312, text="hello")
    conn.commit()
    conn.close()
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=db,
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
        fake_agents=False,
    )
    code = main(["--config", str(config_path), "run"])
    assert code == 1
    err = capsys.readouterr().err
    assert "--subchat" in err or "Укажите --subchat" in err
    assert "topic_107312" in err


def test_cli_list_subchats(tmp_path: Path, capsys):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn, username="itsysdes")
    add_message(conn, chat_id, 107312, thread_id=107312, text="Применение нейросетей и LLM")
    add_message(conn, chat_id, 107313, thread_id=107312, text="follow-up")
    conn.commit()
    conn.close()
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=db,
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
        fake_agents=False,
    )
    code = main(["--config", str(config_path), "list-subchats"])
    assert code == 0
    out = capsys.readouterr().out
    assert "itsysdes" in out
    assert "topic_107312" in out
    assert "Применение нейросетей и LLM" in out
    assert "2" in out


def _write_failed_session(output: Path) -> None:
    (output / "cache" / "topic_1").mkdir(parents=True, exist_ok=True)
    (output / ".checkpoints").mkdir(parents=True, exist_ok=True)
    (output / "articles" / "itsysdes").mkdir(parents=True, exist_ok=True)
    (output / ".gitkeep").write_text("", encoding="utf-8")
    (output / "cache" / "topic_1" / "batch_001.jsonl").write_text("{}\n", encoding="utf-8")
    (output / ".checkpoints" / "lg.sqlite").write_text("not-a-checkpoint", encoding="utf-8")
    (output / "articles" / "itsysdes" / "rules.draft.md").write_text("draft", encoding="utf-8")
    (output / "articles" / "itsysdes" / "rules.critic.json").write_text("{}\n", encoding="utf-8")
    (output / "articles" / "itsysdes" / "rules.md").write_text("final", encoding="utf-8")
    (output / "articles" / "itsysdes" / "other.draft.md").write_text("keep-draft", encoding="utf-8")
    (output / "articles" / "itsysdes" / "other.md").write_text("keep-final", encoding="utf-8")
    (output / "last_run.json").write_text(
        json.dumps(
            {
                "thread_id": "kb-failed",
                "fake": True,
                "until": "polish",
                "work_items": [
                    {
                        "subchat_id": "topic_1",
                        "folder_name": "itsysdes/topic_1",
                        "part_file": "batch_001",
                        "part_index": 1,
                        "chat_db_id": 1,
                        "telegram_id": 1,
                        "thread_id": 1,
                        "first_message_id": 1,
                        "last_message_id": 2,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "hierarchy.json").write_text(
        json.dumps(
            {
                "folders": {
                    "itsysdes": {
                        "articles": {
                            "rules": {
                                "title": "Rules",
                                "source_subchat": "topic_1",
                                "messages": [
                                    {"id": "c:1", "context": "", "part": "batch_001"}
                                ],
                            },
                            "other": {
                                "title": "Other",
                                "source_subchat": "topic_999",
                                "messages": [
                                    {"id": "c:2", "context": "", "part": "batch_001"}
                                ],
                            },
                        },
                        "folders": {},
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_clean_requires_mode(tmp_path: Path):
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
    )
    try:
        main(["--config", str(config_path), "clean"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected argparse to require --session, --checkpoints or --kb")


def test_clean_session_without_last_run(tmp_path: Path, capsys):
    output = tmp_path / "kb"
    output.mkdir()
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=output,
    )
    code = main(["--config", str(config_path), "clean", "--session", "-y"])
    assert code == 1
    assert "last_run.json" in capsys.readouterr().err


def test_clean_session_removes_runtime_cache_and_drafts(tmp_path: Path):
    output = tmp_path / "kb"
    _write_failed_session(output)
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=output,
    )
    code = main(["--config", str(config_path), "clean", "--session", "-y"])
    assert code == 0
    assert not (output / "last_run.json").exists()
    assert not (output / "cache" / "topic_1" / "batch_001.jsonl").exists()
    assert not (output / ".checkpoints" / "lg.sqlite").exists()
    assert not (output / "articles" / "itsysdes" / "rules.draft.md").exists()
    assert not (output / "articles" / "itsysdes" / "rules.critic.json").exists()
    assert (output / "articles" / "itsysdes" / "rules.md").read_text(encoding="utf-8") == "final"
    assert (output / "articles" / "itsysdes" / "other.draft.md").exists()
    assert (output / "articles" / "itsysdes" / "other.md").exists()
    assert (output / "hierarchy.json").exists()
    assert (output / ".gitkeep").exists()


def test_clean_session_skips_completed_run(tmp_path: Path, capsys):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    add_message(conn, chat_id, 1, thread_id=107312, text="hello")
    conn.commit()
    conn.close()
    output = tmp_path / "kb"
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=db,
        files_root=tmp_path / "data",
        output_dir=output,
    )
    run_code = main(
        [
            "--config",
            str(config_path),
            "run",
            "--subchat",
            "topic_107312",
            "--part",
            "1",
            "--fake",
        ]
    )
    assert run_code == 0
    assert (output / "last_run.json").exists()
    finals = list(output.joinpath("articles").rglob("*.md"))
    assert finals
    code = main(["--config", str(config_path), "clean", "--session", "-y"])
    assert code == 0
    assert "уже завершён" in capsys.readouterr().out
    assert (output / "last_run.json").exists()
    assert list(output.joinpath("articles").rglob("*.md"))


def test_clean_kb_wipes_output(tmp_path: Path):
    output = tmp_path / "kb"
    _write_failed_session(output)
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=output,
    )
    code = main(["--config", str(config_path), "clean", "--kb", "-y"])
    assert code == 0
    assert (output / ".gitkeep").exists()
    assert not (output / "last_run.json").exists()
    assert not (output / "hierarchy.json").exists()
    assert not list(output.joinpath("articles").rglob("*"))
    assert (output / "articles").is_dir()
    assert (output / "cache").is_dir()
    assert (output / ".checkpoints").is_dir()


def test_clean_checkpoints_keeps_articles(tmp_path: Path):
    output = tmp_path / "kb"
    _write_failed_session(output)
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=tmp_path / "export.db",
        files_root=tmp_path / "data",
        output_dir=output,
    )
    code = main(["--config", str(config_path), "clean", "--checkpoints", "-y"])
    assert code == 0
    assert not (output / ".checkpoints" / "lg.sqlite").exists()
    assert (output / "last_run.json").exists()
    assert (output / "hierarchy.json").exists()
    assert (output / "articles" / "itsysdes" / "rules.md").exists()
    assert (output / "articles" / "itsysdes" / "rules.draft.md").exists()
    assert (output / ".checkpoints").is_dir()
