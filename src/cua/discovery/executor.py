"""Executes one Claude-proposed action against the live page.

Never raises — always returns a structured ExecutionOutcome, mirroring
the same "harness never crashes on execution errors" pattern used by
cua.replay.executor. Reuses cua.replay.locators for target resolution
(same ambiguity rules, same "never guess" behavior) and
target_resolution for building candidate targets and recognizing
parameter values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cua.artifact.schema import LiteralRef, LocatorStrategy, ParamRef
from cua.replay.locators import (
    LocatorAmbiguousError,
    LocatorNotFoundError,
    SupportsLocatorProtocol,
    SupportsPageProtocol,
    resolve_target,
)

from . import target_resolution
from .resolved_locator import resolve_artifact_locator


@dataclass
class ExecutionOutcome:
    ok: bool
    resolved_locator_strategy: str | None = None
    resolved_locator: LocatorStrategy | None = None
    value_source: ParamRef | LiteralRef | None = None
    resulting_url: str | None = None
    error: str | None = None


_EDITABLE_TAGS = {"input", "textarea"}


def _tag_name(locator: SupportsLocatorProtocol) -> str:
    try:
        return (locator.evaluate("el => el.tagName.toLowerCase()") or "").strip()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - treat as unknown, not a crash
        return ""


def _is_contenteditable(locator: SupportsLocatorProtocol) -> bool:
    try:
        return bool(locator.evaluate("el => el.isContentEditable"))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - treat as not editable, not a crash
        return False


def _actionability_error(action: str, locator: SupportsLocatorProtocol) -> str | None:
    """A minimal semantic guard — not a reimplementation of Playwright's
    actionability engine — that rejects a resolved-but-wrong-kind-of-node
    (e.g. a <b> label) before it reaches fill()/select_option() and
    raises an opaque low-level Playwright exception instead of a clear,
    structured one.
    """
    if action == "type_text":
        tag = _tag_name(locator)
        if tag in _EDITABLE_TAGS or _is_contenteditable(locator):
            return None
        return f"resolved target is <{tag or 'unknown'}>, not an editable control (expected input/textarea/contenteditable)"
    if action == "select_option":
        tag = _tag_name(locator)
        if tag == "select":
            return None
        return f"resolved target is <{tag or 'unknown'}>, not a select/combobox control"
    return None


def execute_action(
    page: SupportsPageProtocol,
    action: str,
    args: dict[str, Any],
    *,
    base_url: str,
    declared_params: dict[str, str],
) -> ExecutionOutcome:
    try:
        if action == "navigate":
            url_path = args["url_path"]
            full_url = f"{base_url.rstrip('/')}{url_path}"
            page.goto(full_url, wait_until="networkidle", timeout=10_000)  # type: ignore[attr-defined]
            return ExecutionOutcome(ok=True, resulting_url=page.url)  # type: ignore[attr-defined]

        target = target_resolution.build_candidate_target(
            action=action,
            target_description=args.get("target_description", ""),
            accessible_role=args.get("accessible_role"),
            accessible_name=args.get("accessible_name"),
        )
        try:
            resolution = resolve_target(page, target)
        except (LocatorNotFoundError, LocatorAmbiguousError) as exc:
            return ExecutionOutcome(ok=False, error=str(exc))

        actionability_error = _actionability_error(action, resolution.locator)
        if actionability_error is not None:
            return ExecutionOutcome(ok=False, error=f"target '{target.description}': {actionability_error}")

        value_source = None
        if action == "click":
            resolution.locator.click(timeout=5_000)  # type: ignore[attr-defined]
        elif action == "type_text":
            value = args["value"]
            resolution.locator.fill(value, timeout=5_000)  # type: ignore[attr-defined]
            value_source = target_resolution.resolve_value_source(value, declared_params)
        elif action == "select_option":
            value = args["value"]
            resolution.locator.select_option(value, timeout=5_000)  # type: ignore[attr-defined]
            value_source = target_resolution.resolve_value_source(value, declared_params)
        else:
            return ExecutionOutcome(ok=False, error=f"unsupported action '{action}'")

        return ExecutionOutcome(
            ok=True,
            resolved_locator_strategy=resolution.strategy_description,
            resolved_locator=resolve_artifact_locator(resolution),
            value_source=value_source,
            resulting_url=page.url,  # type: ignore[attr-defined]
        )
    except Exception as exc:  # noqa: BLE001 - never let an execution error crash the run
        return ExecutionOutcome(ok=False, error=str(exc))
