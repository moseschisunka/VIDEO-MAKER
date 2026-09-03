"""Phase 9 approval/state contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone

import pytest

from lib.approval_contracts import (
    ApprovalValidationError,
    artifact_digest,
    build_approval_record,
    read_approval_log,
    validate_approval_record,
)
from lib.checkpoint import init_project, read_checkpoint, write_checkpoint
from lib.pipeline_loader import load_pipeline_readonly
from lib.work_order import (
    WorkOrderConflictError,
    WorkOrderStateError,
    build_work_order,
    claim_work_order,
    decide_human_gate,
    write_work_order,
)
from lib.manifest_executor import submit_manifest_stage


PROJECT_ID = "phase9-approval"
RUN_ID = "12345678-1234-4234-8234-123456789abc"
PIPELINE = "screen-demo"
T0 = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)


def _brief() -> dict:
    return {
        "version": "1.0",
        "title": "Phase 9",
        "hook": "A clear approval contract.",
        "key_points": ["Pause", "Approve"],
        "tone": "clear",
        "style": "premium-minimalist",
        "target_platform": "youtube",
        "target_duration_seconds": 30,
    }


def _project(tmp_path: Path) -> Path:
    project = init_project(
        PROJECT_ID,
        title="Phase 9",
        pipeline_type=PIPELINE,
        run_id=RUN_ID,
        pipeline_dir=tmp_path,
    )
    manifest = load_pipeline_readonly(PIPELINE, defs_dir=Path(__file__).resolve().parents[2] / "pipeline_defs")
    order = build_work_order(
        project_id=PROJECT_ID,
        title="Phase 9",
        topic_prompt="Approval",
        target_duration_seconds=30,
        pipeline_type=PIPELINE,
        manifest=manifest,
        manifest_path=Path(__file__).resolve().parents[2] / "pipeline_defs" / "screen-demo.yaml",
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


def _awaiting(project_root: Path) -> None:
    write_checkpoint(
        project_root,
        PROJECT_ID,
        "idea",
        "awaiting_human",
        {"brief": _brief()},
        pipeline_type=PIPELINE,
        run_id=RUN_ID,
        human_approval_required=True,
    )


def test_record_is_self_digesting_and_cannot_be_edited() -> None:
    record = build_approval_record(
        project_id=PROJECT_ID,
        pipeline_type=PIPELINE,
        run_id=RUN_ID,
        attempt=1,
        stage="idea",
        artifact_ref="checkpoint_idea.json",
        artifact_digest_value="a" * 64,
        artifact_version="2026-09-02T08:00:00+00:00",
        approver_id="reviewer-1",
        decision="approve",
        recorded_at=T0,
    )
    validate_approval_record(record)
    record["notes"] = "tampered"
    with pytest.raises(ApprovalValidationError, match="digest"):
        validate_approval_record(record)


def test_gated_agent_submission_pauses_and_boolean_cannot_advance(tmp_path: Path) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", now=T0)
    _awaiting(tmp_path)
    cp = read_checkpoint(tmp_path, PROJECT_ID, "idea")
    assert cp and cp["status"] == "awaiting_human"
    cp["human_approved"] = True
    (project / "checkpoint_idea.json").write_text(json.dumps(cp), encoding="utf-8")
    with pytest.raises(WorkOrderStateError, match="checkpoint failed validation"):
        # A mutable compatibility bit is not a valid human transition.
        from lib.work_order import advance_work_order

        advance_work_order(project, "agent-a", "idea", now=T0)


def test_manifest_stage_completed_request_is_normalized_to_pause(tmp_path: Path) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", now=T0)
    result = submit_manifest_stage(
        project,
        "agent-a",
        "idea",
        {"brief": _brief(), "decision_log": {
            "version": "1.0",
            "project_id": PROJECT_ID,
            "pipeline_type": PIPELINE,
            "run_id": RUN_ID,
            "decisions": [],
        }},
        status="completed",
        human_approved=True,
        now=T0,
    )
    assert result["status"] == "awaiting_human"
    assert result["work_order"]["status"] == "awaiting_approval"


def test_approve_binds_digest_archives_and_advances_once(tmp_path: Path) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", now=T0)
    _awaiting(tmp_path)
    result = decide_human_gate(
        project,
        "idea",
        approver_id="reviewer-1",
        decision="approve",
        notes="Looks correct.",
        now=T0,
    )
    assert result["transition"] == "advanced"
    assert result["work_order"]["next_stage"] == "script"
    cp = read_checkpoint(tmp_path, PROJECT_ID, "idea")
    assert cp and cp["status"] == "completed"
    assert cp["human_approved"] is True
    assert cp["approval_record"]["decision"] == "approve"
    assert cp["approval_record"]["artifact_digest"] == artifact_digest(cp["artifacts"])
    log = read_approval_log(project)
    assert len(log["records"]) == 1
    repeated = decide_human_gate(
        project,
        "idea",
        approver_id="reviewer-1",
        decision="approve",
        now=T0,
    )
    assert repeated["idempotent"] is True
    assert len(read_approval_log(project)["records"]) == 1


def test_revise_reopens_stage_without_deleting_history(tmp_path: Path) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", now=T0)
    _awaiting(tmp_path)
    result = decide_human_gate(
        project,
        "idea",
        approver_id="reviewer-1",
        decision="revise",
        notes="Clarify the hook.",
        now=T0,
    )
    assert result["transition"] == "revising"
    assert result["work_order"]["status"] == "revising"
    assert result["work_order"]["next_stage"] == "idea"
    assert len(read_approval_log(project)["records"]) == 1
    assert read_checkpoint(tmp_path, PROJECT_ID, "idea")["status"] == "awaiting_human"


def test_approve_without_live_worker_queues_the_exact_next_stage(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _awaiting(tmp_path)
    result = decide_human_gate(
        project,
        "idea",
        approver_id="reviewer-1",
        decision="approve",
        now=T0,
    )
    assert result["transition"] == "approved_queued"
    assert result["work_order"]["status"] == "queued"
    assert result["work_order"]["next_stage"] == "script"
    assert result["work_order"]["stages"][0]["status"] == "completed"


def test_double_approval_is_serialized_and_idempotent(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _awaiting(tmp_path)

    def approve() -> dict:
        return decide_human_gate(
            project,
            "idea",
            approver_id="reviewer-1",
            decision="approve",
            now=T0,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _unused: approve(), (0, 1)))

    assert len(read_approval_log(project)["records"]) == 1
    assert {item["transition"] for item in results} <= {
        "advanced", "already_advanced", "already_completed", "approved_queued"
    }
    assert any(item["idempotent"] for item in results)


def test_revision_quarantines_downstream_outputs_without_deleting_history(tmp_path: Path) -> None:
    project = _project(tmp_path)
    order = json.loads((project / "work_order.json").read_text(encoding="utf-8"))
    for record in order["stages"]:
        if record["order"] < 5:
            record.update({
                "status": "completed",
                "checkpoint": f"checkpoint_{record['name']}.json",
                "completed_at": T0.isoformat(),
                "error": None,
            })
        elif record["name"] == "compose":
            record.update({"status": "awaiting_approval", "checkpoint": "checkpoint_compose.json"})
    order.update({
        "status": "awaiting_approval",
        "current_stage": "compose",
        "next_stage": "compose",
        "resume": {
            "last_successful_stage": "edit",
            "last_successful_checkpoint": "checkpoint_edit.json",
            "next_action": "await_approval:compose",
            "resume_from_stage": "compose",
        },
        "blocker": {"code": "human_approval_required", "message": "compose", "details": {}},
    })
    (project / "work_order.json").write_text(json.dumps(order), encoding="utf-8")
    write_checkpoint(
        project.parent,
        PROJECT_ID,
        "compose",
        "awaiting_human",
        {"render_report": {
            "version": "1.0",
            "outputs": [{"path": "renders/final.mp4", "format": "mp4", "resolution": "320x240", "duration_seconds": 2}],
        }},
        pipeline_type=PIPELINE,
        run_id=RUN_ID,
        human_approval_required=True,
        timestamp=T0,
    )
    publish_artifact = project / "artifacts" / "publish_log.json"
    publish_artifact.write_text(json.dumps({"version": "1.0", "entries": []}), encoding="utf-8")
    stale_publish_checkpoint = project / "checkpoint_publish.json"
    stale_publish_checkpoint.write_text(json.dumps({
        "version": "1.0",
        "project_id": PROJECT_ID,
        "pipeline_type": PIPELINE,
        "run_id": RUN_ID,
        "stage": "publish",
        "status": "completed",
        "timestamp": T0.isoformat(),
        "checkpoint_policy": "guided",
        "human_approval_required": True,
        "human_approved": False,
        "artifacts": {"publish_log": {"version": "1.0", "entries": []}},
        "producer_stage": "publish",
        "producer_tool": "test",
    }), encoding="utf-8")

    result = decide_human_gate(
        project,
        "compose",
        approver_id="reviewer-1",
        decision="revise",
        notes="Rework the edit before publishing.",
        now=T0,
    )

    assert result["transition"] == "revising"
    assert not publish_artifact.exists()
    assert not stale_publish_checkpoint.exists()
    assert list((project / "artifacts" / "history").glob("invalidated_*/publish_log.json"))
    assert list((project / "history").glob("checkpoint_publish_*_invalidated.json"))
    updated = json.loads((project / "work_order.json").read_text(encoding="utf-8"))
    publish_stage = next(item for item in updated["stages"] if item["name"] == "publish")
    assert publish_stage["status"] == "pending"
    assert result["work_order"]["blocker"]["details"]["invalidated_paths"]


def test_approval_cannot_claim_another_live_worker_lease(tmp_path: Path) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", now=T0)
    _awaiting(tmp_path)
    with pytest.raises(WorkOrderConflictError, match="live lease"):
        decide_human_gate(
            project,
            "idea",
            approver_id="reviewer-1",
            decision="approve",
            agent_id="agent-b",
            now=T0,
        )
