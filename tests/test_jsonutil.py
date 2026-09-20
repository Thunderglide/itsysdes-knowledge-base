import json

from kb_pipeline.jsonutil import extract_json_object


def test_extract_valid_object():
    assert extract_json_object('prefix {"a": 1} trailing') == {"a": 1}


def test_extract_fenced_object():
    text = """```json
{"folders": {"A": {"articles": {}, "folders": {}}}}
```"""
    assert extract_json_object(text)["folders"]["A"]["articles"] == {}


def test_repairs_truncated_nested_object():
    raw = '{"folders": {"A": {"articles": {"x": {"title": "ok"}}, "folders": {}'
    data = extract_json_object(raw)
    assert data["folders"]["A"]["articles"]["x"]["title"] == "ok"


def test_repairs_truncated_string_and_keeps_last_key():
    raw = '{"folders": {"A": {"articles": {"x": {"title": "cut'
    data = extract_json_object(raw)
    assert data["folders"]["A"]["articles"]["x"]["title"] == "cut"


def test_repairs_trailing_comma():
    raw = '{"questions": ["q1",'
    data = extract_json_object(raw)
    assert data["questions"] == ["q1"]


def test_repairs_truncated_key_without_null_value():
    raw = (
        '{"folders": {"A": {"articles": {"ok": {"title": "t"}, '
        '"requirements-analysis-tools":'
    )
    data = extract_json_object(raw)
    articles = data["folders"]["A"]["articles"]
    assert articles["ok"]["title"] == "t"
    assert "requirements-analysis-tools" not in articles
