from pathlib import Path

from kb_pipeline.parser import parse_messages_file, parse_messages_text

FIXTURE = Path(__file__).parent / "fixtures" / "messages_part_001.txt"


def test_parse_fixture_ids_and_collision():
    messages = parse_messages_file(FIXTURE, subchat_id="topic_test", part_file="messages_part_001.txt")
    assert len(messages) == 4
    assert messages[0].id == "2023-03-14 10:12:22|166567328"
    assert messages[1].id == "2023-03-14 10:12:22|166567328|#2"
    assert messages[0].text == ""
    assert "Правила проектирования" in messages[1].text
    assert messages[2].attachments[0].path.endswith("63334.jpg")
    assert messages[2].attachments[0].description.startswith("jpg:")
    assert messages[3].author == "539512226"
    assert messages[1].part_file == "messages_part_001.txt"
    assert messages[1].subchat_id == "topic_test"


def test_parse_skips_channel_header_only():
    text = Path(FIXTURE).read_text(encoding="utf-8")
    messages = parse_messages_text(text)
    assert not any(item.date.startswith("#") for item in messages)
    assert messages[0].date.startswith("2023")
