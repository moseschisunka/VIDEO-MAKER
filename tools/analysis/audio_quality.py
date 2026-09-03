"""Measured audio-quality checks for final mixes and audio stems.

The checks intentionally use FFmpeg's meters rather than filename metadata:
integrated loudness and true peak are measured from the rendered samples, and
silence/clipping are reported as actionable facts.  This module is read-only
against the input media and is safe to run during a release gate.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from lib.media_profiles import get_profile
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


_NUMBER = r"(-?(?:\d+(?:\.\d*)?|\.\d+)|-?inf)"


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _float_or_none(value: str | None) -> float | None:
    if value is None or value.lower() in {"-inf", "inf", "nan"}:
        return None if value is None or value.lower() == "-inf" else math.inf
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _duration(path: Path) -> float | None:
    result = _run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return _float_or_none(result.stdout.strip())


def _audio_stream_info(path: Path) -> dict[str, Any]:
    """Return the measured audio stream/channel facts used by release gates."""

    result = _run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,channel_layout",
            "-of",
            "json",
            str(path),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        return {}
    try:
        stream = (json.loads(result.stdout or "{}").get("streams") or [{}])[0]
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(stream, dict):
        return {}
    try:
        channels = int(stream.get("channels") or 0)
    except (TypeError, ValueError):
        channels = 0
    return {
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream["sample_rate"]) if str(stream.get("sample_rate", "")).isdigit() else None,
        "channels": channels,
        "channel_layout": stream.get("channel_layout"),
    }


def _parse_ebur128(stderr: str) -> tuple[float | None, float | None]:
    integrated = re.findall(r"\bI:\s*" + _NUMBER + r"\s+LUFS", stderr, flags=re.IGNORECASE)
    true_peak = re.findall(
        r"\b(?:True\s+peak|TPK|TP):\s*" + _NUMBER + r"\s+dB(?:FS|TP)",
        stderr,
        flags=re.IGNORECASE,
    )
    if not true_peak:
        # FFmpeg's summary uses a two-line form: "True peak:" followed by
        # "Peak: -1.0 dBFS".
        true_peak = re.findall(
            r"True\s+peak:\s*\n\s*Peak:\s*" + _NUMBER + r"\s+dB(?:FS|TP)",
            stderr,
            flags=re.IGNORECASE,
        )
    # The final summary is the authoritative value when a stream has multiple
    # loudness sections.  FFmpeg may print -inf for silence.
    i_value = _float_or_none(integrated[-1]) if integrated else None
    tp_value = _float_or_none(true_peak[-1]) if true_peak else None
    return i_value, tp_value


def _parse_clipping(stderr: str) -> tuple[float | None, int]:
    max_values = re.findall(r"max_volume:\s*" + _NUMBER + r"\s*dB", stderr, flags=re.IGNORECASE)
    histograms = re.findall(r"histogram_0db:\s*(\d+)", stderr, flags=re.IGNORECASE)
    max_db = _float_or_none(max_values[-1]) if max_values else None
    clipped = int(histograms[-1]) if histograms else 0
    return max_db, clipped


def _parse_silence(stderr: str, duration: float | None) -> tuple[float, list[dict[str, float]]]:
    starts = [float(value) for value in re.findall(r"silence_start:\s*([\d.]+)", stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([\d.]+)", stderr)]
    intervals: list[dict[str, float]] = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else (float(duration or start) if duration is not None else start)
        if end >= start:
            intervals.append({"start_seconds": start, "end_seconds": end, "duration_seconds": end - start})
    total_silence = sum(item["duration_seconds"] for item in intervals)
    ratio = total_silence / duration if duration and duration > 0 else 0.0
    return min(1.0, max(0.0, ratio)), intervals


def probe_audio_quality(
    input_path: str | Path,
    *,
    profile: str = "generic_hd",
    speech_path: str | Path | None = None,
) -> dict[str, Any]:
    """Measure and evaluate an audio file against a media-profile contract."""

    path = Path(input_path)
    profile_facts = get_profile(profile)
    base: dict[str, Any] = {
        "file": str(path),
        "profile": profile_facts.name,
        "target_lufs": profile_facts.audio_loudness_lufs,
        "loudness_tolerance_lufs": profile_facts.audio_loudness_tolerance_lufs,
        "true_peak_limit_db": profile_facts.audio_true_peak_db,
        "silence_max_ratio": profile_facts.audio_silence_max_ratio,
        "clipping_max_samples": profile_facts.audio_clipping_max_samples,
    }
    if not path.is_file():
        base.update({"valid": False, "errors": [f"audio file not found: {path}"]})
        return base
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        base.update({"valid": False, "errors": ["ffmpeg and ffprobe are required for audio quality checks"]})
        return base

    duration = _duration(path)
    audio_stream = _audio_stream_info(path)
    ebur = _run([ffmpeg, "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"], timeout=180)
    loudness, true_peak = _parse_ebur128((ebur.stderr or "") + "\n" + (ebur.stdout or ""))
    volume = _run([ffmpeg, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], timeout=180)
    max_volume, clipped = _parse_clipping((volume.stderr or "") + "\n" + (volume.stdout or ""))
    silence = _run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-af", "silencedetect=n=-60dB:d=0.1", "-f", "null", "-"],
        timeout=180,
    )
    silence_ratio, silence_intervals = _parse_silence(
        (silence.stderr or "") + "\n" + (silence.stdout or ""), duration
    )

    errors: list[str] = []
    if duration is None or duration <= 0:
        errors.append("audio duration is missing or non-positive")
    if not audio_stream or int(audio_stream.get("channels") or 0) < 1:
        errors.append("audio stream/channel information is missing")
    if loudness is None:
        errors.append("integrated loudness could not be measured")
    elif abs(loudness - profile_facts.audio_loudness_lufs) > profile_facts.audio_loudness_tolerance_lufs:
        errors.append(
            f"integrated loudness {loudness:.2f} LUFS is outside target "
            f"{profile_facts.audio_loudness_lufs:.2f} ± {profile_facts.audio_loudness_tolerance_lufs:.2f} LU"
        )
    if true_peak is None:
        errors.append("true peak could not be measured")
    elif true_peak > profile_facts.audio_true_peak_db + 0.1:
        errors.append(
            f"true peak {true_peak:.2f} dBTP exceeds {profile_facts.audio_true_peak_db:.2f} dBTP"
        )
    if clipped > profile_facts.audio_clipping_max_samples:
        errors.append(f"clipping detected: {clipped} samples at 0 dBFS")
    if silence_ratio > profile_facts.audio_silence_max_ratio:
        errors.append(
            f"silence ratio {silence_ratio:.3f} exceeds {profile_facts.audio_silence_max_ratio:.3f}"
        )

    speech_report: dict[str, Any] | None = None
    if speech_path:
        speech_report = probe_audio_quality(speech_path, profile=profile)
        speech_lufs = speech_report.get("integrated_lufs")
        if speech_lufs is None or speech_lufs < -35:
            errors.append("speech stem is silent or too quiet for intelligibility")

    base.update(
        {
            "duration_seconds": round(duration, 3) if duration is not None else None,
            "audio_stream": audio_stream,
            "audio_channels": int(audio_stream.get("channels") or 0),
            "audio_channel_layout": audio_stream.get("channel_layout"),
            "integrated_lufs": round(loudness, 3) if loudness is not None else None,
            "true_peak_db": round(true_peak, 3) if true_peak is not None else None,
            "max_volume_db": round(max_volume, 3) if max_volume is not None else None,
            "clipping_samples": clipped,
            "silence_ratio": round(silence_ratio, 5),
            "silence_intervals": silence_intervals,
            "speech": speech_report,
            "valid": not errors,
            "errors": errors,
        }
    )
    return base


def assert_audio_quality(report: dict[str, Any]) -> None:
    """Raise a concise error when a measured report is not release-safe."""

    if report.get("valid") is not True:
        errors = "; ".join(str(item) for item in report.get("errors") or [])
        raise ValueError(f"audio quality check failed: {errors or 'unknown error'}")


class AudioQualityProbe(BaseTool):
    """Registry tool exposing measured loudness/peak/silence checks."""

    name = "audio_quality_probe"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "ffmpeg"
    stability = ToolStability.PRODUCTION
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL
    dependencies = ["cmd:ffmpeg", "cmd:ffprobe"]
    capabilities = ["loudness_meter", "true_peak_meter", "silence_detection", "clipping_detection", "speech_intelligibility"]
    best_for = ["release-gate audio checks", "profile-specific loudness validation"]
    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string"},
            "profile": {"type": "string", "default": "generic_hd"},
            "speech_path": {"type": "string"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=0, network_required=False)
    side_effects = []

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if shutil.which("ffmpeg") and shutil.which("ffprobe") else ToolStatus.UNAVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        report = probe_audio_quality(
            inputs.get("input_path", ""),
            profile=str(inputs.get("profile") or "generic_hd"),
            speech_path=inputs.get("speech_path"),
        )
        if report.get("valid") is not True:
            return ToolResult(success=False, data=report, error="; ".join(report.get("errors") or ["audio quality check failed"]))
        return ToolResult(success=True, data=report)


__all__ = ["AudioQualityProbe", "assert_audio_quality", "probe_audio_quality"]
