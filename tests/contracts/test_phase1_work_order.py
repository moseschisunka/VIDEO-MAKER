"""PR-104 durable manifest work-order contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from lib.pipeline_loader import load_pipeline_readonly
from lib.work_order import (
    WorkOrderValidationError,
    build_work_order,
    manifest_digest,
    read_work_order,
    validate_work_order,
    write_work_order,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = PROJECT_ROOT / "pipeline_defs"


def _build() -> dict:
    manifest = load_pipeline_readonly("screen-demo", defs_dir=PIPELINE_DIR)
    return build_work_order(
        project_id="work-order-test",
        title="Work order contract",
        topic_prompt="Prove the durable execution envelope.",
        target_duration_seconds=30,
        pipeline_type="screen-demo",
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
    )


def test_initial_work_order_is_manifest_derived_and_queued() -> None:
    order = _build()
    manifest = load_pipeline_readonly("screen-demo", defs_dir=PIPELINE_DIR)

    validate_work_order(
        order,
        manifest=manifest,
        manifest_path=PIPELINE_DIR / "screen-demo.yaml",
    )
    assert order["version"] == "1.0"
    assert order["status"] == "queued"
    assert len(order["run_id"]) == 36
    assert order["attempt"] == 1
    assert order["current_stage"] == "idea"
    assert order["next_stage"] == "idea"
    assert order["resume"]["next_action"] == "start_stage:idea"
    assert order["approvals"][0]["status"] == "pending"
    assert order["claim"]["claimed_by"] is None
    assert order["blocker"]["code"] is None
    assert order["manifest_hash"] == manifest_digest(PIPELINE_DIR / "screen-demo.yaml")
    assert [stage["name"] for stage in order["stages"]] == [
        stage["name"] for stage in manifest["stages"]
    ]


def test_work_order_round_trips_atomically(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    order = _build()

    path = write_work_order(project_dir, order)

    assert path == project_dir / "work_order.json"
    assert json.loads(path.read_text(encoding="utf-8")) == order
    assert read_work_order(
        project_dir,
        manifest=load_pipeline_readonly("screen-demo", defs_dir=PIPELINE_DIR),
        manifest_path=PIPELINE_DIR / "screen-demo.yaml",
    ) == order
    assert not list(project_dir.glob(".work_order.json.*.tmp"))


def test_work_order_rejects_manifest_identity_or_hash_drift() -> None:
    order = _build()
    manifest = load_pipeline_readonly("screen-demo", defs_dir=PIPELINE_DIR)

    changed = copy.deepcopy(order)
    changed["pipeline_type"] = "talking-head"
    with pytest.raises(WorkOrderValidationError, match="pipeline identity"):
        validate_work_order(changed, manifest=manifest)

    changed = copy.deepcopy(order)
    changed["manifest_hash"] = "0" * 64
    with pytest.raises(WorkOrderValidationError, match="manifest_hash"):
        validate_work_order(
            changed,
            manifest=manifest,
            manifest_path=PIPELINE_DIR / "screen-demo.yaml",
        )


def test_work_order_rejects_skipped_stage() -> None:
    order = _build()
    changed = copy.deepcopy(order)
    changed["stages"][2]["status"] = "completed"
    with pytest.raises(WorkOrderValidationError, match="skipped earlier"):
        validate_work_order(changed)
