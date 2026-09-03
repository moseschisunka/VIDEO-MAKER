"""PR-205 contracts for current-run freshness and stale-output rejection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lib import output_promotion
from lib.output_promotion import OutputPromotionError, candidate_path, promote_candidate
from lib.run_record import read_run_record
from lib.work_order import (
    cancel_work_order,
    claim_work_order,
    read_work_order,
    restart_work_order,
    resume_work_order,
)
from tests.contracts.test_phase1_claim_resume import _project


def _fake_probe(_path: Path) -> dict:
    return {
        "valid_container": True,
        "duration_seconds": 1.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
        "stream_count": 2,
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "file_size_bytes": 4,
    }


def test_candidate_predating_run_is_rejected_without_replacing_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(output_promotion, "probe_media", _fake_probe)
    candidate = candidate_path(tmp_path / "candidates", tmp_path / "renders" / "final.mp4")
    final = tmp_path / "renders" / "final.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"known-good")
    candidate.write_bytes(b"stale")

    run_started = datetime.now(timezone.utc) + timedelta(seconds=30)
    with pytest.raises(OutputPromotionError, match="predates the current run"):
        promote_candidate(candidate, final, run_started_at=run_started)

    assert final.read_bytes() == b"known-good"
    assert candidate.read_bytes() == b"stale"


def test_fresh_candidate_records_hash_and_timestamp_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(output_promotion, "probe_media", _fake_probe)
    candidate = candidate_path(tmp_path / "candidates", tmp_path / "renders" / "final.mp4")
    final = tmp_path / "renders" / "final.mp4"
    candidate.write_bytes(b"fresh")
    run_started = datetime.now(timezone.utc) - timedelta(seconds=1)

    promoted = promote_candidate(candidate, final, run_started_at=run_started)

    assert final.read_bytes() == b"fresh"
    assert len(promoted["sha256"]) == 64
    assert promoted["freshness"]["new_hash_recorded"] is True
    assert promoted["freshness"]["run_started_at"]
    assert promoted["freshness"]["candidate_mtime_ns"] > 0
    assert promoted["freshness"]["output_mtime_ns"] >= promoted["freshness"]["candidate_mtime_ns"]


def test_cancel_and_restart_preserve_checkpoint_boundary_and_run_evidence(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    claim_work_order(project, "agent-a", lease_seconds=60)

    cancelled = cancel_work_order(project, "agent-a", reason="worker shutdown")
    assert cancelled["status"] == "cancelled"
    assert cancelled["next_stage"] == "idea"
    assert cancelled["stages"][0]["status"] == "cancelled"
    assert cancelled["claim"]["claimed_by"] is None
    assert cancelled["resume"]["next_action"] == "restart_required"

    restarted = restart_work_order(project, "agent-b", reason="operator retry")
    assert restarted["status"] == "queued"
    assert restarted["next_stage"] == "idea"
    assert restarted["stages"][0]["status"] == "ready"
    assert restarted["metadata"]["restart_count"] == 1

    record = read_run_record(project, restarted["run_id"])
    assert record["status"] == "queued"
    assert record["finished_at"] is None
    assert record["metadata"]["restart_count"] == 1


def test_expired_worker_lease_is_reclaimable_and_records_recovery(tmp_path: Path) -> None:
    project = _project(tmp_path)
    from datetime import datetime, timezone

    start = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    claim_work_order(project, "dead-agent", lease_seconds=1, now=start)
    resumed = resume_work_order(
        project,
        "replacement-agent",
        lease_seconds=60,
        now=start + timedelta(seconds=2),
    )

    assert resumed["status"] == "running"
    assert resumed["claim"]["claimed_by"] == "replacement-agent"
    assert resumed["metadata"]["last_recovery_reason"] == "worker_lease_expired"
    assert resumed["metadata"]["last_recovered_from_agent"] == "dead-agent"
    record = read_run_record(project, resumed["run_id"])
    assert record["metadata"]["attempt_started_at"] == resumed["metadata"]["attempt_started_at"]
    assert record["metadata"]["last_recovery_reason"] == "worker_lease_expired"
