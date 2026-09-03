"""Phase 9 real-media QA and cross-artifact contracts."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path

from lib.cross_artifact import validate_cross_artifact_consistency
from lib.voice_contracts import normalize_voice_identity
from lib.video_quality import inspect_video
from tools.video.video_compose import VideoCompose


def _video(path: Path, *, source: str, duration: float = 2.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"{source}:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return path


def _frozen_video(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:d=1",
            "-vf", "tpad=stop_mode=clone:stop_duration=2", "-t", "3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return path


def test_decoded_black_and_frozen_media_is_detected(tmp_path: Path) -> None:
    report = inspect_video(
        _video(tmp_path / "black.mp4", source="color=c=black:s=320x240"),
        sample_count=4,
    )
    assert report["valid"] is False
    assert any("black" in item.lower() for item in report["errors"])
    assert report["samples"]


def test_missing_media_fails_closed() -> None:
    report = inspect_video("does-not-exist.mp4")
    assert report["valid"] is False
    assert any("not found" in item for item in report["errors"])


def test_final_review_nonpass_is_not_a_success(tmp_path: Path) -> None:
    output = _video(tmp_path / "black.mp4", source="color=c=black:s=320x240")
    review = VideoCompose()._run_final_review(
        output,
        {"version": "1.0", "render_runtime": "ffmpeg", "renderer_family": "screen-demo", "cuts": [{"id": "c1", "in_seconds": 0, "out_seconds": 2}]},
    )
    assert review["status"] in {"revise", "fail"}
    assert review["output_sha256"]
    assert review["review_id"]


def test_cross_artifact_validator_catches_runtime_duration_and_review_link_drift() -> None:
    artifacts = {
        "proposal_packet": {
            "project_id": "p", "pipeline_type": "screen-demo", "run_id": "r",
            "production_plan": {"render_runtime": "remotion", "output_profile": "youtube_landscape"},
            "output_profile": "youtube_landscape", "target_duration_seconds": 10,
        },
        "edit_decisions": {
            "project_id": "p", "pipeline_type": "screen-demo", "run_id": "r",
            "render_runtime": "ffmpeg", "output_profile": "youtube_landscape", "total_duration_seconds": 10,
        },
        "render_report": {
            "project_id": "p", "pipeline_type": "screen-demo", "run_id": "r",
            "render_runtime": "ffmpeg", "output_profile": "youtube_landscape",
            "outputs": [{"path": "renders/final.mp4", "duration_seconds": 11}],
            "final_review_ref": "wrong.json",
        },
        "final_review": {
            "project_id": "p", "pipeline_type": "screen-demo", "run_id": "r",
            "output_path": "renders/other.mp4", "status": "pass",
            "checks": {"promise_preservation": {"render_runtime_used": "ffmpeg"}},
        },
    }
    result = validate_cross_artifact_consistency(artifacts)
    assert result["valid"] is False
    assert any("runtime" in item for item in result["errors"])
    assert any("duration" in item for item in result["errors"])
    assert any("final_review_ref" in item for item in result["errors"])


def test_final_review_flags_language_and_voice_identity_mismatch(tmp_path: Path) -> None:
    output = _video(tmp_path / "narrated.mp4", source="color=c=navy:s=320x240")
    approved = normalize_voice_identity({
        "provider": "edge_tts",
        "model": "neural",
        "voice_id": "en-US-ChristopherNeural",
        "locale": "en-US",
    }).contract()
    drifted = normalize_voice_identity({
        "provider": "edge_tts",
        "model": "neural",
        "voice_id": "fr-FR-DeniseNeural",
        "locale": "fr-FR",
    }).contract()
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps({
            "language": "fr-FR",
            "voice_identity": drifted,
            "word_timestamps": [{"word": "hello"}, {"word": "world"}],
        }),
        encoding="utf-8",
    )
    review = VideoCompose()._run_final_review(
        output,
        {
            "version": "1.0",
            "render_runtime": "ffmpeg",
            "renderer_family": "screen-demo",
            "voice_identity": approved,
            "audio": {"narration": {"required": True}},
            "cuts": [{"id": "c1", "in_seconds": 0, "out_seconds": 2}],
        },
        asset_manifest={"version": "1.0", "voice_identity": approved, "assets": []},
        narration_transcript_path=transcript,
        script_text="hello world",
    )
    assert review["status"] == "revise"
    voice_check = review["checks"]["voice_over"]
    assert voice_check["language_match"] is False
    assert any("language" in item.lower() for item in voice_check["issues"])
    assert any("voice identity" in item.lower() for item in review["issues_found"])


def test_declared_static_hold_is_not_misclassified_as_renderer_freeze(tmp_path: Path) -> None:
    output = _frozen_video(tmp_path / "static-title.mp4")
    review = VideoCompose()._run_final_review(
        output,
        {
            "version": "1.0",
            "render_runtime": "ffmpeg",
            "renderer_family": "screen-demo",
            "metadata": {
                "qa_policy": {
                    "allowed_static_holds": [
                        {"start_seconds": 0.9, "end_seconds": 3},
                    ]
                }
            },
            "cuts": [{"id": "title", "in_seconds": 0, "out_seconds": 2}],
        },
    )
    assert review["status"] == "pass"
    quality = review["checks"]["visual_spotcheck"]["video_quality"]
    assert quality["allowed_frozen_intervals"]
    assert quality["unapproved_frozen_intervals"] == []
    assert quality["allowed_static_holds"] == [{"start_seconds": 0.9, "end_seconds": 3.0}]


def test_final_review_routes_visual_asset_and_placeholder_faults(tmp_path: Path) -> None:
    output = _video(tmp_path / "visual.mp4", source="testsrc=size=320x240:rate=30")
    review = VideoCompose()._run_final_review(
        output,
        {
            "version": "1.0",
            "render_runtime": "ffmpeg",
            "renderer_family": "screen-demo",
            "cuts": [{"id": "c1", "source": "missing.mp4", "in_seconds": 0, "out_seconds": 2}],
            "overlays": [{
                "asset_id": "missing-overlay.png",
                "start_seconds": 0,
                "end_seconds": 2,
                "position": {"x": 500, "y": 0, "width": 40, "height": 40},
            }],
            "metadata": {"title": "[PLACEHOLDER]"},
        },
        asset_manifest={"version": "1.0", "assets": []},
    )
    assert review["status"] == "revise"
    assert review["recommended_action"] == "revise_assets"
    contract = review["checks"]["visual_spotcheck"]["visual_contract"]
    assert contract["missing_sources"]
    assert contract["placeholder_tokens"]
    assert any("outside the frame" in item for item in contract["errors"])
