"""Manual diagnostic utility for cua.discovery.llm.groq_provider.

NOT an automated test — it makes REAL requests to the Groq API using your
real GROQ_API_KEY (from .env) and costs real tokens. It is intentionally
excluded from pytest collection (it lives outside tests/ and is not named
test_*.py).

Purpose: isolate whether "GROQ_API_KEY + GROQ_MODEL + our tool schemas +
tool_choice + response parsing" work together, with zero browser and zero
discovery-engine involvement. If this script fails, the bug is in the
provider/request construction, not in ParaBank or the discovery loop.

Never logs the API key. Only prints model, phase, and safe response summary.

Usage:
    python scripts/groq_provider_smoke_test.py            # full six-tool test
    python scripts/groq_provider_smoke_test.py --minimal  # one-tool sanity check first
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import groq  # noqa: E402

from cua.discovery.llm import LLMActionCall, LLMDecisionError  # noqa: E402
from cua.discovery.llm.groq_provider import GroqProvider  # noqa: E402
from cua.discovery.tools import TOOLS  # noqa: E402

_MINIMAL_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Declare that the goal has been achieved.",
        "parameters": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string", "description": "One short sentence."},
            },
            "required": ["rationale"],
        },
    },
}


def _run_minimal(model: str) -> None:
    print(f"--- minimal single-tool smoke test (model={model}) ---")
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("GROQ_API_KEY is not set. Aborting.")
        sys.exit(1)

    client = groq.Groq(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Call the finish tool now."}],
            tools=[_MINIMAL_TOOL],
            tool_choice="auto",
            parallel_tool_calls=False,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic script, show everything about the error
        print(f"FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)

    calls = response.choices[0].message.tool_calls or []
    print(f"OK — got {len(calls)} tool call(s): {[c.function.name for c in calls]}")


def _run_full(model: str) -> None:
    print(f"--- full six-tool GroqProvider smoke test (model={model}) ---")
    print(f"Tools declared: {[t['name'] for t in TOOLS]}")

    try:
        provider = GroqProvider(model, system_prompt="You are a test harness. Call the finish tool.")
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to construct GroqProvider: {type(exc).__name__}: {exc}")
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

    # The second turn's PURPOSE here is only to prove the tool-result
    # history/threading is accepted by Groq without a 400 — not to force
    # another tool call. With tool_choice="auto" (not "required"), the
    # model may legitimately respond with plain text acknowledging the
    # completed action instead of calling another tool; that is a
    # successful threading check, not a broken provider. A text-only
    # response surfaces here as an LLMDecisionError (zero actionable
    # calls) — in the real discovery engine that becomes a bounded
    # "invalid_model_response" correction turn, since discovery always
    # requires an action, but this script's job stops at proving no 400.
    try:
        decision2 = provider.propose_action()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED on second propose_action() (tool-result-turn threading): {type(exc).__name__}: {exc}")
        sys.exit(1)

    if isinstance(decision2, LLMDecisionError):
        print(
            "OK — second request succeeded with no 400 (tool-result threading accepted); "
            f"model responded with text instead of another tool call: {decision2.reason}"
        )
    else:
        assert isinstance(decision2, LLMActionCall)
        print(f"OK — second turn model proposed another tool call: {decision2.name}({decision2.args})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimal", action="store_true", help="Run only the one-tool sanity check.")
    args = parser.parse_args()

    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

    if args.minimal:
        _run_minimal(model)
    else:
        _run_minimal(model)
        print()
        _run_full(model)
