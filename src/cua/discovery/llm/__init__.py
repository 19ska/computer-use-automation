"""Provider-neutral LLM seam for the discovery loop.

`base` defines the contract (LLMProvider Protocol, LLMActionCall,
LLMDecisionError, LLMProviderError, validate_single_call). Each concrete
provider (`gemini_provider.GeminiProvider`, `groq_provider.GroqProvider`)
implements that contract in its own module — the discovery engine
imports only from `base`, never from a specific provider module
directly. `factory.create_llm_provider` (driven by the LLM_PROVIDER
environment variable) is the one place that picks a concrete provider;
that wiring happens once, in engine.py's `run_discovery`.
"""

from .base import LLMActionCall, LLMDecision, LLMDecisionError, LLMProvider, LLMProviderError, validate_single_call

__all__ = [
    "LLMActionCall",
    "LLMDecision",
    "LLMDecisionError",
    "LLMProvider",
    "LLMProviderError",
    "validate_single_call",
]
