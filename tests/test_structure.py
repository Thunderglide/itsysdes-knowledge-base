import json
import os
from pathlib import Path

import pytest

from kb_pipeline.agents.common import LLMError, complete_json, complete_text
from kb_pipeline.agents.polisher import run_polisher
from kb_pipeline.agents.structure import run_structure
from kb_pipeline.config import Config
from kb_pipeline.llm.fake import FakeBackend
from kb_pipeline.models import (
    Article,
    ArticleRecord,
    CriticReport,
    Folder,
    Hierarchy,
    Message,
    MessageRef,
)
from kb_pipeline.persist import article_final_is_current, save_article_artifacts


def test_complete_json_repairs_truncated_object_without_retry():
    class Once:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            self.calls += 1
            return '{"a": 1'

    backend = Once()
    assert complete_json(backend, "sys", "user") == {"a": 1}
    assert backend.calls == 1


def test_structure_sends_listing_and_keeps_existing_articles():
    captured: dict[str, str] = {}

    class Capture(FakeBackend):
        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            captured["user"] = user
            return super().complete(system, user, json_mode=json_mode)

    hierarchy = Hierarchy(
        folders={
            "Архитектура": Folder(
                articles={
                    "old": Article(
                        title="Старая",
                        messages=[MessageRef(id="old-id", context="x")],
                    )
                }
            )
        }
    )
    updated, touched = run_structure(
        Capture(),
        Config(sqlite_path="db.sqlite", files_root="files"),
        hierarchy=hierarchy,
        messages=[Message(id="new-id", author="a", date="2020-01-01", text="hello")],
        subchat_id="topic_1",
        part_file="batch_005",
    )
    payload = json.loads(captured["user"])
    assert "existing_articles" in payload
    assert "hierarchy" not in payload
    assert payload["existing_articles"][0]["title"] == "Старая"
    assert payload["existing_articles"][0]["message_count"] == 1
    assert "old" in updated.folders["Архитектура"].articles
    assert touched


def test_structure_listing_excludes_bulk_unrelated():
    captured: dict[str, str] = {}

    class Capture(FakeBackend):
        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            captured["user"] = user
            return super().complete(system, user, json_mode=json_mode)

    articles = {
        f"n-{i:03d}": Article(title=f"Нейтральная тема {i} зебра", messages=[])
        for i in range(80)
    }
    articles["old"] = Article(title="Старая", messages=[MessageRef(id="old-id")])
    hierarchy = Hierarchy(folders={"Архитектура": Folder(articles=articles)})
    run_structure(
        Capture(),
        Config(sqlite_path="db.sqlite", files_root="files"),
        hierarchy=hierarchy,
        messages=[Message(id="new-id", author="a", date="2020-01-01", text="hello")],
        subchat_id="topic_1",
        part_file="batch_005",
    )
    payload = json.loads(captured["user"])
    assert len(payload["existing_articles"]) <= 40


def test_structure_truncates_long_messages():
    captured: dict[str, str] = {}

    class Capture(FakeBackend):
        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            captured["user"] = user
            return super().complete(system, user, json_mode=json_mode)

    run_structure(
        Capture(),
        Config(sqlite_path="db.sqlite", files_root="files", max_message_chars=60000),
        hierarchy=Hierarchy(),
        messages=[
            Message(id="long-id", author="a", date="2020-01-01", text="x" * 5000)
        ],
        subchat_id="topic_1",
        part_file="batch_024",
    )
    payload = json.loads(captured["user"])
    assert len(payload["messages"][0]["text"]) < 1000
    assert payload["messages"][0]["text"].endswith("…[обрезано]")


def test_complete_text_retries_empty_response():
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            self.calls += 1
            return "" if self.calls == 1 else "# ok\n"

    backend = Flaky()
    assert complete_text(backend, "sys", "user") == "# ok"
    assert backend.calls == 2


def test_complete_text_uses_fallback_without_retry():
    class Empty:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            self.calls += 1
            return "  "

    backend = Empty()
    assert complete_text(backend, "sys", "user", fallback="# draft\n") == "# draft"
    assert backend.calls == 1


def test_complete_text_raises_without_fallback():
    class Empty:
        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            return ""

    with pytest.raises(LLMError, match="Empty LLM response"):
        complete_text(Empty(), "sys", "user")


def test_polisher_falls_back_to_draft_on_empty_response():
    class Empty:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            self.calls += 1
            return ""

    backend = Empty()
    text = run_polisher(
        backend,
        Config(sqlite_path="db.sqlite", files_root="files"),
        draft="# Черновик\n\nТекст.",
        critic=CriticReport(questions=["Что уточнить?"]),
        messages=[],
        article_listing=[],
    )
    assert text.startswith("# Черновик")
    assert "Что уточнить?" in text
    assert backend.calls == 1


def test_polisher_sends_compact_listing_and_truncated_messages():
    captured: dict[str, str] = {}

    class Capture:
        def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
            captured["user"] = user
            return "# ok\n"

    run_polisher(
        Capture(),
        Config(sqlite_path="db.sqlite", files_root="files", max_message_chars=60000),
        draft="# Черновик",
        critic=CriticReport(),
        messages=[Message(id="1", author="a", date="2020-01-01", text="x" * 5000)],
        article_listing=[{"id": "A/b", "title": "B", "folder": "A", "path": "A/b.md"}],
    )
    payload = json.loads(captured["user"])
    assert payload["articles"] == [{"title": "B", "path": "A/b.md"}]
    assert len(payload["messages"][0]["text"]) < 3000


def test_article_final_is_current(tmp_path: Path):
    config = Config(
        sqlite_path=tmp_path / "db.sqlite",
        files_root=tmp_path / "files",
        output_dir=tmp_path / "kb",
    )
    record = ArticleRecord(
        article_id="A/b",
        title="B",
        folder="A",
        slug="b",
        draft="# draft",
        final="# final",
    )
    save_article_artifacts(config, record, write_final=True)
    assert article_final_is_current(config, record)
    draft_path = tmp_path / "kb" / "articles" / "A" / "b.draft.md"
    draft_path.write_text("# newer draft", encoding="utf-8")
    newer = draft_path.stat().st_mtime + 5
    os.utime(draft_path, (newer, newer))
    assert not article_final_is_current(config, record)


def test_new_critic_does_not_count_as_polished(tmp_path: Path):
    config = Config(
        sqlite_path=tmp_path / "db.sqlite",
        files_root=tmp_path / "files",
        output_dir=tmp_path / "kb",
    )
    record = ArticleRecord(
        article_id="A/b",
        title="B",
        folder="A",
        slug="b",
        draft="# draft",
        critic_comments={"questions": ["q"]},
        final="# final",
    )
    save_article_artifacts(config, record, write_final=True)
    critic_path = tmp_path / "kb" / "articles" / "A" / "b.critic.json"
    critic_path.write_text('{"questions": ["new"]}\n', encoding="utf-8")
    newer = critic_path.stat().st_mtime + 5
    os.utime(critic_path, (newer, newer))
    assert not article_final_is_current(config, record)
