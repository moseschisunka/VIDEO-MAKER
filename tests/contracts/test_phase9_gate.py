"""PR-9G release-candidate gate for approvals, QA, and final-review blocking."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lib.checkpoint import init_project
from lib.manifest_executor import ManifestExecutionError, submit_manifest_stage
from lib.pipeline_loader import load_pipeline_readonly
from lib.work_order import build_work_order, claim_work_order, write_work_order


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "12345678-1234-4234-8234-123456789abc"


def _identity_metadata() -> dict[str, str]:
    return {
        "project_id": "phase9-gate",
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
    }


def _compose_fixture(tmp_path: Path) -> Path:
    project = init_project(
        "phase9-gate",
        title="Phase 9 gate",
        pipeline_type="screen-demo",
        run_id=RUN_ID,
        pipeline_dir=tmp_path,
    )
    manifest = load_pipeline_readonly("screen-demo", defs_dir=ROOT / "pipeline_defs")
    order = build_work_order(
        project_id="phase9-gate",
        title="Phase 9 gate",
        topic_prompt="Verify final-review blocking.",
        target_duration_seconds=1,
        pipeline_type="screen-demo",
        manifest=manifest,
        manifest_path=ROOT / "pipeline_defs" / "screen-demo.yaml",
        selections={
            "playbook": "premium-minimalist",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "ffmpeg",
            "output_profile": "generic_hd",
            "aspect_ratio": "16:9",
            "source_mode": "synthetic_terminal",
        },
        run_id=RUN_ID,
    )
    write_work_order(project, order)
    claim_work_order(project, "gate-agent", lease_seconds=300)

    order = json.loads((project / "work_order.json").read_text(encoding="utf-8"))
    compose_index = next(item["order"] for item in order["stages"] if item["name"] == "compose")
    for item in order["stages"]:
        if item["order"] < compose_index:
            item.update({"status": "completed", "completed_at": "2026-09-02T08:00:00+00:00", "error": None})
        elif item["order"] == compose_index:
            item.update({"status": "ready", "completed_at": None, "error": None})
        else:
            item.update({"status": "pending", "completed_at": None, "error": None})
    order.update({
        "status": "running",
        "current_stage": "compose",
        "next_stage": "compose",
        "resume": {
            "last_successful_stage": "edit",
            "last_successful_checkpoint": "checkpoint_edit.json",
            "next_action": "start_stage:compose",
            "resume_from_stage": "compose",
        },
        "blocker": {"code": None, "message": None, "details": {}},
    })
    write_work_order(project, order)

    identity = _identity_metadata()
    (project / "artifacts" / "asset_manifest.json").write_text(
        json.dumps({"version": "1.0", "assets": [], "metadata": identity}),
        encoding="utf-8",
    )
    (project / "artifacts" / "edit_decisions.json").write_text(
        json.dumps({
            "version": "1.0",
            **identity,
            "render_runtime": "ffmpeg",
            "cuts": [{"id": "c", "source": "generated", "in_seconds": 0, "out_seconds": 1}],
        }),
        encoding="utf-8",
    )
    (project / "renders").mkdir(exist_ok=True)
    output = project / "renders" / "final.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return project


def _render_report_and_review(project: Path, status: str) -> dict[str, dict]:
    identity = _identity_metadata()
    report = {
        "version": "1.0",
        **identity,
        "render_runtime": "ffmpeg",
        "outputs": [{"path": "renders/final.mp4", "format": "mp4", "resolution": "320x240", "duration_seconds": 1}],
        "final_review_ref": "artifacts/final_review.json",
    }
    review = {
        "version": "1.0",
        **identity,
        "output_path": "renders/final.mp4",
        "status": status,
        "checks": {
            "technical_probe": {},
            "visual_spotcheck": {},
            "audio_spotcheck": {},
            "promise_preservation": {},
            "subtitle_check": {},
        },
    }
    return {"render_report": report, "final_review": review}


def test_phase9_gate_blocks_nonpass_final_review_before_persistence(tmp_path: Path) -> None:
    project = _compose_fixture(tmp_path)
    with pytest.raises(ManifestExecutionError, match="final_review.status='revise'"):
        submit_manifest_stage(
            project,
            "gate-agent",
            "compose",
            _render_report_and_review(project, "revise"),
        )
    assert not (project / "artifacts" / "render_report.json").exists()
    assert not (project / "artifacts" / "final_review.json").exists()


def test_phase9_gate_keeps_global_lock_and_requires_seeded_evidence() -> None:
    tracker = (ROOT / "docs" / "production-readiness" / "PROGRESS_TRACKER.md").read_text(encoding="utf-8")
    corpus = json.loads((ROOT / "tests" / "eval" / "qa_fault_corpus.json").read_text(encoding="utf-8"))
    assert "Current phase | Phase 10" in tracker
    assert "| `PR-9G` | Phase 9 gate |" in tracker
    assert "| `PR-9G` | Phase 9 gate | `PR-900`–`PR-909` | `COMPLETE`" in tracker
    assert "Production decision | Not eligible" in tracker
    assert "PR-11G" in tracker
    assert corpus["provider_access"] == "offline_only"
    assert len(corpus["cases"]) >= 12
