"""Tests for .env-backed settings and the LLM factory."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import Settings
from app.utils.llm import LLMFactory, build_llm


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
