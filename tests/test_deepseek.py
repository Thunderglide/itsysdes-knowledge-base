from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import pytest

from kb_pipeline.config import AgentLLMConfig, Config, DeepSeekSettings
from kb_pipeline.llm.deepseek import DeepSeekBackend, DeepSeekError
from kb_pipeline.llm.factory import get_backend


def _backend(model: str = "deepseek-flash") -> DeepSeekBackend:
    backend = DeepSeekBackend(api_key="sk-test", model=model)
    backend._client = MagicMock()
    return backend


def test_from_config_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = Config(sqlite_path="export.db", files_root="data")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekBackend.from_config(config)


def test_from_config_reads_key_and_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    config = Config(
        sqlite_path="export.db",
        files_root="data",
        deepseek=DeepSeekSettings(model="deepseek-flash", timeout_sec=120, max_retries=0),
    )
    backend = DeepSeekBackend.from_config(config, "deepseek-v4-pro")
    assert backend._model == "deepseek-v4-pro"
    assert backend._client.api_key == "sk-from-env"
    assert backend._client.max_retries == 0


def test_complete_sends_chat_messages():
    backend = _backend()
    backend._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
    )
    text = backend.complete("sys", "user")
    assert text == "ok"
    kwargs = backend._client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "deepseek-flash"
    assert kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert kwargs["max_tokens"] == 16384
    assert "response_format" not in kwargs


def test_json_mode_large_payload_raises_max_tokens():
    backend = _backend()
    backend._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )
    backend.complete("sys", "x" * 80_000, json_mode=True)
    kwargs = backend._client.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 32768


def test_json_mode_caps_max_tokens():
    backend = _backend()
    backend._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )
    backend.complete("sys", "user", json_mode=True)
    kwargs = backend._client.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 8192


def test_complete_scales_max_tokens_to_draft():
    backend = _backend()
    backend._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
    )
    draft = "# Статья\n\n" + ("абзац про брокеры. " * 3000)
    backend.complete("sys", json.dumps({"draft": draft}, ensure_ascii=False))
    budget = backend._client.chat.completions.create.call_args.kwargs["max_tokens"]
    assert 8192 < budget <= 131072


def test_json_mode_sets_response_format():
    backend = _backend()
    backend._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )
    text = backend.complete("sys", "user", json_mode=True)
    assert text == "{}"
    kwargs = backend._client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}


def test_json_mode_retries_only_response_format_errors():
    backend = _backend()
    ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])
    backend._client.chat.completions.create.side_effect = [
        Exception("response_format json_object is not supported"),
        ok,
    ]
    text = backend.complete("sys", "user", json_mode=True)
    assert text == "{}"
    assert backend._client.chat.completions.create.call_count == 2
    second = backend._client.chat.completions.create.call_args_list[1].kwargs
    assert "response_format" not in second


def test_complete_returns_empty_string_for_blank_content():
    backend = _backend()
    backend._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=""),
                finish_reason="stop",
            )
        ]
    )
    assert backend.complete("sys", "user") == ""


def test_complete_wraps_api_errors():
    backend = _backend()
    backend._client.chat.completions.create.side_effect = Exception("401 invalid api key")
    with pytest.raises(DeepSeekError, match="invalid api key"):
        backend.complete("sys", "user")


def test_factory_builds_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-factory")
    config = Config(
        sqlite_path="export.db",
        files_root="data",
        agents={"structure": AgentLLMConfig(backend="deepseek", model="deepseek-flash")},
    )
    backend = get_backend("structure", config)
    assert isinstance(backend, DeepSeekBackend)
    assert backend._model == "deepseek-flash"
