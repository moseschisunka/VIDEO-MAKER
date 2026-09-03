"""Phase 10 Backlot staging fixtures must obey the live approval contract."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

from lib.approval_contracts import read_approval_log
from lib import checkpoint as checkpoint_mod
from lib.checkpoint import init_project, read_checkpoint
from lib import project_pipeline
from scripts import backlot_screenshot_stage, backlot_simulate_run
from tests.contracts.test_phase0_contracts import sample_artifact


@pytest.mark.release_blocker
def test_backlot_screenshot_stage_builds_immutable_gated_fixtures(tmp_path: Path, monkeypatch):
    """The canonical visual-eval stage cannot regress to boolean approvals."""

    stage_root = tmp_path / "screenshot-stage"
    monkeypatch.setattr(backlot_screenshot_stage, "STAGE_DIR", stage_root)

    backlot_screenshot_stage.build_stage()

    expected = {
        "the-last-lighthouse": ("complete", 3),
        "signal-in-the-static": ("assets_live", 2),
        "the-slow-orchard": ("script_gate", 0),
        "paper-boats": ("early", 0),
    }
    for project_id, (state, approval_count) in expected.items():
        project = stage_root / project_id
        marker = json.loads((project / "project.json").read_text(encoding="utf-8"))
        run_id = uuid.UUID(str(marker["run_id"]))
        assert run_id.version == 4
        log = read_approval_log(project)
        assert len(log["records"]) == approval_count

        script = read_checkpoint(stage_root, project_id, "script")
        scene_plan = read_checkpoint(stage_root, project_id, "scene_plan")
        assets = read_checkpoint(stage_root, project_id, "assets")
        if state == "complete":
            assert script and script["status"] == "completed" and script["approval_record"]["decision"] == "approve"
            assert scene_plan and scene_plan["status"] == "completed" and scene_plan["approval_record"]["decision"] == "approve"
            assert assets and assets["status"] == "completed" and assets["approval_record"]["decision"] == "approve"
        elif state == "assets_live":
            assert script and script["status"] == "completed" and script["approval_record"]["decision"] == "approve"
            assert scene_plan and scene_plan["status"] == "completed" and scene_plan["approval_record"]["decision"] == "approve"
            assert assets and assets["status"] == "in_progress"
            assert "approval_record" not in assets
        elif state == "script_gate":
            assert script and script["status"] == "awaiting_human"
            assert "approval_record" not in script
            assert scene_plan is None
            assert assets is None
        else:
            assert script and script["status"] == "in_progress"
            assert scene_plan is None
            assert assets is None


@pytest.mark.release_blocker
def test_backlot_simulation_uses_immutable_gated_approvals(tmp_path: Path, monkeypatch):
    """The live-board demo must exercise the same approval authority as Backlot."""

    projects_root = tmp_path / "projects"
    monkeypatch.setattr(backlot_simulate_run, "PROJECTS_DIR", projects_root)
    monkeypatch.setattr(checkpoint_mod, "PROJECTS_DIR", projects_root)
    monkeypatch.setattr(
        sys,
        "argv",
        ["backlot_simulate_run.py", "--fast", "--project", "pr1018-simulation"],
    )

    assert backlot_simulate_run.main() == 0

    project = projects_root / "pr1018-simulation"
    log = read_approval_log(project)
    assert len(log["records"]) == 3
    for stage in ("script", "scene_plan", "assets"):
        checkpoint = read_checkpoint(projects_root, project.name, stage)
        assert checkpoint and checkpoint["status"] == "completed"
        assert checkpoint["human_approved"] is True
        assert checkpoint["approval_record"]["decision"] == "approve"


@pytest.mark.release_blocker
def test_quarantined_internal_demo_runner_uses_immutable_gate(tmp_path: Path, monkeypatch):
    """The explicitly marked legacy fixture must not bypass the live contract."""

    monkeypatch.setattr(project_pipeline, "PROJECTS_DIR", tmp_path)
    run_id = str(uuid.uuid4())
    init_project(
        "internal-demo-fixture",
        title="Internal demo fixture",
        pipeline_type="animated-explainer",
        run_id=run_id,
        pipeline_dir=tmp_path,
    )

    project_pipeline._write_internal_demo_checkpoint(
        "internal-demo-fixture",
        "script",
        {"script": sample_artifact("script")},
        pipeline_type="animated-explainer",
        run_id=run_id,
    )

    checkpoint = read_checkpoint(tmp_path, "internal-demo-fixture", "script")
    assert checkpoint and checkpoint["status"] == "completed"
    assert checkpoint["human_approved"] is True
    assert checkpoint["approval_record"]["approver_id"] == "internal-demo-fixture"
    assert checkpoint["approval_record"]["decision"] == "approve"
    assert len(read_approval_log(tmp_path / "internal-demo-fixture")["records"]) == 1
