from kb_pipeline.models import Article, Folder, Hierarchy, MessageRef, coerce_hierarchy, merge_hierarchy, slugify


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
