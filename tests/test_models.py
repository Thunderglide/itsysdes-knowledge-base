from kb_pipeline.models import (
    Article,
    CriticReport,
    Folder,
    Hierarchy,
    MessageRef,
    coerce_hierarchy,
    compact_message_contexts,
    merge_hierarchy,
    slugify,
)


def test_message_ref_truncates_long_context():
    ref = MessageRef.model_validate({"id": "1", "context": "слово " * 80})
    assert len(ref.context) <= 241
    assert ref.context.endswith("…")


def test_compact_message_contexts_keeps_ids():
    hierarchy = Hierarchy(
        folders={
            "A": Folder(
                articles={
                    "old": Article(
                        title="Старая",
                        messages=[MessageRef(id="old-id", context="подробности")],
                    )
                }
            )
        }
    )
    compacted = compact_message_contexts(hierarchy)
    assert compacted.folders["A"].articles["old"].messages[0].id == "old-id"
    assert compacted.folders["A"].articles["old"].messages[0].context == ""
    assert hierarchy.folders["A"].articles["old"].messages[0].context == "подробности"


def test_to_listing_includes_message_count():
    hierarchy = Hierarchy(
        folders={
            "A": Folder(
                articles={
                    "old": Article(
                        title="Старая",
                        messages=[
                            MessageRef(id="a"),
                            MessageRef(id="b"),
                        ],
                    )
                }
            )
        }
    )
    row = hierarchy.to_listing()[0]
    assert row["id"] == "A/old"
    assert row["message_count"] == 2
    assert row["path"] == "A/old.md"
    assert row["tags"] == []


def test_folder_drops_null_articles():
    hierarchy = coerce_hierarchy(
        {
            "folders": {
                "Методологии и процессы": {
                    "articles": {
                        "ok": {"title": "Ок", "messages": []},
                        "requirements-analysis-tools": None,
                    },
                    "folders": {"broken": None},
                }
            }
        }
    )
    folder = hierarchy.folders["Методологии и процессы"]
    assert list(folder.articles) == ["ok"]
    assert folder.folders == {}


def test_article_drops_message_refs_without_id():
    article = Article.model_validate(
        {
            "title": "Импорт",
            "messages": [
                {"id": "ok-1", "context": "есть"},
                {"id": None, "context": "обрезано"},
                {"context": "без id"},
            ],
        }
    )
    assert [item.id for item in article.messages] == ["ok-1"]


def test_critic_report_coerces_question_objects():
    report = CriticReport.model_validate(
        {
            "remarks": [{"section": "В целом", "kind": "note", "text": "ok", "line": 12}],
            "questions": [
                {"question": "Чем заменить шаблон?"},
                "Какие ограничения стоит зафиксировать?",
                {"text": "Был ли согласован формат?"},
                {"question": ""},
            ],
        }
    )
    assert report.questions == [
        "Чем заменить шаблон?",
        "Какие ограничения стоит зафиксировать?",
        "Был ли согласован формат?",
    ]
    assert report.remarks == [
        {"section": "В целом", "kind": "note", "text": "ok", "line": "12"}
    ]


def test_slugify_keeps_cyrillic():
    assert slugify("Компромиссы проектирования") == "компромиссы-проектирования"


def test_coerce_simple_mapping():
    hierarchy = coerce_hierarchy({"Архитектура": ["Компромиссы проектирования"]})
    article = hierarchy.folders["Архитектура"].articles["компромиссы-проектирования"]
    assert article.title == "Компромиссы проектирования"


def test_merge_does_not_drop_existing_and_marks_touched():
    existing = Hierarchy(
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
    incoming = coerce_hierarchy(
        {
            "folders": {
                "Архитектура": {
                    "articles": {
                        "new": {
                            "title": "Новая",
                            "messages": [{"id": "2025-01-01 00:00:00|1", "context": "c"}],
                        }
                    },
                    "folders": {},
                }
            }
        }
    )
    merged, touched = merge_hierarchy(
        existing,
        incoming,
        current_message_ids={"2025-01-01 00:00:00|1"},
        current_part="messages_part_001.txt",
        current_subchat="topic_1",
    )
    assert "old" in merged.folders["Архитектура"].articles
    assert "new" in merged.folders["Архитектура"].articles
    assert touched == ["Архитектура/new"]
    assert merged.folders["Архитектура"].articles["new"].messages[0].part == "messages_part_001.txt"
