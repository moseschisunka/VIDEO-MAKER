"""PR-8G narrated multi-runtime integration gate."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.caption_contracts import validate_verified_transcript
from lib.music_contracts import normalize_music_source
from tools.analysis.audio_quality import probe_audio_quality
from tools.audio.audio_mixer import AudioMixer
from tools.subtitle.subtitle_gen import SubtitleGen
from tools.video.hyperframes_compose import HyperFramesCompose
from tools.video.remotion_caption_burn import RemotionCaptionBurn
from tools.video.video_compose import VideoCompose


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe required",
)


def _sine(path: Path, duration: float = 2.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-ar", "48000", "-ac", "2", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def _video(path: Path, duration: float = 2.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x123B31:s=320x240:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def _transcript() -> dict:
    return {
        "verified": True,
        "verification_status": "approved",
        "language": "en-US",
        "segments": [{"words": [
            {"word": "Open", "start": 0.0, "end": 0.5},
            {"word": "Montage", "start": 0.55, "end": 1.25},
        ]}],
    }


def test_phase8_gate_audio_caption_and_runtime_parity(tmp_path: Path):
    transcript = _transcript()
    verification = validate_verified_transcript(transcript, expected_language="en", expected_text="Open Montage")
    assert verification["valid"] is True, verification["errors"]
    assert normalize_music_source({"source_type": "none", "reason": "lesson has no music"})["source_type"] == "none"

    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    source_video = tmp_path / "source.mp4"
    _sine(voice)
    _sine(music)
    _video(source_video)

    mixed = tmp_path / "mixed.wav"
    mix = AudioMixer().execute(
        {
            "operation": "mix",
            "tracks": [
                {"path": str(voice), "role": "speech"},
                {"path": str(music), "role": "music", "volume": 0.18},
            ],
            "normalize": True,
            "quality_check": True,
            "profile": "generic_hd",
            "output_path": str(mixed),
        }
    )
    assert mix.success, mix.error
    quality = probe_audio_quality(mixed, profile="generic_hd", speech_path=voice)
    assert quality["valid"] is True, quality["errors"]

    captions = tmp_path / "captions.srt"
    subtitle = SubtitleGen().execute(
        {
            "segments": transcript["segments"],
            "transcript": transcript,
            "require_verified_transcript": True,
            "expected_language": "en-US",
            "expected_text": "Open Montage",
            "output_path": str(captions),
        }
    )
    assert subtitle.success, subtitle.error

    remotion_out = tmp_path / "remotion-captioned.mp4"
    remotion = RemotionCaptionBurn().execute(
        {
            "input_path": str(source_video),
            "output_path": str(remotion_out),
            "transcript": transcript,
            "require_verified_transcript": True,
            "force_ffmpeg": True,
        }
    )
    assert remotion.success, remotion.error
    assert remotion.data["caption_render_contract"]["runtime"] == "remotion"

    ffmpeg = VideoCompose().execute(
        {
            "operation": "burn_subtitles",
            "input_path": str(source_video),
            "subtitle_path": str(captions),
            "output_path": str(tmp_path / "ffmpeg-captioned.mp4"),
        }
    )
    assert ffmpeg.success, ffmpeg.error
    assert ffmpeg.data["caption_render_contract"]["runtime"] == "ffmpeg"

    workspace = tmp_path / "hyperframes"
    hyperframes = HyperFramesCompose().execute(
        {
            "operation": "scaffold_workspace",
            "workspace_path": str(workspace),
            "edit_decisions": {
                "version": "1.0",
                "render_runtime": "hyperframes",
                "renderer_family": "animation-first",
                "cuts": [{"id": "title", "source": "", "type": "text_card", "text": "Open Montage", "in_seconds": 0, "out_seconds": 2}],
            },
            "asset_manifest": {"assets": []},
            "subtitle_path": str(captions),
            "require_verified_transcript": False,
        }
    )
    assert hyperframes.success, hyperframes.error
    assert hyperframes.data["caption_render_contract"]["runtime"] == "hyperframes"
    assert (workspace / "caption_render_contract.json").is_file()
    assert "data-caption-start" in (workspace / "index.html").read_text(encoding="utf-8")


def test_phase8_gate_keeps_global_production_lock_and_evidence():
    tracker = (ROOT / "docs" / "production-readiness" / "PROGRESS_TRACKER.md").read_text(encoding="utf-8")
    assert "PR-8G" in tracker
    assert "Production decision | Not eligible" in tracker
    assert "PR-11G" in tracker
    evidence = (ROOT / "docs" / "production-readiness" / "evidence" / "PR-807.md").read_text(encoding="utf-8")
    assert "Status: **VERIFIED**" in evidence
