"""Provider selection seam: turns LLM_PROVIDER (+ per-provider model env
vars) into a concrete LLMProvider instance. This is the ONLY module,
besides run_discovery() calling it, that ever names GeminiProvider or
GroqProvider directly — the rest of the discovery engine talks solely to
the LLMProvider Protocol from .base.
"""

from __future__ import annotations

import os

from .base import LLMProvider, LLMProviderError
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider

_ENV_PROVIDER = "LLM_PROVIDER"
DEFAULT_PROVIDER = "gemini"

_DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "groq": "openai/gpt-oss-120b",
}
_MODEL_ENV_VARS = {
    "gemini": "GEMINI_MODEL",
    "groq": "GROQ_MODEL",
}

SUPPORTED_PROVIDERS = tuple(_DEFAULT_MODELS)


def resolve_provider_name() -> str:
    """Reads LLM_PROVIDER (default: gemini). Raises LLMProviderError —
    the same structured, secret-free error type every other provider
    failure uses — for an unsupported value, so an unsupported provider
    fails cleanly before discovery starts, exactly like a missing API key
    would."""
    raw = os.environ.get(_ENV_PROVIDER, DEFAULT_PROVIDER).strip().lower()
    if raw not in SUPPORTED_PROVIDERS:
        raise LLMProviderError(
            f"Unsupported {_ENV_PROVIDER} '{raw}'; supported values: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    return raw


def resolve_model(provider_name: str) -> str:
    """Never raises — used both on the happy path and to fill in a
    best-effort `model` value for a structured failure result, including
    when provider_name itself turned out to be unsupported."""
    env_var = _MODEL_ENV_VARS.get(provider_name)
    default = _DEFAULT_MODELS.get(provider_name, "unknown")
    if env_var is None:
        return default
    return os.environ.get(env_var, default)


def create_llm_provider(provider_name: str, model: str, *, system_prompt: str) -> LLMProvider:
    if provider_name == "gemini":
        return GeminiProvider(model, system_prompt=system_prompt)
    if provider_name == "groq":
        return GroqProvider(model, system_prompt=system_prompt)
    # Unreachable when provider_name came from resolve_provider_name(),
    # kept as a defensive guard for direct callers.
    raise LLMProviderError(
        f"Unsupported {_ENV_PROVIDER} '{provider_name}'; supported values: {', '.join(SUPPORTED_PROVIDERS)}"
    )
