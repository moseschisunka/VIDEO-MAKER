"""PR-105 contracts for the quarantined legacy Studio/demo runner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from lib.demo_runner import is_internal_demo_project
from lib.project_pipeline import run_project


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


def _marker(project: Path, *, pipeline_type: str = "animated-explainer") -> None:
    project.mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps({
            "project_id": project.name,
            "title": "Internal demo fixture",
            "pipeline_type": pipeline_type,
            "runner_kind": "internal_demo",
            "demo_runner": True,
        }),
        encoding="utf-8",
    )


def test_regular_project_run_uses_manifest_agent_without_spawning_demo_runner(client, monkeypatch) -> None:
    test_client, projects = client
    created = test_client.post("/api/project/create", json={"title": "A real screen demo"})
    assert created.status_code == 200

    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: spawned.append((args, kwargs)))
    response = test_client.post(f"/api/project/{created.json()['project_id']}/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_mode"] == "manifest_agent"
    assert payload["next_stage"] == "idea"
    assert payload["stage_skill"] == "pipelines/screen-demo/idea-director"
    assert spawned == []
    assert (projects / created.json()["project_id"] / "work_order.json").is_file()


def test_only_explicit_internal_demo_marker_can_launch_legacy_runner(client, monkeypatch) -> None:
    test_client, projects = client
    project = projects / "demo-fixture"
    _marker(project)
    assert is_internal_demo_project(project)

    class FakeProcess:
        pid = 41001
        returncode = 0

        def wait(self):
            return self.returncode

    launched = {}

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    response = test_client.post("/api/project/demo-fixture/run")

    assert response.status_code == 200
    assert launched["command"][-1:] == ["--internal-demo"]


def test_project_pipeline_function_requires_explicit_internal_demo_flag(tmp_path: Path, monkeypatch) -> None:
    # The function-level guard protects callers that bypass Backlot entirely.
    from lib import project_pipeline

    monkeypatch.setattr(project_pipeline, "PROJECTS_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="quarantined"):
        run_project("ordinary-project")

    project = tmp_path / "demo-fixture"
    _marker(project)
    with pytest.raises(RuntimeError, match="quarantined"):
        # The explicit flag is still required; this assertion prevents a
        # marker alone from becoming an accidental production escape hatch.
        run_project("demo-fixture")


def test_generated_project_launcher_is_a_quarantine_stub() -> None:
    template = server_mod._PROJECT_RUNNER_TEMPLATE
    assert "lib.project_pipeline" not in template
    assert "quarantined" in template.lower()
