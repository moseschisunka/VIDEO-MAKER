"""PR-805 runtime parity tests for certified caption rendering."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lib.caption_contracts import (
    CaptionContractError,
    build_caption_render_contract,
    caption_accessibility_report,
    load_caption_cues,
    normalize_caption_cues,
)
from tools.analysis.audio_quality import probe_audio_quality
from tools.video.hyperframes_compose import HyperFramesCompose
from tools.video.remotion_caption_burn import RemotionCaptionBurn
from tools.video.video_compose import VideoCompose


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe required",
)


def _video(path: Path, duration: float = 2.0) -> None:
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=navy:s=320x240:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0


def _audio_fixture(path: Path, expression: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc={expression}:d=1:s=48000",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def _transcript() -> dict:
    return {
        "verified": True,
        "verification_status": "verified",
        "language": "en-US",
        "segments": [
            {
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.65},
                    {"word": "world", "start": 0.70, "end": 1.45},
                ]
            }
        ],
    }


def test_caption_contract_normalizes_wrapping_and_safe_area():
    contract = build_caption_render_contract(
        runtime="ffmpeg",
        mode="burn_in",
        cues=[{"start": 0, "end": 1.5, "text": "This is a concise caption"}],
        width=1080,
        height=1920,
        duration_seconds=2,
        max_chars_per_line=16,
        max_lines=2,
    )
    assert contract["valid"] is True
    assert contract["cue_count"] == 1
    assert contract["safe_area"]["pixels"]["bottom"] == 230
    assert contract["cues"][0]["line_count"] == 2
    assert len(contract["cue_digest"]) == 64


def test_caption_contract_rejects_overlap_and_unrenderable_long_word():
    with pytest.raises(CaptionContractError, match="overlaps"):
        normalize_caption_cues(
            [
                {"start": 0, "end": 1, "text": "one"},
                {"start": 0.5, "end": 2, "text": "two"},
            ]
        )
    with pytest.raises(CaptionContractError, match="longer than"):
        normalize_caption_cues(
            [{"start": 0, "end": 1, "text": "supercalifragilistic"}],
            max_chars_per_line=5,
            max_lines=2,
        )


def test_caption_sidecar_parsers_support_srt_vtt_and_json(tmp_path: Path):
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello world\n", encoding="utf-8")
    assert load_caption_cues(srt)[0]["text"] == "Hello world"
    vtt = tmp_path / "captions.vtt"
    vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n", encoding="utf-8")
    assert load_caption_cues(vtt)[0]["end"] == 1.0
    payload = tmp_path / "captions.caption.json"
    payload.write_text('{"cues":[{"start":0,"end":1,"text":"Hello"}]}', encoding="utf-8")
    assert load_caption_cues(payload)[0]["text"] == "Hello"


def test_remotion_caption_burn_ffmpeg_fallback_returns_certified_contract(tmp_path: Path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "captioned.mp4"
    _video(source)
    result = RemotionCaptionBurn().execute(
        {
            "input_path": str(source),
            "output_path": str(output),
            "transcript": _transcript(),
            "require_verified_transcript": True,
            "expected_language": "en",
            "force_ffmpeg": True,
        }
    )
    assert result.success, result.error
    assert output.is_file()
    contract = result.data["caption_render_contract"]
    assert contract["runtime"] == "remotion"
    assert contract["mode"] == "burn_in"
    assert contract["transcript_verification"]["verified"] is True
    assert contract["safe_area"]["pixels"]["bottom"] > 0


def test_remotion_caption_burn_fails_closed_without_verified_transcript(tmp_path: Path):
    source = tmp_path / "source.mp4"
    _video(source)
    result = RemotionCaptionBurn().execute(
        {
            "input_path": str(source),
            "output_path": str(tmp_path / "captioned.mp4"),
            "segments": _transcript()["segments"],
            "require_verified_transcript": True,
            "force_ffmpeg": True,
        }
    )
    assert result.success is False
    assert "verified" in (result.error or "")


def test_hyperframes_scaffold_has_overlay_contract_and_safe_area(tmp_path: Path):
    workspace = tmp_path / "hyperframes"
    result = HyperFramesCompose().execute(
        {
            "operation": "scaffold_workspace",
            "workspace_path": str(workspace),
            "edit_decisions": {
                "render_runtime": "hyperframes",
                "cuts": [{"id": "c1", "source": "", "in_seconds": 0, "out_seconds": 2, "type": "text_card", "text": "Scene"}],
            },
            "asset_manifest": {"assets": []},
            "transcript": _transcript(),
            "require_verified_transcript": True,
        }
    )
    assert result.success, result.error
    contract = result.data["caption_render_contract"]
    assert contract["runtime"] == "hyperframes"
    html = (workspace / "index.html").read_text(encoding="utf-8")
    assert "data-caption-start" in html
    assert "data-caption-end" in html
    assert (workspace / "caption_render_contract.json").is_file()
    assert "bottom: 130px" in html


def test_hyperframes_sidecar_mode_retains_packaged_sidecar_without_overlay(tmp_path: Path):
    workspace = tmp_path / "hyperframes"
    result = HyperFramesCompose().execute(
        {
            "operation": "scaffold_workspace",
            "workspace_path": str(workspace),
            "edit_decisions": {
                "render_runtime": "hyperframes",
                "cuts": [{"id": "c1", "source": "", "in_seconds": 0, "out_seconds": 2, "type": "text_card", "text": "Scene"}],
            },
            "asset_manifest": {"assets": []},
            "captions": [{"start": 0, "end": 1.5, "text": "Sidecar caption"}],
            "caption_mode": "sidecar",
        }
    )
    assert result.success, result.error
    assert result.data["caption_render_contract"]["mode"] == "sidecar"
    assert result.data["caption_sidecar"]
    html = (workspace / "index.html").read_text(encoding="utf-8")
    assert "data-caption-start" not in html
    assert Path(result.data["caption_sidecar"]).is_file()


def test_ffmpeg_standalone_burn_reports_contract(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subtitle = tmp_path / "captions.srt"
    output = tmp_path / "burned.mp4"
    _video(source)
    subtitle.write_text("1\n00:00:00,000 --> 00:00:01,400\nHello world\n", encoding="utf-8")
    result = VideoCompose().execute(
        {
            "operation": "burn_subtitles",
            "input_path": str(source),
            "subtitle_path": str(subtitle),
            "output_path": str(output),
        }
    )
    assert result.success, result.error
    assert output.is_file()
    assert result.data["caption_render_contract"]["runtime"] == "ffmpeg"
    assert result.data["caption_render_contract"]["mode"] == "burn_in"


def test_audio_quality_rejects_full_silence(tmp_path: Path):
    silence = tmp_path / "silence.wav"
    _audio_fixture(silence, "0")
    report = probe_audio_quality(silence)
    assert report["valid"] is False
    assert report["audio_channels"] == 2
    assert report["silence_ratio"] == 1.0
    assert any("silence ratio" in error for error in report["errors"])


def test_audio_quality_rejects_clipped_samples_and_true_peak(tmp_path: Path):
    clipped = tmp_path / "clipped.wav"
    _audio_fixture(clipped, "sin(2*PI*440*t)*10")
    report = probe_audio_quality(clipped)
    assert report["valid"] is False
    assert report["clipping_samples"] > 0
    assert report["true_peak_db"] > -1.0
    assert any("clipping" in error for error in report["errors"])


def test_audio_quality_rejects_video_without_audio_channel(tmp_path: Path):
    silent_video = tmp_path / "silent-video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1", "-c:v", "libx264", str(silent_video)],
        capture_output=True,
        text=True,
        check=True,
    )
    report = probe_audio_quality(silent_video)
    assert report["valid"] is False
    assert report["audio_channels"] == 0
    assert any("audio stream/channel" in error for error in report["errors"])


def test_caption_contract_rejects_truncated_caption_timing():
    with pytest.raises(CaptionContractError, match="beyond video duration"):
        build_caption_render_contract(
            runtime="hyperframes",
            mode="overlay",
            cues=[{"start": 0.5, "end": 2.1, "text": "late caption"}],
            width=1920,
            height=1080,
            duration_seconds=2,
        )


def test_caption_accessibility_rejects_low_contrast_and_fast_reading():
    report = caption_accessibility_report(
        cues=[{"start": 0, "end": 1, "text": "This caption is far too dense to read in one second"}],
        width=1920,
        height=1080,
        font_size=42,
        max_lines=2,
        max_chars_per_line=42,
        safe_area={"bottom_ratio": 0.12},
        style={"primary_color": "#AAAAAA", "back_color": "#FFFFFF"},
    )
    assert report["valid"] is False
    assert any("contrast" in issue for issue in report["issues"])
    assert any("reading speed" in issue for issue in report["issues"])


def test_profile_caption_policy_rejects_small_text_and_narrow_safe_area():
    with pytest.raises(CaptionContractError, match="minimum|safe area"):
        build_caption_render_contract(
            runtime="ffmpeg",
            mode="burn_in",
            cues=[{"start": 0, "end": 1.5, "text": "Readable enough"}],
            width=1080,
            height=1920,
            duration_seconds=2,
            font_size=18,
            safe_area={"bottom_ratio": 0.05},
            profile_name="youtube_shorts",
        )
