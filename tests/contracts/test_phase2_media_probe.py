"""PR-207 contracts for ffprobe-backed media facts and report honesty."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.output_promotion import OutputPromotionError, probe_media, validate_media_contract


def _make_video(path: Path, *, duration: float = 1.0, size: str = "1920x1080") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x123B31:s={size}:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def test_probe_reports_actual_streams_codecs_dimensions_and_duration(tmp_path: Path) -> None:
    output = tmp_path / "render.mp4"
    _make_video(output, duration=1.2)

    facts = probe_media(output)

    assert facts["valid_container"] is True
    assert facts["width"] == 1920
    assert facts["height"] == 1080
    assert facts["fps"] == pytest.approx(30.0, abs=0.1)
    assert facts["duration_seconds"] == pytest.approx(1.2, abs=0.1)
    assert facts["video_codec"] == "h264"
    assert facts["audio_codec"] == "aac"
    assert facts["has_audio"] is True
    assert facts["stream_count"] == 2


def test_media_contract_rejects_profile_mismatch_and_duration_drift(tmp_path: Path) -> None:
    output = tmp_path / "render.mp4"
    _make_video(output, duration=1.0, size="1280x720")
    facts = probe_media(output)

    with pytest.raises(OutputPromotionError, match="dimensions"):
        validate_media_contract(facts, profile="youtube_landscape")

    with pytest.raises(OutputPromotionError, match="outside the expected"):
        validate_media_contract(facts, expected_duration_seconds=10.0)
