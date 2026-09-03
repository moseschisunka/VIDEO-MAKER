"""PR-1016 contracts for the approved source-footage talking-head lane.

The fixture is deliberately local and deterministic: a generated test pattern
stands in for supplied source footage and a local SRT stands in for the
transcriber/subtitle contract.  No provider or network call is allowed.  The
test proves that the selected talking-head manifest, not the quarantined
Studio runner, owns the complete agent artifact chain and that Backlot exposes
the same certified handoff as the approved launch scope.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from lib.manifest_executor import (
    CERTIFIED_EXECUTOR_PIPELINES,
    load_manifest_stage_context,
    is_certified_executor_order,
    submit_manifest_stage,
)
from lib.pipeline_loader import load_pipeline_readonly
from lib.project_identity import validate_project_identity
from lib.work_order import (
    build_work_order,
    claim_work_order,
    decide_human_gate,
    write_work_order,
)
from schemas.artifacts import validate_artifact
from tools.video.video_compose import VideoCompose


PROJECT_ID = "talking-head-golden"
RUN_ID = "22345678-1234-4234-8234-123456789abc"
PROFILE = "youtube_shorts"
TARGET_DURATION = 15
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
        "title": "Talking-head launch fixture",
        "pipeline_type": "talking-head",
        "run_id": RUN_ID,
        "style_playbook": "clean-professional",
        "source_mode": "source_footage",
        "output_profile": PROFILE,
        "aspect_ratio": "9:16",
    }
    (project / "project.json").write_text(json.dumps(marker), encoding="utf-8")

    manifest = load_pipeline_readonly("talking-head", defs_dir=PIPELINE_DIR)
    order = build_work_order(
        project_id=PROJECT_ID,
        title="Talking-head launch fixture",
        topic_prompt="Preserve and caption a supplied talking-head clip.",
        target_duration_seconds=TARGET_DURATION,
        pipeline_type="talking-head",
        manifest=manifest,
        manifest_path=PIPELINE_DIR / "talking-head.yaml",
        selections={
            "playbook": "clean-professional",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "ffmpeg",
            "output_profile": PROFILE,
            "aspect_ratio": "9:16",
            "source_mode": "source_footage",
        },
        run_id=RUN_ID,
    )
    write_work_order(project, order)
    (project / "artifacts" / "project_config.json").write_text(
        json.dumps(
            {
                "project_id": PROJECT_ID,
                "pipeline_type": "talking-head",
                "run_id": RUN_ID,
                "title": "Talking-head launch fixture",
                "source_mode": "source_footage",
                "output_profile": PROFILE,
                "aspect_ratio": "9:16",
            }
        ),
        encoding="utf-8",
    )
    return project


def _write_source_fixture(project: Path) -> None:
    source = project / "assets" / "video" / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=360x640:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            str(TARGET_DURATION),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-af",
            "volume=2.5",
            "-shortest",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    (project / "assets" / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:07,500\nWelcome to the talking-head fixture.\n\n"
        "2\n00:00:07,500 --> 00:00:15,000\nThe supplied footage remains the primary visual.\n",
        encoding="utf-8",
    )


def _identity_metadata() -> dict[str, str]:
    return {
        "project_id": PROJECT_ID,
        "pipeline_type": "talking-head",
        "run_id": RUN_ID,
        "output_profile": PROFILE,
        "profile": PROFILE,
        "aspect_ratio": "9:16",
        "source_mode": "source_footage",
    }


def _brief() -> dict:
    return {
        "version": "1.0",
        "title": "Talking-head launch fixture",
        "hook": "Keep the speaker and the message in frame.",
        "key_points": ["Use supplied footage", "Keep captions readable"],
        "tone": "clear",
        "style": "clean-professional",
        "target_platform": "youtube",
        "target_duration_seconds": TARGET_DURATION,
        "output_profile": PROFILE,
        "profile": PROFILE,
        "aspect_ratio": "9:16",
        "reference_material": ["assets/video/source.mp4"],
        "source_refs": ["assets/video/source.mp4"],
        "metadata": _identity_metadata(),
    }


def _script() -> dict:
    return {
        "version": "1.0",
        "title": "Talking-head launch fixture",
        "total_duration_seconds": TARGET_DURATION,
        "target_duration_seconds": TARGET_DURATION,
        "output_profile": PROFILE,
        "profile": PROFILE,
        "aspect_ratio": "9:16",
        "sections": [
            {
                "id": "opening",
                "label": "Opening",
                "text": "Welcome to the talking-head fixture.",
                "start_seconds": 0,
                "end_seconds": 7.5,
                "speaker_directions": "Measured and direct.",
            },
            {
                "id": "proof",
                "label": "Proof",
                "text": "The supplied footage remains the primary visual.",
                "start_seconds": 7.5,
                "end_seconds": TARGET_DURATION,
                "speaker_directions": "Hold the final phrase clearly.",
            },
        ],
        "metadata": _identity_metadata(),
    }


def _scene_plan() -> dict:
    return {
        "version": "1.0",
        "style_playbook": "clean-professional",
        "target_duration_seconds": TARGET_DURATION,
        "output_profile": PROFILE,
        "profile": PROFILE,
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "id": "scene-opening",
                "type": "talking_head",
                "description": "Keep the supplied speaker footage centered and readable.",
                "start_seconds": 0,
                "end_seconds": 7.5,
                "script_section_id": "opening",
                "framing": "medium_close",
                "movement": "static",
                "transition_in": "cut",
                "transition_out": "dissolve",
                "shot_intent": "Establish the speaker without changing the source identity.",
                "narrative_role": "introduce_subject",
                "required_assets": [
                    {"type": "video", "description": "supplied talking-head footage", "source": "source"}
                ],
            },
            {
                "id": "scene-proof",
                "type": "talking_head",
                "description": "Hold the source footage while the captioned proof lands.",
                "start_seconds": 7.5,
                "end_seconds": TARGET_DURATION,
                "script_section_id": "proof",
                "framing": "medium_close",
                "movement": "static",
                "transition_in": "dissolve",
                "transition_out": "fade",
                "shot_intent": "Preserve the supplied footage through the close.",
                "narrative_role": "resolution",
                "required_assets": [
                    {"type": "video", "description": "supplied talking-head footage", "source": "source"}
                ],
            },
        ],
        "metadata": _identity_metadata(),
    }


def _asset_manifest() -> dict:
    return {
        "version": "1.0",
        "assets": [
            {
                "id": "asset-source-footage",
                "type": "video",
                "path": "assets/video/source.mp4",
                "source_tool": "deterministic_source_footage_fixture",
                "scene_id": "scene-opening",
                "duration_seconds": TARGET_DURATION,
                "resolution": "360x640",
                "format": "mp4",
                "subtype": "source_footage",
            },
            {
                "id": "asset-subtitles",
                "type": "subtitle",
                "path": "assets/subtitles.srt",
                "source_tool": "deterministic_subtitle_fixture",
                "scene_id": "scene-opening",
                "format": "srt",
                "subtype": "source_transcript_captions",
            },
        ],
        "total_cost_usd": 0,
        "metadata": _identity_metadata(),
    }


def _edit_decisions() -> dict:
    return {
        "version": "1.0",
        "project_id": PROJECT_ID,
        "pipeline_type": "talking-head",
        "run_id": RUN_ID,
        "target_duration_seconds": TARGET_DURATION,
        "output_profile": PROFILE,
        "profile": PROFILE,
        "aspect_ratio": "9:16",
        "render_runtime": "ffmpeg",
        "renderer_family": "presenter",
        "composition_mode": "templated",
        "cuts": [
            {
                "id": "cut-source-footage",
                "source": "asset-source-footage",
                "in_seconds": 0,
                "out_seconds": TARGET_DURATION,
                "speed": 1,
                "layer": "primary",
                "transition_in": "cut",
                "transition_out": "fade",
                "reason": "Preserve the complete supplied talking-head source.",
            }
        ],
        "audio": {},
        "subtitles": {
            "enabled": True,
            "source": "assets/subtitles.srt",
            "position": "bottom-center",
        },
        "metadata": {
            **_identity_metadata(),
            "target_duration_seconds": TARGET_DURATION,
            "qa_policy": {"allowed_static_holds": [{"start_seconds": 0, "end_seconds": TARGET_DURATION}]},
        },
    }


def _decision_log() -> dict:
    return {
        "version": "1.0",
        "project_id": PROJECT_ID,
        "pipeline_type": "talking-head",
        "run_id": RUN_ID,
        "decisions": [
            {
                "decision_id": "talking-head-runtime",
                "stage": "idea",
                "category": "render_runtime_selection",
                "subject": "Composition runtime",
                "options_considered": [
                    {
                        "option_id": "ffmpeg",
                        "label": "FFmpeg",
                        "score": 0.9,
                        "reason": "Deterministic source-footage trim and caption burn-in.",
                    },
                    {
                        "option_id": "remotion",
                        "label": "Remotion",
                        "score": 0.7,
                        "reason": "Available for presenter overlays, but not needed for this fixture.",
                    },
                    {
                        "option_id": "hyperframes",
                        "label": "HyperFrames",
                        "score": 0.5,
                        "reason": "Available for kinetic layouts, but not needed for this fixture.",
                    },
                ],
                "selected": "ffmpeg",
                "reason": "The approved source-footage lane needs deterministic trim and caption evidence.",
                "user_visible": True,
                "user_approved": True,
            }
        ],
    }


def _render_report(project: Path, review: dict) -> dict:
    output = project / "renders" / "final.mp4"
    probe = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    video = next(stream for stream in probe["streams"] if stream.get("codec_type") == "video")
    audio = next(stream for stream in probe["streams"] if stream.get("codec_type") == "audio")
    numerator, denominator = (int(value) for value in video.get("r_frame_rate", "30/1").split("/"))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    report = {
        "version": "1.0",
        "project_id": PROJECT_ID,
        "pipeline_type": "talking-head",
        "run_id": RUN_ID,
        "target_duration_seconds": TARGET_DURATION,
        "output_profile": PROFILE,
        "profile": PROFILE,
        "aspect_ratio": "9:16",
        "render_runtime": "ffmpeg",
        "producer_stage": "compose",
        "producer_tool": "video_compose",
        "provenance_ref": f"runs/{RUN_ID}/run.json",
        "outputs": [
            {
                "path": "renders/final.mp4",
                "format": "mp4",
                "codec": video.get("codec_name", "unknown"),
                "audio_codec": audio.get("codec_name", "unknown"),
                "has_audio": True,
                "stream_count": len(probe["streams"]),
                "format_name": probe.get("format", {}).get("format_name", "mp4"),
                "resolution": f"{video['width']}x{video['height']}",
                "fps": numerator / max(denominator, 1),
                "duration_seconds": float(probe["format"]["duration"]),
                "file_size_bytes": output.stat().st_size,
                "sha256": digest,
                "platform_target": "youtube",
                "output_profile": PROFILE,
                "profile": PROFILE,
                "aspect_ratio": "9:16",
            }
        ],
        "verification_notes": [
            "ffprobe passed",
            "current-run output exists",
            "source-footage and project-relative subtitle paths resolved inside the project",
        ],
        "warnings": [],
        "render_grammar": "presenter",
        "final_review_ref": "artifacts/final_review.json",
        "metadata": {"media_probe": probe, **_identity_metadata()},
    }
    validate_artifact("render_report", report)
    return report


@pytest.mark.release_blocker
def test_talking_head_certification_is_source_footage_only() -> None:
    assert is_certified_executor_order(
        {
            "pipeline_type": "talking-head",
            "selections": {"source_mode": "source_footage"},
        }
    )
    assert not is_certified_executor_order(
        {
            "pipeline_type": "talking-head",
            "selections": {"source_mode": "avatar"},
        }
    )
    assert not is_certified_executor_order(
        {"pipeline_type": "talking-head", "selections": {}}
    )


@pytest.mark.release_blocker
def test_talking_head_manifest_faithful_golden_path(tmp_path: Path) -> None:
    assert "talking-head" in CERTIFIED_EXECUTOR_PIPELINES
    project = _create_project(tmp_path)
    _write_source_fixture(project)

    claim_work_order(project, "talking-head-agent", lease_seconds=300)
    context = load_manifest_stage_context(project)
    assert context.stage == "idea"
    assert context.director_skill == "pipelines/talking-head/idea-director"

    stages = {
        "idea": {"brief": _brief(), "decision_log": _decision_log()},
        "script": {"script": _script()},
        "scene_plan": {"scene_plan": _scene_plan()},
        "assets": {"asset_manifest": _asset_manifest()},
        "edit": {"edit_decisions": _edit_decisions()},
    }
    for stage, artifacts in stages.items():
        result = submit_manifest_stage(project, "talking-head-agent", stage, artifacts)
        if result["work_order"]["next_stage"] == stage:
            result = decide_human_gate(
                project,
                stage,
                approver_id="reviewer-1",
                agent_id="talking-head-agent",
            )
            assert result["transition"] == "advanced"
        assert result["work_order"]["next_stage"] != stage

    edit = json.loads((project / "artifacts" / "edit_decisions.json").read_text(encoding="utf-8"))
    assets = json.loads((project / "artifacts" / "asset_manifest.json").read_text(encoding="utf-8"))
    render_result = VideoCompose().execute(
        {
            "operation": "render",
            "project_dir": str(project),
            "output_path": "renders/final.mp4",
            "edit_decisions": edit,
            "asset_manifest": assets,
            "profile": PROFILE,
        }
    )
    assert render_result.success, render_result.error
    assert render_result.data["final_review"]["status"] == "pass", render_result.data["final_review"]
    review = dict(render_result.data["final_review"])
    review.update(
        {
            "output_path": "renders/final.mp4",
            "project_id": PROJECT_ID,
            "pipeline_type": "talking-head",
            "run_id": RUN_ID,
            "output_profile": PROFILE,
            "profile": PROFILE,
        }
    )
    report = _render_report(project, review)
    submit_manifest_stage(
        project,
        "talking-head-agent",
        "compose",
        {"render_report": report, "final_review": review},
        producing_tool="video_compose",
    )

    publish = {
        "version": "1.0",
        "entries": [
            {
                "platform": "youtube",
                "status": "exported",
                "timestamp": "2026-09-03T00:00:00+00:00",
                "export_path": "renders/final.mp4",
                "metadata_used": {
                    "title": "Talking-head launch fixture",
                    "description": "Deterministic source-footage certification output.",
                    "hashtags": ["#OpenMontage", "#talkinghead"],
                },
            }
        ],
        "metadata": _identity_metadata(),
    }
    final = submit_manifest_stage(
        project,
        "talking-head-agent",
        "publish",
        {"publish_log": publish},
    )
    assert final["work_order"]["status"] == "awaiting_approval"
    final = decide_human_gate(
        project,
        "publish",
        approver_id="reviewer-1",
        agent_id="talking-head-agent",
    )
    assert final["work_order"]["status"] == "completed"
    assert final["work_order"]["next_stage"] is None
    assert (project / "renders" / "final.mp4").is_file()
    assert validate_project_identity(project, strict=True)["valid"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", str(projects.resolve()).lower())

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as test_client:
        yield test_client, projects


@pytest.mark.release_blocker
def test_talking_head_run_returns_manifest_agent_handoff(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, _projects = client
    created = test_client.post(
        "/api/project/create",
        json={
            "title": "Talking-head source footage",
            "pipeline_type": "talking-head",
            "playbook": "clean-professional",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "ffmpeg",
            "output_profile": PROFILE,
            "aspect_ratio": "9:16",
            "source_mode": "source_footage",
            "target_duration_seconds": TARGET_DURATION,
        },
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: spawned.append((args, kwargs)))
    response = test_client.post(f"/api/project/{project_id}/run?agent_id=talking-head-ui")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["execution_mode"] == "manifest_agent"
    assert payload["next_stage"] == "idea"
    assert payload["stage_skill"] == "pipelines/talking-head/idea-director"
    assert payload["work_order"]["claim"]["claimed_by"] == "talking-head-ui"
    assert spawned == []


@pytest.mark.release_blocker
def test_talking_head_run_rejects_non_source_footage_persisted_scope(client) -> None:
    """A legacy/hand-edited order cannot broaden the certified launch lane."""

    test_client, projects = client
    project = projects / "talking-head-avatar-order"
    project.mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "project_id": project.name,
                "title": "Uncertified avatar order",
                "pipeline_type": "talking-head",
                "run_id": "32345678-1234-4234-8234-123456789abc",
                "style_playbook": "clean-professional",
            }
        ),
        encoding="utf-8",
    )
    manifest = load_pipeline_readonly("talking-head", defs_dir=PIPELINE_DIR)
    order = build_work_order(
        project_id=project.name,
        title="Uncertified avatar order",
        topic_prompt="An order outside the approved source-footage lane.",
        target_duration_seconds=TARGET_DURATION,
        pipeline_type="talking-head",
        manifest=manifest,
        manifest_path=PIPELINE_DIR / "talking-head.yaml",
        selections={
            "playbook": "clean-professional",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "ffmpeg",
            "output_profile": PROFILE,
            "aspect_ratio": "9:16",
            "source_mode": "avatar",
        },
        run_id="32345678-1234-4234-8234-123456789abc",
    )
    write_work_order(project, order)

    response = test_client.post(f"/api/project/{project.name}/run?agent_id=scope-check")
    assert response.status_code == 409, response.text
    assert "source-footage-only" in response.json()["detail"]
    assert "avatar" in response.json()["detail"]
