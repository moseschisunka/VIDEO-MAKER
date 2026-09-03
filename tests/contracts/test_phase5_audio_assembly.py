"""Safe audio assembly regression tests for Phase 5."""

import shutil
import subprocess

import pytest

from lib.audio_assembly import assemble_audio_segments


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_assembly_decodes_and_reencodes_ordered_segments(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    output = tmp_path / "narration.mp3"
    for path, frequency in ((first, 440), (second, 660)):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=0.35", str(path)],
            capture_output=True,
            check=True,
            timeout=30,
        )

    report = assemble_audio_segments([first, second], output)
    assert output.is_file() and output.stat().st_size > 0
    assert report["segment_count"] == 2
    assert report["assembly"] == "ffmpeg_decode_normalize_encode"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(output)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert float(probe.stdout.strip()) == pytest.approx(0.7, abs=0.15)


def test_assembly_rejects_missing_or_empty_segments(tmp_path):
    with pytest.raises(ValueError, match="missing or empty"):
        assemble_audio_segments([tmp_path / "missing.mp3"], tmp_path / "out.mp3")
