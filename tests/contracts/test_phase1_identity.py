"""PR-107 cross-artifact project identity contracts."""

from __future__ import annotations

import json
from pathlib import Path

from lib.project_identity import (
    ProjectIdentityValidationError,
    assert_project_identity,
    validate_project_identity,
)
from lib.events import emit_event, read_events
from lib.checkpoint import init_project, read_checkpoint, write_checkpoint


PROJECT_ID = "identity-demo"
PIPELINE = "screen-demo"
RUN_ID = "12345678-1234-4234-8234-123456789abc"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _seed_project(tmp_path: Path) -> Path:
    project = tmp_path / PROJECT_ID
    (project / "artifacts").mkdir(parents=True)
    _write(project / "project.json", {
        "version": "1.0",
        "project_id": PROJECT_ID,
        "pipeline_type": PIPELINE,
        "run_id": RUN_ID,
    })
    _write(project / "work_order.json", {
        "project_id": PROJECT_ID,
        "pipeline_type": PIPELINE,
        "run_id": RUN_ID,
    })
    for name in ("project_config", "proposal_packet", "decision_log"):
        _write(project / "artifacts" / f"{name}.json", {
            "project_id": PROJECT_ID,
            "pipeline_type": PIPELINE,
            "run_id": RUN_ID,
        })
    _write(project / "artifacts" / "render_report.json", {
        "project_id": PROJECT_ID,
        "pipeline_type": PIPELINE,
        "run_id": RUN_ID,
    })
    _write(project / "checkpoint_compose.json", {
        "project_id": PROJECT_ID,
        "pipeline_type": PIPELINE,
        "run_id": RUN_ID,
    })
    (project / "events.jsonl").write_text(
        json.dumps({
            "event": "created",
            "project_id": PROJECT_ID,
            "pipeline_type": PIPELINE,
            "run_id": RUN_ID,
        }) + "\n",
        encoding="utf-8",
    )
    return project


def test_identity_report_accepts_matching_artifacts(tmp_path: Path) -> None:
    report = validate_project_identity(_seed_project(tmp_path), strict=True)

    assert report["valid"] is True
    assert report["expected"] == {
        "project_id": PROJECT_ID,
        "pipeline_type": PIPELINE,
        "run_id": RUN_ID,
    }
    assert report["issues"] == []


def test_identity_report_detects_pipeline_mismatch(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    render_path = project / "artifacts" / "render_report.json"
    render = json.loads(render_path.read_text(encoding="utf-8"))
    render["pipeline_type"] = "talking-head"
    render_path.write_text(json.dumps(render), encoding="utf-8")

    report = validate_project_identity(project)

    assert report["valid"] is False
    assert any(
        issue["code"] == "identity_mismatch"
        and issue["source"] == "render_report"
        and issue["field"] == "pipeline_type"
        for issue in report["issues"]
    )


def test_identity_report_detects_stale_nested_proposal_pipeline(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    proposal_path = project / "artifacts" / "proposal_packet.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["production_plan"] = {"pipeline": "animated-explainer"}
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")

    report = validate_project_identity(project)

    assert report["valid"] is False
    assert any(
        issue["field"] == "production_plan.pipeline"
        and issue["code"] == "identity_mismatch"
        for issue in report["issues"]
    )


def test_identity_report_checks_inline_checkpoint_artifacts(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    checkpoint_path = project / "checkpoint_compose.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["artifacts"] = {
        "render_report": {
            "project_id": PROJECT_ID,
            "pipeline_type": PIPELINE,
            "run_id": "12345678-1234-4234-8234-123456789abd",
        }
    }
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    report = validate_project_identity(project)

    assert report["valid"] is False
    assert any(
        issue["source"].endswith("artifacts.render_report")
        and issue["field"] == "run_id"
        and issue["code"] == "identity_mismatch"
        for issue in report["issues"]
    )


def test_identity_report_rejects_non_uuid4_run_identity(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    work_order_path = project / "work_order.json"
    work_order = json.loads(work_order_path.read_text(encoding="utf-8"))
    work_order["run_id"] = "not-a-uuid"
    work_order_path.write_text(json.dumps(work_order), encoding="utf-8")

    report = validate_project_identity(project)

    assert report["valid"] is False
    assert any(issue["code"] == "run_identity_invalid" for issue in report["issues"])


def test_identity_report_detects_project_and_run_mismatch(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    event_path = project / "events.jsonl"
    event_path.write_text(
        json.dumps({
            "event": "finish",
            "project_id": "other-project",
            "pipeline_type": PIPELINE,
            "run_id": "12345678-1234-4234-8234-123456789abd",
        }) + "\n",
        encoding="utf-8",
    )

    report = validate_project_identity(project)

    assert report["valid"] is False
    assert sum(issue["code"] == "identity_mismatch" for issue in report["issues"]) >= 2


def test_malformed_event_is_a_warning_when_other_identity_sources_match(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    with open(project / "events.jsonl", "a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")

    report = validate_project_identity(project)

    assert report["valid"] is True
    assert any(warning["code"] == "malformed_event" for warning in report["warnings"])


def test_assert_identity_raises_with_actionable_mismatch(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    marker_path = project / "project.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["run_id"] = "12345678-1234-4234-8234-123456789abd"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    try:
        assert_project_identity(project)
    except ProjectIdentityValidationError as exc:
        assert "marker" in str(exc)
        assert "run_id" in str(exc)
    else:  # pragma: no cover - assertion keeps the contract explicit
        raise AssertionError("mismatched identity should raise")


def test_event_writer_enriches_legacy_tool_payload_from_durable_identity(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    (project / "events.jsonl").unlink()

    emit_event(project, {"event": "tool_start", "tool": "fixture"})

    event = read_events(project)[0]
    assert event["project_id"] == PROJECT_ID
    assert event["pipeline_type"] == PIPELINE
    assert event["run_id"] == RUN_ID


def test_checkpoint_writer_backfills_run_identity_from_marker(tmp_path: Path) -> None:
    init_project(
        PROJECT_ID,
        title="Identity demo",
        pipeline_type=PIPELINE,
        run_id=RUN_ID,
        pipeline_dir=tmp_path,
    )

    write_checkpoint(tmp_path, PROJECT_ID, "idea", "in_progress", {})

    checkpoint = read_checkpoint(tmp_path, PROJECT_ID, "idea")
    assert checkpoint is not None
    assert checkpoint["pipeline_type"] == PIPELINE
    assert checkpoint["run_id"] == RUN_ID
