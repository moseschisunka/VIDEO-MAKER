"""Approval-aware fallback classification helpers."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import FallbackClass


def classify_fallback(
    *,
    original_provider: str,
    replacement_provider: str | None = None,
    original_media_type: str | None = None,
    replacement_media_type: str | None = None,
    deterministic_retry: bool = False,
    available: bool = True,
) -> FallbackClass:
    """Classify a proposed alternative without executing it."""
    if not available:
        return FallbackClass.UNAVAILABLE
    if replacement_media_type and original_media_type and replacement_media_type != original_media_type:
        return FallbackClass.MATERIAL_MEDIA_CHANGE
    if replacement_provider and replacement_provider != original_provider:
        return FallbackClass.MATERIAL_PROVIDER_CHANGE
    if deterministic_retry:
        return FallbackClass.EQUIVALENT_NON_MATERIAL
    return FallbackClass.EQUIVALENT_NON_MATERIAL


def fallback_plan(
    original: Mapping[str, Any],
    alternatives: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return observable ranked alternatives; no provider call is made."""
    original_provider = str(original.get("provider") or "")
    original_media_type = str(original.get("media_type") or "") or None
    ranked: list[dict[str, Any]] = []
    for index, alternative in enumerate(alternatives):
        replacement_provider = str(alternative.get("provider") or "") or None
        replacement_media_type = str(alternative.get("media_type") or "") or None
        classification = classify_fallback(
            original_provider=original_provider,
            replacement_provider=replacement_provider,
            original_media_type=original_media_type,
            replacement_media_type=replacement_media_type,
            deterministic_retry=bool(alternative.get("deterministic_retry")),
            available=bool(alternative.get("available", True)),
        )
        ranked.append({
            **dict(alternative),
            "rank": index + 1,
            "fallback_class": classification.value,
            "requires_approval": classification in {
                FallbackClass.MATERIAL_PROVIDER_CHANGE,
                FallbackClass.MATERIAL_MEDIA_CHANGE,
            },
        })
    return {
        "original": dict(original),
        "alternatives": ranked,
        "execution": "blocked_until_approval"
        if any(item["requires_approval"] for item in ranked)
        else "agent_selects_and_executes",
    }


__all__ = ["classify_fallback", "fallback_plan"]
