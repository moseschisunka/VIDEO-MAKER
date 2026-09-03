"""PR-109 golden thin vertical for the certified screen-demo path.

The fixture uses a deterministic local screen capture and FFmpeg.  No provider
or network call is involved; the test proves that an agent-authored artifact
chain can traverse the actual screen-demo manifest, pause at a human gate,
render a current local output, and publish only after every declared stage is
complete.  The quarantined ``lib.project_pipeline`` runner is never invoked.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lib.checkpoint import read_checkpoint, write_checkpoint
from lib.manifest_executor import (
    CERTIFIED_EXECUTOR_PIPELINES,
    load_manifest_stage_context,
    submit_manifest_stage,
)
from lib.pipeline_loader import load_pipeline_readonly
from lib.project_identity import validate_project_identity
from lib.work_order import build_work_order, claim_work_order, decide_human_gate, write_work_order
from schemas.artifacts import validate_artifact
from tools.video.video_compose import VideoCompose


PROJECT_ID = "screen-demo-golden"
RUN_ID = "12345678-1234-4234-8234-123456789abc"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = PROJECT_ROOT / "pipeline_defs"


def _create_project(tmp_path: Path) -> Path:
    project = tmp_path / PROJECT_ID
    (project / "artifacts").mkdir(parents=True)
    (project / "assets" / "video").mkdir(parents=True)
    (project / "renders").mkdir()

    marker = {
        "version": "1.0",
        "project_id": PROJECT_ID,
        "title": "Install walkthrough",
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
        "style_playbook": "premium-minimalist",
    }
    (project / "project.json").write_text(json.dumps(marker), encoding="utf-8")

    manifest = load_pipeline_readonly("screen-demo", defs_dir=PIPELINE_DIR)
    order = build_work_order(
        project_id=PROJECT_ID,
        title="Install walkthrough",
        topic_prompt="Show the install flow.",
        target_duration_seconds=30,
        pipeline_type="screen-demo",
        manifest=manifest,
        manifest_path=PIPELINE_DIR / "screen-demo.yaml",
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
    write_work_order(project, order)
    (project / "artifacts" / "project_config.json").write_text(
        json.dumps({
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
            "title": "Install walkthrough",
        }),
        encoding="utf-8",
    )
    (project / "events.jsonl").write_text(
        json.dumps({
            "event": "created",
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
        }) + "\n",
        encoding="utf-8",
    )
    return project


def _brief() -> dict:
    return {
        "version": "1.0",
        "title": "Install walkthrough",
        "hook": "Install the package and verify the first run.",
        "key_points": ["Open the terminal", "Run the install", "Verify the result"],
        "tone": "clear",
        "style": "premium-minimalist",
        "target_platform": "youtube",
        "target_duration_seconds": 30,
        "reference_material": ["assets/video/source.mp4"],
        "metadata": {
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
            "production_mode": "real_capture",
            "source_path": "assets/video/source.mp4",
            "source_resolution": "1920x1080",
            "source_duration_seconds": 30,
            "software_shown": ["OpenMontage terminal"],
            "demo_archetype": "tutorial",
            "critical_moments": ["install command", "verified result"],
        },
    }


def _script() -> dict:
    return {
        "version": "1.0",
        "title": "Install walkthrough",
        "total_duration_seconds": 30,
        "sections": [
            {
                "id": "step_1",
                "label": "Run the install",
                "text": "Run the install command and wait for completion.",
                "start_seconds": 0,
                "end_seconds": 18,
                "speaker_directions": "Measured and procedural.",
            },
            {
                "id": "step_2",
                "label": "Verify the result",
                "text": "Open the package and verify the first run.",
                "start_seconds": 18,
                "end_seconds": 30,
                "speaker_directions": "Hold the result at normal speed.",
            },
        ],
        "metadata": {
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
            "interaction_map": [
                {
                    "timestamp_seconds": 4,
                    "action_type": "type",
                    "target": "install command",
                    "importance": "high",
                    "suggested_treatment": "realtime",
                },
                {
                    "timestamp_seconds": 24,
                    "action_type": "result",
                    "target": "verified result",
                    "importance": "high",
                    "suggested_treatment": "realtime",
                },
            ],
            "speed_plan": [],
            "command_beats": [
                {"timestamp_seconds": 4, "command": "om install", "hold_seconds": 0.5},
                {"timestamp_seconds": 24, "command": "om verify", "hold_seconds": 2},
            ],
        },
    }


def _scene_plan() -> dict:
    return {
        "version": "1.0",
        "style_playbook": "premium-minimalist",
        "scenes": [
            {
                "id": "scene_1",
                "type": "screen_recording",
                "description": "Show the install command and completion output.",
                "start_seconds": 0,
                "end_seconds": 18,
                "script_section_id": "step_1",
                "movement": "static",
                "transition_in": "cut",
                "transition_out": "dissolve",
                "shot_intent": "Keep the command legible.",
                "narrative_role": "introduce_subject",
                "required_assets": [
                    {"type": "screen_capture", "description": "terminal capture", "source": "source"}
                ],
            },
            {
                "id": "scene_2",
                "type": "screen_recording",
                "description": "Hold the verified result in context.",
                "start_seconds": 18,
                "end_seconds": 30,
                "script_section_id": "step_2",
                "movement": "static",
                "transition_in": "dissolve",
                "transition_out": "fade",
                "shot_intent": "Keep the successful result readable.",
                "narrative_role": "resolution",
                "required_assets": [
                    {"type": "screen_capture", "description": "terminal capture", "source": "source"}
                ],
            },
        ],
        "metadata": {
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
            "production_mode": "real_capture",
            "crop_regions": [],
            "callout_plan": [],
            "aspect_ratio_viability": {"16:9": True, "1:1": False, "9:16": False},
        },
    }


def _asset_manifest() -> dict:
    return {
        "version": "1.0",
        "assets": [
            {
                "id": "asset_source_capture",
                "type": "video",
                "path": "assets/video/source.mp4",
                "source_tool": "deterministic_screen_capture_fixture",
                "scene_id": "scene_1",
                "duration_seconds": 30,
                "resolution": "1920x1080",
                "format": "mp4",
                "subtype": "source_capture",
            }
        ],
        "total_cost_usd": 0,
        "metadata": {
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
            "production_mode": "real_capture",
            "capture_or_terminal_source_recorded": True,
            "subtitle_zones": [],
            "overlay_kit": [],
        },
    }


def _edit_decisions() -> dict:
    return {
        "version": "1.0",
        "project_id": PROJECT_ID,
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
        "render_runtime": "ffmpeg",
        "renderer_family": "screen-demo",
        "composition_mode": "templated",
        "cuts": [
            {
                "id": "cut_1",
                "source": "asset_source_capture",
                "in_seconds": 0,
                "out_seconds": 30,
                "speed": 1,
                "layer": "primary",
                "transition_in": "cut",
                "transition_out": "fade",
                "reason": "Preserve the complete install and verification flow.",
            }
        ],
        "audio": {},
        "subtitles": {"enabled": False, "position": "bottom-center"},
        "metadata": {
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
            "target_duration_seconds": 30,
            "speed_plan": [],
            "qa_policy": {
                "allowed_static_holds": [
                    {"start_seconds": 0, "end_seconds": 30},
                ],
            },
        },
    }


def _render_report(project: Path, review: dict) -> dict:
    output = project / "renders" / "final.mp4"
    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(output)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    video = next(stream for stream in probe["streams"] if stream.get("codec_type") == "video")
    audio = next(stream for stream in probe["streams"] if stream.get("codec_type") == "audio")
    report = {
        "version": "1.0",
        "project_id": PROJECT_ID,
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
        "render_runtime": "ffmpeg",
        "outputs": [{
            "path": "renders/final.mp4",
            "format": "mp4",
            "codec": video.get("codec_name", "unknown"),
            "audio_codec": audio.get("codec_name", "unknown"),
            "resolution": f"{video['width']}x{video['height']}",
            "fps": 30,
            "duration_seconds": float(probe["format"]["duration"]),
            "file_size_bytes": output.stat().st_size,
            "platform_target": "youtube",
        }],
        "verification_notes": ["ffprobe passed", "current-run output exists", "screen text remains in the source frame"],
        "warnings": [],
        "render_grammar": "screen-demo",
        "final_review_ref": "artifacts/final_review.json",
        "metadata": {"media_probe": probe, "text_is_readable": True, "output_is_playable": True},
    }
    validate_artifact("render_report", report)
    return report


def _final_review() -> dict:
    return {
        "version": "1.0",
        "output_path": "renders/final.mp4",
        "status": "pass",
        "checks": {
            "technical_probe": {
                "valid_container": True,
                "duration_seconds": 30,
                "resolution": "1920x1080",
                "fps": 30,
                "has_audio": True,
                "codec": "h264",
                "file_size_bytes": 1,
                "issues": [],
            },
            "visual_spotcheck": {
                "frames_sampled": 4,
                "frame_paths": ["renders/qa/first.png", "renders/qa/middle.png", "renders/qa/result.png", "renders/qa/end.png"],
                "black_frames_detected": False,
                "broken_overlays": False,
                "missing_assets": False,
                "unreadable_text": False,
                "issues": [],
            },
            "audio_spotcheck": {
                "narration_present": False,
                "music_present": False,
                "unexpected_silence": False,
                "clipping_detected": False,
                "mix_intelligible": True,
                "issues": [],
            },
            "promise_preservation": {
                "delivery_promise_honored": True,
                "renderer_family_used": "screen-demo",
                "render_runtime_used": "ffmpeg",
                "runtime_swap_detected": False,
                "runtime_swap_check": "ok — ffmpeg locked in work order and edit decisions",
                "silent_downgrade_detected": False,
                "issues": [],
            },
            "subtitle_check": {
                "subtitles_expected": False,
                "subtitles_present": False,
                "coverage_ratio": 1,
                "timing_drift_detected": False,
                "issues": [],
            },
        },
        "issues_found": [],
        "recommended_action": "present_to_user",
    }


def test_screen_demo_manifest_faithful_golden_path(tmp_path: Path) -> None:
    assert "screen-demo" in CERTIFIED_EXECUTOR_PIPELINES
    project = _create_project(tmp_path)

    # Deterministic, local source-footage fixture: no provider/network call.
    source = project / "assets" / "video" / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x123B31:s=1920x1080:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    claim_work_order(project, "golden-agent", lease_seconds=300)
    context = load_manifest_stage_context(project)
    assert context.stage == "idea"
    assert context.director_skill == "pipelines/screen-demo/idea-director"

    # Idea is a real human gate.  Submit the agent artifact, pause, then use
    # the checkpoint approval path before advancing.
    idea = submit_manifest_stage(
        project, "golden-agent", "idea", {"brief": _brief(), "decision_log": {
            "version": "1.0",
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
            "decisions": [{
                "decision_id": "d-001",
                "stage": "idea",
                "category": "render_runtime_selection",
                "subject": "Composition runtime",
                "options_considered": [
                    {"option_id": "ffmpeg", "label": "FFmpeg", "score": 0.8, "reason": "Deterministic source-footage trim."},
                    {"option_id": "remotion", "label": "Remotion", "score": 0.7, "reason": "Available but unnecessary for this cut."},
                ],
                "selected": "ffmpeg",
                "reason": "The source-footage path needs only deterministic trim/encode.",
                "user_visible": True,
                "user_approved": True,
            }],
        }}, status="awaiting_human")
    assert idea["work_order"]["status"] == "awaiting_approval"
    approved = decide_human_gate(
        project,
        "idea",
        approver_id="reviewer-1",
        agent_id="golden-agent",
    )
    assert approved["transition"] == "advanced"

    stages = {
        "script": {"script": _script()},
        "scene_plan": {"scene_plan": _scene_plan()},
        "assets": {"asset_manifest": _asset_manifest()},
        "edit": {"edit_decisions": _edit_decisions()},
    }
    for stage, artifacts in stages.items():
        result = submit_manifest_stage(
            project, "golden-agent", stage, artifacts
        )
        if result["work_order"]["next_stage"] == stage:
            approved = decide_human_gate(
                project,
                stage,
                approver_id="reviewer-1",
                agent_id="golden-agent",
            )
            assert approved["transition"] == "advanced"
            result = approved
        assert result["work_order"]["next_stage"] != stage

    # The actual manifest-selected FFmpeg runtime produces the local output.
    edit = json.loads((project / "artifacts" / "edit_decisions.json").read_text(encoding="utf-8"))
    assets = json.loads((project / "artifacts" / "asset_manifest.json").read_text(encoding="utf-8"))
    render_result = VideoCompose().execute({
        "operation": "render",
        "edit_decisions": edit,
        "asset_manifest": assets,
        "output_path": str(project / "renders" / "final.mp4"),
        "profile": "youtube_landscape",
    })
    assert render_result.success, render_result.error
    review = render_result.data["final_review"]
    review["output_path"] = "renders/final.mp4"
    report = _render_report(project, review)
    submit_manifest_stage(
        project,
        "golden-agent",
        "compose",
        {"render_report": report, "final_review": review},
    )
    publish = {
        "version": "1.0",
        "entries": [{
            "platform": "youtube",
            "status": "exported",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "export_path": "renders/final.mp4",
            "metadata_used": {
                "title": "Install walkthrough",
                "description": "Install and verify the OpenMontage package.",
                "hashtags": ["#OpenMontage", "#screenrecording"],
                "chapters": [{"start_seconds": 0, "title": "Install"}, {"start_seconds": 18, "title": "Verify"}],
            },
        }],
        "metadata": {"pipeline_type": "screen-demo", "run_id": RUN_ID},
    }
    final = submit_manifest_stage(
        project,
        "golden-agent",
        "publish",
        {"publish_log": publish},
    )
    assert final["work_order"]["status"] == "awaiting_approval"
    final = decide_human_gate(
        project,
        "publish",
        approver_id="reviewer-1",
        agent_id="golden-agent",
    )
    assert final["work_order"]["status"] == "completed"
    assert final["work_order"]["next_stage"] is None
    assert (project / "renders" / "final.mp4").is_file()
    assert validate_project_identity(project, strict=True)["valid"]
