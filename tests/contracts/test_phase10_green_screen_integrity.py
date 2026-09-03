"""PR-1027 green-screen completeness and atomic-output regressions."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.video.green_screen_processor import GreenScreenProcessor


def _completed() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr="")


def test_failed_frame_extraction_rejects_partial_output(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    def fail_after_partial(self, cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"partial")
        raise RuntimeError("ffmpeg failed after one frame")

    monkeypatch.setattr(GreenScreenProcessor, "run_command", fail_after_partial)

    assert GreenScreenProcessor()._extract_frames(tmp_path / "input.mp4", frames_dir, 15, 0) == 0
    assert list(frames_dir.glob("frame_*.png")), "fixture must prove partial output existed"


def test_frame_extraction_rejects_gaps(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    def write_gap(self, cmd, **kwargs):
        (frames_dir / "frame_000001.png").write_bytes(b"one")
        (frames_dir / "frame_000003.png").write_bytes(b"three")
        return _completed()

    monkeypatch.setattr(GreenScreenProcessor, "run_command", write_gap)

    assert GreenScreenProcessor()._extract_frames(tmp_path / "input.mp4", frames_dir, 15, 0) == 0


def test_chromakey_requires_every_frame_to_succeed(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    processed_dir = tmp_path / "processed"
    frames_dir.mkdir()
    processed_dir.mkdir()
    (frames_dir / "frame_000001.png").write_bytes(b"one")
    (frames_dir / "frame_000002.png").write_bytes(b"two")

    def one_frame_only(self, cmd, **kwargs):
        output = Path(cmd[-1])
        if output.name == "frame_000001.png":
            output.write_bytes(b"processed")
            return _completed()
        raise RuntimeError("second frame failed")

    monkeypatch.setattr(GreenScreenProcessor, "run_command", one_frame_only)

    assert not GreenScreenProcessor()._process_chromakey(
        frames_dir, processed_dir, "#0E172A", 2, 320, 240
    )


def test_chromakey_retry_keeps_requested_background_contract(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    processed_dir = tmp_path / "processed"
    frames_dir.mkdir()
    processed_dir.mkdir()
    (frames_dir / "frame_000001.png").write_bytes(b"one")
    calls: list[list[str]] = []

    def fail_once_then_write(self, cmd, **kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            raise RuntimeError("primary filter unsupported")
        Path(cmd[-1]).write_bytes(b"composited")
        return _completed()

    monkeypatch.setattr(GreenScreenProcessor, "run_command", fail_once_then_write)

    assert GreenScreenProcessor()._process_chromakey(
        frames_dir, processed_dir, "#0E172A", 1, 320, 240
    )
    assert len(calls) == 2, "the documented retry must actually execute"
    retry = " ".join(calls[1])
    assert "-filter_complex" in retry
    assert "size=320x240" in retry
    assert "0x0E172A" in retry


def test_execute_promotes_only_valid_candidate_and_preserves_previous_output(tmp_path, monkeypatch):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    output_path.write_bytes(b"previous-final")

    def probe(self, path):
        return {"duration": 1.0, "width": 320, "height": 240, "fps": 15.0}

    def extract(self, input_file, frames_dir, fps, max_frames):
        (frames_dir / "frame_000001.png").write_bytes(b"frame")
        return 1

    def process(self, *args, **kwargs):
        processed_dir = args[1]
        (processed_dir / "frame_000001.png").write_bytes(b"processed")
        return True

    def reconstruct(self, frames_dir, candidate, fps, width, height):
        candidate.write_bytes(b"new-final")

    monkeypatch.setattr(GreenScreenProcessor, "_probe_video", probe)
    monkeypatch.setattr(GreenScreenProcessor, "_extract_frames", extract)
    monkeypatch.setattr(GreenScreenProcessor, "_process_chromakey", process)
    monkeypatch.setattr(GreenScreenProcessor, "_reconstruct_video", reconstruct)

    result = GreenScreenProcessor().execute(
        {"input_path": str(input_path), "output_path": str(output_path), "method": "chromakey"}
    )

    assert result.success
    assert output_path.read_bytes() == b"new-final"
    assert not list(tmp_path.glob(".*.candidate*.mp4"))


def test_execute_failed_reconstruction_does_not_clobber_previous_output(tmp_path, monkeypatch):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    output_path.write_bytes(b"previous-final")

    def probe(self, path):
        return {"duration": 1.0, "width": 320, "height": 240, "fps": 15.0}

    def extract(self, input_file, frames_dir, fps, max_frames):
        (frames_dir / "frame_000001.png").write_bytes(b"frame")
        return 1

    monkeypatch.setattr(GreenScreenProcessor, "_probe_video", probe)
    monkeypatch.setattr(GreenScreenProcessor, "_extract_frames", extract)
    monkeypatch.setattr(GreenScreenProcessor, "_process_chromakey", lambda *args, **kwargs: True)

    def fail_reconstruct(self, frames_dir, candidate, fps, width, height):
        candidate.write_bytes(b"partial-candidate")
        raise RuntimeError("encoder failed")

    monkeypatch.setattr(GreenScreenProcessor, "_reconstruct_video", fail_reconstruct)

    result = GreenScreenProcessor().execute(
        {"input_path": str(input_path), "output_path": str(output_path), "method": "chromakey"}
    )

    assert not result.success
    assert output_path.read_bytes() == b"previous-final"
    assert not list(tmp_path.glob(".*.candidate*.mp4"))


def test_probe_rejects_non_positive_media_facts(monkeypatch, tmp_path):
    def bad_probe(self, cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=list(cmd),
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"duration": "0"},
                    "streams": [{"width": 320, "height": 240, "r_frame_rate": "15/1"}],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(GreenScreenProcessor, "run_command", bad_probe)
    assert GreenScreenProcessor()._probe_video(tmp_path / "bad.mp4") is None


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required",
)
def test_execute_real_chromakey_promotes_valid_video(tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x00FF00:size=160x120:duration=0.4:rate=5",
            "-vf",
            "drawbox=x=40:y=30:w=80:h=60:color=red:t=fill",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(input_path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )

    result = GreenScreenProcessor().execute(
        {
            "input_path": str(input_path),
            "output_path": str(output_path),
            "method": "chromakey",
            "fps": 5,
        }
    )

    assert result.success, result.error
    assert output_path.exists() and output_path.stat().st_size > 0
    assert result.data["frame_count"] == 2
