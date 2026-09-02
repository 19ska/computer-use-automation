"""Deterministic artifact compiler (Milestone 5).

Converts a successful discovery run's evidence into a CapabilityArtifact
consumable by the existing deterministic replay engine. Makes ZERO LLM
calls — it only reads structured, already-recorded evidence
(cua.discovery.evidence's compile-ready fields: resolved_locator,
value_source, url_path, outcome) and combines it with a capability-
specific CompilationTemplate (templates.py). Generic compiler logic
(events.py, steps.py, compile.py) contains no capability-specific
business logic — that lives entirely in a template instance.
"""

from .compile import CompileResult, compile_and_write, compile_artifact
from .events import CompilationError
from .templates import CompilationTemplate, get_template

__all__ = [
    "CompilationError",
    "CompilationTemplate",
    "CompileResult",
    "compile_and_write",
    "compile_artifact",
    "get_template",
]
