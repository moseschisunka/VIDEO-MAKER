"""Contracts for the Backlot to external-agent launch boundary."""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from lib import agent_launcher
from lib import local_director
from lib.agent_launcher import (
    AgentLaunch,
    AgentLaunchError,
    _write_workspace_mcp_config,
    configured_agent_command,
)
from lib.local_director_capabilities import issue_local_director_capability
from lib.local_director_capabilities import validate_local_director_capability


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


def test_claude_subscription_cli_does_not_claim_a_run(client, monkeypatch) -> None:
    test_client, projects = client
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m lib.local_director claude")
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda name: "C:/tools/claude.exe" if name in {"claude", "claude.exe"} else None,
    )
    created = test_client.post("/api/project/create", json={"title": "Local director setup"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    response = test_client.post(f"/api/project/{project_id}/run")

    assert response.status_code == 503
    assert "interactive" in response.json()["detail"].lower()
    assert "subscription" in response.json()["detail"].lower()
    order = json.loads((projects / project_id / "work_order.json").read_text(encoding="utf-8"))
    assert order["status"] == "queued"
    assert order["claim"]["claimed_by"] is None
    assert order["stages"][0]["status"] == "ready"


def test_local_directors_endpoint_reports_choices_without_credentials(client, monkeypatch) -> None:
    test_client, _projects = client
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m my_agent")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-returned")
    monkeypatch.setattr(
        server_mod,
        "local_director_catalog",
        lambda: [
            {"id": "codex", "label": "Codex", "installed": True, "ready": True},
            {
                "id": "claude",
                "label": "Claude Code",
                "installed": True,
                "ready": False,
                "mode": "interactive_only",
            },
        ],
    )

    response = test_client.get("/api/local-directors")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured_runner"] is True
    assert [item["id"] for item in payload["directors"]] == ["codex", "claude"]
    assert "must-not-be-returned" not in response.text


def test_configured_agent_is_launched_and_receives_run_identity(client, monkeypatch) -> None:
    test_client, projects = client
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m my_agent")
    monkeypatch.setenv("OPENMONTAGE_AGENT_ID", "configured-agent")
    created = test_client.post("/api/project/create", json={"title": "Launch an agent"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    captured = {}
    launches = []

    def fake_launch(project_dir, order, *, agent_id, backlot_url, execution_context=None):
        launches.append(str(order["run_id"]))
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

    replay = test_client.post(f"/api/project/{project_id}/run")

    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["work_order"]["claim"] == payload["work_order"]["claim"]
    assert replay.json()["agent_launch"]["status"] == "already_running"
    assert launches == [created.json()["work_order"]["run_id"]]


def test_selected_local_director_launches_without_command_env(client, monkeypatch) -> None:
    test_client, projects = client
    monkeypatch.delenv("OPENMONTAGE_AGENT_COMMAND", raising=False)
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda command: "C:/tools/agy.exe" if command in {"agy.exe", "agy"} else None,
    )
    created = test_client.post("/api/project/create", json={"title": "Choose a director"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]
    captured = {}

    def fake_launch(
        project_dir, order, *, agent_id, backlot_url, director=None, execution_context=None
    ):
        captured.update(
            project_dir=project_dir,
            agent_id=agent_id,
            backlot_url=backlot_url,
            director=director,
            run_id=order["run_id"],
        )
        return AgentLaunch(
            pid=8831,
            agent_id=agent_id,
            run_id=str(order["run_id"]),
            started_at="2026-09-13T00:00:00+00:00",
            log_path="agent.log",
            command=(sys.executable, "-m", "lib.local_director", "antigravity"),
            cwd=str(projects / project_id),
        )

    monkeypatch.setattr(server_mod, "launch_agent", fake_launch)
    response = test_client.post(f"/api/project/{project_id}/run?director=antigravity")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["agent_id"] == "openmontage-antigravity"
    assert payload["agent_launch"]["status"] == "started"
    assert captured["director"] == "antigravity"
    assert captured["agent_id"] == "openmontage-antigravity"
    assert captured["run_id"] == created.json()["work_order"]["run_id"]
    order = json.loads((projects / project_id / "work_order.json").read_text(encoding="utf-8"))
    assert order["claim"]["claimed_by"] == "openmontage-antigravity"


def test_local_director_capability_endpoint_is_run_scoped_and_needs_no_global_token(
    client,
    monkeypatch,
) -> None:
    test_client, projects = client
    created = test_client.post("/api/project/create", json={"title": "Capability scope"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]
    handoff = test_client.post(
        f"/api/project/{project_id}/run",
        params={"agent_id": "codex-test"},
    )
    assert handoff.status_code == 200, handoff.text
    order = handoff.json()["work_order"]
    project_dir = projects / project_id
    issued = issue_local_director_capability(
        project_dir,
        project_id,
        order["run_id"],
        "codex-test",
    )
    headers = {"Authorization": f"Bearer {issued['token']}"}
    payload = {
        "project_dir": str(project_dir.resolve()),
        "project_id": project_id,
        "run_id": order["run_id"],
        "agent_id": "codex-test",
    }

    # A remote Backlot deployment normally requires its global token. The
    # local bridge route accepts only this narrower, live-run capability.
    monkeypatch.setenv("BACKLOT_HOST", "0.0.0.0")
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "global-operator-token")
    valid = test_client.post(
        "/api/local-director/capability/validate",
        headers=headers,
        json=payload,
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["agent_id"] == "codex-test"
    assert valid.headers["cache-control"] == "no-store"

    wrong_run = test_client.post(
        "/api/local-director/capability/validate",
        headers=headers,
        json={**payload, "run_id": "another-run"},
    )
    assert wrong_run.status_code == 401

    wrong_path = test_client.post(
        "/api/local-director/capability/validate",
        headers=headers,
        json={**payload, "project_dir": str((projects / "elsewhere").resolve())},
    )
    assert wrong_path.status_code == 401

    order_path = project_dir / "work_order.json"
    stale_order = json.loads(order_path.read_text(encoding="utf-8"))
    stale_order["claim"]["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    order_path.write_text(json.dumps(stale_order), encoding="utf-8")
    stale_lease = test_client.post(
        "/api/local-director/capability/validate",
        headers=headers,
        json=payload,
    )
    assert stale_lease.status_code == 401

    no_capability = test_client.post(
        "/api/local-director/capability/validate",
        json=payload,
    )
    assert no_capability.status_code == 401


def test_codex_director_rejects_api_key_sign_in(monkeypatch) -> None:
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda command: "C:/tools/codex.exe" if command in {"codex.exe", "codex"} else None,
    )
    monkeypatch.setattr(local_director, "_codex_auth_status", lambda _path: "api_key")

    status = agent_launcher.agent_command_status("codex")

    assert status["configured"] is True
    assert status["valid"] is False
    assert "API key" in status["error"]
    assert "ChatGPT sign-in" in status["error"]


def test_codex_director_requires_confirmed_chatgpt_sign_in(monkeypatch) -> None:
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda command: "C:/tools/codex.exe" if command in {"codex.exe", "codex"} else None,
    )
    monkeypatch.setattr(local_director, "_codex_auth_status", lambda _path: "unknown")

    status = agent_launcher.agent_command_status("codex")
    catalog_entry = next(
        item for item in local_director.local_director_catalog() if item["id"] == "codex"
    )

    assert status["configured"] is True
    assert status["valid"] is False
    assert "Could not confirm" in status["error"]
    assert catalog_entry["ready"] is False
    assert "ChatGPT sign-in" in catalog_entry["status_note"]


def test_codex_director_allows_confirmed_chatgpt_sign_in(monkeypatch) -> None:
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda command: "C:/tools/codex.exe" if command in {"codex.exe", "codex"} else None,
    )
    monkeypatch.setattr(local_director, "_codex_auth_status", lambda _path: "chatgpt")

    status = agent_launcher.agent_command_status("codex")

    assert status["configured"] is True
    assert status["valid"] is True
    assert status["director"] == "codex"


def test_antigravity_catalog_requires_scoped_mcp_permissions(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(
        local_director,
        "antigravity_settings_path",
        lambda: settings_file,
    )
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda name: "C:/tools/agy.exe" if name in {"agy.exe", "agy"} else None,
    )

    catalog_entry = next(
        item for item in local_director.local_director_catalog() if item["id"] == "antigravity"
    )
    assert catalog_entry["installed"] is True
    assert catalog_entry["ready"] is False
    assert "three exact OpenMontage MCP allow rules" in catalog_entry["status_note"]
    preflight = agent_launcher.agent_command_status("antigravity")
    assert preflight["valid"] is False
    assert "no model was started" in preflight["error"]

    settings_file.write_text(
        json.dumps({
            "permissions": {
                "allow": list(local_director.ANTIGRAVITY_MCP_ALLOW_RULES),
            }
        }),
        encoding="utf-8",
    )
    ready_entry = next(
        item for item in local_director.local_director_catalog() if item["id"] == "antigravity"
    )
    assert ready_entry["ready"] is True
    assert ready_entry["auth_status"] == "unknown"
    assert "sign-in and account quota cannot be verified" in ready_entry["status_note"]

    settings_file.write_text(
        json.dumps({
            "permissions": {
                "allow": list(local_director.ANTIGRAVITY_MCP_ALLOW_RULES),
                "ask": ["mcp(openmontage-local-director/*)"],
            }
        }),
        encoding="utf-8",
    )
    blocked_entry = next(
        item for item in local_director.local_director_catalog() if item["id"] == "antigravity"
    )
    assert blocked_entry["ready"] is False
    assert "Ask or Deny rule overrides" in blocked_entry["status_note"]

    settings_file.write_text(
        json.dumps({
            "permissions": {
                "allow": list(local_director.ANTIGRAVITY_MCP_ALLOW_RULES),
                "deny": ["mcp(*)"],
            }
        }),
        encoding="utf-8",
    )
    globally_blocked_entry = next(
        item for item in local_director.local_director_catalog() if item["id"] == "antigravity"
    )
    assert globally_blocked_entry["ready"] is False
    assert "Ask or Deny rule overrides" in globally_blocked_entry["status_note"]


def test_antigravity_adapter_does_not_start_a_model_without_permissions(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setattr(
        local_director,
        "antigravity_settings_path",
        lambda: tmp_path / "missing-settings.json",
    )
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda name: "C:/tools/agy.exe" if name in {"agy.exe", "agy"} else None,
    )
    monkeypatch.setattr(
        local_director.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Antigravity must not start without MCP permission"),
    )

    result = local_director.run_local_director(
        "antigravity",
        env={
            "OPENMONTAGE_AGENT_PROMPT": "do not send this",
            "OPENMONTAGE_PROJECT_DIR": str(tmp_path),
        },
    )

    assert result == 2
    assert "no model was started" in capsys.readouterr().err


def test_antigravity_permission_failure_does_not_claim_a_fresh_run(
    client,
    monkeypatch,
    tmp_path: Path,
) -> None:
    test_client, projects = client
    monkeypatch.delenv("OPENMONTAGE_AGENT_COMMAND", raising=False)
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda name: "C:/tools/agy.exe" if name in {"agy.exe", "agy"} else None,
    )
    monkeypatch.setattr(
        local_director,
        "antigravity_settings_path",
        lambda: tmp_path / "missing-settings.json",
    )
    monkeypatch.setattr(
        server_mod,
        "launch_agent",
        lambda *_args, **_kwargs: pytest.fail("preflight must block before launch"),
    )
    created = test_client.post("/api/project/create", json={"title": "Antigravity preflight"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    response = test_client.post(f"/api/project/{project_id}/run?director=antigravity")

    assert response.status_code == 503
    assert "no model was started" in response.json()["detail"]
    order = json.loads((projects / project_id / "work_order.json").read_text(encoding="utf-8"))
    assert order["status"] == "queued"
    assert order["claim"]["claimed_by"] is None
    assert order["stages"][0]["status"] == "ready"


def test_concurrent_run_requests_launch_one_agent(client, monkeypatch) -> None:
    test_client, _projects = client
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m my_agent")
    monkeypatch.setenv("OPENMONTAGE_AGENT_ID", "race-agent")
    created = test_client.post("/api/project/create", json={"title": "Concurrent run requests"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    original_load = server_mod.load_manifest_stage_context
    initial_loads = Barrier(2)
    load_count = 0
    load_count_lock = Lock()

    def synchronized_load(project_dir):
        nonlocal load_count
        context = original_load(project_dir)
        with load_count_lock:
            load_count += 1
            is_initial_read = load_count <= 2
        if is_initial_read:
            initial_loads.wait(timeout=5)
        return context

    monkeypatch.setattr(server_mod, "load_manifest_stage_context", synchronized_load)
    launches = []
    launch_lock = Lock()

    def fake_launch(project_dir, order, *, agent_id, backlot_url, execution_context=None):
        with launch_lock:
            launches.append(str(order["run_id"]))
        return AgentLaunch(
            pid=7314,
            agent_id=agent_id,
            run_id=str(order["run_id"]),
            started_at="2026-09-12T00:00:00+00:00",
            log_path="agent.log",
            command=("python", "-m", "my_agent"),
            cwd=str(project_dir),
        )

    monkeypatch.setattr(server_mod, "launch_agent", fake_launch)
    run_url = f"/api/project/{project_id}/run"
    with TestClient(server_mod.create_app()) as second_client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(test_client.post, run_url)
            second = executor.submit(second_client.post, run_url)
            responses = [first.result(timeout=10), second.result(timeout=10)]

    assert [response.status_code for response in responses] == [200, 200]
    payloads = [response.json() for response in responses]
    assert sorted(payload["idempotent_replay"] for payload in payloads) == [False, True]
    assert {payload["agent_launch"]["status"] for payload in payloads} == {
        "started",
        "already_running",
    }
    assert {payload["work_order"]["run_id"] for payload in payloads} == {
        created.json()["work_order"]["run_id"]
    }
    assert launches == [created.json()["work_order"]["run_id"]]


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
    monkeypatch.setenv("OPENAI_API_KEY", "production-openai-key")
    monkeypatch.setenv("FAL_KEY", "production-fal-key")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "production-speech-key")
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "global-backlot-token")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "C:/secrets/provider.json")
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
    for secret_name in (
        "OPENAI_API_KEY",
        "FAL_KEY",
        "AZURE_SPEECH_KEY",
        "BACKLOT_AUTH_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        assert secret_name not in captured["kwargs"]["env"]
    assert launch.pid == 7711
    record = json.loads((tmp_path / "agent_process.json").read_text(encoding="utf-8"))
    assert record["status"] == "started"
    assert record["command"] == ["python", "-m", "my_agent"]


def test_codex_launch_receives_only_a_live_run_capability(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class FakeProcess:
        pid = 7744

    monkeypatch.setattr(
        agent_launcher,
        "director_launcher_command",
        lambda director: (sys.executable, "-m", "lib.local_director", director),
    )
    monkeypatch.setattr(
        agent_launcher.subprocess,
        "Popen",
        lambda argv, **kwargs: (captured.update(argv=argv, kwargs=kwargs) or FakeProcess()),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "production-key")
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "global-backlot-token")
    order = {
        "project_id": "codex-capability",
        "run_id": "8f6b4b7b-2c1f-4c1d-9d3e-7d6b6f6c8e1a",
        "next_stage": "idea",
    }

    agent_launcher.launch_agent(
        tmp_path,
        order,
        agent_id="openmontage-codex",
        director="codex",
        backlot_url="http://127.0.0.1:4750",
    )

    env = captured["kwargs"]["env"]
    token = env["OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN"]
    assert env["OPENMONTAGE_BACKLOT_URL"] == "http://127.0.0.1:4750"
    assert "OPENAI_API_KEY" not in env
    assert "BACKLOT_AUTH_TOKEN" not in env
    assert "OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN" not in local_director.director_environment(env)
    assert validate_local_director_capability(
        token,
        tmp_path,
        order["project_id"],
        order["run_id"],
        "openmontage-codex",
    )["agent_id"] == "openmontage-codex"


def test_launcher_bundles_manifest_and_stage_instructions_for_local_director(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m my_agent")

    class FakeProcess:
        pid = 7722

    monkeypatch.setattr(agent_launcher.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    class ExecutionContext:
        manifest = {"id": "explainer", "stages": [{"name": "idea"}]}

        @staticmethod
        def as_dict():
            return {
                "pipeline_type": "explainer",
                "next_stage": "idea",
                "director_skill": "skills/pipelines/explainer/idea-director.md",
            }

    order = {
        "project_id": "handoff-test",
        "run_id": "8f6b4b7b-2c1f-4c1d-9d3e-7d6b6f6c8e1a",
        "next_stage": "idea",
    }
    agent_launcher.launch_agent(
        tmp_path,
        order,
        agent_id="agent-a",
        execution_context=ExecutionContext(),
    )

    handoff_dir = tmp_path / ".openmontage"
    handoff = json.loads((handoff_dir / "agent_handoff.json").read_text(encoding="utf-8"))
    assert handoff["manifest"]["id"] == "explainer"
    assert handoff["execution"]["next_stage"] == "idea"
    assert (handoff_dir / "AGENT_GUIDE.md").is_file()
    assert (handoff_dir / "PROJECT_CONTEXT.md").is_file()
    assert "idea" in (handoff_dir / "stage_director.md").read_text(encoding="utf-8").lower()


def test_launcher_rejects_symlinked_handoff_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m my_agent")
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (tmp_path / ".openmontage").symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    monkeypatch.setattr(
        agent_launcher.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("must reject the symlink before launch"),
    )

    with pytest.raises(AgentLaunchError, match="must not be a symlink"):
        agent_launcher.launch_agent(
            tmp_path,
            {"project_id": "symlink", "run_id": "8f6b4b7b-2c1f-4c1d-9d3e-7d6b6f6c8e1a"},
            agent_id="agent-a",
        )


@pytest.mark.parametrize(
    ("director", "relative_config"),
    [
        ("antigravity", Path(".agents") / "mcp_config.json"),
        ("claude", Path(".mcp.json")),
    ],
)
def test_workspace_director_mcp_config_preserves_existing_servers(
    tmp_path: Path,
    director: str,
    relative_config: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / relative_config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "mcpServers": {"existing": {"command": "existing-mcp"}},
    }), encoding="utf-8")

    result = _write_workspace_mcp_config(
        project,
        {"project_id": "workspace-mcp", "run_id": "8f6b4b7b-2c1f-4c1d-9d3e-7d6b6f6c8e1a"},
        agent_id="agent-a",
        director=director,
        backlot_url="http://127.0.0.1:8000",
        capability_token="test-scoped-capability",
    )

    payload = json.loads(Path(result).read_text(encoding="utf-8"))
    servers = payload["mcpServers"]
    assert servers["existing"] == {"command": "existing-mcp"}
    server = servers["openmontage-local-director"]
    assert server["args"] == ["-m", "lib.agent_mcp"]
    assert server["env"]["OPENMONTAGE_AGENT_ID"] == "agent-a"
    assert server["env"]["OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN"] == "test-scoped-capability"
    assert not any("API_KEY" in name for name in server["env"])
    assert f"/{relative_config.as_posix()}" in (project / ".gitignore").read_text(encoding="utf-8")


def test_local_director_command_status_blocks_claude_subscription_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m lib.local_director claude")
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda name: "C:/tools/claude.exe" if name in {"claude", "claude.exe"} else None,
    )

    status = agent_launcher.agent_command_status()

    assert status["configured"] is True
    assert status["valid"] is False
    assert status["director"] == "claude"
    assert "interactive" in status["error"].lower()
    assert "subscription" in status["error"].lower()


def test_local_director_can_start_a_powershell_cli_shim(monkeypatch) -> None:
    monkeypatch.setattr(local_director.os, "name", "nt")

    def fake_which(command: str) -> str | None:
        return {
            "codex": "C:/tools/codex.cmd",
            "codex.ps1": "C:/tools/codex.ps1",
            "powershell.exe": "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        }.get(command)

    monkeypatch.setattr(local_director.shutil, "which", fake_which)

    executable = local_director.find_director_executable("codex")
    argv, stdin_prompt = local_director.command_for_director(
        "codex",
        executable,
        "make a local edit",
        working_directory="C:/project",
    )

    assert argv[:7] == [
        "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:/tools/codex.ps1",
    ]
    assert argv[7:11] == ["exec", "--sandbox", "workspace-write", "--cd"]
    assert argv[11:] == ["C:/project", "-"]
    assert stdin_prompt == "make a local edit"


@pytest.mark.parametrize(
    ("director", "expected_executable", "expected_args", "prompt_on_stdin"),
    [
        ("codex", "codex.exe", ["exec", "--sandbox", "workspace-write", "--cd"], True),
        ("antigravity", "agy.exe", ["--sandbox", "-p"], False),
    ],
)
def test_local_director_uses_account_sign_in_and_receives_prompt(
    director: str,
    expected_executable: str,
    expected_args: list[str],
    prompt_on_stdin: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda command: f"C:/tools/{expected_executable}"
        if command in {
            local_director.DIRECTOR_EXECUTABLES[director],
            f"{local_director.DIRECTOR_EXECUTABLES[director]}.exe",
        }
        else None,
    )

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(local_director.subprocess, "run", fake_run)
    source_env = {
        "OPENMONTAGE_AGENT_PROMPT": "produce the requested local video",
        "OPENMONTAGE_PROJECT_DIR": str(tmp_path),
        "OPENAI_API_KEY": "openai-production-key",
        "ANTHROPIC_API_KEY": "anthropic-production-key",
        "ANTHROPIC_AUTH_TOKEN": "anthropic-api-token",
        "GEMINI_API_KEY": "gemini-production-key",
        "GOOGLE_API_KEY": "google-production-key",
        "FAL_KEY": "fal-production-key",
        "AZURE_SPEECH_KEY": "speech-production-key",
        "GOOGLE_APPLICATION_CREDENTIALS": "C:/secrets/google.json",
        "BACKLOT_AUTH_TOKEN": "backlot-runtime-token",
    }

    result = local_director.run_local_director(director, env=source_env)

    assert result == 0
    assert captured["argv"][0].endswith(expected_executable)
    if director == "codex":
        assert captured["argv"][1:5] == expected_args
        assert captured["argv"][5] == str(tmp_path.resolve())
        assert captured["argv"][6] == "-"
    else:
        command_end = 1 + len(expected_args)
        assert captured["argv"][1:command_end] == expected_args
        assert captured["argv"][command_end] == source_env["OPENMONTAGE_AGENT_PROMPT"]
    assert captured["kwargs"]["input"] == (
        source_env["OPENMONTAGE_AGENT_PROMPT"] if prompt_on_stdin else None
    )
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())
    for credential in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "FAL_KEY",
        "AZURE_SPEECH_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "BACKLOT_AUTH_TOKEN",
    ):
        assert credential not in captured["kwargs"]["env"]
    assert source_env["OPENAI_API_KEY"] == "openai-production-key"


def test_claude_native_user_install_is_discovered_outside_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / ".local" / "bin" / "claude.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native cli placeholder")
    monkeypatch.setattr(local_director.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(local_director.shutil, "which", lambda _name: None)

    assert local_director.find_director_executable("claude") == str(executable)
    catalog_entry = next(
        item for item in local_director.local_director_catalog() if item["id"] == "claude"
    )
    assert catalog_entry["installed"] is True
    assert catalog_entry["mode"] == "interactive_only"


def test_claude_subscription_is_interactive_only(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda name: "C:/tools/claude.exe" if name in {"claude", "claude.exe"} else None,
    )
    monkeypatch.setattr(
        local_director.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Backlot must not submit a Claude prompt"),
    )

    catalog_entry = next(
        item for item in local_director.local_director_catalog() if item["id"] == "claude"
    )
    assert catalog_entry["installed"] is True
    assert catalog_entry["ready"] is False
    assert catalog_entry["mode"] == "interactive_only"
    assert catalog_entry["handoff_agent_id"] == local_director.CLAUDE_INTERACTIVE_AGENT_ID

    status = agent_launcher.agent_command_status("claude")
    assert status["valid"] is False
    assert "subscription" in status["error"].lower()

    result = local_director.run_local_director(
        "claude",
        env={
            "OPENMONTAGE_AGENT_PROMPT": "do not send this",
            "OPENMONTAGE_PROJECT_DIR": str(tmp_path),
        },
    )

    assert result == 2
    assert "interactive" in capsys.readouterr().err.lower()


def test_claude_manual_handoff_is_prepared_without_dispatching_a_model(client, monkeypatch) -> None:
    test_client, projects = client
    monkeypatch.setenv("OPENAI_API_KEY", "production-key-must-not-enter-director-prompt")
    monkeypatch.setattr(
        agent_launcher.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("manual Claude handoff must not launch a process"),
    )
    created = test_client.post("/api/project/create", json={"title": "Manual Claude director"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    response = test_client.post(
        f"/api/project/{project_id}/run?agent_id={local_director.CLAUDE_INTERACTIVE_AGENT_ID}"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    manual = payload["manual_handoff"]
    assert payload["agent_launch"]["status"] == "handoff"
    assert manual["mode"] == "interactive_manual_handoff"
    assert manual["agent_id"] == local_director.CLAUDE_INTERACTIVE_AGENT_ID
    assert manual["project_id"] == project_id
    assert manual["run_id"] == payload["work_order"]["run_id"]
    assert manual["stage"] == "idea"
    assert "submit this prompt manually" in manual["prompt"].lower()
    assert "did not send it to claude" in manual["prompt"].lower()
    assert "do not read or print .env" in manual["prompt"].lower()
    assert "production-key-must-not-enter-director-prompt" not in manual["prompt"]
    assert Path(manual["handoff_path"]).is_file()
    assert Path(manual["prompt_path"]).read_text(encoding="utf-8").strip() == manual["prompt"]
    assert not (projects / project_id / "agent_process.json").exists()


def test_local_director_requires_project_workspace(monkeypatch) -> None:
    monkeypatch.setattr(
        local_director.shutil,
        "which",
        lambda command: "C:/tools/codex.exe" if command in {"codex", "codex.exe"} else None,
    )
    monkeypatch.setattr(
        local_director.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("director must not start without a project"),
    )

    result = local_director.run_local_director(
        "codex",
        env={"OPENMONTAGE_AGENT_PROMPT": "work", "OPENAI_API_KEY": "production-key"},
    )

    assert result == 2


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
