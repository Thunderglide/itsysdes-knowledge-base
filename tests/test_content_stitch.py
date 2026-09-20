from kb_pipeline.content_stitch import (
    citations_lost,
    extract_part_markdown,
    remainder_markdown,
    split_frontmatter,
    stitch_markdown,
    strip_scaffold,
    with_frontmatter,
)
from kb_pipeline.persist import (
    load_article_record,
    move_article_artifacts,
    save_article_artifacts,
)
from kb_pipeline.config import Config
from kb_pipeline.models import ArticleRecord
from kb_pipeline.taxonomy import filter_tags, load_taxonomy


def test_strip_scaffold_drops_placeholder_sections():
    text = """# SQL

> В текущем файле есть только одно сообщение — вопрос.

### О чём эта статья

Факт про индексы [сообщение](id:1:1:1).

### Возможные подразделы

- Заказы

### Что нужно добавить

Нужны примеры.

### Вопросы из чата

Вопрос [x](id:1099595414:66639:101252).
"""
    cleaned = strip_scaffold(text)
    assert "Что нужно добавить" not in cleaned
    assert "Возможные подразделы" not in cleaned
    assert "id:1099595414:66639:101252" in cleaned
    assert "В текущем файле есть только одно сообщение" not in cleaned


def test_stitch_concatenates_named_parts():
    text = stitch_markdown(
        "SQL: архивы",
        [
            ("SQL-запросы для работы с архивом", "# SQL-запросы для работы с архивом\n\nАрхив Hive [a](id:1:1:10)\n"),
            ("маркерплейс", "# SQL-запросы для работы с маркерплейсом\n\nМаркет [b](id:1:1:11)\n"),
        ],
    )
    assert text.startswith("# SQL: архивы")
    assert "id:1:1:10" in text
    assert "id:1:1:11" in text
    assert "## " in text


def test_extract_and_remainder_by_citations():
    text = """# Большая

## Kafka
text [a](id:1099595414:1:30)

## Rabbit
text [b](id:1099595414:1:32)
"""
    part = extract_part_markdown(
        text, title="Kafka", headings=[], message_ids=["1099595414:1:30"]
    )
    rest = remainder_markdown(text, headings=[], message_ids=["1099595414:1:30"])
    assert "Kafka" in part
    assert "id:1099595414:1:30" in part
    assert "Rabbit" in rest
    assert "id:1099595414:1:32" in rest
    assert "id:1099595414:1:30" not in rest


def test_frontmatter_roundtrip(tmp_path):
    wrapped = with_frontmatter("# Body\n", title="T", folder="A/B", tags=["sql", "howto"])
    meta, body = split_frontmatter(wrapped)
    assert meta["title"] == "T"
    assert meta["folder"] == "A/B"
    assert meta["tags"] == ["sql", "howto"]
    assert body.strip() == "# Body"
    config = Config(
        sqlite_path=tmp_path / "db.sqlite",
        files_root=tmp_path / "files",
        output_dir=tmp_path / "kb",
    )
    record = ArticleRecord(
        article_id="A/B/s",
        title="T",
        folder="A/B",
        slug="s",
        final="# Body\n",
        tags=["sql"],
    )
    save_article_artifacts(config, record, write_final=True)
    loaded = load_article_record(config, "A/B/s", "T", "A/B", "s")
    assert loaded.final.strip() == "# Body"
    assert loaded.tags == ["sql"]
    move_article_artifacts(config, "A/B", "s", "C", "s")
    moved = load_article_record(config, "C/s", "T", "C", "s", tags=["sql"])
    assert moved.final.strip() == "# Body"
    assert not (tmp_path / "kb" / "articles" / "A" / "B" / "s.md").exists()


def test_filter_tags_keeps_vocab_only():
    taxonomy = load_taxonomy()
    kept, proposed = filter_tags(["sql", "not-a-real-tag", "howto", "SQL"], taxonomy)
    assert "sql" in kept
    assert "howto" in kept
    assert kept.count("sql") == 1
    assert "not-a-real-tag" in proposed


def test_citations_lost_detects_drop():
    original = "keep [a](id:1:1:1)"
    assert citations_lost(original, "no cites")
    assert not citations_lost(original, "keep [a](id:1:1:1) extra")
