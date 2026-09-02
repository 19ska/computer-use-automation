"""Loads and validates a CapabilityArtifact from a JSON file on disk.

Deliberately narrow: a flat allow-list of supported schema versions, no
migration/upgrade machinery. Rejects malformed input cleanly (never a raw
Pydantic/JSON traceback bubbling up to a caller) before any browser action
is ever considered.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .schema import CapabilityArtifact

SUPPORTED_SCHEMA_VERSIONS = {"1.0"}


class ArtifactLoadError(Exception):
    """Raised when an artifact file can't be read or doesn't validate."""


def load_artifact(path: str | Path) -> CapabilityArtifact:
    path = Path(path)

    try:
        raw_text = path.read_text()
    except FileNotFoundError as exc:
        raise ArtifactLoadError(f"artifact file not found: {path}") from exc
    except OSError as exc:
        raise ArtifactLoadError(f"could not read artifact file {path}: {exc}") from exc

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ArtifactLoadError(f"artifact at {path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ArtifactLoadError(f"artifact at {path} must be a JSON object, got {type(raw).__name__}")

    version = raw.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ArtifactLoadError(
            f"unsupported artifact schema_version {version!r} in {path}; "
            f"supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )

    try:
        return CapabilityArtifact.model_validate(raw)
    except ValidationError as exc:
        raise ArtifactLoadError(f"artifact at {path} failed schema validation:\n{exc}") from exc
