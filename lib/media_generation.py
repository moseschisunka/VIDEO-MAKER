"""Generation-plan and output-integrity helpers for image/video providers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from lib.media_contracts import (
    AssetRequest,
    AssetResult,
    MediaContractError,
    build_asset_request,
    validate_asset_result,
)
from lib.media_ingestion import MediaValidationError, file_sha256, validate_media_file


class GenerationValidationError(ValueError):
    """Raised when a generated asset is not safe to promote."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _serializable_payload(inputs: Mapping[str, Any]) -> dict[str, Any]:
    controls = {
        "sample_approval", "sample_approved", "sample_required", "batch",
        "strict_media_validation", "production_mode", "provider_executor",
        "provider_kernel", "approved", "provider_approved", "cost_tracker",
    }
    result: dict[str, Any] = {}
    for key, value in inputs.items():
        if key in controls or str(key).lower().endswith(("_api_key", "_token", "_secret")):
            continue
        try:
            json.dumps(value, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        result[str(key)] = value
    return result


def build_generation_plan(
    request: Mapping[str, Any] | AssetRequest,
    *,
    provider: str,
    model: str = "",
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a stable, provider-specific plan before any paid generation."""
    asset_request = build_asset_request(request)
    if not isinstance(provider, str) or not provider.strip():
        raise GenerationValidationError("generation provider is required")
    payload = _serializable_payload(inputs or {})
    identity = {
        "asset_request_key": asset_request.stable_key,
        "provider": provider.strip().lower(),
        "model": str(model or "").strip(),
        "payload": payload,
    }
    plan_key = hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()
    return {
        "plan_id": f"gen_{plan_key[:16]}",
        "idempotency_key": plan_key,
        "asset_request": asset_request.to_dict(),
        "provider": provider.strip().lower(),
        "model": str(model or "").strip(),
        "payload": payload,
        "status": "planned",
    }


def require_sample_approval(
    request: Mapping[str, Any] | AssetRequest,
    approval: Mapping[str, Any] | None,
    *,
    plan_id: str = "",
) -> dict[str, Any]:
    """Fail closed when a request declares sample-first/batch generation."""
    asset_request = build_asset_request(request)
    required = bool(asset_request.sample_required)
    raw = dict(approval or {})
    approved = raw.get("approved") is True
    if required and not approved:
        raise GenerationValidationError(
            "sample approval is required before batch generation"
            + (f" for {plan_id}" if plan_id else "")
        )
    if required and not str(raw.get("approval_id") or "").strip():
        raise GenerationValidationError("sample approval must include an approval_id")
    if approved and raw.get("plan_id") and plan_id and str(raw["plan_id"]) != plan_id:
        raise GenerationValidationError("sample approval belongs to a different generation plan")
    return {
        "required": required,
        "approved": approved or not required,
        "status": "approved" if approved else ("not_required" if not required else "pending"),
        "approval_id": str(raw.get("approval_id") or ""),
        "manifest_hash": str(raw.get("manifest_hash") or ""),
        "plan_id": plan_id,
        "review_notes": str(raw.get("review_notes") or ""),
    }


def collect_output_paths(result: Any) -> list[Path]:
    """Extract local artifact paths from common ToolResult/provider shapes."""
    values: list[Any] = []
    if hasattr(result, "artifacts"):
        values.extend(getattr(result, "artifacts") or [])
    data = getattr(result, "data", result)
    if isinstance(data, Mapping):
        for key in ("output_path", "output", "path", "image_path", "video_path", "audio_path", "paths", "images", "videos"):
            value = data.get(key)
            if isinstance(value, (list, tuple)):
                values.extend(value)
            elif value:
                values.append(value)
        values.extend(data.get("artifacts") or [] if isinstance(data.get("artifacts"), list) else [])
    paths: list[Path] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("path") or value.get("output") or value.get("url")
        if not isinstance(value, (str, os.PathLike)):
            continue
        text = str(value)
        if text.startswith(("http://", "https://", "s3://", "gs://")):
            continue
        path = Path(text).expanduser()
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _video_motion_score(path: Path) -> float:
    """Compare two decoded frames; fail closed when a promised video cannot be inspected."""
    ffmpeg = shutil_which("ffmpeg")
    ffprobe = shutil_which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise GenerationValidationError("ffmpeg and ffprobe are required to verify motion")
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        duration = float((probe.stdout or "0").strip() or 0)
    except Exception as exc:
        raise GenerationValidationError(f"unable to probe generated video duration: {exc}") from exc
    if duration <= 0:
        raise GenerationValidationError("generated video has no positive duration")
    with tempfile.TemporaryDirectory(prefix="openmontage-motion-") as temp:
        first = Path(temp) / "first.jpg"
        middle = Path(temp) / "middle.jpg"
        for seek, output in ((0.0, first), (max(0.0, duration / 2.0), middle)):
            subprocess.run(
                [ffmpeg, "-y", "-ss", str(seek), "-i", str(path), "-frames:v", "1", "-f", "image2", str(output)],
                capture_output=True, timeout=30, check=True,
            )
        try:
            from PIL import Image, ImageChops, ImageStat

            with Image.open(first).convert("RGB") as one, Image.open(middle).convert("RGB") as two:
                if one.size != two.size:
                    two = two.resize(one.size)
                diff = ImageChops.difference(one, two)
                return float(sum(ImageStat.Stat(diff).mean) / (3.0 * 255.0))
        except Exception as exc:
            raise GenerationValidationError(f"unable to compare generated video frames: {exc}") from exc


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def validate_generation_output(
    result: Any,
    *,
    media_type: str,
    constraints: Mapping[str, Any] | None = None,
    motion_required: bool = False,
    strict: bool = True,
) -> dict[str, Any]:
    """Validate generated image/video bytes and declared output constraints."""
    paths = collect_output_paths(result)
    if not paths:
        raise GenerationValidationError("provider returned no local output artifact")
    limits = dict(constraints or {})
    facts_list: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            raise GenerationValidationError(f"provider output is missing: {path}")
        try:
            facts = validate_media_file(path, media_type, strict_decode=strict, min_bytes=int(limits.get("min_bytes", 128)))
        except MediaValidationError as exc:
            raise GenerationValidationError(str(exc)) from exc
        min_width = int(limits.get("min_width", 0) or 0)
        min_height = int(limits.get("min_height", 0) or 0)
        if min_width and int(facts.get("width", 0)) < min_width:
            raise GenerationValidationError(f"generated output width is below minimum {min_width}")
        if min_height and int(facts.get("height", 0)) < min_height:
            raise GenerationValidationError(f"generated output height is below minimum {min_height}")
        duration = float(facts.get("duration_seconds", 0.0) or 0.0)
        if limits.get("min_duration_seconds") is not None and duration < float(limits["min_duration_seconds"]):
            raise GenerationValidationError("generated output is shorter than requested")
        if limits.get("max_duration_seconds") is not None and duration > float(limits["max_duration_seconds"]):
            raise GenerationValidationError("generated output exceeds requested duration")
        if motion_required:
            score = _video_motion_score(path)
            facts["motion_score"] = score
            if score <= float(limits.get("min_motion_score", 0.002)):
                raise GenerationValidationError("motion-required request produced a visually static video")
        facts_list.append(facts)
    return {"valid": True, "outputs": facts_list, "output_count": len(facts_list), "motion_required": motion_required}


def asset_result_from_output(
    request: Mapping[str, Any] | AssetRequest,
    path: Path | str,
    *,
    provider: str,
    model: str = "",
    prompt: str = "",
    validation: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AssetResult:
    req = build_asset_request(request)
    target = Path(path).expanduser().resolve()
    facts = dict(validation or validate_media_file(target, req.media_type, strict_decode=True, min_bytes=128))
    result = AssetResult(
        asset_id=f"asset_{file_sha256(target)[:16]}",
        request_id=req.request_id,
        scene_id=req.scene_id,
        media_type=req.media_type,
        strategy=req.strategy,
        path=str(target),
        provider=str(provider),
        sha256=str(facts.get("sha256") or file_sha256(target)),
        size_bytes=int(facts.get("size_bytes") or target.stat().st_size),
        mime_type=str(facts.get("mime_type") or "application/octet-stream"),
        model=str(model or ""),
        prompt=str(prompt or ""),
        validation_status="validated",
        metadata={**dict(metadata or {}), "validation": facts},
    )
    checked = validate_asset_result(result, request=req)
    if not checked["valid"]:
        raise GenerationValidationError("asset result contract failed: " + "; ".join(checked["errors"]))
    return result


__all__ = [
    "GenerationValidationError", "build_generation_plan", "require_sample_approval",
    "collect_output_paths", "validate_generation_output", "asset_result_from_output",
]
