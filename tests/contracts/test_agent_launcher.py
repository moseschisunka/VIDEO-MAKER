"""Contracts for the Backlot to external-agent launch boundary."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from lib import agent_launcher
from lib.agent_launcher import (
    AgentLaunch,
    AgentLaunchError,
    configured_agent_command,
)


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


def test_missing_agent_command_does_not_claim_or_fake_a_run(client, monkeypatch) -> None:
    test_client, projects = client
    monkeypatch.delenv("OPENMONTAGE_AGENT_COMMAND", raising=False)
    created = test_client.post("/api/project/create", json={"title": "Needs an agent"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    response = test_client.post(f"/api/project/{project_id}/run")

    assert response.status_code == 503
    assert "OPENMONTAGE_AGENT_COMMAND" in response.json()["detail"]
    order = json.loads((projects / project_id / "work_order.json").read_text(encoding="utf-8"))
    assert order["status"] == "queued"
    assert order["claim"]["claimed_by"] is None
    assert order["stages"][0]["status"] == "ready"


def test_configured_agent_is_launched_and_receives_run_identity(client, monkeypatch) -> None:
    test_client, projects = client
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m my_agent")
    monkeypatch.setenv("OPENMONTAGE_AGENT_ID", "configured-agent")
    created = test_client.post("/api/project/create", json={"title": "Launch an agent"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    captured = {}

    def fake_launch(project_dir, order, *, agent_id, backlot_url):
        captured.update(
            {
                "project_dir": project_dir,
                "order": order,
                "agent_id": agent_id,
                "backlot_url": backlot_url,
            }
        )
        return AgentLaunch(
            pid=4312,
            agent_id=agent_id,
            run_id=str(order["run_id"]),
            started_at="2026-09-05T00:00:00+00:00",
            log_path="agent.log",
            command=("python", "-m", "my_agent"),
            cwd=str(projects),
        )

    monkeypatch.setattr(server_mod, "launch_agent", fake_launch)
    response = test_client.post(f"/api/project/{project_id}/run")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["execution_mode"] == "external_agent"
    assert payload["agent_id"] == "configured-agent"
    assert payload["agent_launch"]["status"] == "started"
    assert payload["agent_launch"]["pid"] == 4312
    assert captured["agent_id"] == "configured-agent"
    assert captured["order"]["run_id"] == created.json()["work_order"]["run_id"]
    assert captured["backlot_url"].startswith("http://")


def test_launch_failure_releases_the_claim_for_retry(client, monkeypatch) -> None:
    test_client, projects = client
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m missing_agent")
    created = test_client.post("/api/project/create", json={"title": "Retry launch"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    def fail_launch(*args, **kwargs):
        raise AgentLaunchError("executable not found")

    monkeypatch.setattr(server_mod, "launch_agent", fail_launch)
    response = test_client.post(f"/api/project/{project_id}/run")

    assert response.status_code == 503
    order = json.loads((projects / project_id / "work_order.json").read_text(encoding="utf-8"))
    assert order["status"] == "queued"
    assert order["claim"]["claimed_by"] is None
    assert order["stages"][0]["status"] == "ready"


def test_agent_command_is_parsed_without_shell() -> None:
    # The parser returns an argv tuple; launch_agent passes that vector to
    # Popen with shell=False, so a separator remains data rather than a second
    # command.
    import os

    previous = os.environ.get("OPENMONTAGE_AGENT_COMMAND")
    os.environ["OPENMONTAGE_AGENT_COMMAND"] = 'python -m "my agent"'
    try:
        assert configured_agent_command() == ("python", "-m", "my agent")
    finally:
        if previous is None:
            os.environ.pop("OPENMONTAGE_AGENT_COMMAND", None)
        else:
            os.environ["OPENMONTAGE_AGENT_COMMAND"] = previous


def test_launcher_uses_shell_free_argv_and_persists_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m my_agent")
    captured = {}

    class FakeProcess:
        pid = 7711

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(agent_launcher.subprocess, "Popen", fake_popen)
    order = {
        "project_id": "demo",
        "run_id": "8f6b4b7b-2c1f-4c1d-9d3e-7d6b6f6c8e1a",
        "next_stage": "idea",
    }
    launch = agent_launcher.launch_agent(tmp_path, order, agent_id="agent-a", backlot_url="http://127.0.0.1:4750")

    assert captured["argv"] == ["python", "-m", "my_agent"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] is agent_launcher.subprocess.DEVNULL
    assert captured["kwargs"]["env"]["OPENMONTAGE_PROJECT_ID"] == "demo"
    assert captured["kwargs"]["env"]["OPENMONTAGE_RUN_ID"] == order["run_id"]
    assert "OPENMONTAGE_AGENT_PROMPT" in captured["kwargs"]["env"]
    assert "begin at stage 'idea'" in captured["kwargs"]["env"]["OPENMONTAGE_AGENT_PROMPT"]
    assert launch.pid == 7711
    record = json.loads((tmp_path / "agent_process.json").read_text(encoding="utf-8"))
    assert record["status"] == "started"
    assert record["command"] == ["python", "-m", "my_agent"]


def test_launcher_can_start_a_real_short_lived_process(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "agent-marker.json"
    script = tmp_path / "agent_worker.py"
    script.write_text(
        "import json, os, pathlib; "
        "pathlib.Path(os.environ['OPENMONTAGE_AGENT_MARKER']).write_text(json.dumps({"
        "'project': os.environ['OPENMONTAGE_PROJECT_ID'], "
        "'run': os.environ['OPENMONTAGE_RUN_ID'], "
        "'stage': os.environ['OPENMONTAGE_STAGE']}), encoding='utf-8')",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "OPENMONTAGE_AGENT_COMMAND",
        f'"{sys.executable}" "{script}"',
    )
    monkeypatch.setenv("OPENMONTAGE_AGENT_MARKER", str(marker))
    order = {
        "project_id": "real-process",
        "run_id": "8f6b4b7b-2c1f-4c1d-9d3e-7d6b6f6c8e1a",
        "next_stage": "idea",
    }

    launch = agent_launcher.launch_agent(tmp_path, order, agent_id="agent-real")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.02)

    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload == {
        "project": "real-process",
        "run": order["run_id"],
        "stage": "idea",
    }
    assert launch.pid > 0


def test_explicit_agent_id_keeps_manifest_handoff(client, monkeypatch) -> None:
    test_client, _projects = client
    monkeypatch.delenv("OPENMONTAGE_AGENT_COMMAND", raising=False)
    created = test_client.post("/api/project/create", json={"title": "Explicit handoff"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    response = test_client.post(f"/api/project/{project_id}/run?agent_id=coding-agent")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["execution_mode"] == "manifest_agent"
    assert payload["agent_launch"]["status"] == "handoff"
    assert payload["agent_id"] == "coding-agent"
