"""Tests for cua.artifact.loader — the artifact loading contract."""

import json
from pathlib import Path

import pytest

from cua.artifact.loader import ArtifactLoadError, load_artifact

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "examples"
    / "capabilities"
    / "parabank_transfer_funds.json"
)


def test_load_valid_fixture():
    artifact = load_artifact(FIXTURE_PATH)
    assert artifact.capability_id == "parabank.transfer_funds"


def test_load_rejects_missing_file(tmp_path):
    with pytest.raises(ArtifactLoadError, match="not found"):
        load_artifact(tmp_path / "does_not_exist.json")


def test_load_rejects_malformed_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{ this is not valid json")
    with pytest.raises(ArtifactLoadError, match="not valid JSON"):
        load_artifact(path)


def test_load_rejects_unsupported_schema_version(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": "9.9"}))
    with pytest.raises(ArtifactLoadError, match="unsupported artifact schema_version"):
        load_artifact(path)


def test_load_rejects_schema_validation_errors(tmp_path):
    path = tmp_path / "incomplete.json"
    # Valid schema_version, but missing every other required field.
    path.write_text(json.dumps({"schema_version": "1.0"}))
    with pytest.raises(ArtifactLoadError, match="failed schema validation"):
        load_artifact(path)
