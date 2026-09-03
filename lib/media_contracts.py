"""Canonical request/result contracts shared by every visual source."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from lib.music_contracts import validate_music_asset_provenance


class MediaContractError(ValueError):
    """Raised when a visual asset request/result is incomplete or unsafe."""


MEDIA_TYPES = frozenset({"image", "video", "audio", "diagram", "animation", "user_media", "stock", "generated"})
STRATEGIES = frozenset({"user", "stock", "ai", "diagram", "generated", "local", "source"})


def strict_bool(value: Any, field_name: str) -> bool:
    """Accept only a real boolean for media gating and policy fields."""
    if not isinstance(value, bool):
        raise MediaContractError(f"{field_name} must be boolean")
    return value


def _text(value: Any, field: str, *, required: bool = True) -> str:
    if value in (None, ""):
        if required:
            raise MediaContractError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise MediaContractError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise MediaContractError(f"{field} is required")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class AssetRequest:
    request_id: str
    scene_id: str
    intent: str
    media_type: str
    strategy: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    provenance_required: bool = True
    source_refs: Sequence[str] = ()
    sample_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id"))
        object.__setattr__(self, "scene_id", _text(self.scene_id, "scene_id"))
        object.__setattr__(self, "intent", _text(self.intent, "intent"))
        media_type = _text(self.media_type, "media_type").lower()
        if media_type not in MEDIA_TYPES:
            raise MediaContractError(f"unsupported media_type {media_type!r}")
        object.__setattr__(self, "media_type", media_type)
        strategy = _text(self.strategy, "strategy").lower()
        if strategy not in STRATEGIES:
            raise MediaContractError(f"unsupported asset strategy {strategy!r}")
        object.__setattr__(self, "strategy", strategy)
        if not isinstance(self.constraints, Mapping):
            raise MediaContractError("constraints must be an object")
        if not isinstance(self.source_refs, Sequence) or isinstance(self.source_refs, (str, bytes)):
            raise MediaContractError("source_refs must be an array")
        strict_bool(self.provenance_required, "provenance_required")
        strict_bool(self.sample_required, "sample_required")
        object.__setattr__(self, "constraints", json.loads(_canonical(dict(self.constraints))))
        object.__setattr__(self, "source_refs", tuple(_text(item, "source_refs[]") for item in self.source_refs))

    @property
    def stable_key(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict(include_key=False)).encode("utf-8")).hexdigest()

    def to_dict(self, *, include_key: bool = True) -> dict[str, Any]:
        payload = {
            "request_id": self.request_id,
            "scene_id": self.scene_id,
            "intent": self.intent,
            "media_type": self.media_type,
            "strategy": self.strategy,
            "constraints": dict(self.constraints),
            "provenance_required": bool(self.provenance_required),
            "source_refs": list(self.source_refs),
            "sample_required": bool(self.sample_required),
        }
        if include_key:
            payload["stable_key"] = self.stable_key
        return payload


@dataclass(frozen=True)
class AssetResult:
    asset_id: str
    request_id: str
    scene_id: str
    media_type: str
    strategy: str
    path: str
    provider: str
    sha256: str
    size_bytes: int
    mime_type: str
    source_url: str = ""
    creator: str = ""
    license: str = ""
    license_url: str = ""
    attribution_required: bool = False
    restrictions: Sequence[str] = ()
    model: str = ""
    prompt: str = ""
    retrieved_at: str = ""
    validation_status: str = "validated"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "request_id": self.request_id,
            "scene_id": self.scene_id,
            "media_type": self.media_type,
            "strategy": self.strategy,
            "path": self.path,
            "provider": self.provider,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "source_url": self.source_url,
            "creator": self.creator,
            "license": self.license,
            "license_url": self.license_url,
            "attribution_required": bool(self.attribution_required),
            "restrictions": list(self.restrictions),
            "model": self.model,
            "prompt": self.prompt,
            "retrieved_at": self.retrieved_at,
            "validation_status": self.validation_status,
            "metadata": dict(self.metadata),
        }


def build_asset_request(value: Mapping[str, Any] | AssetRequest) -> AssetRequest:
    if isinstance(value, AssetRequest):
        return value
    if not isinstance(value, Mapping):
        raise MediaContractError("asset request must be an object")
    return AssetRequest(
        request_id=str(value.get("request_id") or value.get("id") or ""),
        scene_id=str(value.get("scene_id") or value.get("slot_id") or ""),
        intent=str(value.get("intent") or value.get("description") or value.get("prompt") or ""),
        media_type=str(value.get("media_type") or value.get("type") or ""),
        strategy=str(value.get("strategy") or value.get("source_strategy") or value.get("source") or ""),
        constraints=value.get("constraints") or {},
        provenance_required=(
            strict_bool(value["provenance_required"], "provenance_required")
            if "provenance_required" in value else True
        ),
        source_refs=value.get("source_refs") or (),
        sample_required=(
            strict_bool(value["sample_required"], "sample_required")
            if "sample_required" in value else False
        ),
    )


def validate_asset_request(value: Mapping[str, Any] | AssetRequest) -> dict[str, Any]:
    try:
        request = build_asset_request(value)
    except MediaContractError as exc:
        return {"valid": False, "errors": [str(exc)], "request": None}
    errors: list[str] = []
    constraints = request.constraints
    if constraints.get("orientation") and constraints["orientation"] not in {"landscape", "portrait", "square", "any"}:
        errors.append("constraints.orientation must be landscape, portrait, square, or any")
    for field_name in ("min_width", "min_height", "min_duration_seconds", "max_duration_seconds"):
        if field_name in constraints:
            try:
                if float(constraints[field_name]) < 0:
                    errors.append(f"constraints.{field_name} cannot be negative")
            except (TypeError, ValueError):
                errors.append(f"constraints.{field_name} must be numeric")
    if request.strategy in {"stock", "source"} and request.provenance_required is not True:
        errors.append("stock/source requests must require provenance")
    return {"valid": not errors, "errors": errors, "request": request.to_dict()}


def validate_asset_result(
    value: Mapping[str, Any] | AssetResult,
    *,
    request: Mapping[str, Any] | AssetRequest | None = None,
) -> dict[str, Any]:
    if isinstance(value, AssetResult):
        result = value
    elif isinstance(value, Mapping):
        errors: list[str] = []
        for field_name in ("asset_id", "request_id", "scene_id", "media_type", "strategy", "path", "provider", "sha256", "mime_type"):
            if not _text(value.get(field_name), field_name, required=False):
                errors.append(f"{field_name} is required")
        try:
            size = int(value.get("size_bytes"))
            if size <= 0:
                errors.append("size_bytes must be positive")
        except (TypeError, ValueError):
            errors.append("size_bytes must be a positive integer")
        for field_name in ("source_url", "creator", "license"):
            if str(value.get("strategy") or "").lower() in {"stock", "source"} and not _text(value.get(field_name), field_name, required=False):
                errors.append(f"{field_name} is required for stock/source assets")
        sha = str(value.get("sha256") or "").lower()
        if sha and not re.fullmatch(r"[a-f0-9]{64}", sha):
            errors.append("sha256 must be a 64-character lowercase hexadecimal digest")
        mime = str(value.get("mime_type") or "")
        if mime.count("/") != 1 or any(not part for part in mime.split("/", 1)):
            errors.append("mime_type must be a concrete type such as image/png")
        if value.get("restrictions") is not None and not isinstance(value.get("restrictions"), (list, tuple)):
            errors.append("restrictions must be an array")
        if errors:
            return {"valid": False, "errors": errors, "result": None}
        result = AssetResult(
            asset_id=str(value["asset_id"]), request_id=str(value["request_id"]), scene_id=str(value["scene_id"]),
            media_type=str(value["media_type"]), strategy=str(value["strategy"]), path=str(value["path"]),
            provider=str(value["provider"]), sha256=str(value["sha256"]), size_bytes=int(value["size_bytes"]),
            mime_type=str(value["mime_type"]), source_url=str(value.get("source_url") or ""),
            creator=str(value.get("creator") or ""), license=str(value.get("license") or ""),
            license_url=str(value.get("license_url") or ""),
            attribution_required=bool(value.get("attribution_required", False)),
            restrictions=tuple(str(item) for item in (value.get("restrictions") or ())),
            model=str(value.get("model") or ""), prompt=str(value.get("prompt") or ""),
            retrieved_at=str(value.get("retrieved_at") or ""), validation_status=str(value.get("validation_status") or "validated"),
            metadata=value.get("metadata") or {},
        )
    else:
        return {"valid": False, "errors": ["asset result must be an object"], "result": None}
    errors = []
    if result.media_type not in MEDIA_TYPES:
        errors.append(f"unsupported result media_type {result.media_type!r}")
    if result.strategy not in STRATEGIES:
        errors.append(f"unsupported result strategy {result.strategy!r}")
    if result.size_bytes <= 0:
        errors.append("size_bytes must be positive")
    if not re.fullmatch(r"[a-f0-9]{64}", str(result.sha256).lower()):
        errors.append("sha256 must be a 64-character lowercase hexadecimal digest")
    if not result.path.strip():
        errors.append("path is required")
    if "/" not in result.mime_type:
        errors.append("mime_type must be a concrete type such as image/png")
    if result.validation_status not in {"validated", "pending", "rejected"}:
        errors.append("validation_status must be validated, pending, or rejected")
    if result.validation_status == "rejected":
        errors.append("rejected asset results cannot be promoted")
    if request is not None:
        req = build_asset_request(request)
        if result.request_id != req.request_id:
            errors.append("result.request_id does not match request")
        if result.scene_id != req.scene_id:
            errors.append("result.scene_id does not match request")
        if req.strategy in {"stock", "source"} and (not result.source_url or not result.license):
            errors.append("stock/source result is missing source URL or license")
        if req.strategy in {"ai", "generated"} and not (result.provider and (result.model or result.prompt)):
            errors.append("generated result must retain provider plus model or prompt provenance")
    return {"valid": not errors, "errors": errors, "result": result.to_dict()}


def validate_mixed_media_coverage(
    asset_manifest: Mapping[str, Any],
    edit_decisions: Mapping[str, Any],
    *,
    contact_sheet: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure approved mixed-media assets are provenance-complete and edited in."""
    errors: list[str] = []
    assets = list(asset_manifest.get("assets") or []) if isinstance(asset_manifest, Mapping) else []
    by_id = {str(item.get("id")): item for item in assets if isinstance(item, Mapping) and item.get("id")}
    references: set[str] = set()
    for cut in (edit_decisions.get("cuts") or []) if isinstance(edit_decisions, Mapping) else []:
        if isinstance(cut, Mapping) and cut.get("source") is not None:
            references.add(str(cut.get("source")))
    for overlay in (edit_decisions.get("overlays") or []) if isinstance(edit_decisions, Mapping) else []:
        if isinstance(overlay, Mapping) and overlay.get("asset_id") is not None:
            references.add(str(overlay.get("asset_id")))
    audio = edit_decisions.get("audio") if isinstance(edit_decisions, Mapping) else {}
    if isinstance(audio, Mapping):
        for key in ("narration", "music"):
            value = audio.get(key)
            if isinstance(value, Mapping):
                if value.get("asset_id") is not None:
                    references.add(str(value["asset_id"]))
                for segment in value.get("segments") or []:
                    if isinstance(segment, Mapping) and segment.get("asset_id") is not None:
                        references.add(str(segment["asset_id"]))
        for item in audio.get("sfx") or []:
            if isinstance(item, Mapping) and item.get("asset_id") is not None:
                references.add(str(item["asset_id"]))
    if isinstance(edit_decisions, Mapping):
        music = edit_decisions.get("music")
        if isinstance(music, Mapping) and music.get("asset_id") is not None:
            references.add(str(music["asset_id"]))

    for asset_id, asset in by_id.items():
        strategy = str(asset.get("strategy") or asset.get("subtype") or "").lower()
        if not strategy:
            errors.append(f"asset {asset_id} missing canonical strategy")
        if asset.get("type") in {"image", "video", "diagram", "animation"} and not str(asset.get("sha256") or "").strip():
            errors.append(f"asset {asset_id} missing content hash")
        if strategy in {"stock", "source", "licensed_stock"}:
            if not (str(asset.get("source_url") or asset.get("original_url") or "").strip()):
                errors.append(f"asset {asset_id} missing stock provenance source_url")
            for key in ("creator", "license"):
                if not str(asset.get(key) or "").strip():
                    errors.append(f"asset {asset_id} missing stock provenance {key}")
        elif strategy in {"ai", "generated", "generated_ai"}:
            if not str(asset.get("provider") or "").strip():
                errors.append(f"asset {asset_id} missing generation provider")
            if not (str(asset.get("model") or "").strip() or str(asset.get("prompt") or "").strip()):
                errors.append(f"asset {asset_id} missing generation model/prompt")
        elif strategy in {"user", "user_media"}:
            validation = asset.get("validation") or {}
            if validation.get("consent") is not True and asset.get("consent") is not True:
                errors.append(f"asset {asset_id} missing explicit user-media consent evidence")
        elif strategy == "diagram":
            validation = asset.get("validation") or {}
            semantic = validation.get("semantic_validation") or validation.get("semantic") or {}
            if not isinstance(semantic, Mapping) or semantic.get("valid") is not True:
                errors.append(f"diagram asset {asset_id} is missing semantic validation evidence")
        if asset.get("validation_status") == "rejected":
            errors.append(f"rejected asset {asset_id} is present in canonical manifest")
        for music_error in validate_music_asset_provenance(asset):
            errors.append(f"asset {asset_id}: {music_error}")
        if contact_sheet and contact_sheet.get("approval_status") == "approved":
            approved_ids = set(str(value) for value in (contact_sheet.get("approved_candidate_ids") or []))
            for candidate in contact_sheet.get("candidates") or []:
                if not isinstance(candidate, Mapping) or candidate.get("rejected"):
                    continue
                if approved_ids and str(candidate.get("candidate_id")) not in approved_ids:
                    continue
                if str(candidate.get("candidate_id")) == asset_id and asset_id not in references:
                    errors.append(f"approved candidate {asset_id} does not appear in edit decisions")
    return {"valid": not errors, "errors": errors, "asset_count": len(by_id), "referenced_asset_ids": sorted(references)}


__all__ = [
    "MEDIA_TYPES", "STRATEGIES", "MediaContractError", "AssetRequest", "AssetResult",
    "build_asset_request", "strict_bool", "validate_asset_request", "validate_asset_result",
    "validate_mixed_media_coverage",
]
