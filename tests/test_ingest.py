from pathlib import Path

import pytest

from kb_pipeline.config import Config
from kb_pipeline.ingest import (
    UsageError,
    build_work_items,
    filter_part_indices,
    ingest_part,
)
from kb_pipeline.persist import relative_media_path
from kb_pipeline.source import (
    message_token_count,
    normalize_date,
    pack_batches,
    parse_telegram_message_id,
    source_message_id,
    strip_attachment_paths,
    subchat_id_for,
)
from tests.dbutil import add_attachment, add_chat, add_message, create_db


def _config(tmp_path: Path, db: Path, **kwargs) -> Config:
    return Config(
        sqlite_path=db,
        files_root=tmp_path / "data",
        output_dir=tmp_path / "kb",
        **kwargs,
    )


def test_subchat_id_for():
    assert subchat_id_for(thread_id=107312, telegram_id=1099595414) == "topic_107312"
    assert subchat_id_for(thread_id=None, telegram_id=2881596567) == "chat_2881596567"


def test_source_message_id_format():
    assert source_message_id(1099595414, 60491, 91452) == "1099595414:60491:91452"
    assert source_message_id(1099595414, None, 12345) == "1099595414:0:12345"
    assert source_message_id(1099595414, 0, 12345) == "1099595414:0:12345"


def test_parse_telegram_message_id_uses_last_segment():
    assert parse_telegram_message_id("1099595414:60491:91452") == 91452
    assert parse_telegram_message_id("1099595414:0:12345") == 12345
    assert parse_telegram_message_id("91452") == 91452


def test_filter_part_indices_inclusive_range():
    indices = [1, 2, 3, 4, 5]
    assert filter_part_indices(indices, part=2, from_part=None, to_part=None) == [2]
    assert filter_part_indices(indices, part=None, from_part=2, to_part=4) == [2, 3, 4]


def test_filter_part_indices_rejects_mixed_flags():
    with pytest.raises(ValueError):
        filter_part_indices([1], part=1, from_part=1, to_part=None)


def test_build_work_items_requires_subchat(tmp_path: Path):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    add_message(conn, chat_id, 10, thread_id=107312, text="hello")
    conn.commit()
    conn.close()
    cfg = _config(tmp_path, db)
    with pytest.raises(UsageError):
        build_work_items(
            cfg, subchats=None, all_subchats=False, part=None, from_part=None, to_part=None
        )
    items = build_work_items(
        cfg, subchats=["topic_107312"], all_subchats=False, part=1, from_part=None, to_part=None
    )
    assert len(items) == 1
    assert items[0].subchat_id == "topic_107312"
    assert items[0].part_file == "batch_001"
    assert items[0].thread_id == 107312
    assert items[0].first_message_id == 10
    assert items[0].last_message_id == 10


def test_groups_by_thread_and_orders_old_to_new(tmp_path: Path):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    add_message(conn, chat_id, 30, thread_id=2, date="2024-02-01T00:00:00+00:00", text="newer thread2")
    add_message(conn, chat_id, 11, thread_id=1, date="2024-01-02T00:00:00+00:00", text="second")
    add_message(conn, chat_id, 10, thread_id=1, date="2024-01-01T00:00:00+00:00", text="first")
    add_message(conn, chat_id, 5, thread_id=None, date="2023-01-01T00:00:00+00:00", text="channel")
    conn.commit()
    conn.close()
    cfg = _config(tmp_path, db)
    items = build_work_items(
        cfg, subchats=None, all_subchats=True, part=None, from_part=None, to_part=None
    )
    assert [item.subchat_id for item in items] == ["topic_1", "topic_2", "chat_1099595414"]
    messages, _ = ingest_part(cfg, items[0])
    assert [message.text for message in messages] == ["first", "second"]
    assert messages[0].id == "1099595414:1:10"
    assert messages[0].date == "2024-01-01 00:00:00"
    channel = next(item for item in items if item.subchat_id == "chat_1099595414")
    channel_messages, _ = ingest_part(cfg, channel)
    assert channel_messages[0].id == "1099595414:0:5"
    assert channel.first_message_id == 5
    assert channel.last_message_id == 5


def test_chats_filter(tmp_path: Path):
    db = tmp_path / "export.db"
    conn = create_db(db)
    keep = add_chat(conn, telegram_id=1, username="keep")
    skip = add_chat(conn, telegram_id=2, username="skip")
    add_message(conn, keep, 1, thread_id=9, text="keep me")
    add_message(conn, skip, 1, thread_id=9, text="skip me")
    conn.commit()
    conn.close()
    cfg = _config(tmp_path, db, chats=["keep"])
    items = build_work_items(
        cfg, subchats=None, all_subchats=True, part=None, from_part=None, to_part=None
    )
    assert len(items) == 1
    assert items[0].telegram_id == 1


def test_strips_attachment_paths_and_skips_empty(tmp_path: Path):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    add_message(conn, chat_id, 1, thread_id=7, text="")
    pk = add_message(
        conn,
        chat_id,
        2,
        thread_id=7,
        text="photo caption\n\nfiles/1/2/photo.jpg",
    )
    add_attachment(conn, pk, file_path="files/1/2/photo.jpg", file_name="photo.jpg")
    conn.commit()
    conn.close()
    cfg = _config(tmp_path, db)
    items = build_work_items(
        cfg, subchats=["7"], all_subchats=False, part=None, from_part=None, to_part=None
    )
    messages, _ = ingest_part(cfg, items[0])
    assert len(messages) == 1
    assert messages[0].text == "photo caption"
    assert messages[0].attachments[0].path == "files/1/2/photo.jpg"


def test_token_batches_split_when_over_limit(tmp_path: Path):
    db = tmp_path / "export.db"
    conn = create_db(db)
    chat_id = add_chat(conn)
    add_message(conn, chat_id, 1, thread_id=3, text="alpha " * 20)
    add_message(conn, chat_id, 2, thread_id=3, text="bravo " * 20)
    conn.commit()
    conn.close()
    cfg = _config(tmp_path, db)
    items_all = build_work_items(
        cfg, subchats=["topic_3"], all_subchats=False, part=None, from_part=None, to_part=None
    )
    sample, _ = ingest_part(cfg, items_all[0])
    one_cost = message_token_count(sample[0], cfg.max_message_chars)
    cfg_small = _config(tmp_path, db, max_batch_tokens=one_cost)
    items = build_work_items(
        cfg_small,
        subchats=["topic_3"],
        all_subchats=False,
        part=None,
        from_part=None,
        to_part=None,
    )
    assert len(items) == 2
    assert items[0].part_index == 1
    assert items[1].part_index == 2
    only_second = build_work_items(
        cfg_small,
        subchats=["topic_3"],
        all_subchats=False,
        part=2,
        from_part=None,
        to_part=None,
    )
    assert len(only_second) == 1
    assert only_second[0].first_message_id == 2


def test_strip_and_normalize_helpers():
    assert strip_attachment_paths("hi\nfiles/a.jpg\n", ["files/a.jpg"]) == "hi"
    assert normalize_date("2023-03-14T10:12:22+00:00") == "2023-03-14 10:12:22"
    packed = pack_batches([], max_tokens=10, max_chars=100)
    assert packed == []


def test_relative_media_path_keeps_exporter_relative(tmp_path: Path):
    cfg = _config(tmp_path, tmp_path / "export.db")
    assert relative_media_path(cfg, "files/1/2/photo.jpg") == "files/1/2/photo.jpg"
    absolute = cfg.files_root_resolved / "files" / "1" / "2" / "photo.jpg"
    assert relative_media_path(cfg, str(absolute)) == "files/1/2/photo.jpg"
