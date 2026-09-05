from pathlib import Path

from kb_pipeline.cli import main
from tests.dbutil import add_chat, add_message, create_db, write_config


def _seed(tmp_path: Path) -> Path:
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    add_message(
        conn,
        chat_id,
        10,
        thread_id=107312,
        author_name="166567328",
        date="2023-03-14T10:12:22+00:00",
        text="Правила проектирования взаимодействия и интерфейсов",
    )
    add_message(
        conn,
        chat_id,
        11,
        thread_id=107312,
        author_name="539512226",
        date="2023-03-20T07:08:03+00:00",
        text="28 марта указали, и действительно, сразу возникла нагрузка",
    )
    conn.commit()
    conn.close()
    return db


def test_fake_pipeline_one_file(tmp_path: Path):
    db = _seed(tmp_path)
    output = tmp_path / "kb"
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=db,
        files_root=tmp_path / "data",
        output_dir=output,
    )
    code = main(
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
    assert code == 0
    assert (output / "hierarchy.json").exists()
    articles = list(output.joinpath("articles").rglob("*.md"))
    drafts = list(output.joinpath("articles").rglob("*.draft.md"))
    critics = list(output.joinpath("articles").rglob("*.critic.json"))
    finals = [path for path in articles if not path.name.endswith(".draft.md")]
    assert drafts
    assert critics
    assert finals


def test_until_structure_skips_later_agents(tmp_path: Path):
    db = _seed(tmp_path)
    output = tmp_path / "kb"
    config_path = write_config(
        tmp_path / "config.yaml",
        sqlite_path=db,
        files_root=tmp_path / "data",
        output_dir=output,
    )
    code = main(
        [
            "--config",
            str(config_path),
            "run",
            "--subchat",
            "topic_107312",
            "--part",
            "1",
            "--until",
            "structure",
            "--fake",
        ]
    )
    assert code == 0
    assert (output / "hierarchy.json").exists()
    assert not list(output.joinpath("articles").rglob("*.md"))
