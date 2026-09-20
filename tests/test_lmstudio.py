from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kb_pipeline.config import Config, LMStudioSettings
from kb_pipeline.llm.lmstudio import (
    LMStudioBackend,
    LMStudioError,
    _is_response_format_error,
    _is_timeout_error,
)


def _backend(model: str = "qwen/qwen3.5-9b") -> LMStudioBackend:
    backend = LMStudioBackend(
        base_url="http://127.0.0.1:1234/v1",
        api_key="lm-studio",
        model=model,
    )
    backend._client = MagicMock()
    return backend


def test_complete_raises_when_no_models_listed():
    backend = _backend()
    backend._client.models.list.return_value = SimpleNamespace(data=[])
    with pytest.raises(LMStudioError, match="downloaded ≠ loaded"):
        backend.complete("sys", "user", json_mode=True)
    backend._client.chat.completions.create.assert_not_called()


def test_complete_raises_when_model_id_unknown():
    backend = _backend("qqwen/qwen3.5-9b")
    backend._client.models.list.return_value = SimpleNamespace(
        data=[SimpleNamespace(id="qwen/qwen3.5-9b")]
    )
    with pytest.raises(LMStudioError, match="qqwen/qwen3.5-9b"):
        backend.complete("sys", "user")
    backend._client.chat.completions.create.assert_not_called()


def test_json_mode_does_not_retry_no_models_loaded():
    backend = _backend()
    backend._client.models.list.return_value = SimpleNamespace(
        data=[SimpleNamespace(id="qwen/qwen3.5-9b")]
    )
    backend._client.chat.completions.create.side_effect = Exception(
        'Error code: 400 - No models loaded. Please load a model in the developer page'
    )
    with pytest.raises(LMStudioError, match="lms load"):
        backend.complete("sys", "user", json_mode=True)
    assert backend._client.chat.completions.create.call_count == 1


def test_json_mode_retries_only_response_format_errors():
    backend = _backend()
    backend._client.models.list.return_value = SimpleNamespace(
        data=[SimpleNamespace(id="qwen/qwen3.5-9b")]
    )
    ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])
    backend._client.chat.completions.create.side_effect = [
        Exception("response_format json_object is not supported"),
        ok,
    ]
    text = backend.complete("sys", "user", json_mode=True)
    assert text == "{}"
    assert backend._client.chat.completions.create.call_count == 2


def test_response_format_detector_ignores_no_models():
    exc = Exception("No models loaded. Please load a model")
    assert _is_response_format_error(exc) is False


def test_json_mode_does_not_retry_timeout():
    backend = _backend()
    backend._client.models.list.return_value = SimpleNamespace(
        data=[SimpleNamespace(id="qwen/qwen3.5-9b")]
    )
    backend._client.chat.completions.create.side_effect = Exception("Request timed out.")
    with pytest.raises(LMStudioError, match="Повторный запрос не отправлялся"):
        backend.complete("sys", "user", json_mode=True)
    assert backend._client.chat.completions.create.call_count == 1


def test_timeout_detector():
    assert _is_timeout_error(Exception("Request timed out.")) is True
    assert _is_timeout_error(Exception("No models loaded")) is False
    assert _is_response_format_error(Exception("Request timed out.")) is False


def test_openai_client_disables_retries_and_uses_long_timeout():
    backend = LMStudioBackend.from_config(
        Config(
            sqlite_path="export.db",
            files_root="data",
            lmstudio=LMStudioSettings(
                base_url="http://127.0.0.1:1234/v1",
                model="qwen/qwen3.5-9b",
                timeout_sec=3600,
                max_retries=0,
            ),
        )
    )
    assert backend._client.max_retries == 0
    timeout = backend._client.timeout
    read = getattr(timeout, "read", timeout)
    assert read == 3600 or read is None

