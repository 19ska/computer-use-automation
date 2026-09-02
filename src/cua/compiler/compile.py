"""Top-level orchestration for compiling a successful discovery run into
a CapabilityArtifact — zero LLM calls, fully deterministic given the same
evidence + template.

events.jsonl -> parse + filter to the winning executed-action path ->
map action vocabulary -> consume resolved_locator/value_source directly
(never reconstructed) -> combine with a CompilationTemplate's static
capability knowledge -> CapabilityArtifact -> Pydantic validation -> JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from cua.artifact.schema import ActionStep, CapabilityArtifact

from .events import CompilationError, assert_run_succeeded, load_events, winning_path
from .steps import compile_step
from .templates import CompilationTemplate, get_template

DEFAULT_OUTPUT_ROOT = Path("generated_capabilities")


@dataclass(frozen=True)
class CompileResult:
    output_path: Path
    artifact: CapabilityArtifact
    discovered_step_count: int


def compile_artifact(run_dir: Path, template: CompilationTemplate) -> tuple[CapabilityArtifact, int]:
    """Returns (artifact, discovered_step_count). Raises CompilationError
    for every fail-closed condition; never returns a partially-built
    artifact.
    """
    events = load_events(run_dir)
    assert_run_succeeded(events)

    winning = winning_path(events)
    if not winning:
        raise CompilationError(f"{run_dir}: no successfully executed UI actions to compile")

    known_params = frozenset(i.name for i in template.inputs)
    discovered_steps: list[ActionStep] = []
    for step_id, event in enumerate(winning, start=1):
        step = compile_step(event, step_id, known_params=known_params)
        # Risk marking is capability-specific knowledge (which discovered
        # click is the state-changing action) — it lives entirely in the
        # template's risky_click_accessible_names; this generic loop only
        # ever consults that set, never a hardcoded label.
        if step.action == "click" and event.accessible_name in template.risky_click_accessible_names:
            step = step.model_copy(update={"risk": "risky"})
        discovered_steps.append(step)

    next_step_id = len(discovered_steps) + 1
    trailing_steps: list[ActionStep] = []
    for step in template.trailing_steps:
        trailing_steps.append(step.model_copy(update={"step_id": next_step_id}))
        next_step_id += 1

    provider = next((e.provider for e in winning if e.provider), None)
    model = next((e.model for e in winning if e.model), None)
    notes = f"Compiled from discovery run {run_dir.name} (provider={provider}, model={model})."

    try:
        artifact = CapabilityArtifact(
            capability_id=template.capability_id,
            capability_version=template.capability_version,
            display_name=template.display_name,
            description=template.description,
            target_app=template.target_app,
            vendor_product=template.vendor_product,
            session_requirement=template.session_requirement,
            inputs=template.inputs,
            outputs=template.outputs,
            steps=[*discovered_steps, *trailing_steps],
            success_checkpoint=template.success_checkpoint,
            business_outcomes=template.business_outcomes,
            policy=template.policy,
            created_at=datetime.now(timezone.utc),
            created_by="discovery_agent",
            discovery_run_id=run_dir.name,
            notes=notes,
        )
    except ValidationError as exc:
        raise CompilationError(f"generated artifact failed schema validation: {exc}") from exc

    return artifact, len(discovered_steps)


def compile_and_write(
    run_dir: Path, capability_id: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> CompileResult:
    template = get_template(capability_id)
    artifact, discovered_step_count = compile_artifact(run_dir, template)

    # Nothing is written to disk unless compilation fully succeeded above.
    output_dir = output_root / template.capability_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"v{template.capability_version}.json"
    output_path.write_text(artifact.model_dump_json(indent=2) + "\n")

    return CompileResult(output_path=output_path, artifact=artifact, discovered_step_count=discovered_step_count)
