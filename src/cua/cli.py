"""Minimal CLI for deterministic replay and LLM-driven discovery."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from .discovery.engine import run_discovery
from .replay.engine import run_replay

DEFAULT_BASE_URL = "https://parabank.parasoft.com/parabank"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cua")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay_parser = subparsers.add_parser(
        "replay", help="Deterministically replay a capability artifact (zero LLM calls)"
    )
    replay_parser.add_argument("--artifact", required=True, type=Path)
    replay_parser.add_argument("--amount", required=True)
    replay_parser.add_argument("--from-account-id", required=True, dest="from_account_id")
    replay_parser.add_argument("--to-account-id", required=True, dest="to_account_id")
    replay_parser.add_argument(
        "--headless", action="store_true", default=False, help="Run without a visible browser"
    )

    discover_parser = subparsers.add_parser(
        "discover", help="Run a genuine LLM-driven discovery loop (uses the Gemini API)"
    )
    discover_parser.add_argument("--goal", required=True)
    discover_parser.add_argument("--amount", required=True)
    discover_parser.add_argument("--from-account-id", required=True, dest="from_account_id")
    discover_parser.add_argument("--to-account-id", required=True, dest="to_account_id")
    discover_parser.add_argument(
        "--headless", action="store_true", default=False, help="Run without a visible browser"
    )
    discover_parser.add_argument("--max-steps", type=int, default=15, dest="max_steps")

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    if args.command == "replay":
        raw_inputs = {
            "amount": args.amount,
            "from_account_id": args.from_account_id,
            "to_account_id": args.to_account_id,
        }
        result = run_replay(args.artifact, raw_inputs, headless=args.headless)
        print(result.model_dump_json(indent=2))
        return 0 if result.status != "failure" else 1

    if args.command == "discover":
        declared_params = {
            "amount": args.amount,
            "from_account_id": args.from_account_id,
            "to_account_id": args.to_account_id,
        }
        base_url = os.environ.get("PARABANK_BASE_URL", DEFAULT_BASE_URL)
        result = run_discovery(
            args.goal,
            declared_params,
            base_url=base_url,
            headless=args.headless,
            max_steps=args.max_steps,
        )
        print(result.model_dump_json(indent=2))
        return 0 if result.status != "failure" else 1

    return 1  # pragma: no cover - argparse enforces a valid subcommand
