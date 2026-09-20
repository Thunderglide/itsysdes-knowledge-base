from kb_pipeline.models import Article, Folder, Hierarchy, Message, MessageRef, merge_hierarchy
from kb_pipeline.structure_guard import (
    compact_polish_listing,
    guard_incoming,
    listing_for_structure,
)


def _article(title: str, slug: str, ids: list[str]) -> tuple[str, Article]:
    return slug, Article(
        title=title,
        messages=[MessageRef(id=item) for item in ids],
    )


def test_guard_drops_articles_without_current_ids():
    existing = Hierarchy()
    incoming = Hierarchy(
        folders={
            "Тема": Folder(
                articles={
                    "empty": Article(title="Пустая", messages=[]),
                    "other": Article(
                        title="Чужая",
                        messages=[MessageRef(id="old-id")],
                    ),
                }
            )
        }
    )
    guarded = guard_incoming(
        existing,
        incoming,
        [Message(id="new-id", author="a", date="2020-01-01", text="индексы в таблицах")],
    )
    assert guarded.folders == {}
    merged, touched = merge_hierarchy(
        existing,
        guarded,
        current_message_ids={"new-id"},
        current_part="batch_001",
        current_subchat="topic_1",
    )
    assert merged.folders == {}
    assert touched == []


def test_guard_collapses_fanout_to_existing_overlap():
    existing = Hierarchy(
        folders={
            "Данные": Folder(
                articles={
                    "indeksy": Article(
                        title="Индексы в базах данных",
                        messages=[MessageRef(id=f"old-{i}") for i in range(12)],
                    )
                }
            )
        }
    )
    incoming_articles = {}
    for name in ("llm", "nejrosetyami", "marshrutami"):
        incoming_articles[f"sql-zaprosy-dlya-raboty-s-{name}"] = Article(
            title=f"SQL-запросы для работы с {name}",
            messages=[MessageRef(id="101252")],
        )
    incoming = Hierarchy(
        folders={"Методологии": Folder(articles=incoming_articles)}
    )
    messages = [
        Message(
            id="101252",
            author="a",
            date="2025-01-23",
            text="Зачем таблицам присваиваются индексы",
        )
    ]
    guarded = guard_incoming(existing, incoming, messages)
    assert "Методологии" not in guarded.folders
    data = guarded.folders["Данные"].articles["indeksy"]
    assert data.message_ids() == {"101252"}
    merged, touched = merge_hierarchy(
        existing,
        guarded,
        current_message_ids={"101252"},
        current_part="batch_055",
        current_subchat="topic_66639",
    )
    assert list(merged.folders["Данные"].articles) == ["indeksy"]
    assert "101252" in merged.folders["Данные"].articles["indeksy"].message_ids()
    assert touched == ["Данные/indeksy"]


def test_guard_allows_one_new_when_no_overlap():
    existing = Hierarchy(
        folders={
            "Прочее": Folder(
                articles={"old": Article(title="Карьера аналитика", messages=[MessageRef(id="x")] * 10)}
            )
        }
    )
    incoming = Hierarchy(
        folders={
            "Тема": Folder(
                articles={
                    f"sql-{i}": Article(
                        title=f"SQL-запросы для работы с {i}",
                        messages=[MessageRef(id="m1"), MessageRef(id="m2")],
                    )
                    for i in range(10)
                }
            )
        }
    )
    messages = [
        Message(id="m1", author="a", date="2020-01-01", text="hello world example"),
        Message(id="m2", author="a", date="2020-01-01", text="another unique snippet"),
    ]
    guarded = guard_incoming(existing, incoming, messages)
    folder = next(iter(guarded.folders.values()))
    assert len(folder.articles) == 1
    article = next(iter(folder.articles.values()))
    assert article.message_ids() == {"m1", "m2"}


def test_guard_existing_title_keeps_id_not_duplicate():
    existing = Hierarchy(
        folders={
            "Тема": Folder(
                articles={"old": Article(title="Старая тема", messages=[MessageRef(id="keep")])}
            )
        }
    )
    incoming = Hierarchy(
        folders={
            "Тема": Folder(
                articles={
                    "old": Article(
                        title="Старая тема",
                        messages=[MessageRef(id="m1")],
                    ),
                    "clone": Article(
                        title="Другое имя",
                        messages=[MessageRef(id="m1")],
                    ),
                }
            )
        }
    )
    guarded = guard_incoming(
        existing,
        incoming,
        [Message(id="m1", author="a", date="2020-01-01", text="тема старая обсуждение")],
    )
    assert list(guarded.folders["Тема"].articles) == ["old"]
    assert guarded.folders["Тема"].articles["old"].message_ids() == {"m1"}


def test_listing_for_structure_is_capped():
    articles = {
        f"a-{i:03d}": Article(title=f"Тема номер {i} уникальная", messages=[MessageRef(id=str(i))])
        for i in range(60)
    }
    articles["indexes"] = Article(
        title="Индексы в базах данных",
        messages=[MessageRef(id="related")],
    )
    hierarchy = Hierarchy(folders={"A": Folder(articles=articles)})
    messages = [
        Message(id="related", author="a", date="2020-01-01", text="индексы таблиц"),
        Message(id="new", author="a", date="2020-01-01", text="индексы"),
    ]
    listing = listing_for_structure(hierarchy, messages)
    ids = [row["id"] for row in listing]
    assert "A/indexes" in ids
    assert len(listing) <= 41


def test_polish_listing_caps_large_catalog():
    listing = [
        {
            "id": f"F/a-{i}",
            "title": f"Статья {i}",
            "folder": "F",
            "path": f"F/a-{i}.md",
        }
        for i in range(120)
    ]
    listing.append(
        {
            "id": "Other/x",
            "title": "Индексы",
            "folder": "Other",
            "path": "Other/x.md",
        }
    )
    compact = compact_polish_listing(listing, title="Индексы в базах", folder="Other")
    assert len(compact) == 80
    assert compact[0]["folder"] == "Other"
