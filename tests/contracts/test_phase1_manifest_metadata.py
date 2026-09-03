"""PR-100 manifest release-metadata contracts."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from lib.pipeline_loader import get_manifest_release_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "pipelines" / "pipeline_manifest.schema.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_metadata_fields_are_schema_valid_and_strictly_typed() -> None:
    manifest = {
        "name": "metadata-fixture",
        "version": "1.0",
        "ui_visible": False,
        "maturity": "experimental",
        "supported_runtimes": ["remotion"],
        "supported_profiles": ["vertical-9x16"],
        "required_capabilities": ["video_compose"],
        "required_artifacts": ["script"],
        "deprecated": False,
        "stages": [{"name": "idea"}],
    }
    jsonschema.validate(manifest, _schema())
    release = get_manifest_release_metadata(manifest)
    assert release["ui_visible"] is False
    assert release["maturity"] == "experimental"
    assert release["supported_runtimes"] == ["remotion"]
    assert release["supported_profiles"] == ["vertical-9x16"]
    assert release["required_capabilities"] == ["video_compose"]
    assert release["required_artifacts"] == ["script"]


def test_missing_metadata_defaults_cannot_look_production_ready() -> None:
    manifest = {
        "name": "legacy-fixture",
        "version": "1.0",
        "stability": "production",
        "stages": [{"name": "idea"}],
    }
    jsonschema.validate(manifest, _schema())
    release = get_manifest_release_metadata(manifest)
    assert release["ui_visible"] is False
    assert release["maturity"] == "experimental"
    assert release["supported_runtimes"] == []
    assert release["supported_profiles"] == []
    assert release["required_capabilities"] == []
    assert release["required_artifacts"] == []
    assert release["deprecated"] is False


def test_invalid_maturity_is_conservatively_downgraded_by_effective_loader() -> None:
    manifest = {"maturity": "production-ish", "ui_visible": True}
    release = get_manifest_release_metadata(manifest)
    assert release["maturity"] == "experimental"
    # Effective defaults are independent of a caller's accidental visibility
    # flag; catalog/release scope still controls whether this is user-visible.
    assert release["ui_visible"] is True
