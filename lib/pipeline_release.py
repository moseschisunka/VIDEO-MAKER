"""Release-scope and non-production labelling for pipeline discovery.

This module deliberately sits below the Backlot UI and API.  A manifest's
declared ``stability`` is useful descriptive metadata, but it is not a release
decision.  The approved Phase 0 scope file is the source of truth for what is
visible and creatable before the production-readiness gates pass.  Missing or
malformed scope data fails closed to an internal experimental state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parent.parent
SCOPE_PATH = REPO_ROOT / "config" / "pipeline_release_scope.json"
PRODUCTION_GATE = "PR-11G"
LANE_ORDER = {"launch": 0, "beta": 1, "experimental": 2, "test": 3}

_FALLBACK_SCOPE: dict[str, Any] = {
    "schema_version": "1.0",
    "decision_id": "UNRECORDED",
    "production_gate": PRODUCTION_GATE,
    "studio_release_status": "internal_preview",
    "studio_release_label": "Internal preview — production certification pending",
    "pipelines": {},
}


def _load_scope() -> dict[str, Any]:
    """Read the reviewed release scope, failing closed on any bad input."""
    try:
        data = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return dict(_FALLBACK_SCOPE)
    if not isinstance(data, dict):
        return dict(_FALLBACK_SCOPE)
    pipelines = data.get("pipelines")
    if not isinstance(pipelines, dict):
        return dict(_FALLBACK_SCOPE)
    result = dict(_FALLBACK_SCOPE)
    result.update({k: data[k] for k in (
        "schema_version", "decision_id", "production_gate",
        "studio_release_status", "studio_release_label",
    ) if k in data})
    result["pipelines"] = pipelines
    return result


def studio_release_status() -> dict[str, Any]:
    """Return the global release status exposed by every user-facing surface."""
    scope = _load_scope()
    return {
        "status": str(scope.get("studio_release_status") or "internal_preview"),
        "label": str(scope.get("studio_release_label") or _FALLBACK_SCOPE["studio_release_label"]),
        "production_ready": False,
        "production_gate": str(scope.get("production_gate") or PRODUCTION_GATE),
        "scope_decision_id": str(scope.get("decision_id") or "UNRECORDED"),
    }


def pipeline_release_metadata(
    pipeline_id: str | None,
    *,
    manifest: Mapping[str, Any] | None = None,
    schema_valid: bool = True,
    validation_error: str | None = None,
) -> dict[str, Any]:
    """Derive honest, serialisable release metadata for one pipeline.

    ``schema_valid`` is supplied by the canonical manifest loader.  A failed
    validation can never become creatable merely because a scope entry says
    ``launch``; this prevents a malformed manifest from being advertised as a
    usable workflow.
    """
    pipeline_name = str(pipeline_id or "unknown")
    scope = _load_scope()
    entry = scope.get("pipelines", {}).get(pipeline_name)
    if not isinstance(entry, dict):
        # Unknown/unclassified is deliberately less mature than experimental.
        entry = {
            "lane": "experimental",
            "ui_visible": False,
            "creation_enabled": False,
            "boundary": "Pipeline is not present in the approved release scope.",
        }

    lane = str(entry.get("lane") or "experimental").lower()
    if lane not in LANE_ORDER:
        lane = "experimental"
    declared_stability = None
    category = None
    manifest_release: dict[str, Any] = {
        "ui_visible": False,
        "maturity": "experimental",
        "supported_runtimes": [],
        "supported_profiles": [],
        "required_capabilities": [],
        "required_artifacts": [],
        "deprecated": False,
        "replacement_pipeline": None,
        "deprecation_reason": None,
    }
    if isinstance(manifest, Mapping):
        declared_stability = manifest.get("stability")
        category = manifest.get("category")
        try:
            from lib.pipeline_loader import get_manifest_release_metadata
            manifest_release = get_manifest_release_metadata(dict(manifest))
        except Exception:
            # Keep the conservative defaults if a malformed object reaches
            # this boundary outside the canonical loader.
            pass

    ui_visible = bool(entry.get("ui_visible", False))
    creation_enabled = bool(entry.get("creation_enabled", False))
    availability = "available" if creation_enabled else "preview_only"
    reason = str(entry.get("boundary") or "Held for production-readiness evidence.")
    if not schema_valid:
        ui_visible = False
        creation_enabled = False
        if manifest is not None or validation_error:
            availability = "blocked"
            reason = "Manifest validation failed; this pipeline is hidden until its contract is repaired."
        else:
            availability = "unclassified"
            reason = str(entry.get("boundary") or "Pipeline is not present in the approved release scope.")
    elif lane == "test":
        ui_visible = False
        creation_enabled = False
        availability = "test_only"
        reason = str(entry.get("boundary") or "Internal test fixture; never user-visible.")

    labels = {
        "launch": "Launch candidate — not production-certified",
        "beta": "Beta — not production-certified",
        "experimental": "Experimental — internal preview only",
        "test": "Test only — not user-facing",
    }
    return {
        "release_lane": lane,
        "release_label": labels[lane],
        "release_status": "not_certified",
        "production_ready": False,
        "production_gate": str(scope.get("production_gate") or PRODUCTION_GATE),
        "scope_decision_id": str(scope.get("decision_id") or "UNRECORDED"),
        "ui_visible": ui_visible,
        "creation_enabled": creation_enabled,
        "availability": availability,
        "availability_reason": reason,
        "launch_modes": list(entry.get("launch_modes") or []),
        "manifest_stability": declared_stability,
        "manifest_category": category,
        "manifest_maturity": manifest_release["maturity"],
        "manifest_ui_visible": manifest_release["ui_visible"],
        "supported_runtimes": manifest_release["supported_runtimes"],
        "supported_profiles": manifest_release["supported_profiles"],
        "required_capabilities": manifest_release["required_capabilities"],
        "required_artifacts": manifest_release["required_artifacts"],
        "deprecated": manifest_release["deprecated"],
        "replacement_pipeline": manifest_release["replacement_pipeline"],
        "deprecation_reason": manifest_release["deprecation_reason"],
        "schema_valid": bool(schema_valid),
        "validation_error": str(validation_error)[:500] if validation_error else None,
    }


def pipeline_sort_key(record: Mapping[str, Any]) -> tuple[int, str]:
    """Place launch candidates first, then held lanes alphabetically."""
    return (LANE_ORDER.get(str(record.get("release_lane")), LANE_ORDER["experimental"]), str(record.get("id") or ""))


__all__ = [
    "LANE_ORDER",
    "PRODUCTION_GATE",
    "SCOPE_PATH",
    "pipeline_release_metadata",
    "pipeline_sort_key",
    "studio_release_status",
]
