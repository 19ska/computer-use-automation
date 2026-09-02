"""Tests for cua.discovery.llm.factory — provider selection driven by
LLM_PROVIDER. No real network access: provider construction fails fast
on the missing-API-key check before ever reaching a real SDK client.
"""

from cua.discovery.llm import LLMProviderError
from cua.discovery.llm.factory import create_llm_provider, resolve_model, resolve_provider_name
from cua.discovery.llm.gemini_provider import GeminiProvider
from cua.discovery.llm.groq_provider import GroqProvider


def test_resolve_provider_name_defaults_to_gemini(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert resolve_provider_name() == "gemini"


def test_resolve_provider_name_reads_env_case_insensitively(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "GROQ")
    assert resolve_provider_name() == "groq"


def test_resolve_provider_name_rejects_unsupported_value(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    try:
        resolve_provider_name()
        raised = False
    except LLMProviderError as exc:
        raised = True
        assert "openai" in str(exc)
        assert "gemini" in str(exc)
        assert "groq" in str(exc)
    assert raised


def test_resolve_model_uses_provider_specific_env_var(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-custom")
    monkeypatch.setenv("GROQ_MODEL", "groq-custom")
    assert resolve_model("gemini") == "gemini-custom"
    assert resolve_model("groq") == "groq-custom"


def test_resolve_model_falls_back_to_documented_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    assert resolve_model("gemini") == "gemini-2.5-flash"
    assert resolve_model("groq") == "openai/gpt-oss-120b"


def test_resolve_model_never_raises_for_unknown_provider():
    assert resolve_model("openai") == "unknown"


def test_create_llm_provider_selects_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = create_llm_provider("gemini", "gemini-2.5-flash", system_prompt="x")
    assert isinstance(provider, GeminiProvider)
    assert provider.provider_name == "gemini"
    assert provider.model == "gemini-2.5-flash"


def test_create_llm_provider_selects_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    provider = create_llm_provider("groq", "openai/gpt-oss-120b", system_prompt="x")
    assert isinstance(provider, GroqProvider)
    assert provider.provider_name == "groq"
    assert provider.model == "openai/gpt-oss-120b"


def test_create_llm_provider_rejects_unsupported_provider_name():
    try:
        create_llm_provider("openai", "gpt-4", system_prompt="x")
        raised = False
    except LLMProviderError:
        raised = True
    assert raised


def test_create_llm_provider_propagates_missing_api_key_as_llm_provider_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    try:
        create_llm_provider("groq", "openai/gpt-oss-120b", system_prompt="x")
        raised = False
    except LLMProviderError:
        raised = True
    assert raised
