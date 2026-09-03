"""PR-201 contracts: durable run identity and result provenance."""

from __future__ import annotations

import json
from pathlib import Path

from lib.checkpoint import read_checkpoint, init_project
from lib.events import read_events
from lib.manifest_executor import submit_manifest_stage
from lib.pipeline_loader import load_pipeline_readonly
from lib.run_record import read_run_record, run_record_path
from lib.work_order import build_work_order, claim_work_order, write_work_order
from tools.base_tool import BaseTool, ToolResult


RUN_ID = "12345678-1234-4234-8234-123456789abc"


def _project(tmp_path: Path, project_id: str = "run-record-project") -> Path:
    project = init_project(
        project_id,
        title="Run record fixture",
        pipeline_type="screen-demo",
        run_id=RUN_ID,
        pipeline_dir=tmp_path,
    )
    manifest_path = Path(__file__).resolve().parents[2] / "pipeline_defs" / "screen-demo.yaml"
    manifest = load_pipeline_readonly("screen-demo", defs_dir=manifest_path.parent)
    order = build_work_order(
        project_id=project_id,
        title="Run record fixture",
        topic_prompt="Prove durable provenance.",
        target_duration_seconds=10,
        pipeline_type="screen-demo",
        manifest=manifest,
        manifest_path=manifest_path,
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


def test_work_order_creates_and_syncs_run_record(tmp_path: Path) -> None:
    project = _project(tmp_path)
    record_path = run_record_path(project, RUN_ID)
    assert record_path == project / "runs" / RUN_ID / "run.json"
    record = read_run_record(project, RUN_ID)
    assert record["project_id"] == project.name
    assert record["pipeline_type"] == "screen-demo"
    assert record["attempt"] == 1
    assert record["work_order_ref"] == "work_order.json"
    assert record["status"] == "queued"

    claim_work_order(project, "provenance-agent", lease_seconds=60)
    claimed = read_run_record(project, RUN_ID)
    assert claimed["status"] == "running"
    assert claimed["current_stage"] == "idea"
    assert claimed["next_stage"] == "idea"
    assert claimed["started_at"]


def test_manifest_stage_records_checkpoint_and_artifact_provenance(tmp_path: Path) -> None:
    project = _project(tmp_path, "manifest-provenance-project")
    claim_work_order(project, "manifest-agent", lease_seconds=60)
    result = submit_manifest_stage(
        project,
        "manifest-agent",
        "idea",
        {
            "brief": {
                "version": "1.0",
                "title": "Provenance demo",
                "hook": "Every result is traceable.",
                "key_points": ["Run identity", "Stage identity"],
                "tone": "clear",
                "style": "premium-minimalist",
                "target_platform": "youtube",
                "target_duration_seconds": 10,
            },
            "decision_log": {
                "version": "1.0",
                "project_id": project.name,
                "pipeline_type": "screen-demo",
                "run_id": RUN_ID,
                "decisions": [],
            },
        },
        status="awaiting_human",
        producing_tool="idea-director",
    )

    checkpoint = read_checkpoint(tmp_path, project.name, "idea")
    assert checkpoint is not None
    assert checkpoint["attempt"] == 1
    assert checkpoint["producer_stage"] == "idea"
    assert checkpoint["producer_tool"] == "idea-director"

    record = read_run_record(project, RUN_ID)
    for key in ("artifact:brief", "artifact:decision_log", "checkpoint:idea"):
        entry = record["artifacts"][key]
        assert entry["project_id"] == project.name
        assert entry["pipeline_type"] == "screen-demo"
        assert entry["run_id"] == RUN_ID
        assert entry["attempt"] == 1
        assert entry["stage"] == "idea"
        assert entry["tool"] == "idea-director"
        assert entry["path"]
    assert result["work_order"]["status"] == "awaiting_approval"


def test_base_tool_result_contains_durable_provenance(tmp_path: Path, monkeypatch) -> None:
    import lib.events as events_module

    monkeypatch.setattr(events_module, "PROJECTS_DIR", tmp_path)
    project = _project(tmp_path, "tool-provenance-project")

    class ProvenanceTool(BaseTool):
        name = "provenance_fixture"

        def execute(self, inputs):
            output = Path(inputs["project_dir"]) / "renders" / "fixture.txt"
            output.write_text("current run output\n", encoding="utf-8")
            return ToolResult(success=True, artifacts=[str(output)])

    result = ProvenanceTool().execute(
        {
            "project_dir": str(project),
            "output_path": "renders/fixture.txt",
            "stage": "idea",
            "agent_id": "tool-agent",
        }
    )
    assert result.success is True
    provenance = result.data["provenance"]
    assert provenance["project_id"] == project.name
    assert provenance["pipeline_type"] == "screen-demo"
    assert provenance["run_id"] == RUN_ID
    assert provenance["attempt"] == 1
    assert provenance["stage"] == "idea"
    assert provenance["tool"] == "provenance_fixture"
    assert provenance["agent_id"] == "tool-agent"

    record = read_run_record(project, RUN_ID)
    assert any(entry["path"] == "renders/fixture.txt" for entry in record["outputs"])
    events = read_events(project)
    assert [event["event"] for event in events] == ["start", "finish"]
    assert all(event["stage"] == "idea" for event in events)
    assert all(event["run_id"] == RUN_ID for event in events)
    assert all(event["attempt"] == 1 for event in events)
