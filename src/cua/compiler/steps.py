"""Maps one successfully-executed DiscoveryEvent into one artifact
ActionStep — pure, deterministic, and dependent only on the fields
already recorded in evidence. Never calls
cua.discovery.target_resolution.build_candidate_target(): the locator
that succeeded is consumed directly from `resolved_locator`, not
re-derived.
"""

from __future__ import annotations

from cua.artifact.schema import (
    ActionStep,
    ActionType,
    CssLocator,
    ElementTarget,
    LabelTextLocator,
    LiteralRef,
    LocatorStrategy,
    ParamRef,
    RoleLocator,
    ValueRef,
)

from .events import CompilationError, DiscoveryEvent

# discovery action name -> artifact ActionType. "finish"/"give_up" are
# model decisions, never executed browser actions, and have no mapping.
_ACTION_MAP: dict[str, ActionType] = {
    "navigate": "navigate",
    "click": "click",
    "type_text": "type",
    "select_option": "select_option",
}

_LOCATOR_KINDS = {"role": RoleLocator, "label_text": LabelTextLocator, "css": CssLocator}


def _describe(event: DiscoveryEvent) -> str:
    return f"step_number={event.step_number} action={event.action!r}"


def _compile_locator(event: DiscoveryEvent) -> LocatorStrategy:
    raw = event.resolved_locator
    if raw is None:
        raise CompilationError(
            f"{_describe(event)}: successfully executed action has no resolved_locator — "
            "cannot compile a target for it. This run's evidence is not compile-ready."
        )

    kind = raw.get("kind")
    model_cls = _LOCATOR_KINDS.get(kind)
    if model_cls is None:
        raise CompilationError(f"{_describe(event)}: unsupported resolved_locator kind {kind!r}")

    strategy = model_cls.model_validate(raw)

    # Defense in depth: resolved_locator is only ever populated by
    # cua.discovery.resolved_locator.resolve_artifact_locator(), which
    # cannot produce an xpath= CssLocator — but the compiler must never
    # trust that invariant blindly when reading a JSON file from disk.
    if isinstance(strategy, CssLocator) and strategy.selector.startswith("xpath="):
        raise CompilationError(
            f"{_describe(event)}: resolved_locator is an internal XPath selector, not a valid CSS "
            "selector — refusing to persist it as kind='css' in the artifact"
        )

    return strategy


def _compile_value(event: DiscoveryEvent, *, known_params: frozenset[str]) -> ValueRef:
    raw = event.value_source
    if raw is None:
        raise CompilationError(f"{_describe(event)}: missing value_source for a '{event.action}' action")

    kind = raw.get("kind")
    if kind == "param":
        ref = ParamRef.model_validate(raw)
        if ref.name not in known_params:
            raise CompilationError(
                f"{_describe(event)}: value_source references unknown input parameter '{ref.name}'"
            )
        return ref
    if kind == "literal":
        return LiteralRef.model_validate(raw)

    raise CompilationError(f"{_describe(event)}: unsupported value_source kind {kind!r}")


def compile_step(event: DiscoveryEvent, step_id: int, *, known_params: frozenset[str]) -> ActionStep:
    artifact_action = _ACTION_MAP.get(event.action)
    if artifact_action is None:
        raise CompilationError(f"{_describe(event)}: discovery action has no artifact action mapping")

    if artifact_action == "navigate":
        if not event.url_path:
            raise CompilationError(
                f"{_describe(event)}: missing url_path for a successfully executed navigate action"
            )
        return ActionStep(step_id=step_id, action="navigate", url=event.url_path)

    if not event.target_description:
        raise CompilationError(f"{_describe(event)}: missing target_description")

    target = ElementTarget(
        description=event.target_description,
        strategies=[_compile_locator(event)],
    )

    if artifact_action == "click":
        return ActionStep(step_id=step_id, action="click", target=target)

    # type / select_option
    value = _compile_value(event, known_params=known_params)
    return ActionStep(step_id=step_id, action=artifact_action, target=target, value=value)
