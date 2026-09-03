"""PR-204 contracts for candidate probing and atomic promotion."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.output_promotion import (
    OutputPromotionError,
    candidate_path,
    probe_media,
    promote_candidate,
)


def _make_video(path: Path, *, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=0x123B31:s=1920x1080:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def test_candidate_is_probed_and_atomically_promoted(tmp_path: Path) -> None:
    candidates = tmp_path / "runs" / "run" / "candidates"
    final = tmp_path / "renders" / "final.mp4"
    candidate = candidate_path(candidates, final)
    _make_video(candidate)

    probe = probe_media(candidate)
    assert probe["valid_container"] is True
    assert probe["width"] == 1920
    assert probe["height"] == 1080
    assert probe["has_audio"] is True

    previous = b"previous-final"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(previous)
    promoted = promote_candidate(
        candidate,
        final,
        profile="youtube_landscape",
        expected_duration_seconds=1.0,
        provenance={"run_id": "12345678-1234-4234-8234-123456789abc"},
    )
    assert final.is_file()
    assert final.read_bytes() != previous
    assert not candidate.exists()
    assert promoted["path"] == str(final.resolve())
    assert len(promoted["sha256"]) == 64
    assert promoted["probe"]["duration_seconds"] > 0
    assert promoted["provenance"]["run_id"]


def test_invalid_candidate_does_not_replace_existing_final(tmp_path: Path) -> None:
    candidates = tmp_path / "runs" / "run" / "candidates"
    final = tmp_path / "renders" / "final.mp4"
    candidate = candidate_path(candidates, final)
    candidate.write_bytes(b"not a video")
    final.parent.mkdir(parents=True, exist_ok=True)
    previous = b"known-good-final"
    final.write_bytes(previous)

    with pytest.raises(OutputPromotionError):
        promote_candidate(candidate, final, profile="youtube_landscape")
    assert final.read_bytes() == previous
    assert candidate.exists()
