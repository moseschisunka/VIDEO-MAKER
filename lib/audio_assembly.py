"""Container-safe audio segment assembly.

TTS providers return independently encoded files.  Raw byte joining (or
``concat`` with ``-c copy`` across unlike MP3 streams) can produce broken
timestamps, clicks, missing tails, or an output that decodes differently on
another player.  This utility validates inputs and asks FFmpeg to decode and
re-encode one canonical output stream.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


class AudioAssemblyError(ValueError):
    """Raised when narration segments cannot be assembled safely."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_concat_manifest(paths: Iterable[Path], destination: Path) -> None:
    values = list(paths)
    if not values:
        raise AudioAssemblyError("at least one audio segment is required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for path in values:
                # FFmpeg concat manifests use single-quote escaping by
                # doubling a quote inside the path.
                escaped = path.as_posix().replace("'", "'\\''")
                handle.write(f"file '{escaped}'\n")
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def assemble_audio_segments(
    paths: Iterable[Path | str],
    output_path: Path | str,
    *,
    ffmpeg_bin: str = "ffmpeg",
    sample_rate: int = 48_000,
    channels: int = 2,
) -> dict[str, object]:
    """Decode, normalize, and encode ordered audio segments into one file."""
    inputs = [Path(path).expanduser().resolve() for path in paths]
    if not inputs:
        raise AudioAssemblyError("at least one audio segment is required")
    for path in inputs:
        if not path.is_file() or path.stat().st_size <= 0:
            raise AudioAssemblyError(f"audio segment is missing or empty: {path}")
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_suffix(output.suffix + ".concat.txt")
    _write_concat_manifest(inputs, manifest)
    suffix = output.suffix.lower()
    codec_args = ["-c:a", "libmp3lame", "-q:a", "4"] if suffix in {".mp3", ".m4a", ".aac"} else ["-c:a", "pcm_s16le"]
    command = [
        ffmpeg_bin,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(manifest),
        "-vn",
        "-ar", str(int(sample_rate)),
        "-ac", str(int(channels)),
        *codec_args,
        str(output),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=True, timeout=300)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise AudioAssemblyError(f"FFmpeg audio assembly failed: {str(detail)[-500:]}") from exc
    if not output.is_file() or output.stat().st_size <= 0:
        raise AudioAssemblyError(f"FFmpeg reported success but output is missing or empty: {output}")
    return {
        "output_path": str(output),
        "segment_count": len(inputs),
        "input_paths": [str(path) for path in inputs],
        "output_sha256": _file_sha256(output),
        "assembly": "ffmpeg_decode_normalize_encode",
        "command": command,
        "stderr_tail": str(getattr(completed, "stderr", "") or "")[-500:],
    }


__all__ = ["AudioAssemblyError", "assemble_audio_segments"]
