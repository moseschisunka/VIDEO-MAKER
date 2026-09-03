"""Phase 0 release-labeling contracts.

These checks are intentionally offline.  They prove that discovery and project
state carry a non-production release decision; they do not certify a pipeline
or contact a provider.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot.server import _load_pipelines_data
from lib.pipeline_release import pipeline_release_metadata, studio_release_status


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "pipeline_release_scope.json"
PIPELINE_DIR = PROJECT_ROOT / "pipeline_defs"


def _scope() -> dict:
    return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))


def test_every_manifest_has_an_explicit_reviewed_release_lane() -> None:
    scope = _scope()
    manifest_ids = {path.stem for path in PIPELINE_DIR.glob("*.yaml")}
    assert set(scope["pipelines"]) == manifest_ids
    assert {entry["lane"] for entry in scope["pipelines"].values()} <= {
        "launch", "beta", "experimental", "test"
    }
    assert scope["decision_id"] == "PR-002-SCOPE-2026-09-02"


def test_global_release_status_is_unambiguously_non_production() -> None:
    status = studio_release_status()
    assert status["status"] == "internal_preview"
    assert status["production_ready"] is False
    assert status["production_gate"] == "PR-11G"
    assert "pending" in status["label"].lower()


def test_user_catalog_hides_test_and_keeps_held_lanes_non_creatable() -> None:
    records = _load_pipelines_data()
    ids = {record["id"] for record in records}
    assert "framework-smoke" not in ids
    # PR-101 repaired documentary-montage's category contract. It is now
    # discoverable as an explicitly held experimental lane, but remains
    # non-creatable until its corpus/retrieval gates pass.
    assert "documentary-montage" in ids
    assert next(record for record in records if record["id"] == "documentary-montage")["creation_enabled"] is False
    assert {"screen-demo", "talking-head"} <= ids
    assert all(record["production_ready"] is False for record in records)
    assert all(record["release_status"] == "not_certified" for record in records)
    assert all(record["production_gate"] == "PR-11G" for record in records)


def test_only_approved_launch_candidates_are_creatable() -> None:
    records = _load_pipelines_data()
    by_id = {record["id"]: record for record in records}
    assert by_id["screen-demo"]["creation_enabled"] is True
    assert by_id["talking-head"]["creation_enabled"] is True
    assert all(
        record["creation_enabled"] is False
        for record in records
        if record["release_lane"] != "launch"
    )


def test_bad_or_unclassified_pipeline_fails_closed() -> None:
    metadata = pipeline_release_metadata("not-a-real-pipeline", manifest=None, schema_valid=False)
    assert metadata["ui_visible"] is False
    assert metadata["creation_enabled"] is False
    assert metadata["production_ready"] is False
    assert metadata["release_status"] == "not_certified"


def test_create_api_enforces_scope_before_writing_project_state(tmp_path, monkeypatch) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", str(projects.resolve()).lower())

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as client:
        held = client.post(
            "/api/project/create",
            json={"title": "Held", "pipeline_type": "animated-explainer"},
        )
        assert held.status_code == 400
        assert list(projects.iterdir()) == []

        created = client.post(
            "/api/project/create",
            json={"title": "Screen walkthrough", "pipeline_type": "screen-demo"},
        )
        assert created.status_code == 200
        body = created.json()
        assert body["production_ready"] is False
        assert body["production_gate"] == "PR-11G"
        project = projects / body["project_id"]
        marker = json.loads((project / "project.json").read_text(encoding="utf-8"))
        assert marker["pipeline_type"] == "screen-demo"
        assert marker["production_ready"] is False
