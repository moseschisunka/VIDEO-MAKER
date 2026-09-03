"""Deterministic contracts for proposal-stage music decisions.

Music is optional, but the *decision* is not.  Keeping this contract in one
place prevents a missing music choice from silently turning into a generated
track (or an unexplained silent mix) later in the pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


MUSIC_SOURCE_TYPES = ("user_library", "ai_generated", "bring_your_own", "none")


class MusicContractError(ValueError):
    """Raised when a proposal music decision is missing or ambiguous."""


EDIT_RIGHTS = ("allowed", "not_allowed", "unknown")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_music_source(
    value: Mapping[str, Any] | None,
    *,
    require_explicit: bool = True,
) -> dict[str, Any]:
    """Return the canonical proposal-stage music decision.

    ``none`` is a supported, intentional decision and must include a short
    reason.  Other source types carry only fields that can be audited later;
    downstream asset provenance adds the measured duration and rights facts.
    """

    if value is None:
        if not require_explicit:
            return {"source_type": "none", "reason": "No music decision supplied."}
        raise MusicContractError(
            "production_plan.music_source is required; choose user_library, "
            "ai_generated, bring_your_own, or none"
        )
    if not isinstance(value, Mapping):
        raise MusicContractError("production_plan.music_source must be an object")

    source_type = _text(value.get("source_type"))
    if source_type not in MUSIC_SOURCE_TYPES:
        raise MusicContractError(
            "production_plan.music_source.source_type must be one of "
            + ", ".join(MUSIC_SOURCE_TYPES)
        )

    result: dict[str, Any] = {"source_type": source_type}
    for key in (
        "track_path",
        "provider",
        "mood_direction",
        "prompt",
        "model",
        "license",
        "license_url",
        "source_url",
        "reason",
    ):
        text = _text(value.get(key))
        if text is not None:
            result[key] = text
    if "estimated_cost_usd" in value and value.get("estimated_cost_usd") is not None:
        try:
            cost = float(value["estimated_cost_usd"])
        except (TypeError, ValueError) as exc:
            raise MusicContractError("music_source.estimated_cost_usd must be numeric") from exc
        if cost < 0:
            raise MusicContractError("music_source.estimated_cost_usd cannot be negative")
        result["estimated_cost_usd"] = cost

    if source_type == "none":
        if not result.get("reason"):
            raise MusicContractError(
                "music_source.reason is required when source_type is 'none'"
            )
        # A no-music decision must not retain an accidental track reference.
        if result.get("track_path"):
            raise MusicContractError(
                "music_source.track_path must be omitted when source_type is 'none'"
            )
    elif source_type in {"user_library", "bring_your_own"}:
        if not result.get("track_path"):
            raise MusicContractError(
                f"music_source.track_path is required for source_type '{source_type}'"
            )
        if source_type == "bring_your_own" and not (
            result.get("license") or result.get("reason")
        ):
            raise MusicContractError(
                "bring_your_own music requires license or a rights rationale"
            )
    elif source_type == "ai_generated":
        if not result.get("provider"):
            raise MusicContractError("ai_generated music requires provider")
        if not (result.get("prompt") or result.get("mood_direction")):
            raise MusicContractError(
                "ai_generated music requires prompt or mood_direction"
            )

    return result


def append_music_decision(
    decision_log: Mapping[str, Any] | None,
    music_source: Mapping[str, Any] | None,
    *,
    stage: str = "proposal",
    user_approved: bool = False,
) -> dict[str, Any]:
    """Append or preserve the auditable proposal music decision.

    Re-running a project with the same decision is idempotent.  A changed
    decision receives a new decision id, preserving the history needed for
    approval and later provenance checks.
    """

    normalized = normalize_music_source(music_source)
    if not isinstance(user_approved, bool):
        raise MusicContractError("music_source.user_approved must be boolean")
    result = dict(decision_log or {})
    decisions = list(result.get("decisions") or [])
    selected = normalized["source_type"]
    subject = "Music source"
    prior = next(
        (
            item
            for item in reversed(decisions)
            if isinstance(item, Mapping)
            and item.get("category") == "music_source"
            and item.get("subject") == subject
        ),
        None,
    )
    if prior and prior.get("selected") == selected:
        result["decisions"] = decisions
        return result

    options: list[dict[str, Any]] = []
    labels = {
        "user_library": "Use a track from the project music library",
        "ai_generated": "Generate an original music bed with an AI provider",
        "bring_your_own": "Use a user-supplied track with recorded rights",
        "none": "Proceed without music",
    }
    for option_type in MUSIC_SOURCE_TYPES:
        option: dict[str, Any] = {
            "option_id": option_type,
            "label": labels[option_type],
            "score": 1.0 if option_type == selected else 0.5,
            "reason": "Selected proposal-stage music decision."
            if option_type == selected
            else "Available alternative surfaced before asset generation.",
        }
        if option_type != selected:
            option["rejected_because"] = "Not selected for this run."
        options.append(option)

    decisions.append(
        {
            "decision_id": f"d-{len(decisions) + 1:03d}",
            "stage": stage,
            "category": "music_source",
            "subject": subject,
            "options_considered": options,
            "selected": selected,
            "reason": normalized.get("reason")
            or normalized.get("mood_direction")
            or f"Selected {selected} at proposal stage.",
            "user_visible": True,
            "user_approved": user_approved,
            "confidence": 1.0,
        }
    )
    result["decisions"] = decisions
    return result


def normalize_music_provenance(
    value: Mapping[str, Any] | None,
    *,
    source_type: str | None = None,
    source_tool: str | None = None,
) -> dict[str, Any]:
    """Normalize provenance attached to a generated or sourced music asset.

    Unknown rights are represented explicitly as ``unknown``/``unverified``;
    they are never silently promoted to a permissive license.  A later release
    gate can therefore block the asset while still preserving the full record.
    """

    raw = dict(value or {})
    resolved_type = _text(raw.get("source_type")) or _text(source_type)
    if resolved_type not in MUSIC_SOURCE_TYPES or resolved_type == "none":
        raise MusicContractError(
            "music provenance source_type must be user_library, ai_generated, or bring_your_own"
        )
    tool = _text(raw.get("source_tool")) or _text(source_tool)
    provider = _text(raw.get("provider"))
    if not tool:
        raise MusicContractError("music provenance source_tool is required")
    if not provider:
        raise MusicContractError("music provenance provider is required")

    duration_raw = raw.get("duration_seconds")
    duration: float | None
    if duration_raw in (None, ""):
        duration = None
    else:
        try:
            duration = float(duration_raw)
        except (TypeError, ValueError) as exc:
            raise MusicContractError("music provenance duration_seconds must be numeric") from exc
        if duration < 0:
            raise MusicContractError("music provenance duration_seconds cannot be negative")

    loop_allowed = raw.get("loop_allowed")
    if loop_allowed not in (True, False, None):
        raise MusicContractError("music provenance loop_allowed must be boolean or null")
    edit_rights = _text(raw.get("edit_rights")) or "unknown"
    if edit_rights not in EDIT_RIGHTS:
        raise MusicContractError("music provenance edit_rights must be allowed, not_allowed, or unknown")

    license_name = _text(raw.get("license")) or "unverified"
    result: dict[str, Any] = {
        "source_type": resolved_type,
        "source_tool": tool,
        "provider": provider,
        "license": license_name,
        "duration_seconds": duration,
        "loop_allowed": loop_allowed,
        "edit_rights": edit_rights,
        "rights_status": "verified" if license_name.lower() not in {"unknown", "unverified", "not_recorded"} and edit_rights != "unknown" else "needs_review",
    }
    for key in (
        "source_url",
        "license_url",
        "prompt",
        "model",
        "creator",
        "retrieved_at",
    ):
        text = _text(raw.get(key))
        if text is not None:
            result[key] = text
    if isinstance(raw.get("restrictions"), (list, tuple)):
        result["restrictions"] = [str(item).strip() for item in raw["restrictions"] if str(item).strip()]
    if resolved_type == "ai_generated" and not (result.get("prompt") or result.get("model")):
        raise MusicContractError("AI music provenance requires prompt or model")
    return result


def music_provenance_from_output(
    data: Mapping[str, Any] | None,
    inputs: Mapping[str, Any] | None = None,
    *,
    source_tool: str,
    source_type: str = "ai_generated",
) -> dict[str, Any]:
    """Build a complete manifest-ready record from a provider result."""

    result = dict(inputs or {})
    result.update(dict(data or {}))
    if source_type == "user_library":
        source_type = "user_library"
    return normalize_music_provenance(
        {
            "source_type": source_type,
            "source_tool": source_tool,
            "provider": result.get("provider") or "unknown",
            "source_url": result.get("source_url") or result.get("freesound_url"),
            "license_url": result.get("license_url"),
            "license": result.get("license") or "unverified",
            "prompt": result.get("prompt") or result.get("query"),
            "model": result.get("model"),
            "creator": result.get("creator") or result.get("artist"),
            "duration_seconds": result.get("duration_seconds"),
            "loop_allowed": result.get("loop_allowed"),
            "edit_rights": result.get("edit_rights") or "unknown",
            "restrictions": result.get("restrictions"),
            "retrieved_at": result.get("retrieved_at"),
        },
        source_type=source_type,
        source_tool=source_tool,
    )


def validate_music_asset_provenance(asset: Mapping[str, Any]) -> list[str]:
    """Return actionable errors for a canonical ``type=music`` asset row."""

    if str(asset.get("type") or "").lower() != "music":
        return []
    try:
        normalize_music_provenance(
            asset.get("music_provenance") if isinstance(asset.get("music_provenance"), Mapping) else asset,
            source_type=(asset.get("music_provenance") or {}).get("source_type") if isinstance(asset.get("music_provenance"), Mapping) else asset.get("source_type"),
            source_tool=asset.get("source_tool"),
        )
    except MusicContractError as exc:
        return [str(exc)]
    return []


__all__ = [
    "EDIT_RIGHTS",
    "MUSIC_SOURCE_TYPES",
    "MusicContractError",
    "append_music_decision",
    "music_provenance_from_output",
    "normalize_music_provenance",
    "normalize_music_source",
    "validate_music_asset_provenance",
]
