"""LLM-driven discovery loop (Milestone 4).

Genuinely uses the Gemini API to observe/decide/act against a live
browser. Does NOT compile a reusable capability artifact — that is
Milestone 5. This package only produces a structured discovery
transcript/evidence trail. The LLM provider is isolated behind
cua.discovery.llm.LLMProvider — see that package for the provider-neutral
seam and cua.discovery.llm.gemini_provider for the concrete Gemini
implementation.
"""
