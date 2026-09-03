"""Fail-closed media download, ingestion, validation, and source selection."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import subprocess
import tempfile
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


class MediaValidationError(ValueError):
    """Raised when a downloaded or user-supplied media file is not valid."""


_IMAGE_MAGIC = (
    (b"\xFF\xD8\xFF", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
)
_AUDIO_MAGIC = ((b"ID3", "audio/mpeg"), (b"\xFF\xFB", "audio/mpeg"), (b"RIFF", "audio/wav"), (b"OggS", "audio/ogg"))
_VIDEO_MAGIC = ((b"ftyp", "video/mp4"), (b"\x1A\x45\xDF\xA3", "video/webm"), (b"RIFF", "video/avi"))


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _magic_mime(path: Path) -> str | None:
    head = path.read_bytes()[:64]
    if head.startswith(b"RIFF") and len(head) >= 12:
        riff_kind = head[8:12]
        if riff_kind == b"WEBP":
            return "image/webp"
        if riff_kind == b"WAVE":
            return "audio/wav"
        if riff_kind == b"AVI ":
            return "video/avi"
    for signature, mime in (*_IMAGE_MAGIC, *_AUDIO_MAGIC, *_VIDEO_MAGIC):
        if signature == b"ftyp":
            if len(head) >= 12 and head[4:8] == b"ftyp":
                return mime
        elif signature != b"RIFF" and head.startswith(signature):
            return mime
    return None


def _expected_families(media_type: str) -> set[str]:
    kind = str(media_type or "").lower()
    if kind in {"image", "diagram"}:
        return {"image"}
    if kind in {"audio"}:
        return {"audio"}
    if kind in {"video", "animation", "stock"}:
        return {"video"}
    return {"image", "audio", "video"}


def _mime_family(mime: str | None) -> str:
    return str(mime or "").split("/", 1)[0].lower()


def validate_media_file(
    path: Path | str,
    media_type: str,
    *,
    expected_mime: str | None = None,
    min_bytes: int = 128,
    strict_decode: bool = True,
) -> dict[str, Any]:
    """Validate size, magic, and (when available) decode/probe facts."""
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise MediaValidationError(f"media file does not exist: {candidate}")
    size = candidate.stat().st_size
    if size < int(min_bytes):
        raise MediaValidationError(f"media file is empty or too small ({size} bytes): {candidate}")
    magic_mime = _magic_mime(candidate)
    if magic_mime is None:
        raise MediaValidationError(f"media magic signature is unknown: {candidate}")
    family = _mime_family(magic_mime)
    if family not in _expected_families(media_type):
        raise MediaValidationError(f"media magic {magic_mime} does not match declared type {media_type!r}")
    if expected_mime and expected_mime not in {"application/octet-stream", "binary/octet-stream"} and magic_mime != expected_mime and _mime_family(expected_mime) != family:
        raise MediaValidationError(f"media MIME {magic_mime} does not match expected {expected_mime}")

    facts: dict[str, Any] = {
        "valid": True,
        "path": str(candidate),
        "size_bytes": size,
        "mime_type": magic_mime,
        "sha256": file_sha256(candidate),
        "decoded": False,
    }
    if family == "image":
        try:
            from PIL import Image

            with Image.open(candidate) as image:
                image.verify()
            with Image.open(candidate) as image:
                facts.update({"width": int(image.width), "height": int(image.height), "format": image.format})
            facts["decoded"] = True
        except ImportError:
            if strict_decode:
                raise MediaValidationError("Pillow is required for strict image decode validation")
        except Exception as exc:
            raise MediaValidationError(f"image decode validation failed: {exc}") from exc
    else:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            if strict_decode:
                raise MediaValidationError("ffprobe is required for strict audio/video validation")
        else:
            try:
                process = subprocess.run(
                    [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(candidate)],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=60,
                )
                import json

                payload = json.loads(process.stdout or "{}")
                streams = payload.get("streams") or []
                if not streams:
                    raise MediaValidationError("ffprobe found no decodable streams")
                fmt = payload.get("format") or {}
                facts["duration_seconds"] = float(fmt.get("duration") or 0.0)
                facts["stream_count"] = len(streams)
                facts["decoded"] = True
            except MediaValidationError:
                raise
            except Exception as exc:
                raise MediaValidationError(f"media decode validation failed: {exc}") from exc
    return facts


def _response_status(response: Any) -> int:
    status = getattr(response, "status_code", 200)
    try:
        return int(status)
    except (TypeError, ValueError):
        return 200


def download_stream_atomic(
    url: str,
    destination: Path | str,
    *,
    media_type: str,
    request_get: Callable[..., Any] | None = None,
    timeout_seconds: float = 120.0,
    max_bytes: int = 1_000_000_000,
    min_bytes: int = 128,
    strict_decode: bool = True,
) -> dict[str, Any]:
    """Stream a remote file to ``.part``, validate, and atomically promote it."""
    if not isinstance(url, str) or not url.strip():
        raise MediaValidationError("download URL is required")
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    part.unlink(missing_ok=True)
    if request_get is None:
        import requests

        request_get = requests.get
    response = None
    try:
        response = request_get(url, stream=True, timeout=timeout_seconds)
        status = _response_status(response)
        if status < 200 or status >= 300:
            raise MediaValidationError(f"download returned HTTP {status}")
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        headers = {str(key).lower(): str(value) for key, value in dict(getattr(response, "headers", {}) or {}).items()}
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type in {"text/html", "application/json", "text/plain"}:
            raise MediaValidationError(f"download returned non-media MIME type {content_type}")
        announced = headers.get("content-length")
        if announced:
            try:
                if int(announced) > max_bytes:
                    raise MediaValidationError("download exceeds maximum allowed size")
            except ValueError:
                pass
        total = 0
        with part.open("wb") as handle:
            iterator = response.iter_content(chunk_size=1024 * 1024) if hasattr(response, "iter_content") else []
            for chunk in iterator:
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise MediaValidationError("download exceeds maximum allowed size")
                handle.write(chunk)
        if announced and announced.isdigit() and int(announced) != total:
            raise MediaValidationError(f"download length mismatch: expected {announced}, received {total}")
        facts = validate_media_file(
            part,
            media_type,
            expected_mime=content_type or None,
            min_bytes=min_bytes,
            strict_decode=strict_decode,
        )
        os.replace(part, target)
        facts.update({"path": str(target), "url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(), "content_type_header": content_type, "content_length": total})
        return facts
    except Exception:
        part.unlink(missing_ok=True)
        raise
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def ingest_user_media(
    source: Path | str,
    destination: Path | str,
    *,
    media_type: str,
    consent: bool,
    provenance: Mapping[str, Any] | None = None,
    strict_decode: bool = True,
    allowed_root: Path | str | None = None,
    max_bytes: int = 1_000_000_000,
) -> dict[str, Any]:
    """Copy user media safely and require explicit consent/provenance."""
    if consent is not True:
        raise MediaValidationError("user-media ingestion requires explicit consent=true")
    source_input = Path(source).expanduser()
    if source_input.is_symlink():
        raise MediaValidationError("symbolic-link user media is not accepted")
    source_path = source_input.resolve()
    if not source_path.is_file():
        raise MediaValidationError(f"user media does not exist: {source_path}")
    destination_path = Path(destination).expanduser().resolve()
    if allowed_root is not None:
        root = Path(allowed_root).expanduser().resolve()
        try:
            destination_path.relative_to(root)
        except ValueError as exc:
            raise MediaValidationError(
                f"user media destination escapes allowed project root: {destination_path}"
            ) from exc
    if destination_path == source_path:
        raise MediaValidationError("user media destination must differ from source")
    if source_path.stat().st_size > int(max_bytes):
        raise MediaValidationError("user media exceeds maximum allowed size")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    part = destination_path.with_name(destination_path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        shutil.copyfile(source_path, part)
        facts = validate_media_file(part, media_type, strict_decode=strict_decode, min_bytes=128)
        os.replace(part, destination_path)
        facts.update({
            "path": str(destination_path),
            "source_path": str(source_path),
            "consent": True,
            "provenance": dict(provenance or {}),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        })
        return facts
    except Exception:
        part.unlink(missing_ok=True)
        raise


def rank_asset_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    intent: str = "",
    orientation: str | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
    exclude_ids: Iterable[str] = (),
    max_per_source: int | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates with deterministic semantic, format, and diversity signals."""
    excluded = {str(value) for value in exclude_ids}
    query_tokens = {token.lower() for token in re.findall(r"[\w-]+", str(intent)) if token.strip()}
    source_counts: dict[str, int] = {}
    fingerprints: set[str] = set()
    scored: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        candidate = dict(raw)
        candidate_id = str(candidate.get("clip_id") or candidate.get("asset_id") or candidate.get("id") or index)
        source = str(candidate.get("source") or candidate.get("provider") or "unknown")
        reasons: list[str] = []
        if candidate_id in excluded:
            reasons.append("duplicate/excluded candidate")
        fingerprint = str(candidate.get("sha256") or candidate.get("download_url") or candidate.get("source_url") or candidate_id)
        if fingerprint in fingerprints:
            reasons.append("duplicate media fingerprint")
        width, height = int(candidate.get("width") or 0), int(candidate.get("height") or 0)
        duration = float(candidate.get("duration") or candidate.get("duration_seconds") or 0)
        if orientation and width and height:
            actual = "landscape" if width > height else "portrait" if height > width else "square"
            if orientation != "any" and actual != orientation:
                reasons.append(f"orientation mismatch ({actual} != {orientation})")
        if min_duration is not None and duration < min_duration:
            reasons.append("below minimum duration")
        if max_duration is not None and duration > max_duration:
            reasons.append("above maximum duration")
        tags = str(candidate.get("source_tags") or candidate.get("tags") or candidate.get("description") or "").lower()
        semantic = sum(1 for token in query_tokens if token in tags) / max(1, len(query_tokens))
        format_score = 1.0 if not reasons else 0.0
        try:
            quality = min(1.0, max(0.0, float(candidate.get("quality_score") or candidate.get("quality") or 0.5)))
        except (TypeError, ValueError):
            quality = 0.0
        diversity_penalty = source_counts.get(source, 0) * 0.15
        score = round(0.55 * semantic + 0.30 * quality + 0.15 * format_score - diversity_penalty, 6)
        scored.append({"candidate": candidate, "candidate_id": candidate_id, "source": source, "score": score, "rejected": bool(reasons), "reasons": reasons})
        if not reasons:
            source_counts[source] = source_counts.get(source, 0) + 1
            fingerprints.add(fingerprint)
    scored.sort(key=lambda item: (-float(item["score"]), str(item["candidate_id"])))
    if max_per_source is not None:
        counts: dict[str, int] = {}
        for item in scored:
            source = str(item["source"])
            if not item["rejected"] and counts.get(source, 0) >= max_per_source:
                item["rejected"] = True
                item["reasons"].append("source diversity limit")
            elif not item["rejected"]:
                counts[source] = counts.get(source, 0) + 1
    return scored


__all__ = [
    "MediaValidationError", "file_sha256", "validate_media_file", "download_stream_atomic",
    "ingest_user_media", "rank_asset_candidates",
]
