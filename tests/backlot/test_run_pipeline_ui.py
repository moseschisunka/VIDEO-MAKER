"""Browser-to-process contract for Backlot's Run Pipeline action."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import URLError

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_backlot(base_url: str, server: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"Backlot exited before health check:\n{log_path.read_text(encoding='utf-8')}")
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1):
                return
        except (URLError, TimeoutError):
            time.sleep(0.2)
    raise RuntimeError(f"Backlot did not become healthy:\n{log_path.read_text(encoding='utf-8')}")


@pytest.mark.release_blocker
def test_run_pipeline_button_launches_agent_and_delivers_handoff(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    worker_script = tmp_path / "fake_agent.py"
    marker_path = tmp_path / "agent-handoff.json"
    worker_script.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "handoff = {\n"
        "    'project_id': os.environ['OPENMONTAGE_PROJECT_ID'],\n"
        "    'project_dir': os.environ['OPENMONTAGE_PROJECT_DIR'],\n"
        "    'run_id': os.environ['OPENMONTAGE_RUN_ID'],\n"
        "    'stage': os.environ['OPENMONTAGE_STAGE'],\n"
        "    'prompt': os.environ['OPENMONTAGE_AGENT_PROMPT'],\n"
        "}\n"
        "Path(os.environ['OPENMONTAGE_AGENT_MARKER']).write_text(\n"
        "    json.dumps(handoff), encoding='utf-8'\n"
        ")\n",
        encoding="utf-8",
    )
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env.update(
        {
            "OPENMONTAGE_PROJECTS_DIR": str(projects_dir),
            "OPENMONTAGE_AGENT_COMMAND": f'"{sys.executable}" "{worker_script}"',
            "OPENMONTAGE_AGENT_ID": "ui-smoke-agent",
            "OPENMONTAGE_AGENT_MARKER": str(marker_path),
        }
    )
    log_path = tmp_path / "backlot.log"

    with log_path.open("w", encoding="utf-8") as server_log:
        server = subprocess.Popen(
            [sys.executable, "-m", "backlot", "serve", "--port", str(port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_backlot(base_url, server, log_path)
            request = urllib.request.Request(
                f"{base_url}/api/project/create",
                data=json.dumps({"title": "Browser agent handoff smoke"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                created = json.loads(response.read())
            project_id = created["project_id"]

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    dialogs: list[str] = []

                    def dismiss_dialog(dialog) -> None:
                        dialogs.append(dialog.message)
                        dialog.accept()

                    page.on("dialog", dismiss_dialog)
                    page.goto(f"{base_url}/p/{project_id}", wait_until="networkidle")
                    run_button = page.get_by_title("Run automated video production")
                    expect(run_button).to_contain_text("Run Pipeline")
                    run_url = f"{base_url}/api/project/{project_id}/run"
                    with page.expect_response(
                        lambda response: response.url == run_url
                        and response.request.method == "POST",
                        timeout=10_000,
                    ) as response_info:
                        run_button.click()
                    run_response = response_info.value
                    assert run_response.status == 200, run_response.text()
                    run_payload = run_response.json()
                    assert run_payload["agent_launch"]["status"] == "started"
                    expect(run_button).to_contain_text("Agent started", timeout=1500)
                    assert not dialogs, dialogs
                finally:
                    browser.close()

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not marker_path.is_file():
                time.sleep(0.02)
            assert marker_path.is_file(), "the configured worker did not receive the browser-launched handoff"
            handoff = json.loads(marker_path.read_text(encoding="utf-8"))
            assert handoff["project_id"] == project_id
            assert handoff["project_dir"] == str((projects_dir / project_id).resolve())
            assert handoff["stage"] == "idea"
            assert handoff["run_id"] == created["work_order"]["run_id"]
            assert project_id in handoff["prompt"]
            assert handoff["run_id"] in handoff["prompt"]

            process_record = json.loads(
                (projects_dir / project_id / "agent_process.json").read_text(encoding="utf-8")
            )
            assert process_record["status"] == "started"
            assert process_record["agent_id"] == "ui-smoke-agent"
            assert process_record["run_id"] == handoff["run_id"]
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
