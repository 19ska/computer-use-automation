"""Deterministic replay engine (Milestone 3).

Executes a CapabilityArtifact against a live Playwright browser with NO
LLM calls anywhere in this package. Every decision (which locator to try,
what to type, what "success" means) comes from the artifact's own data.
"""
