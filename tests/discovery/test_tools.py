"""Tests for cua.discovery.tools — the provider-neutral tool schema.

Response-shape validation (exactly one actionable call, etc.) now lives
in cua.discovery.llm.base and is tested in test_llm_base.py, since that
logic is provider-neutral and no longer tied to any one SDK's response
shape.
"""

from cua.discovery.tools import TOOL_NAMES, TOOLS


def test_all_six_logical_tools_are_declared():
    assert TOOL_NAMES == {"navigate", "click", "type_text", "select_option", "finish", "give_up"}


def test_every_tool_has_a_name_description_and_input_schema():
    for tool in TOOLS:
        assert tool["name"]
        assert tool["description"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema


def test_action_tools_require_a_rationale():
    for tool in TOOLS:
        if tool["name"] == "give_up":
            continue  # give_up uses "reason" instead
        assert "rationale" in tool["input_schema"]["required"]


def test_give_up_requires_a_reason():
    give_up = next(t for t in TOOLS if t["name"] == "give_up")
    assert "reason" in give_up["input_schema"]["required"]
