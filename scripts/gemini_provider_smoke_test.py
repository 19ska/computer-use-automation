"""Manual diagnostic utility for cua.discovery.llm.gemini_provider.

NOT an automated test — it makes REAL requests to the Gemini API using your
real GEMINI_API_KEY (from .env) and costs real tokens. It is intentionally
excluded from pytest collection (it lives outside tests/ and is not named
test_*.py).

Purpose: isolate whether "GEMINI_API_KEY + GEMINI_MODEL + our
FunctionDeclaration schemas + tool_config + response parsing" work together,
with zero browser and zero discovery-engine involvement. If this script
fails, the bug is in the provider/request construction, not in ParaBank or
the discovery loop.

Never logs the API key. Only prints model, phase, and safe response summary.

Usage:
    python scripts/gemini_provider_smoke_test.py            # full six-tool test
    python scripts/gemini_provider_smoke_test.py --minimal  # one-tool sanity check first
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from cua.discovery.llm import LLMActionCall, LLMDecisionError  # noqa: E402
from cua.discovery.llm.gemini_provider import GeminiProvider  # noqa: E402
from cua.discovery.tools import TOOLS  # noqa: E402

_MINIMAL_TOOL = {
    "name": "finish",
    "description": "Declare that the goal has been achieved.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {"type": "string", "description": "One short sentence."},
        },
        "required": ["rationale"],
    },
}


def _run_minimal(model: str) -> None:
    print(f"--- minimal single-tool smoke test (model={model}) ---")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is not set. Aborting.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    declaration = types.FunctionDeclaration(
        name=_MINIMAL_TOOL["name"],
        description=_MINIMAL_TOOL["description"],
        parameters_json_schema=_MINIMAL_TOOL["input_schema"],
    )
    tool = types.Tool(function_declarations=[declaration])

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Call the finish tool now.")],
                )
            ],
            config=types.GenerateContentConfig(
                tools=[tool],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.ANY
                    )
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic script, show everything about the error
        print(f"FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)

    calls = response.function_calls or []
    print(f"OK — got {len(calls)} function call(s): {[c.name for c in calls]}")


def _run_full(model: str) -> None:
    print(f"--- full six-tool GeminiProvider smoke test (model={model}) ---")
    print(f"Tools declared: {[t['name'] for t in TOOLS]}")

    try:
        provider = GeminiProvider(model, system_prompt="You are a test harness. Call the finish tool.")
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to construct GeminiProvider: {type(exc).__name__}: {exc}")
        sys.exit(1)

    provider.start(
        goal="Call the finish tool to prove the provider works.",
        declared_params={},
        observation_text="(no real page — this is a provider-only smoke test)",
    )

    try:
        decision = provider.propose_action()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED on propose_action(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    if isinstance(decision, LLMDecisionError):
        print(f"Provider returned a structured decision error (not a crash): {decision.reason}")
        return

    assert isinstance(decision, LLMActionCall)
    print(f"OK — model proposed: {decision.name}({decision.args})")

    provider.record_tool_result(result_text="Confirmed: goal achieved.", is_error=False)
    print("record_tool_result() completed without error.")

    try:
        decision2 = provider.propose_action()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED on second propose_action() (tool-result-turn threading): {type(exc).__name__}: {exc}")
        sys.exit(1)

    if isinstance(decision2, LLMDecisionError):
        print(f"Second turn: structured decision error: {decision2.reason}")
    else:
        assert isinstance(decision2, LLMActionCall)
        print(f"OK — second turn model proposed: {decision2.name}({decision2.args})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimal", action="store_true", help="Run only the one-tool sanity check.")
    args = parser.parse_args()

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    if args.minimal:
        _run_minimal(model)
    else:
        _run_minimal(model)
        print()
        _run_full(model)
