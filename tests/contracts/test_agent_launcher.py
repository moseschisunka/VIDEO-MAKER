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


def test_missing_local_director_cli_does_not_claim_a_run(client, monkeypatch) -> None:
    test_client, projects = client
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m lib.local_director claude")
    monkeypatch.setattr(local_director.shutil, "which", lambda _name: None)
    created = test_client.post("/api/project/create", json={"title": "Local director setup"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    response = test_client.post(f"/api/project/{project_id}/run")

    assert response.status_code == 503
    assert "claude" in response.json()["detail"]
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
            {"id": "claude", "label": "Claude Code", "installed": False, "ready": False},
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


def test_local_director_command_status_rejects_missing_cli(monkeypatch) -> None:
    monkeypatch.setenv("OPENMONTAGE_AGENT_COMMAND", "python -m lib.local_director claude")
    monkeypatch.setattr(local_director.shutil, "which", lambda _name: None)

    status = agent_launcher.agent_command_status()

    assert status["configured"] is True
    assert status["valid"] is False
    assert status["director"] == "claude"
    assert "claude" in status["error"]


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
        ("claude", "claude.exe", ["-p"], False),
        ("antigravity", "agy.exe", ["-p"], False),
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
        assert captured["argv"][1:2] == expected_args
        assert captured["argv"][2] == source_env["OPENMONTAGE_AGENT_PROMPT"]
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
