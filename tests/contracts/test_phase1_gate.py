"""Independent PR-1G integration gate for the Phase 1 contract chain."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from lib.pipeline_loader import list_pipeline_catalog, load_pipeline_readonly


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", str(projects.resolve()).lower())

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as test_client:
        yield test_client, projects


def test_visible_catalog_is_truthful_before_pr11g() -> None:
    records = list_pipeline_catalog()
    assert records
    for record in records:
        assert record["ui_visible"] is True
        assert record["schema_valid"] is True
        assert record["maturity"] in {"test", "experimental", "beta", "production"}
        assert record["production_ready"] is False
        assert record["production_gate"] == "PR-11G"
        assert record["release_status"] != "production"
        # A visible held lane may be discoverable, but it cannot be created or
        # executed until its complete agent/runtime contract is certified.
        if record["creation_enabled"]:
            assert record["agent_contract_valid"] is True
        if not record["agent_contract_valid"]:
            assert record["creation_enabled"] is False


def test_certified_run_is_manifest_derived_and_never_spawns_legacy_runner(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, projects = client
    created = test_client.post(
        "/api/project/create",
        json={"title": "Phase 1 gate screen demo", "pipeline_type": "screen-demo"},
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    execution = test_client.get(f"/api/project/{project_id}/execution")
    assert execution.status_code == 200, execution.text
    context = execution.json()["execution"]
    manifest = load_pipeline_readonly("screen-demo")
    assert context["next_stage"] == manifest["stages"][0]["name"]
    assert context["director_skill"] == manifest["stages"][0]["skill"]
    assert context["manifest_hash"] == created.json()["work_order"]["manifest_hash"]

    spawned: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: spawned.append((args, kwargs)),
    )
    run = test_client.post(f"/api/project/{project_id}/run?agent_id=phase1-gate")
    assert run.status_code == 200, run.text
    payload = run.json()
    assert payload["execution_mode"] == "manifest_agent"
    assert payload["next_stage"] == manifest["stages"][0]["name"]
    assert payload["stage_skill"] == manifest["stages"][0]["skill"]
    assert payload["work_order"]["claim"]["claimed_by"] == "phase1-gate"
    assert spawned == []
    assert (projects / project_id / "work_order.json").is_file()
