"""Validated, atomic promotion of renderer candidates to deliverables."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from filelock import FileLock, Timeout as FileLockTimeout
except ImportError:  # pragma: no cover - bundled environments include filelock
    FileLock = None

    class FileLockTimeout(TimeoutError):
        pass


class OutputPromotionError(ValueError):
    """Raised when a render candidate cannot be safely promoted."""


_PROMOTION_THREAD_LOCK = threading.RLock()


@contextmanager
def _promotion_lock(final: Path):
    """Serialize final-path replacement across threads and processes."""
    lock_path = final.with_name(final.name + ".promotion.lock")
    with _PROMOTION_THREAD_LOCK:
        if FileLock is None:
            yield
            return
        try:
            with FileLock(str(lock_path), timeout=30):
                yield
        except FileLockTimeout as exc:
            raise OutputPromotionError(
                f"timed out waiting for output promotion lock: {final}"
            ) from exc


def _parse_timestamp(value: datetime | str | None) -> datetime | None:
    """Normalise an ISO timestamp for freshness checks."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise OutputPromotionError(
                f"invalid run start timestamp for output freshness: {value!r}"
            )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def candidate_path(candidates_dir: Path | str, final_path: Path | str) -> Path:
    """Return a unique candidate path that retains the media extension."""
    root = Path(candidates_dir).expanduser().resolve()
    final = Path(final_path)
    suffix = final.suffix or ".mp4"
    name = f"{final.stem}.{uuid.uuid4().hex}.part{suffix}"
    path = root / name
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise OutputPromotionError("candidate path escapes the run candidates directory") from exc
    root.mkdir(parents=True, exist_ok=True)
    return path


def _parse_fps(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def probe_media(path: Path | str) -> dict[str, Any]:
    """Probe a candidate with ffprobe and return normalized media facts."""
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise OutputPromotionError(f"render candidate is missing or empty: {source}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise OutputPromotionError("ffprobe is required before output promotion")
    try:
        process = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=True,
        )
        payload = json.loads(process.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise OutputPromotionError(f"ffprobe failed for render candidate: {exc}") from exc
    if not isinstance(payload, dict):
        raise OutputPromotionError("ffprobe returned a non-object payload")
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise OutputPromotionError("ffprobe returned no stream list")
    video = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
    if video is None:
        raise OutputPromotionError("render candidate has no video stream")
    audio = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), None)
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        duration = None
    if duration is None or duration <= 0:
        raise OutputPromotionError("render candidate has no positive duration")
    try:
        width = int(video.get("width"))
        height = int(video.get("height"))
    except (TypeError, ValueError) as exc:
        raise OutputPromotionError("render candidate has invalid video dimensions") from exc
    return {
        "valid_container": True,
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "fps": _parse_fps(video.get("r_frame_rate") or video.get("avg_frame_rate")),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "has_audio": audio is not None,
        "stream_count": len(streams),
        "format_name": fmt.get("format_name"),
        "file_size_bytes": source.stat().st_size,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_fingerprint(path: Path | str) -> dict[str, Any]:
    """Return the hash, size, and nanosecond mtime of a local artifact.

    This is deliberately independent of media probing so callers can prove
    freshness even when a renderer produced an invalid file.  An invalid or
    missing path is an error rather than a stale-success signal.
    """
    source = Path(path).expanduser().resolve()
    try:
        stat = source.stat()
    except OSError as exc:
        raise OutputPromotionError(f"artifact fingerprint failed: {source}: {exc}") from exc
    if not source.is_file() or stat.st_size <= 0:
        raise OutputPromotionError(f"artifact fingerprint requires a non-empty file: {source}")
    return {
        "sha256": _sha256(source),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def validate_media_contract(
    probe: Mapping[str, Any],
    *,
    profile: str | None = None,
    expected_duration_seconds: float | None = None,
) -> None:
    """Validate probed media against the declared profile/timeline contract."""
    if not isinstance(probe, Mapping) or not probe.get("valid_container"):
        raise OutputPromotionError("media probe did not certify a valid container")
    if int(probe.get("width") or 0) <= 0 or int(probe.get("height") or 0) <= 0:
        raise OutputPromotionError("media probe returned invalid video dimensions")
    try:
        duration = float(probe.get("duration_seconds"))
    except (TypeError, ValueError):
        raise OutputPromotionError("media probe returned an invalid duration")
    if duration <= 0:
        raise OutputPromotionError("media probe returned a non-positive duration")

    if profile:
        try:
            from lib.media_profiles import get_profile

            media_profile = get_profile(profile)
        except (ImportError, ValueError) as exc:
            raise OutputPromotionError(f"unknown output profile {profile!r}: {exc}") from exc
        if int(probe["width"]) != int(media_profile.width) or int(probe["height"]) != int(media_profile.height):
            raise OutputPromotionError(
                f"rendered media dimensions {probe['width']}x{probe['height']} do not match "
                f"profile {profile!r} ({media_profile.width}x{media_profile.height})"
            )
        if probe.get("fps") is not None and abs(float(probe["fps"]) - float(media_profile.fps)) > 0.5:
            raise OutputPromotionError(
                f"rendered media fps {probe['fps']} does not match profile {profile!r} ({media_profile.fps})"
            )
        maximum = getattr(media_profile, "max_duration_seconds", None)
        if maximum is not None and duration > float(maximum) + 0.01:
            raise OutputPromotionError(
                f"rendered media duration {duration:.3f}s exceeds profile {profile!r} limit {maximum:g}s"
            )

    if expected_duration_seconds is not None:
        expected = float(expected_duration_seconds)
        from lib.media_profiles import duration_tolerance_seconds

        tolerance = duration_tolerance_seconds(expected)
        if abs(duration - expected) > tolerance:
            raise OutputPromotionError(
                f"render duration {duration:.3f}s is outside the expected "
                f"{expected:.3f}s ± {tolerance:.3f}s"
            )


def promote_candidate(
    candidate: Path | str,
    final_path: Path | str,
    *,
    profile: str | None = None,
    expected_duration_seconds: float | None = None,
    provenance: Mapping[str, Any] | None = None,
    run_started_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Probe and atomically promote a fresh candidate.

    A successful render must carry a candidate written after the current run
    started.  The final path is never treated as evidence by itself; its
    digest, size, and timestamp are recorded only after the validated
    candidate is atomically adopted.
    """
    candidate_path_value = Path(candidate).expanduser().resolve()
    final = Path(final_path).expanduser().resolve()
    try:
        candidate_fingerprint = artifact_fingerprint(candidate_path_value)
    except OutputPromotionError:
        # Keep the candidate in place for diagnostics and let the caller
        # decide whether/when to clean it up.
        raise

    parsed_start = _parse_timestamp(run_started_at)
    if parsed_start is not None:
        start_ns = int(parsed_start.timestamp() * 1_000_000_000)
        if candidate_fingerprint["mtime_ns"] <= start_ns:
            raise OutputPromotionError(
                "render candidate predates the current run; refusing stale success"
            )

    probe = probe_media(candidate_path_value)

    validate_media_contract(
        probe,
        profile=profile,
        expected_duration_seconds=expected_duration_seconds,
    )

    final.parent.mkdir(parents=True, exist_ok=True)
    with _promotion_lock(final):
        previous_fingerprint: dict[str, Any] | None = None
        if final.is_file():
            try:
                previous_fingerprint = artifact_fingerprint(final)
            except OutputPromotionError:
                # A corrupt/empty prior final is not a reason to accept a stale
                # candidate, but it also must not block replacing it with a
                # fully validated fresh artifact.
                previous_fingerprint = None
        try:
            # Candidate and final live under the same project filesystem.
            # os.replace is atomic on that volume and leaves an existing final
            # untouched if validation failed above.
            os.replace(candidate_path_value, final)
        except OSError as exc:
            raise OutputPromotionError(f"atomic output promotion failed: {exc}") from exc

        final_fingerprint = artifact_fingerprint(final)
        # os.replace should preserve the candidate's bytes. Re-hash after the
        # rename so a concurrent writer or filesystem anomaly cannot be
        # reported as a successful render.
        if final_fingerprint["sha256"] != candidate_fingerprint["sha256"]:
            raise OutputPromotionError("promoted output hash differs from validated candidate")
        if parsed_start is not None:
            start_ns = int(parsed_start.timestamp() * 1_000_000_000)
            if final_fingerprint["mtime_ns"] <= start_ns:
                raise OutputPromotionError(
                    "promoted output timestamp does not prove current-run freshness"
                )
    result = {
        "path": str(final),
        "candidate_path": str(candidate_path_value),
        "sha256": final_fingerprint["sha256"],
        "size_bytes": final_fingerprint["size_bytes"],
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "freshness": {
            "run_started_at": parsed_start.isoformat() if parsed_start else None,
            "candidate_created_at": candidate_fingerprint["created_at"],
            "candidate_mtime_ns": candidate_fingerprint["mtime_ns"],
            "output_created_at": final_fingerprint["created_at"],
            "output_mtime_ns": final_fingerprint["mtime_ns"],
            "new_hash_recorded": True,
            "prior_output_sha256": (
                previous_fingerprint["sha256"] if previous_fingerprint else None
            ),
        },
        "probe": probe,
    }
    if provenance:
        result["provenance"] = dict(provenance)
    return result


__all__ = [
    "OutputPromotionError",
    "artifact_fingerprint",
    "candidate_path",
    "probe_media",
    "promote_candidate",
    "validate_media_contract",
]
