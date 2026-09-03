"""PR-108 claim, checkpoint advance, and resume contracts."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.checkpoint import init_project, write_checkpoint
from lib.pipeline_loader import load_pipeline_readonly
from lib.work_order import (
    WorkOrderConflictError,
    WorkOrderStateError,
    advance_work_order,
    build_work_order,
    claim_work_order,
    decide_human_gate,
    heartbeat_work_order,
    next_stage_from_work_order,
    release_work_order,
    resume_work_order,
    write_work_order,
)
from backlot import server as server_mod


PROJECT_ID = "claim-resume-demo"
PIPELINE = "screen-demo"
RUN_ID = "12345678-1234-4234-8234-123456789abc"
PIPELINE_DIR = Path(__file__).resolve().parents[2] / "pipeline_defs"
T0 = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)


def _brief() -> dict:
    return {
        "version": "1.0",
        "title": "Install walkthrough",
        "hook": "Watch the install complete without guessing.",
        "key_points": ["Open the terminal", "Run the command"],
        "tone": "clear",
        "style": "premium-minimalist",
        "target_platform": "youtube",
        "target_duration_seconds": 30,
    }


def _project(tmp_path: Path) -> Path:
    project = tmp_path / PROJECT_ID
    project.mkdir()
    init_project(
        PROJECT_ID,
        title="Install walkthrough",
        pipeline_type=PIPELINE,
        run_id=RUN_ID,
        pipeline_dir=tmp_path,
    )
    manifest = load_pipeline_readonly(PIPELINE, defs_dir=PIPELINE_DIR)
    order = build_work_order(
        project_id=PROJECT_ID,
        title="Install walkthrough",
        topic_prompt="Show the install flow.",
        target_duration_seconds=30,
        pipeline_type=PIPELINE,
        manifest=manifest,
        manifest_path=PIPELINE_DIR / "screen-demo.yaml",
        selections={
            "playbook": "premium-minimalist",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "remotion",
            "output_profile": "youtube_landscape",
            "aspect_ratio": "16:9",
            "source_mode": "synthetic_terminal",
        },
        run_id=RUN_ID,
    )
    write_work_order(project, order)
    return project


def _awaiting_idea_checkpoint(tmp_path: Path) -> None:
    write_checkpoint(
        tmp_path,
        PROJECT_ID,
        "idea",
        "awaiting_human",
        {
            "brief": _brief(),
            "decision_log": {
                "version": "1.0",
                "project_id": PROJECT_ID,
                "pipeline_type": PIPELINE,
                "run_id": RUN_ID,
                "decisions": [],
            },
        },
        pipeline_type=PIPELINE,
        run_id=RUN_ID,
        human_approval_required=True,
        human_approved=False,
    )


def test_only_one_live_agent_can_claim_and_heartbeat(tmp_path: Path) -> None:
    _project(tmp_path)

    claimed = claim_work_order(tmp_path / PROJECT_ID, "agent-a", lease_seconds=60, now=T0)
    assert claimed["status"] == "running"
    assert claimed["claim"]["claimed_by"] == "agent-a"
    assert claimed["next_stage"] == "idea"
    assert claimed["claim"]["lease_version"] == 1

    with pytest.raises(WorkOrderConflictError, match="another live agent"):
        claim_work_order(tmp_path / PROJECT_ID, "agent-b", lease_seconds=60, now=T0)

    renewed = heartbeat_work_order(tmp_path / PROJECT_ID, "agent-a", lease_seconds=120, now=T0)
    assert renewed["claim"]["claimed_by"] == "agent-a"
    assert renewed["claim"]["lease_version"] == 1


def test_live_claim_renewal_skips_redundant_run_record_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", lease_seconds=60, now=T0)

    from lib import run_record

    sync_calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        run_record,
        "sync_run_record",
        lambda *args, **kwargs: sync_calls.append((args, kwargs)),
    )

    renewed = claim_work_order(
        project,
        "agent-a",
        lease_seconds=120,
        now=T0.replace(second=30),
    )

    assert renewed["claim"]["claimed_by"] == "agent-a"
    assert renewed["claim"]["lease_version"] == 1
    assert sync_calls == []
    # Skipping the redundant write must not make the existing run ledger
    # unreadable or alter its immutable execution identity.
    record = run_record.read_run_record(project, RUN_ID)
    assert record["project_id"] == PROJECT_ID
    assert record["run_id"] == RUN_ID


def test_expired_lease_can_be_reclaimed_and_increments_version(tmp_path: Path) -> None:
    _project(tmp_path)
    claim_work_order(tmp_path / PROJECT_ID, "agent-a", lease_seconds=1, now=T0)

    reclaimed = resume_work_order(
        tmp_path / PROJECT_ID,
        "agent-b",
        lease_seconds=60,
        now=T0.replace(second=2),
    )

    assert reclaimed["claim"]["claimed_by"] == "agent-b"
    assert reclaimed["claim"]["lease_version"] == 2
    assert reclaimed["next_stage"] == "idea"


def test_advance_requires_current_manifest_stage_and_human_gate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", lease_seconds=60, now=T0)
    _awaiting_idea_checkpoint(tmp_path)

    with pytest.raises(WorkOrderStateError, match="manifest-derived next stage"):
        advance_work_order(project, "agent-a", "script", now=T0)

    waiting = advance_work_order(project, "agent-a", "idea", now=T0)
    assert waiting["status"] == "awaiting_approval"
    assert waiting["next_stage"] == "idea"
    assert waiting["stages"][0]["status"] == "awaiting_approval"
    assert waiting["blocker"]["code"] == "human_approval_required"

    advanced = decide_human_gate(
        project,
        "idea",
        approver_id="reviewer-1",
        decision="approve",
        now=T0,
    )["work_order"]
    assert advanced["status"] == "running"
    assert advanced["stages"][0]["status"] == "completed"
    assert advanced["current_stage"] == "script"
    assert advanced["next_stage"] == "script"
    assert advanced["resume"]["last_successful_stage"] == "idea"
    assert advanced["approvals"][0]["status"] == "approved"


def test_failed_checkpoint_is_resumable_at_the_same_stage(tmp_path: Path) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", lease_seconds=60, now=T0)
    # Complete the first stage through an approved checkpoint.
    _awaiting_idea_checkpoint(tmp_path)
    decide_human_gate(
        project,
        "idea",
        approver_id="reviewer-1",
        decision="approve",
        now=T0,
    )

    write_checkpoint(
        tmp_path,
        PROJECT_ID,
        "script",
        "failed",
        {},
        pipeline_type=PIPELINE,
        run_id=RUN_ID,
        error="transcriber unavailable",
    )
    failed = advance_work_order(project, "agent-a", "script", now=T0)
    assert failed["status"] == "failed"
    assert failed["next_stage"] == "script"
    assert failed["resume"]["next_action"] == "retry_stage:script"
    assert failed["blocker"]["code"] == "stage_failed"

    released = release_work_order(project, "agent-a", now=T0)
    assert released["status"] == "queued"
    resumed = resume_work_order(project, "agent-b", lease_seconds=60, now=T0)
    assert resumed["status"] == "running"
    assert resumed["next_stage"] == "script"


def test_manifest_derived_pointer_never_skips_stage(tmp_path: Path) -> None:
    project = _project(tmp_path)
    order_path = project / "work_order.json"
    order = json.loads(order_path.read_text(encoding="utf-8"))
    order["next_stage"] = "script"
    order_path.write_text(json.dumps(order), encoding="utf-8")

    with pytest.raises(Exception, match="next_stage"):
        resume_work_order(project, "agent-a", now=T0)

    assert next_stage_from_work_order(order) == "idea"


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", str(projects.resolve()).lower())

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as client:
        yield client, projects


def test_work_order_api_enforces_claim_gate_and_manifest_resume(api_client) -> None:
    client, projects = api_client
    created = client.post(
        "/api/project/create",
        json={
            "title": "API claim flow",
            "topic_prompt": "Show the install flow.",
            "pipeline_type": PIPELINE,
            "playbook": "premium-minimalist",
            "voice_provider": "edge_tts",
            "render_runtime": "remotion",
            "output_profile": "youtube_landscape",
            "source_mode": "synthetic_terminal",
        },
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]
    project = projects / project_id
    run_id = created.json()["work_order"]["run_id"]

    claimed = client.post(
        f"/api/project/{project_id}/claim",
        json={"agent_id": "api-agent-a", "lease_seconds": 60},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["next_stage"] == "idea"
    conflict = client.post(
        f"/api/project/{project_id}/claim",
        json={"agent_id": "api-agent-b", "lease_seconds": 60},
    )
    assert conflict.status_code == 409

    write_checkpoint(
        projects,
        project_id,
        "idea",
        "awaiting_human",
        {"brief": _brief()},
        pipeline_type=PIPELINE,
        run_id=run_id,
        human_approval_required=True,
    )
    waiting = client.post(
        f"/api/project/{project_id}/advance",
        json={"agent_id": "api-agent-a", "stage": "idea"},
    )
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["work_order"]["status"] == "awaiting_approval"

    advanced = client.post(
        f"/api/project/{project_id}/approve",
        json={"agent_id": "api-agent-a", "stage": "idea", "approver_id": "reviewer-1"},
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["work_order"]["next_stage"] == "script"

    heartbeat = client.post(
        f"/api/project/{project_id}/heartbeat",
        json={"agent_id": "api-agent-a", "lease_seconds": 60},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    released = client.post(
        f"/api/project/{project_id}/release",
        json={"agent_id": "api-agent-a"},
    )
    assert released.status_code == 200, released.text
    resumed = client.post(
        f"/api/project/{project_id}/resume",
        json={"agent_id": "api-agent-b", "lease_seconds": 60},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["next_stage"] == "script"
