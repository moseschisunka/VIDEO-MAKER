"""Deterministic technical/video-frame quality inspection for final review.

This module deliberately inspects decoded frames and real streams.  File size
is retained as provenance only; it is never used as a proxy for a black,
frozen, duplicate, corrupt, or missing-media frame.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from lib.media_profiles import get_profile


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, capture_output=True, timeout=timeout, check=False)


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _fps(value: Any) -> float:
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / max(float(denominator), 1.0)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        return {"error": _text(result.stderr).strip() or "ffprobe failed"}
    try:
        payload = json.loads(_text(result.stdout) or "{}")
    except json.JSONDecodeError as exc:
        return {"error": f"ffprobe returned invalid JSON: {exc}"}
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "format_name": fmt.get("format_name"),
        "duration_seconds": duration,
        "video": video if isinstance(video, dict) else None,
        "audio": audio if isinstance(audio, dict) else None,
        "stream_count": len(streams),
    }


def _frame_bytes(path: Path, timestamp: float) -> bytes:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    result = _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            f"{max(0.0, float(timestamp)):.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=64:36:force_original_aspect_ratio=decrease,pad=64:36:(ow-iw)/2:(oh-ih)/2,format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout:
        return b""
    return result.stdout


def _decode_errors(path: Path) -> list[str]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    result = _run(
        [ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
        timeout=180,
    )
    return [line.strip() for line in _text(result.stderr).splitlines() if line.strip()]


def _black_intervals(path: Path) -> list[dict[str, float]]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    result = _run(
        [
            ffmpeg,
            "-v",
            "info",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0.15:pix_th=0.02",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=180,
    )
    text = _text(result.stderr)
    intervals: list[dict[str, float]] = []
    for match in re.finditer(
        r"black_start:(?P<start>[\d.]+)\s+black_end:(?P<end>[\d.]+)\s+black_duration:(?P<duration>[\d.]+)",
        text,
    ):
        intervals.append({
            "start_seconds": float(match.group("start")),
            "end_seconds": float(match.group("end")),
            "duration_seconds": float(match.group("duration")),
        })
    return intervals


def _frozen_intervals(path: Path, duration: float | None = None) -> list[dict[str, float]]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    result = _run(
        [
            ffmpeg,
            "-v",
            "info",
            "-i",
            str(path),
            "-vf",
            "freezedetect=n=-60dB:d=0.8",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=180,
    )
    text = _text(result.stderr)
    starts = [float(value) for value in re.findall(r"freeze_start:\s*([\d.]+)", text)]
    durations = [float(value) for value in re.findall(r"freeze_duration:\s*([\d.]+)", text)]
    ends = [float(value) for value in re.findall(r"freeze_end:\s*([\d.]+)", text)]
    intervals: list[dict[str, float]] = []
    for index, start in enumerate(starts):
        freeze_duration = durations[index] if index < len(durations) else 0.0
        end = ends[index] if index < len(ends) else (
            float(duration) if duration and duration > start else start + freeze_duration
        )
        intervals.append({
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": max(0.0, end - start) if end > start else freeze_duration,
        })
    return intervals


def inspect_video(
    input_path: str | Path,
    *,
    profile: str | None = None,
    sample_count: int = 12,
    black_mean_threshold: float = 3.0,
    allowed_static_holds: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Inspect streams, decoded samples, black/freeze intervals and repeats.

    A static title/diagram hold is valid only when the edit explicitly declares
    its time range in ``metadata.qa_policy.allowed_static_holds``. Undeclared
    long freezes remain release-blocking, so renderer stalls cannot be hidden
    behind a generic "static content" exception.
    """
    path = Path(input_path)
    report: dict[str, Any] = {
        "file": str(path),
        "valid": False,
        "errors": [],
        "warnings": [],
        "samples": [],
        "black_intervals": [],
        "frozen_intervals": [],
        "duplicate_frame_groups": [],
    }
    if not path.is_file():
        report["errors"].append(f"video file not found: {path}")
        return report
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        report["errors"].append("ffmpeg and ffprobe are required for video inspection")
        return report

    probe = _probe(path)
    report["probe"] = probe
    if probe.get("error"):
        report["errors"].append(str(probe["error"]))
        return report
    video = probe.get("video")
    if not isinstance(video, dict):
        report["errors"].append("no video stream found")
        return report
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    duration = float(probe.get("duration_seconds") or 0.0)
    actual_fps = _fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    report.update({
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "fps": round(actual_fps, 3),
        "video_codec": video.get("codec_name"),
        "has_audio": isinstance(probe.get("audio"), dict),
        "audio_channels": int((probe.get("audio") or {}).get("channels") or 0),
        "audio_channel_layout": (probe.get("audio") or {}).get("channel_layout"),
    })
    errors = report["errors"]
    if duration <= 0:
        errors.append("video duration is missing or non-positive")
    if width < 1 or height < 1:
        errors.append("video dimensions are missing or invalid")
    if not isinstance(probe.get("audio"), dict):
        errors.append("no audio stream found")
    elif int((probe.get("audio") or {}).get("channels") or 0) < 1:
        errors.append("audio stream has no declared channels")

    if profile:
        try:
            facts = get_profile(profile)
            report["profile"] = facts.name
            expected_aspect = float(facts.width) / float(facts.height)
            actual_aspect = float(width) / float(height) if height else 0.0
            if width != facts.width or height != facts.height:
                errors.append(
                    f"resolution {width}x{height} does not match profile {facts.name} ({facts.width}x{facts.height})"
                )
            if expected_aspect and abs(actual_aspect - expected_aspect) > 0.01:
                errors.append(f"aspect ratio {actual_aspect:.4f} does not match profile {facts.name}")
            if actual_fps and abs(actual_fps - float(facts.fps)) > 0.5:
                errors.append(f"fps {actual_fps:.3f} does not match profile {facts.name} ({facts.fps})")
        except Exception as exc:
            errors.append(f"unknown output profile {profile!r}: {exc}")

    report["decode_errors"] = _decode_errors(path)
    if report["decode_errors"]:
        errors.append("decoded video contains FFmpeg errors")
    try:
        report["black_intervals"] = _black_intervals(path)
        report["frozen_intervals"] = _frozen_intervals(path, duration=duration)
    except Exception as exc:
        errors.append(f"frame filter inspection failed: {exc}")

    count = max(4, min(60, int(sample_count or 12)))
    timestamps = [
        min(max(0.01, duration * ((index + 0.5) / count)), max(0.01, duration - 0.01))
        for index in range(count)
    ] if duration > 0 else []
    hashes: list[str] = []
    sample_timestamps: list[float] = []
    for timestamp in timestamps:
        data = _frame_bytes(path, timestamp)
        if not data:
            errors.append(f"could not decode a frame at {timestamp:.3f}s")
            continue
        mean = sum(data) / max(1, len(data))
        maximum = max(data) if data else 0
        frame = {
            "timestamp_seconds": round(timestamp, 3),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mean_luma": round(mean, 3),
            "max_luma": maximum,
            "black": bool(mean <= black_mean_threshold and maximum <= 8),
        }
        report["samples"].append(frame)
        hashes.append(frame["sha256"])
        sample_timestamps.append(timestamp)

    black_samples = [item for item in report["samples"] if item.get("black")]
    if black_samples:
        errors.append(
            "decoded black/blank frame samples: "
            + ", ".join(f"{item['timestamp_seconds']:.3f}s" for item in black_samples)
        )
    if report["black_intervals"]:
        errors.append(
            "decoded black/blank intervals detected by blackdetect: "
            + ", ".join(
                f"{item['start_seconds']:.3f}-{item['end_seconds']:.3f}s"
                for item in report["black_intervals"]
            )
        )
    long_freezes = [item for item in report["frozen_intervals"] if item.get("duration_seconds", 0) >= 0.8]
    allowed_holds = []
    for raw in allowed_static_holds or []:
        if not isinstance(raw, Mapping):
            continue
        try:
            start = float(raw.get("start_seconds"))
            end = float(raw.get("end_seconds"))
        except (TypeError, ValueError):
            continue
        if end > start >= 0:
            allowed_holds.append({"start_seconds": start, "end_seconds": end})
    allowed_freezes = [
        item for item in long_freezes
        if any(
            hold["start_seconds"] <= float(item.get("start_seconds", 0)) + 0.05
            and hold["end_seconds"] >= float(item.get("end_seconds", 0)) - 0.05
            for hold in allowed_holds
        )
    ]
    unapproved_freezes = [item for item in long_freezes if item not in allowed_freezes]
    report["allowed_static_holds"] = allowed_holds
    report["allowed_frozen_intervals"] = allowed_freezes
    report["unapproved_frozen_intervals"] = unapproved_freezes
    if unapproved_freezes:
        errors.append("freezedetect found one or more frozen intervals")
    elif allowed_freezes:
        report["warnings"].append(
            "freezedetect intervals are covered by explicitly declared static holds"
        )

    # Identify repeated sampled frames separated by at least one other sample;
    # a simple freeze is already reported above, while this catches duplicated
    # shots such as A → B → A without treating adjacent transition frames as a
    # duplicate cut.
    positions: dict[str, list[int]] = {}
    for index, value in enumerate(hashes):
        positions.setdefault(value, []).append(index)
    duplicate_groups = [
        {"sha256": value, "sample_indexes": indexes}
        for value, indexes in positions.items()
        if len(indexes) >= 2 and any((right - left) >= 2 for left, right in zip(indexes, indexes[1:]))
    ]
    report["duplicate_frame_groups"] = duplicate_groups
    allowed_duplicate_groups = [
        group for group in duplicate_groups
        if allowed_holds
        and all(
            any(
                hold["start_seconds"] - 0.05 <= sample_timestamps[index] <= hold["end_seconds"] + 0.05
                for hold in allowed_holds
            )
            for index in group.get("sample_indexes", [])
            if index < len(sample_timestamps)
        )
    ]
    unapproved_duplicate_groups = [
        group for group in duplicate_groups if group not in allowed_duplicate_groups
    ]
    report["allowed_duplicate_frame_groups"] = allowed_duplicate_groups
    report["unapproved_duplicate_frame_groups"] = unapproved_duplicate_groups
    if unapproved_duplicate_groups:
        errors.append("duplicate sampled frames indicate a repeated shot or frozen segment")
    elif allowed_duplicate_groups:
        report["warnings"].append(
            "duplicate sampled frames are covered by explicitly declared static holds"
        )

    report["valid"] = not errors
    return report


__all__ = ["inspect_video"]
