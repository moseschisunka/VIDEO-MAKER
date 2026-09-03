"""PR-1025 media-probe boundary regressions."""

from __future__ import annotations

import subprocess

import pytest

from tools.video.video_compose import VideoCompose


def test_video_compose_declares_ffprobe_dependency():
    assert "cmd:ffmpeg" in VideoCompose.dependencies
    assert "cmd:ffprobe" in VideoCompose.dependencies


@pytest.mark.parametrize(
    ("probe_output", "expected"),
    [("audio\n", True), ("video\naudio\n", True), ("video\n", False), ("", False)],
)
def test_audio_stream_probe_requires_an_explicit_audio_stream(
    monkeypatch, probe_output: str, expected: bool
):
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *args, **kwargs: probe_output,
    )

    assert VideoCompose._has_audio_stream("clip.mp4") is expected


def test_audio_stream_probe_fails_closed_when_ffprobe_is_missing(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(subprocess, "check_output", missing)

    with pytest.raises(RuntimeError, match="ffprobe is required"):
        VideoCompose._has_audio_stream("clip.mp4")


def test_audio_stream_probe_fails_closed_on_probe_error(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], output="invalid media")

    monkeypatch.setattr(subprocess, "check_output", failed)

    with pytest.raises(RuntimeError, match="ffprobe failed while inspecting source clip.mp4"):
        VideoCompose._has_audio_stream("clip.mp4")


def test_audio_stream_probe_fails_closed_on_probe_timeout(monkeypatch):
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(subprocess, "check_output", timed_out)

    with pytest.raises(RuntimeError, match="ffprobe timed out"):
        VideoCompose._has_audio_stream("clip.mp4")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("in_seconds", -1, "cannot be negative"),
        ("out_seconds", 0, "greater than in_seconds"),
        ("out_seconds", "nan", "must be finite"),
        ("speed", 0, "greater than zero"),
        ("speed", "false", "must be numeric"),
    ],
)
def test_compose_rejects_invalid_source_intervals(field, value, message):
    cut = {"source": "clip.mp4", "in_seconds": 0, "out_seconds": 2, "speed": 1}
    cut[field] = value

    assert message in (VideoCompose._validate_compose_cuts([cut]) or "")


def test_compose_normalizes_numeric_interval_strings():
    cut = {"source": "clip.mp4", "in_seconds": "0.5", "out_seconds": "2.5", "speed": "1.25"}

    assert VideoCompose._validate_compose_cuts([cut]) is None
    assert cut["in_seconds"] == 0.5
    assert cut["out_seconds"] == 2.5
    assert cut["speed"] == 1.25


def test_compose_rejects_missing_requested_audio_source(tmp_path):
    result = VideoCompose()._compose(
        {
            "edit_decisions": {
                "cuts": [
                    {"source": "clip.mp4", "in_seconds": 0, "out_seconds": 2}
                ]
            },
            "audio_path": str(tmp_path / "missing.wav"),
            "output_path": str(tmp_path / "out.mp4"),
        }
    )

    assert result.success is False
    assert "Audio source not found" in (result.error or "")
