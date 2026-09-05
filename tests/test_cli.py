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
