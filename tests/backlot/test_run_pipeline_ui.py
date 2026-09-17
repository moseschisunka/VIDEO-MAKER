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
from lib.provider_approvals import create_provider_approval_request
from lib.providers.contracts import ProviderRequest

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
    handoff_dir = tmp_path / "agent-handoffs"
    handoff_dir.mkdir()
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
        "marker = Path(os.environ['OPENMONTAGE_AGENT_MARKER_DIR']) / f\"{handoff['run_id']}.json\"\n"
        "marker.write_text(json.dumps(handoff), encoding='utf-8')\n",
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
            "OPENMONTAGE_AGENT_MARKER_DIR": str(handoff_dir),
            "OPENMONTAGE_TTS_PROVIDER": "",
            "OPENAI_API_KEY": "",
            "OPENAI_TTS_VOICE": "coral",
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
            project_config = json.loads(
                (projects_dir / project_id / "artifacts" / "project_config.json").read_text(encoding="utf-8")
            )
            assert project_config["tts_provider"] == "edge_tts"
            assert project_config["voice"] == "en-US-ChristopherNeural"

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    page.add_init_script(
                        "Object.defineProperty(navigator, 'clipboard', {configurable: true, "
                        "value: {writeText: async (text) => {window.__lastCopiedPrompt = text;}}});"
                    )
                    dialogs: list[str] = []
                    catalog_events: list[str] = []
                    browser_project_id = ""
                    manual_project_id = ""

                    def is_catalog_url(url: str) -> bool:
                        return any(
                            url.split("?", 1)[0].endswith(path)
                            for path in ("/api/pipelines", "/api/playbooks", "/api/voice-providers")
                        )

                    page.on(
                        "request",
                        lambda request: catalog_events.append(f"request {request.url}")
                        if is_catalog_url(request.url)
                        else None,
                    )
                    page.on(
                        "response",
                        lambda response: catalog_events.append(
                            f"response {response.status} {response.url}"
                        )
                        if is_catalog_url(response.url)
                        else None,
                    )
                    page.on(
                        "requestfailed",
                        lambda request: catalog_events.append(
                            f"failed {request.url}: {request.failure}"
                        )
                        if is_catalog_url(request.url)
                        else None,
                    )

                    def dismiss_dialog(dialog) -> None:
                        dialogs.append(dialog.message)
                        dialog.accept()

                    page.on("dialog", dismiss_dialog)
                    page.route(
                        "**/api/local-directors",
                        lambda route: route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "configured_runner": True,
                                    "configured_runner_label": "test configured agent",
                                    "directors": [
                                        {
                                            "id": "antigravity",
                                            "label": "Antigravity",
                                            "installed": True,
                                            "ready": True,
                                            "auth_status": "unknown",
                                            "status_note": "Authentication could not be verified.",
                                        },
                                        {
                                            "id": "claude",
                                            "label": "Claude Code",
                                            "installed": True,
                                            "ready": False,
                                            "mode": "interactive_only",
                                            "auth_status": "manual",
                                            "handoff_agent_id": "openmontage-claude-interactive",
                                            "status_note": "Paste the handoff into Claude Code's interactive CLI.",
                                        },
                                    ],
                                }
                            ),
                        ),
                    )
                    page.goto(f"{base_url}/p/{project_id}", wait_until="networkidle")
                    activity_panel = page.locator("aside .panel").filter(has_text="Activity")
                    expect(activity_panel.locator(".status")).to_have_text("created")
                    expect(activity_panel.locator(".status.run")).to_have_count(0)
                    expect(
                        page.locator('select[aria-label="Local director"] option[value="antigravity"]')
                    ).to_be_enabled()
                    expect(
                        page.locator('select[aria-label="Local director"] option[value="claude"]')
                    ).to_be_enabled()
                    expect(
                        page.locator('select[aria-label="Local director"] option[value="claude"]')
                    ).to_contain_text("interactive only")
                    expect(page.locator('select[aria-label="Local director"]')).to_have_value(
                        "configured"
                    )
                    run_button = page.get_by_title("Run or hand off the video pipeline")
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

                    work_order_response = urllib.request.urlopen(
                        f"{base_url}/api/project/{project_id}/work-order",
                        timeout=5,
                    )
                    work_order = json.loads(work_order_response.read())["work_order"]
                    provider_request = ProviderRequest(
                        capability="text_generation",
                        operation="generate",
                        provider="openai",
                        model="test-production-model",
                        payload={"prompt": "Draft a short introduction for the approved production."},
                        idempotency_key="ui-provider-approval-smoke",
                        project_id=project_id,
                        pipeline_type=work_order["pipeline_type"],
                        run_id=work_order["run_id"],
                        attempt=work_order["attempt"],
                        stage=work_order["current_stage"],
                        estimated_cost_usd=0.0,
                        metadata={"tool": "test_openai_text", "runtime": "api"},
                    )
                    ticket = create_provider_approval_request(
                        projects_dir / project_id,
                        provider_request,
                        agent_id="ui-smoke-agent",
                        store_dir=projects_dir.parent / ".backlot" / "provider-approvals",
                    )
                    page.reload(wait_until="networkidle")
                    approval_card = page.locator(".provider-approval-card")
                    expect(approval_card).to_contain_text("openai · test-production-model")
                    expect(approval_card).to_contain_text("may still charge")
                    expect(approval_card).to_contain_text("Draft a short introduction")
                    with page.expect_response(
                        lambda response: response.url.endswith(
                            f"/api/project/{project_id}/provider-approvals/{ticket['request_id']}"
                        ) and response.request.method == "POST",
                        timeout=10_000,
                    ) as approval_response_info:
                        page.get_by_role("button", name="Approve and run this call").click()
                    assert approval_response_info.value.status == 200
                    expect(approval_card).to_contain_text("Approved · provider call is starting")

                    page.goto(base_url, wait_until="networkidle")
                    page.locator("#createVideoBtn").click()
                    expect(page.locator('#localDirectorSelect option[value="antigravity"]')).to_be_enabled()
                    expect(page.locator('#localDirectorSelect option[value="claude"]')).to_be_enabled()
                    expect(page.locator("#localDirectorSelect")).to_have_value("configured")
                    openai_provider_option = page.locator(
                        '#voiceProviderSelect option[value="openai"]'
                    )
                    try:
                        expect(openai_provider_option).to_be_attached(timeout=15_000)
                    except AssertionError as exc:
                        raise AssertionError(
                            f"Narration provider catalog did not load: {catalog_events}"
                        ) from exc
                    expect(openai_provider_option).to_be_disabled()
                    expect(page.locator("#submitCreateBtn")).to_be_disabled()
                    page.locator("#voiceProviderSelect").select_option("edge_tts")
                    expect(page.locator("#voiceSelect")).to_have_value("en-US-ChristopherNeural")
                    expect(page.locator("#voiceProviderHint")).to_contain_text("internet connection")
                    expect(page.locator("#submitCreateBtn")).to_be_enabled()
                    page.locator("#projectTitle").fill("Explicit narration provider")
                    page.locator("#projectTopic").fill("Test that voice provider selection is persisted.")
                    create_url = f"{base_url}/api/project/create"
                    with page.expect_request(
                        lambda request: request.url == create_url and request.method == "POST",
                        timeout=10_000,
                    ) as create_request_info:
                        with page.expect_response(
                            lambda response: response.url == create_url
                            and response.request.method == "POST",
                            timeout=10_000,
                        ) as create_response_info:
                            with page.expect_response(
                                lambda response: response.url.startswith(f"{base_url}/api/project/")
                                and response.url.endswith("/run")
                                and response.request.method == "POST",
                                timeout=10_000,
                            ) as auto_run_info:
                                page.locator("#submitCreateBtn").click()
                    create_request_body = create_request_info.value.post_data_json
                    assert create_request_body["voice_provider"] == "edge_tts"
                    assert create_request_body["voice"] == "en-US-ChristopherNeural"
                    assert create_response_info.value.status == 200
                    auto_run_response = auto_run_info.value
                    assert auto_run_response.status == 200, auto_run_response.text()
                    assert auto_run_response.url.startswith(f"{base_url}/api/project/")
                    page.wait_for_url(f"{base_url}/p/**", timeout=10_000)
                    browser_project_id = page.url.split("/p/", 1)[1].split("?", 1)[0].rstrip("/")
                    browser_project_config = json.loads(
                        (projects_dir / browser_project_id / "artifacts" / "project_config.json").read_text(encoding="utf-8")
                    )
                    assert browser_project_config["tts_provider"] == "edge_tts"
                    assert browser_project_config["voice"] == "en-US-ChristopherNeural"

                    page.goto(base_url, wait_until="networkidle")
                    page.locator("#createVideoBtn").click()
                    manual_wizard_director = page.locator("#localDirectorSelect")
                    expect(manual_wizard_director.locator('option[value="claude"]')).to_be_enabled()
                    manual_wizard_director.select_option("claude")
                    page.locator("#voiceProviderSelect").select_option("edge_tts")
                    page.locator("#projectTitle").fill("Manual Claude interactive handoff")
                    page.locator("#projectTopic").fill("Use the local Claude Code session for direction.")
                    manual_run_requests: list[str] = []
                    page.on(
                        "request",
                        lambda request: manual_run_requests.append(request.url)
                        if request.method == "POST"
                        and request.url.split("?", 1)[0].endswith("/run")
                        else None,
                    )
                    manual_create_url = f"{base_url}/api/project/create"
                    with page.expect_response(
                        lambda response: response.url == manual_create_url
                        and response.request.method == "POST",
                        timeout=10_000,
                    ) as manual_create_info:
                        page.locator("#submitCreateBtn").click()
                    manual_create_response = manual_create_info.value
                    assert manual_create_response.status == 200, manual_create_response.text()
                    page.wait_for_url(f"{base_url}/p/**", timeout=10_000)
                    manual_project_id = page.url.split("/p/", 1)[1].split("?", 1)[0].rstrip("/")
                    assert not manual_run_requests, "interactive Claude must not auto-run during project creation"
                    assert len(dialogs) == 1, dialogs
                    assert "click Run Pipeline" in dialogs[0]
                    manual_selector = page.locator('select[aria-label="Local director"]')
                    expect(manual_selector).to_have_value("claude")
                    manual_selector.select_option("claude")
                    manual_run_button = page.get_by_title("Run or hand off the video pipeline")
                    manual_run_url = (
                        f"{base_url}/api/project/{manual_project_id}/run"
                        "?agent_id=openmontage-claude-interactive"
                    )
                    with page.expect_response(
                        lambda response: response.url == manual_run_url
                        and response.request.method == "POST",
                        timeout=10_000,
                    ) as manual_response_info:
                        manual_run_button.click()
                    manual_response = manual_response_info.value
                    assert manual_response.status == 200, manual_response.text()
                    manual_payload = manual_response.json()
                    assert manual_payload["agent_launch"]["status"] == "handoff"
                    assert manual_payload["manual_handoff"]["mode"] == "interactive_manual_handoff"
                    assert page.evaluate("window.__lastCopiedPrompt") == manual_payload["manual_handoff"]["prompt"]
                    assert len(dialogs) == 2, dialogs
                    assert "did not send a request to Claude" in dialogs[1]
                    assert (
                        projects_dir
                        / manual_project_id
                        / ".openmontage"
                        / "claude_interactive_prompt.txt"
                    ).is_file()
                    assert not (projects_dir / manual_project_id / "agent_process.json").exists()
                finally:
                    browser.close()

            deadline = time.monotonic() + 5
            expected_handoffs = [created["work_order"]["run_id"]]
            if browser_project_id:
                expected_handoffs.append(
                    json.loads((projects_dir / browser_project_id / "work_order.json").read_text(encoding="utf-8"))["run_id"]
                )
            while time.monotonic() < deadline and not all(
                (handoff_dir / f"{run_id}.json").is_file() for run_id in expected_handoffs
            ):
                time.sleep(0.02)
            assert all((handoff_dir / f"{run_id}.json").is_file() for run_id in expected_handoffs), (
                "the configured worker did not receive every browser-launched handoff"
            )
            handoff = json.loads((handoff_dir / f"{expected_handoffs[0]}.json").read_text(encoding="utf-8"))
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
            if browser_project_id:
                browser_handoff = json.loads(
                    (handoff_dir / f"{expected_handoffs[1]}.json").read_text(encoding="utf-8")
                )
                assert browser_handoff["project_id"] == browser_project_id
                assert browser_handoff["run_id"] == expected_handoffs[1]
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
