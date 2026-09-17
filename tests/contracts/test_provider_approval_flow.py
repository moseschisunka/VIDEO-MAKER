"""Production provider calls from local directors require exact Backlot consent."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot import state as state_mod
from lib import agent_mcp, provider_approvals
from lib.agent_mcp import LocalDirectorBridge
from lib.pipeline_loader import load_pipeline_readonly
from lib.provider_approvals import (
    ProviderApprovalError,
    consume_provider_approval_request,
    create_provider_approval_request,
    decide_provider_approval_request,
    get_provider_approval_request,
    list_provider_approval_requests,
)
from lib.providers.contracts import FallbackClass, ProviderRequest
from lib.work_order import build_work_order, claim_work_order, read_work_order, write_work_order
from tools.base_tool import BaseTool, ToolResult, ToolRuntime


PROJECT_ID = "provider-approval-test"
RUN_ID = "12345678-1234-4234-8234-123456789abc"
AGENT_ID = "local-director-a"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _provider_request(project_id: str = PROJECT_ID, **changes) -> ProviderRequest:
    values = {
        "capability": "text_generation",
        "operation": "generate",
        "provider": "openai",
        "model": "test-model",
        "payload": {"prompt": "Create a short introduction."},
        "idempotency_key": "approval-test-key",
        "project_id": project_id,
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
        "attempt": 1,
        "stage": "idea",
        "estimated_cost_usd": 0.0,
        "metadata": {"tool": "openai_text", "runtime": "api"},
    }
    values.update(changes)
    return ProviderRequest(**values)


def test_provider_approval_is_request_bound_and_consumed_once(tmp_path: Path) -> None:
    project = tmp_path / PROJECT_ID
    project.mkdir()
    store = tmp_path / ".backlot" / "provider-approvals"
    request = _provider_request()

    ticket = create_provider_approval_request(
        project,
        request,
        agent_id=AGENT_ID,
        store_dir=store,
    )
    repeated = create_provider_approval_request(
        project,
        request,
        agent_id=AGENT_ID,
        store_dir=store,
    )

    assert ticket["request_id"] == repeated["request_id"]
    assert ticket["status"] == "pending"
    assert ticket["prompt_preview"] == "Create a short introduction."
    serialized = (store / f"{PROJECT_ID}.json").read_text(encoding="utf-8")
    assert "payload" not in serialized

    with pytest.raises(ProviderApprovalError, match="request changed"):
        decide_provider_approval_request(
            project,
            ticket["request_id"],
            "0" * 64,
            decision="approve",
            store_dir=store,
        )

    changed = _provider_request(model="different-model")
    changed_ticket = create_provider_approval_request(
        project,
        changed,
        agent_id=AGENT_ID,
        store_dir=store,
    )
    assert changed_ticket["request_id"] != ticket["request_id"]
    rejected = decide_provider_approval_request(
        project,
        changed_ticket["request_id"],
        changed_ticket["request_digest"],
        decision="reject",
        store_dir=store,
    )
    assert rejected["status"] == "rejected"
    assert rejected["prompt_preview"] is None
    assert consume_provider_approval_request(
        project,
        changed_ticket["request_id"],
        changed_ticket["request_digest"],
        store_dir=store,
    ) is False

    approved = decide_provider_approval_request(
        project,
        ticket["request_id"],
        ticket["request_digest"],
        decision="approve",
        store_dir=store,
    )
    assert approved["status"] == "approved"
    assert consume_provider_approval_request(
        project,
        ticket["request_id"],
        ticket["request_digest"],
        store_dir=store,
    ) is True
    assert consume_provider_approval_request(
        project,
        ticket["request_id"],
        ticket["request_digest"],
        store_dir=store,
    ) is False
    assert get_provider_approval_request(
        project,
        ticket["request_id"],
        store_dir=store,
    )["status"] == "consumed"
    assert get_provider_approval_request(
        project,
        ticket["request_id"],
        store_dir=store,
    )["prompt_preview"] is None

    media_ticket = create_provider_approval_request(
        project,
        _provider_request(
            payload={
                "prompt": "Create a short introduction.",
                "image_paths": ["assets/references/cover.png", "https://example.test/reference.png"],
            },
            metadata={
                "tool": "openai_text",
                "runtime": "api",
                "requires_external_media_approval": True,
            },
        ),
        agent_id=AGENT_ID,
        store_dir=store,
    )
    assert media_ticket["media_preview"] == ["assets/references/cover.png"]

    media_fallback_ticket = create_provider_approval_request(
        project,
        _provider_request(
            payload={
                "prompt": "Animate the supplied source clip.",
                "video_paths": ["assets/video/source.mp4"],
            },
            fallback_class=FallbackClass.MATERIAL_MEDIA_CHANGE.value,
            metadata={"tool": "openai_video", "runtime": "api"},
        ),
        agent_id=AGENT_ID,
        store_dir=store,
    )
    assert media_fallback_ticket["external_media_transfer"] is True
    assert media_fallback_ticket["media_preview"] == ["assets/video/source.mp4"]


def test_provider_approval_expiry_fails_closed(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / PROJECT_ID
    project.mkdir()
    store = tmp_path / ".backlot" / "provider-approvals"
    fixed_now = provider_approvals._now()
    monkeypatch.setattr(provider_approvals, "_now", lambda: fixed_now)
    ticket = create_provider_approval_request(
        project,
        _provider_request(),
        agent_id=AGENT_ID,
        store_dir=store,
        ttl_seconds=1,
    )

    monkeypatch.setattr(
        provider_approvals,
        "_now",
        lambda: fixed_now + provider_approvals.timedelta(seconds=2),
    )
    assert list_provider_approval_requests(project, store_dir=store)["requests"] == []
    with pytest.raises(ProviderApprovalError, match="expired"):
        decide_provider_approval_request(
            project,
            ticket["request_id"],
            ticket["request_digest"],
            decision="approve",
            store_dir=store,
        )
    assert get_provider_approval_request(
        project,
        ticket["request_id"],
        store_dir=store,
    )["status"] == "expired"


class _ProviderProbe(BaseTool):
    name = "approval_probe_provider"
    provider = "openai"
    capability = "text_generation"
    runtime = ToolRuntime.API
    input_schema = {
        "type": "object",
        "properties": {"prompt": {"type": "string"}},
        "required": ["prompt"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, inputs):
        self.calls += 1
        return ToolResult(success=True, data={"text": "generated in a test double"})

    def execute(self, inputs):
        return self.generate(inputs)


class _SelectorProbe(BaseTool):
    name = "approval_probe_selector"
    provider = "selector"
    capability = "text_generation"
    runtime = ToolRuntime.HYBRID
    input_schema = _ProviderProbe.input_schema

    def __init__(self, provider: _ProviderProbe) -> None:
        self.provider_tool = provider

    def execute(self, inputs):
        from lib.providers.bridge import execute_with_provider_executor

        # Mirrors production selectors: provider selection happens first, then
        # the selected provider request enters the supplied common executor.
        return execute_with_provider_executor(
            self.provider_tool,
            dict(inputs),
            implementation=self.provider_tool.generate,
            operation="generate",
        )


class _Registry:
    def __init__(self, tool: BaseTool) -> None:
        self.tool = tool

    def get(self, name: str):
        return self.tool if name == self.tool.name else None


def _active_project(projects_root: Path) -> Path:
    project = projects_root / PROJECT_ID
    (project / "artifacts").mkdir(parents=True)
    (project / "assets" / "video").mkdir(parents=True)
    (project / "renders").mkdir()
    manifest_path = REPO_ROOT / "pipeline_defs" / "screen-demo.yaml"
    manifest = load_pipeline_readonly("screen-demo", defs_dir=REPO_ROOT / "pipeline_defs")
    order = build_work_order(
        project_id=PROJECT_ID,
        title="Provider approval test",
        topic_prompt="Exercise a local production handoff without network calls.",
        target_duration_seconds=30,
        pipeline_type="screen-demo",
        manifest=manifest,
        manifest_path=manifest_path,
        selections={
            "playbook": "premium-minimalist",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "ffmpeg",
            "output_profile": "youtube_landscape",
            "aspect_ratio": "16:9",
            "source_mode": "real_capture",
        },
        run_id=RUN_ID,
    )
    (project / "project.json").write_text(json.dumps({
        "version": "1.0",
        "project_id": PROJECT_ID,
        "title": "Provider approval test",
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
    }), encoding="utf-8")
    (project / "artifacts" / "project_config.json").write_text(json.dumps({
        "project_id": PROJECT_ID,
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
        "title": "Provider approval test",
    }), encoding="utf-8")
    (project / "events.jsonl").write_text("", encoding="utf-8")
    write_work_order(project, order)
    claim_work_order(project, AGENT_ID)
    return project


def test_backlot_approval_runs_selected_provider_only_after_exact_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project = _active_project(projects_root)
    monkeypatch.setenv("OPENMONTAGE_PROJECTS_DIR", str(projects_root))
    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects_root)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", os.path.normcase(str(projects_root.resolve())))
    monkeypatch.setattr(server_mod, "_summary_cache", {})
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", projects_root)

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    provider = _ProviderProbe()
    selector = _SelectorProbe(provider)
    bridge = LocalDirectorBridge(
        project_dir=project,
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        agent_id=AGENT_ID,
        capability_validator=lambda: None,
        tool_registry=_Registry(selector),
    )
    monkeypatch.setattr(agent_mcp, "load_manifest_stage_context", lambda _project: SimpleNamespace(
        order=read_work_order(project),
        stage="idea",
        stage_definition={"tools_available": [selector.name], "required_tools": []},
        required_artifacts_in=(),
        produced_artifacts=(),
        as_dict=lambda: {"project_id": PROJECT_ID, "pipeline_type": "screen-demo", "run_id": RUN_ID},
    ))
    store_dir = projects_root.parent / ".backlot" / "provider-approvals"
    ready = Event()
    monkeypatch.setattr(provider_approvals, "DEFAULT_TTL_SECONDS", 5)

    def wait_for_board(ticket):
        ready.set()
        time.sleep(0.02)

    # The injected executor uses the same ticket store as the board route.
    from lib.agent_mcp import LocalDirectorProviderExecutor

    executor = LocalDirectorProviderExecutor(bridge, store_dir=store_dir, sleep_fn=wait_for_board)

    original_executor_factory = agent_mcp.LocalDirectorProviderExecutor
    monkeypatch.setattr(agent_mcp, "LocalDirectorProviderExecutor", lambda _bridge: executor)
    with TestClient(server_mod.create_app()) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(bridge._execute_tool, selector.name, {"prompt": "Draft an opening."})
            assert ready.wait(timeout=3), (
                f"provider approval wait did not begin; result={future.result(timeout=1) if future.done() else 'still running'}"
            )
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                state = client.get(f"/api/project/{PROJECT_ID}/state").json()
                requests = state["provider_approvals"]["requests"]
                if requests:
                    break
                time.sleep(0.02)
            assert requests, state.get("provider_approvals")
            ticket = requests[0]
            assert ticket["provider"] == "openai"
            assert ticket["capability"] == "text_generation"
            assert ticket["stage"] == "idea"
            assert ticket["status"] == "pending"
            assert "payload" not in ticket
            assert provider.calls == 0

            wrong = client.post(
                f"/api/project/{PROJECT_ID}/provider-approvals/{ticket['request_id']}",
                json={"request_digest": "0" * 64, "decision": "approve"},
            )
            assert wrong.status_code == 409
            assert provider.calls == 0

            approved = client.post(
                f"/api/project/{PROJECT_ID}/provider-approvals/{ticket['request_id']}",
                json={"request_digest": ticket["request_digest"], "decision": "approve"},
            )
            assert approved.status_code == 200, approved.text
            result = future.result(timeout=5)
    monkeypatch.setattr(agent_mcp, "LocalDirectorProviderExecutor", original_executor_factory)

    assert result["success"] is True
    assert provider.calls == 1
