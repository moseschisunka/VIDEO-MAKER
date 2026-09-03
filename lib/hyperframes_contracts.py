"""Canonical contracts shared by the OpenMontage HyperFrames adapter.

HyperFrames is intentionally a thin runtime adapter, not a second editorial
engine.  This module turns the canonical ``edit_decisions`` and audio
artifacts into a deterministic, serialisable plan before any HTML is written
or a renderer is invoked.  Unsupported shapes fail closed so a successful
render can never mean that a scene was silently replaced by a placeholder.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
from pathlib import Path
from typing import Any, Mapping, Sequence


class HyperFramesContractError(ValueError):
    """Raised when canonical edit data cannot be represented safely."""


SUPPORTED_CUT_TYPES = frozenset(
    {
        "image",
        "video",
        "text_card",
        "hero_title",
        "callout",
        "composition",
        "html",
        "diagram",
        "teacher_slide",
        "animation",
        "code_snippet",
        "subtitle",
    }
)
SUPPORTED_TRANSITIONS = frozenset(
    {"cut", "none", "fade", "dissolve", "crossfade", "wipe", "slide", "blur"}
)
STATIC_ANIMATIONS = frozenset({"", "none", "static", "hold", "still"})
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"})
_KNOWN_CUT_FIELDS = frozenset(
    {
        "id",
        "source",
        "type",
        "text",
        "title",
        "subtitle",
        "caption",
        "reason",
        "in_seconds",
        "out_seconds",
        "source_in_seconds",
        "media_start_seconds",
        "speed",
        "layer",
        "track",
        "track_index",
        "transform",
        "animation",
        "transition_in",
        "transition_out",
        "transition_duration",
        "teacher_slide",
        "surfaceColor",
        "mutedColor",
        "backgroundColor",
        "accentColor",
        "color",
        "opacity",
        "motion_required",
        "keyframes",
        "data_composition_id",
        "data_composition_src",
    }
)


def _number(value: Any, field: str, *, default: float | None = None) -> float:
    if value is None and default is not None:
        return float(default)
    if isinstance(value, bool):
        raise HyperFramesContractError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise HyperFramesContractError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise HyperFramesContractError(f"{field} must be finite")
    return result


def _asset_lookup(asset_manifest: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    values = (asset_manifest or {}).get("assets", [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise HyperFramesContractError("asset_manifest.assets must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for item in values:
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        result[str(item["id"])] = item
    return result


def _source_for_cut(cut: Mapping[str, Any], assets: Mapping[str, Mapping[str, Any]]) -> tuple[str, Mapping[str, Any] | None]:
    raw = str(cut.get("source") or "").strip()
    if raw in assets:
        asset = assets[raw]
        return str(asset.get("path") or raw), asset
    return raw, None


def _infer_cut_type(cut: Mapping[str, Any], source: str) -> str:
    explicit = str(cut.get("type") or "").strip().lower().replace("-", "_")
    aliases = {
        "still": "image",
        "photo": "image",
        "text": "text_card",
        "title": "hero_title",
        "html_composition": "composition",
        "subcomposition": "composition",
    }
    if explicit:
        return aliases.get(explicit, explicit)
    if source:
        suffix = Path(source).suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            return "image"
        if suffix in _VIDEO_EXTENSIONS:
            return "video"
        if suffix in {".html", ".htm"}:
            return "composition"
    if cut.get("text") or cut.get("title") or cut.get("subtitle"):
        return "text_card"
    return "unknown"


def _normalise_transition(value: Any, field: str, *, default: str | None = None) -> str | None:
    if value is None or str(value).strip() == "":
        return default
    transition = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    transition = {"cross_fade": "crossfade", "dissolve_in": "dissolve"}.get(transition, transition)
    if transition not in SUPPORTED_TRANSITIONS:
        raise HyperFramesContractError(
            f"{field}={value!r} is unsupported by the HyperFrames adapter; "
            f"supported values: {sorted(SUPPORTED_TRANSITIONS)}"
        )
    return transition


def build_audio_plan(
    edit_decisions: Mapping[str, Any],
    asset_manifest: Mapping[str, Any] | None,
    total_duration_seconds: float,
) -> dict[str, Any]:
    """Build a deterministic, stem-preserving HyperFrames audio plan."""

    if total_duration_seconds <= 0:
        raise HyperFramesContractError("audio plan requires a positive timeline duration")
    assets = _asset_lookup(asset_manifest)
    raw_audio = edit_decisions.get("audio")
    audio = dict(raw_audio) if isinstance(raw_audio, Mapping) else {}

    narration_raw = audio.get("narration")
    narration = dict(narration_raw) if isinstance(narration_raw, Mapping) else {}
    narration_segments: list[dict[str, Any]] = []
    segments = narration.get("segments") or []
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)):
        raise HyperFramesContractError("audio.narration.segments must be an array")
    for index, raw_segment in enumerate(segments):
        if not isinstance(raw_segment, Mapping):
            raise HyperFramesContractError(f"audio.narration.segments[{index}] must be an object")
        asset_id = str(raw_segment.get("asset_id") or "").strip()
        if not asset_id:
            raise HyperFramesContractError(f"audio.narration.segments[{index}].asset_id is required")
        start = _number(raw_segment.get("start_seconds"), f"audio.narration.segments[{index}].start_seconds", default=0)
        end_value = raw_segment.get("end_seconds")
        end = None if end_value is None else _number(end_value, f"audio.narration.segments[{index}].end_seconds")
        offset = _number(
            raw_segment.get("offset_seconds"),
            f"audio.narration.segments[{index}].offset_seconds",
            default=0,
        )
        if start < 0 or offset < 0:
            raise HyperFramesContractError(f"audio.narration.segments[{index}] cannot start before zero")
        if end is not None and end <= start:
            raise HyperFramesContractError(f"audio.narration.segments[{index}] has no positive duration")
        if start >= total_duration_seconds + 0.001:
            raise HyperFramesContractError(f"audio.narration.segments[{index}] starts beyond the visual timeline")
        if end is not None and end > total_duration_seconds + 0.001:
            raise HyperFramesContractError(f"audio.narration.segments[{index}] ends beyond the visual timeline")
        duration = (end - start) if end is not None else max(0.0, total_duration_seconds - start)
        asset = assets.get(asset_id)
        volume = _number(raw_segment.get("volume"), f"audio.narration.segments[{index}].volume", default=1)
        if not 0 <= volume <= 1:
            raise HyperFramesContractError(f"audio.narration.segments[{index}].volume must be between 0 and 1")
        narration_segments.append(
            {
                "id": f"narration-{index}",
                "asset_id": asset_id,
                "path": str(asset.get("path")) if asset and asset.get("path") else None,
                "role": "speech",
                "start_seconds": round(start, 6),
                "end_seconds": round(end, 6) if end is not None else None,
                "duration_seconds": round(duration, 6),
                "offset_seconds": round(offset, 6),
                "source_start_seconds": round(offset, 6),
                "volume": round(volume, 6),
            }
        )

    music_raw = audio.get("music")
    if not isinstance(music_raw, Mapping):
        legacy = edit_decisions.get("music")
        music_raw = legacy if isinstance(legacy, Mapping) else {}
    music = dict(music_raw)
    music_plan: dict[str, Any] | None = None
    if music.get("asset_id"):
        asset_id = str(music["asset_id"])
        asset = assets.get(asset_id)
        offset = _number(music.get("offset_seconds", music.get("offset")), "audio.music.offset_seconds", default=0)
        volume = _number(music.get("volume"), "audio.music.volume", default=0.15)
        if not 0 <= volume <= 1 or offset < 0:
            raise HyperFramesContractError("audio.music volume must be between 0 and 1 and offset cannot be negative")
        fade_in = _number(music.get("fade_in_seconds"), "audio.music.fade_in_seconds", default=0)
        fade_out = _number(music.get("fade_out_seconds"), "audio.music.fade_out_seconds", default=0)
        if fade_in < 0 or fade_out < 0:
            raise HyperFramesContractError("audio.music fade durations cannot be negative")
        raw_ducking = music.get("ducking", False)
        if isinstance(raw_ducking, Mapping):
            ducking = dict(raw_ducking)
            ducking_enabled = bool(ducking.get("enabled", True))
        else:
            ducking = {}
            ducking_enabled = bool(raw_ducking)
        reduction_db = ducking.get("reduction_db", -12)
        reduction_db = _number(reduction_db, "audio.music.ducking.reduction_db", default=-12)
        if reduction_db > 0:
            reduction_db = -reduction_db
        attack_ms = _number(ducking.get("attack_ms"), "audio.music.ducking.attack_ms", default=200)
        release_ms = _number(ducking.get("release_ms"), "audio.music.ducking.release_ms", default=500)
        if attack_ms < 0 or release_ms < 0:
            raise HyperFramesContractError("audio.music ducking attack/release cannot be negative")
        music_plan = {
            "id": "music",
            "asset_id": asset_id,
            "path": str(asset.get("path")) if asset and asset.get("path") else None,
            "role": "music",
            "start_seconds": 0.0,
            "end_seconds": round(total_duration_seconds, 6),
            "duration_seconds": round(total_duration_seconds, 6),
            "offset_seconds": round(offset, 6),
            "source_start_seconds": round(offset, 6),
            "volume": round(volume, 6),
            "fade_in_seconds": round(fade_in, 6),
            "fade_out_seconds": round(fade_out, 6),
            "loop": bool(music.get("loop", False)),
            "ducking": {
                "enabled": ducking_enabled,
                "reduction_db": round(reduction_db, 6),
                "attack_ms": round(attack_ms, 6),
                "release_ms": round(release_ms, 6),
            },
        }

    sfx_plans: list[dict[str, Any]] = []
    sfx_raw = audio.get("sfx") or []
    if not isinstance(sfx_raw, Sequence) or isinstance(sfx_raw, (str, bytes)):
        raise HyperFramesContractError("audio.sfx must be an array")
    for index, raw_sfx in enumerate(sfx_raw):
        if not isinstance(raw_sfx, Mapping):
            raise HyperFramesContractError(f"audio.sfx[{index}] must be an object")
        asset_id = str(raw_sfx.get("asset_id") or "").strip()
        if not asset_id:
            raise HyperFramesContractError(f"audio.sfx[{index}].asset_id is required")
        start = _number(raw_sfx.get("start_seconds"), f"audio.sfx[{index}].start_seconds", default=0)
        if start < 0 or start >= total_duration_seconds + 0.001:
            raise HyperFramesContractError(f"audio.sfx[{index}] starts outside the visual timeline")
        end_value = raw_sfx.get("end_seconds")
        end = None if end_value is None else _number(end_value, f"audio.sfx[{index}].end_seconds")
        if end is not None and end <= start:
            raise HyperFramesContractError(f"audio.sfx[{index}] has no positive duration")
        volume = _number(raw_sfx.get("volume"), f"audio.sfx[{index}].volume", default=1)
        if not 0 <= volume <= 1:
            raise HyperFramesContractError(f"audio.sfx[{index}].volume must be between 0 and 1")
        asset = assets.get(asset_id)
        offset = _number(raw_sfx.get("offset_seconds"), f"audio.sfx[{index}].offset_seconds", default=0)
        if offset < 0:
            raise HyperFramesContractError(f"audio.sfx[{index}].offset_seconds cannot be negative")
        sfx_plans.append(
            {
                "id": f"sfx-{index}",
                "asset_id": asset_id,
                "path": str(asset.get("path")) if asset and asset.get("path") else None,
                "role": "sfx",
                "start_seconds": round(start, 6),
                "end_seconds": round(end, 6) if end is not None else None,
                "duration_seconds": round((end - start) if end is not None else max(0.0, total_duration_seconds - start), 6),
                "offset_seconds": round(offset, 6),
                "volume": round(volume, 6),
            }
        )

    return {
        "version": "1.0",
        "duration_seconds": round(total_duration_seconds, 6),
        "narration": narration_segments,
        "music": music_plan,
        "sfx": sfx_plans,
        "stems": {
            "narration": [item["id"] for item in narration_segments],
            "music": ["music"] if music_plan else [],
            "sfx": [item["id"] for item in sfx_plans],
        },
        "ducking": music_plan["ducking"] if music_plan else {"enabled": False},
    }


def build_edit_mapping(
    edit_decisions: Mapping[str, Any],
    asset_manifest: Mapping[str, Any] | None = None,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Map canonical edits to HyperFrames fields, rejecting unsupported data."""

    cuts = edit_decisions.get("cuts")
    if not isinstance(cuts, Sequence) or isinstance(cuts, (str, bytes)) or not cuts:
        raise HyperFramesContractError("edit_decisions.cuts must be a non-empty array")
    assets = _asset_lookup(asset_manifest)
    mapped: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw_cut in enumerate(cuts):
        if not isinstance(raw_cut, Mapping):
            raise HyperFramesContractError(f"cuts[{index}] must be an object")
        cut_id = str(raw_cut.get("id") or "").strip()
        if not cut_id:
            raise HyperFramesContractError(f"cuts[{index}].id is required")
        if cut_id in ids:
            raise HyperFramesContractError(f"duplicate cut id {cut_id!r}")
        ids.add(cut_id)
        source, asset = _source_for_cut(raw_cut, assets)
        cut_type = _infer_cut_type(raw_cut, source)
        if cut_type not in SUPPORTED_CUT_TYPES:
            raise HyperFramesContractError(
                f"cuts[{index}] has unsupported shape/type {cut_type!r}; "
                "define a HyperFrames composition or use a supported cut type"
            )
        if cut_type in {"image", "video", "composition", "html", "animation"} and not source:
            raise HyperFramesContractError(f"cuts[{index}] type {cut_type!r} requires a source")
        if cut_type in {"text_card", "hero_title", "callout"} and not (
            raw_cut.get("text") or raw_cut.get("title") or raw_cut.get("subtitle")
        ):
            raise HyperFramesContractError(f"cuts[{index}] type {cut_type!r} requires text/title content")
        start = _number(raw_cut.get("in_seconds"), f"cuts[{index}].in_seconds", default=0)
        end = _number(raw_cut.get("out_seconds"), f"cuts[{index}].out_seconds")
        if start < 0 or end <= start:
            raise HyperFramesContractError(f"cuts[{index}] must have 0 <= in_seconds < out_seconds")
        duration = end - start
        speed = _number(raw_cut.get("speed"), f"cuts[{index}].speed", default=1)
        if speed <= 0:
            raise HyperFramesContractError(f"cuts[{index}].speed must be greater than zero")
        layer = str(raw_cut.get("layer") or "primary").strip().lower()
        if layer not in {"primary", "overlay", "background"}:
            raise HyperFramesContractError(f"cuts[{index}].layer={layer!r} is unsupported")
        track = raw_cut.get("track_index", raw_cut.get("track"))
        track_index = int(track) if track is not None else {"background": 0, "primary": 1, "overlay": 2}[layer]
        if track_index < 0:
            raise HyperFramesContractError(f"cuts[{index}].track_index cannot be negative")
        transition_duration = _number(
            raw_cut.get("transition_duration"), f"cuts[{index}].transition_duration", default=0
        )
        if transition_duration < 0 or transition_duration > duration / 2 + 1e-6:
            raise HyperFramesContractError(f"cuts[{index}].transition_duration exceeds half the cut duration")
        animation = raw_cut.get("animation")
        if animation is None and isinstance(raw_cut.get("transform"), Mapping):
            animation = raw_cut["transform"].get("animation")
        animation = str(animation or "").strip().lower()
        keyframes = raw_cut.get("keyframes", 3 if animation not in STATIC_ANIMATIONS else 0)
        if isinstance(keyframes, bool):
            raise HyperFramesContractError(f"cuts[{index}].keyframes must be an integer")
        keyframes = int(keyframes)
        if animation not in STATIC_ANIMATIONS and keyframes < 3:
            raise HyperFramesContractError(
                f"cuts[{index}] animation {animation!r} declares only {keyframes} keyframes; "
                "motion requires at least three temporal samples"
            )
        unknown = sorted(set(raw_cut.keys()) - _KNOWN_CUT_FIELDS)
        if unknown and strict:
            raise HyperFramesContractError(
                f"cuts[{index}] contains fields with no HyperFrames mapping: {', '.join(unknown)}"
            )
        mapped.append(
            {
                "id": cut_id,
                "source": source,
                "asset_id": str(raw_cut.get("source")) if raw_cut.get("source") in assets else None,
                "type": cut_type,
                "start_seconds": round(start, 6),
                "duration_seconds": round(duration, 6),
                "end_seconds": round(end, 6),
                "media_start_seconds": round(
                    _number(
                        raw_cut.get("media_start_seconds", raw_cut.get("source_in_seconds")),
                        f"cuts[{index}].media_start_seconds",
                        default=0,
                    ),
                    6,
                ),
                "speed": round(speed, 6),
                "layer": layer,
                "track_index": track_index,
                "transition_in": _normalise_transition(raw_cut.get("transition_in"), f"cuts[{index}].transition_in", default="cut"),
                "transition_out": _normalise_transition(raw_cut.get("transition_out"), f"cuts[{index}].transition_out", default="cut"),
                "transition_duration": round(transition_duration, 6),
                "animation": animation or "static",
                "keyframe_count": keyframes,
                "motion_required": bool(raw_cut.get("motion_required", animation not in STATIC_ANIMATIONS)),
                "text": raw_cut.get("text") or raw_cut.get("title") or "",
                "subtitle": raw_cut.get("subtitle") or raw_cut.get("caption") or "",
                "mapped_fields": sorted(set(raw_cut.keys()) & _KNOWN_CUT_FIELDS),
                "rejected_fields": unknown,
                "asset_validation": {
                    "asset_id": str(raw_cut.get("source")) if raw_cut.get("source") in assets else None,
                    "path_declared": bool(source),
                    "manifest_entry": bool(asset),
                },
            }
        )
    total_duration = max(item["end_seconds"] for item in mapped)
    audio_plan = build_audio_plan(edit_decisions, asset_manifest, total_duration)
    return {
        "version": "1.0",
        "composition_id": "root",
        "renderer_family": edit_decisions.get("renderer_family"),
        "render_runtime": edit_decisions.get("render_runtime"),
        "duration_seconds": round(total_duration, 6),
        "fps": int(edit_decisions.get("fps") or 30),
        "profile": edit_decisions.get("profile") or edit_decisions.get("output_profile"),
        "cuts": mapped,
        "audio": audio_plan,
        "transitions": list(edit_decisions.get("transitions") or []),
        "strict": bool(strict),
    }


def build_motion_sidecar(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Build a seekable motion-intent sidecar for ``hyperframes inspect``."""

    duration = float(mapping.get("duration_seconds") or 0)
    layers: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    for cut in mapping.get("cuts", []):
        if not isinstance(cut, Mapping):
            continue
        start = float(cut.get("start_seconds") or 0)
        end = float(cut.get("end_seconds") or start)
        selector = f"#cut-{len(layers)}"
        layers.append(
            {
                "id": cut.get("id"),
                "selector": selector,
                "start_seconds": start,
                "end_seconds": end,
                "animation": cut.get("animation") or "static",
                "keyframe_count": int(cut.get("keyframe_count") or 0),
                "motion_required": bool(cut.get("motion_required")),
                "sample_times": [round(start, 6), round((start + end) / 2, 6), round(end, 6)],
            }
        )
        assertions.append({"kind": "staysInFrame", "selector": selector})
        if cut.get("motion_required"):
            assertions.append(
                {
                    "kind": "keepsMoving",
                    "withinSelector": selector,
                    "maxStaticSec": max(0.5, min(2.0, (end - start) / 2)),
                }
            )
    return {
        "version": 1,
        "duration": round(duration, 6),
        "layers": layers,
        "assertions": assertions,
    }


def select_worker_policy(
    mapping: Mapping[str, Any],
    *,
    requested_workers: int | None = None,
    cpu_count: int | None = None,
) -> dict[str, Any]:
    """Choose conservative deterministic HyperFrames worker counts."""

    cuts = [item for item in mapping.get("cuts", []) if isinstance(item, Mapping)]
    video_heavy = any(str(item.get("type")) in {"video", "animation"} for item in cuts)
    duration = float(mapping.get("duration_seconds") or 0)
    pixels = 0
    profile = str(mapping.get("profile") or "")
    if "vertical" in profile:
        pixels = 1080 * 1920
    elif "square" in profile:
        pixels = 1080 * 1080
    else:
        pixels = 1920 * 1080
    requested = None if requested_workers is None else int(requested_workers)
    if requested is not None and requested < 1:
        raise HyperFramesContractError("workers must be at least 1")
    safe_cpu = max(1, int(cpu_count or (os.cpu_count() or 1)))
    heavy = video_heavy or pixels >= 1920 * 1080 and duration >= 30
    default = 1 if heavy else min(2, safe_cpu)
    effective = default if requested is None else requested
    capped = False
    reasons: list[str] = []
    # A caller-provided worker count is an upper-bound request, not a license
    # to create an arbitrary number of browser/FFmpeg workers. Four is the
    # maximum safe fan-out for the supported minimum runner and still leaves
    # CPU for the Backlot/API process and the operating system.
    if effective > 4:
        effective = 4
        capped = True
        reasons.append("worker count capped at the global four-worker safety limit")
    if heavy and effective > 1:
        effective = 1
        capped = True
        reasons.append("video-heavy or high-resolution composition defaults to one worker")
    if effective > safe_cpu:
        effective = safe_cpu
        capped = True
        reasons.append("worker count capped at detected CPU count")
    return {
        "requested_workers": requested,
        "workers": effective,
        "cpu_count": safe_cpu,
        "video_heavy": video_heavy,
        "resource_heavy": heavy,
        "capped": capped,
        "reasons": reasons,
    }


def workspace_digest(root: Path | str) -> str:
    """Hash a workspace's files in stable relative-path order."""

    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise HyperFramesContractError(f"HyperFrames workspace does not exist: {path}")
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        rel = file_path.relative_to(path).as_posix()
        if rel.endswith(".part") or ".promotion.lock" in rel:
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def write_motion_sidecar(workspace: Path | str, mapping: Mapping[str, Any]) -> Path:
    """Write the inspect motion sidecar atomically and return its path."""

    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root / "index.motion.json"
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(build_motion_sidecar(mapping), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return output


__all__ = [
    "HyperFramesContractError",
    "SUPPORTED_CUT_TYPES",
    "SUPPORTED_TRANSITIONS",
    "build_audio_plan",
    "build_edit_mapping",
    "build_motion_sidecar",
    "select_worker_policy",
    "workspace_digest",
    "write_motion_sidecar",
]
