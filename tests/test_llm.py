"""Tests for .env-backed settings and the LLM factory."""

from __future__ import annotations

import logging

from langchain_openai import ChatOpenAI

from app.config import Settings
from app.utils.llm import LLMFactory, build_llm, invoke_json


class _Response:
    def __init__(self, content):
        self.content = content


class _BoundLLM:
    def __init__(self, content, raise_on_invoke=False):
        self._content = content
        self._raise = raise_on_invoke

    def invoke(self, prompt):
        if self._raise:
            raise RuntimeError("response_format not supported by this backend")
        return _Response(self._content)


class _FakeLLM:
    """A minimal LLM double supporting .bind(...).invoke(...) like ChatOpenAI."""

    def __init__(self, content, bind_content=None, bind_raises=False):
        self._content = content
        self._bind_content = bind_content if bind_content is not None else content
        self._bind_raises = bind_raises
        self.bind_calls = 0
        self.plain_invoke_calls = 0

    def bind(self, **kwargs):
        self.bind_calls += 1
        return _BoundLLM(self._bind_content, raise_on_invoke=self._bind_raises)

    def invoke(self, prompt):
        self.plain_invoke_calls += 1
        return _Response(self._content)


def test_settings_reads_ai_gateway_api_key(monkeypatch):
    """AI_GATEWAY_API_KEY (Vercel's convention) populates openai_api_key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")
    settings = Settings(_env_file=None)
    assert settings.openai_api_key == "gw-test-key"


def test_settings_falls_back_to_openai_api_key(monkeypatch):
    """OPENAI_API_KEY is accepted when AI_GATEWAY_API_KEY is absent."""
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oai-test-key")
    settings = Settings(_env_file=None)
    assert settings.openai_api_key == "oai-test-key"


def test_settings_no_key_in_env_defaults_to_none(monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.openai_api_key is None


def test_use_llm_true_when_key_present_and_mock_off():
    settings = Settings(openai_api_key="sk-test", mock_mode=False)
    assert settings.use_llm is True


def test_use_llm_false_when_mock_mode_forced():
    settings = Settings(openai_api_key="sk-test", mock_mode=True)
    assert settings.use_llm is False


def test_use_llm_false_when_no_key():
    settings = Settings(openai_api_key=None, mock_mode=False)
    assert settings.use_llm is False


def test_llm_factory_disabled_returns_none(offline_settings):
    assert LLMFactory(offline_settings).enabled is False
    assert LLMFactory(offline_settings).build() is None
    assert build_llm(offline_settings) is None


def test_llm_factory_builds_chatopenai_when_enabled():
    settings = Settings(
        openai_api_key="sk-test",
        openai_base_url="https://example.test/v1",
        model="openai/gpt-4o-mini",
        temperature=0.2,
        mock_mode=False,
    )
    factory = LLMFactory(settings)
    assert factory.enabled is True

    llm = factory.build()

    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "openai/gpt-4o-mini"
    assert llm.temperature == 0.2
    assert llm.openai_api_base == "https://example.test/v1"
    assert llm.openai_api_key.get_secret_value() == "sk-test"


def test_invoke_json_uses_native_json_mode_when_supported():
    llm = _FakeLLM(content="fallback text", bind_content='{"a": 1}')
    assert invoke_json(llm, "prompt") == '{"a": 1}'


def test_invoke_json_falls_back_when_bind_invoke_rejects_response_format():
    llm = _FakeLLM(content="plain text", bind_raises=True)
    assert invoke_json(llm, "prompt") == "plain text"


def test_invoke_json_falls_back_when_llm_has_no_bind():
    class NoBindLLM:
        def invoke(self, prompt):
            return _Response("plain text")

    assert invoke_json(NoBindLLM(), "prompt") == "plain text"


def test_invoke_json_handles_plain_string_response():
    class StringLLM:
        def bind(self, **kwargs):
            raise RuntimeError("no json mode")

        def invoke(self, prompt):
            return "already a string"

    assert invoke_json(StringLLM(), "prompt") == "already a string"


def test_invoke_json_logs_fallback_at_debug(caplog):
    llm = _FakeLLM(content="fallback text", bind_raises=True)
    with caplog.at_level(logging.DEBUG, logger="app.utils.llm"):
        invoke_json(llm, "prompt")
    assert any("JSON-mode invoke failed" in r.message for r in caplog.records)


def test_invoke_json_stops_retrying_json_mode_after_first_rejection():
    # A gateway that rejects response_format should only pay that failed
    # round trip once per llm instance, not on every single call.
    llm = _FakeLLM(content="plain text", bind_raises=True)

    invoke_json(llm, "prompt one")
    invoke_json(llm, "prompt two")
    invoke_json(llm, "prompt three")

    assert llm.bind_calls == 1
    assert llm.plain_invoke_calls == 3


def test_invoke_json_keeps_using_json_mode_once_confirmed_supported():
    llm = _FakeLLM(content="fallback", bind_content='{"a": 1}')

    assert invoke_json(llm, "prompt one") == '{"a": 1}'
    assert invoke_json(llm, "prompt two") == '{"a": 1}'

    assert llm.bind_calls == 2
    assert llm.plain_invoke_calls == 0
