"""Provider-neutral tool schema definitions for the discovery loop.

Plain JSON Schema per tool (name/description/input_schema) — this is the
single source of truth for the six logical tools. Each LLM provider
adapter (see cua.discovery.llm) wraps this same schema into its own
SDK's tool-declaration format; nothing provider-specific lives here.

Response-shape validation (exactly one actionable call, never zero,
never more than one, never an unknown name) lives in
cua.discovery.llm.base.validate_single_call — it operates on the
provider-neutral LLMActionCall type, not on any raw SDK response.
"""

from __future__ import annotations

from typing import Any

_TARGET_FIELDS = {
    "target_description": {
        "type": "string",
        "description": "Human-readable description of the control, e.g. 'Transfer Funds link'.",
    },
    "accessible_role": {
        "type": "string",
        "enum": ["button", "link", "textbox", "combobox", "checkbox", "radio", "heading"],
        "description": "The ARIA role of the control, closest match from this list.",
    },
    "accessible_name": {
        "type": "string",
        "description": "The visible text/label that identifies the control.",
    },
}
_RATIONALE_FIELD = {
    "rationale": {
        "type": "string",
        "description": "One short sentence explaining this action. No internal reasoning.",
    }
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "navigate",
        "description": "Navigate to a path within the target application.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url_path": {
                    "type": "string",
                    "description": "Path relative to the application's base URL, e.g. /transfer.htm",
                },
                **_RATIONALE_FIELD,
            },
            "required": ["url_path", "rationale"],
        },
    },
    {
        "name": "click",
        "description": "Click a described control (button or link).",
        "input_schema": {
            "type": "object",
            "properties": {**_TARGET_FIELDS, **_RATIONALE_FIELD},
            "required": ["target_description", "accessible_role", "accessible_name", "rationale"],
        },
    },
    {
        "name": "type_text",
        "description": "Type a value into a described text field.",
        "input_schema": {
            "type": "object",
            "properties": {
                **_TARGET_FIELDS,
                "value": {"type": "string", "description": "The text to type."},
                **_RATIONALE_FIELD,
            },
            "required": [
                "target_description",
                "accessible_role",
                "accessible_name",
                "value",
                "rationale",
            ],
        },
    },
    {
        "name": "select_option",
        "description": "Select an option in a described dropdown/combobox.",
        "input_schema": {
            "type": "object",
            "properties": {
                **_TARGET_FIELDS,
                "value": {"type": "string", "description": "The option value/text to select."},
                **_RATIONALE_FIELD,
            },
            "required": [
                "target_description",
                "accessible_role",
                "accessible_name",
                "value",
                "rationale",
            ],
        },
    },
    {
        "name": "finish",
        "description": (
            "Declare that the goal has been achieved. This claim will be independently "
            "verified — it is not taken on trust."
        ),
        "input_schema": {
            "type": "object",
            "properties": {**_RATIONALE_FIELD},
            "required": ["rationale"],
        },
    },
    {
        "name": "give_up",
        "description": "Declare that the goal cannot be achieved and stop.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "One short sentence."}},
            "required": ["reason"],
        },
    },
]

TOOL_NAMES = frozenset(t["name"] for t in TOOLS)
