"""PR-202 contracts: repeated manifest-agent run requests are idempotent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_module


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(server_module, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_module, "_PROJECTS_ROOT_STR", str(projects.resolve()).lower())

    async def no_watch():
        return None

    monkeypatch.setattr(server_module, "_watch_projects", no_watch)
    with TestClient(server_module.create_app()) as test_client:
        yield test_client, projects


def test_repeated_run_requests_return_one_active_run(client, monkeypatch) -> None:
    test_client, projects = client
    created = test_client.post(
        "/api/project/create",
        json={"title": "Idempotent screen demo", "pipeline_type": "screen-demo"},
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]
    work_order_path = projects / project_id / "work_order.json"

    spawned: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: spawned.append((args, kwargs)),
    )

    first = test_client.post(f"/api/project/{project_id}/run?agent_id=agent-a")
    assert first.status_code == 200, first.text
    first_payload = first.json()
    first_order = first_payload["work_order"]
    assert first_payload["idempotent_replay"] is False
    assert first_order["claim"]["claimed_by"] == "agent-a"
    first_lease_version = first_order["claim"]["lease_version"]
    first_run_id = first_order["run_id"]

    # Same agent: claim renewal is idempotent and does not create a new run.
    second = test_client.post(f"/api/project/{project_id}/run?agent_id=agent-a")
    assert second.status_code == 200, second.text
    second_payload = second.json()
    second_order = second_payload["work_order"]
    assert second_payload["idempotent_replay"] is True
    assert second_order["run_id"] == first_run_id
    assert second_order["claim"]["lease_version"] == first_lease_version

    # Different caller: the existing live lease is returned read-only rather
    # than producing a conflict that tempts the caller to launch a duplicate.
    third = test_client.post(f"/api/project/{project_id}/run?agent_id=agent-b")
    assert third.status_code == 200, third.text
    third_payload = third.json()
    third_order = third_payload["work_order"]
    assert third_payload["idempotent_replay"] is True
    assert third_payload["agent_id"] == "agent-a"
    assert third_payload["requested_agent_id"] == "agent-b"
    assert third_order["run_id"] == first_run_id
    assert third_order["claim"]["claimed_by"] == "agent-a"
    assert third_order["claim"]["lease_version"] == first_lease_version

    persisted = json.loads(work_order_path.read_text(encoding="utf-8"))
    assert persisted["run_id"] == first_run_id
    assert persisted["claim"]["claimed_by"] == "agent-a"
    assert spawned == []
