"""Deterministic cross-artifact consistency checks for release review."""

from __future__ import annotations

from typing import Any, Mapping


IDENTITY_FIELDS = ("project_id", "pipeline_type", "run_id")


def _identity(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {field: None for field in IDENTITY_FIELDS}
    metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
    return {
        field: value.get(field) if value.get(field) not in (None, "") else metadata.get(field)
        for field in IDENTITY_FIELDS
    }


def _profile(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get("output_profile") or value.get("profile")
    return str(raw).strip().lower() if raw not in (None, "") else None


def _runtime(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get("render_runtime")
    if raw in (None, "") and isinstance(value.get("production_plan"), Mapping):
        raw = value["production_plan"].get("render_runtime")
    if raw in (None, "") and isinstance(value.get("metadata"), Mapping):
        raw = value["metadata"].get("render_runtime")
    return str(raw).strip().lower() if raw not in (None, "") else None


def validate_cross_artifact_consistency(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    expected_identity: Mapping[str, Any] | None = None,
    expected_profile: str | None = None,
    expected_runtime: str | None = None,
) -> dict[str, Any]:
    """Check identity, runtime/profile, duration and render/review linkage."""
    errors: list[str] = []
    warnings: list[str] = []
    values = {str(name): value for name, value in artifacts.items() if isinstance(value, Mapping)}
    canonical = {field: None for field in IDENTITY_FIELDS}
    if isinstance(expected_identity, Mapping):
        canonical.update({field: expected_identity.get(field) for field in IDENTITY_FIELDS})

    # Prefer proposal/brief identity when no durable expected identity was
    # supplied, then require every present artifact to agree.
    if not any(canonical.values()):
        for name in ("brief", "proposal_packet", "script", "scene_plan", "asset_manifest", "edit_decisions", "render_report", "final_review"):
            candidate = _identity(values.get(name))
            for field in IDENTITY_FIELDS:
                if canonical[field] in (None, "") and candidate[field] not in (None, ""):
                    canonical[field] = candidate[field]
    for name, value in values.items():
        identity = _identity(value)
        for field in IDENTITY_FIELDS:
            actual = identity[field]
            expected = canonical[field]
            if actual in (None, ""):
                # Creative fixtures may omit identity until they enter the
                # durable control plane; render/review artifacts may not.
                if name in {"render_report", "final_review"} or expected not in (None, ""):
                    errors.append(f"{name} is missing {field}")
            elif expected not in (None, "") and str(actual) != str(expected):
                errors.append(f"{name}.{field}={actual!r} does not match {expected!r}")
        if name == "proposal_packet" and isinstance(value.get("production_plan"), Mapping):
            nested = value["production_plan"]
            if nested.get("pipeline") not in (None, "") and canonical["pipeline_type"] not in (None, "") and str(nested["pipeline"]) != str(canonical["pipeline_type"]):
                errors.append("proposal_packet.production_plan.pipeline does not match pipeline_type")

    observed_profiles: list[tuple[str, str]] = []
    for name, value in values.items():
        profile = _profile(value)
        if profile:
            observed_profiles.append((name, profile))
        if name == "proposal_packet" and isinstance(value.get("production_plan"), Mapping):
            nested_profile = _profile(value["production_plan"])
            if nested_profile and profile and nested_profile != profile:
                errors.append("proposal_packet.production_plan profile does not match root profile")
    canonical_profile = str(expected_profile).strip().lower() if expected_profile else (
        observed_profiles[0][1] if observed_profiles else None
    )
    if canonical_profile:
        for name, profile in observed_profiles:
            if profile != canonical_profile:
                errors.append(f"{name} profile {profile!r} does not match {canonical_profile!r}")

    observed_runtimes: list[tuple[str, str]] = []
    for name, value in values.items():
        runtime = _runtime(value)
        if runtime:
            observed_runtimes.append((name, runtime))
    canonical_runtime = str(expected_runtime).strip().lower() if expected_runtime else (
        observed_runtimes[0][1] if observed_runtimes else None
    )
    if canonical_runtime:
        for name, runtime in observed_runtimes:
            if runtime != canonical_runtime:
                errors.append(f"{name} runtime {runtime!r} does not match {canonical_runtime!r}")
    # final_review records the runtime inside promise_preservation rather than
    # at its root; include it in the same identity comparison.
    review = values.get("final_review")
    if isinstance(review, Mapping):
        promise = (review.get("checks") or {}).get("promise_preservation") if isinstance(review.get("checks"), Mapping) else {}
        if isinstance(promise, Mapping) and promise.get("render_runtime_used"):
            runtime = str(promise["render_runtime_used"]).strip().lower()
            if canonical_runtime and runtime != canonical_runtime:
                errors.append(f"final_review runtime {runtime!r} does not match {canonical_runtime!r}")

    durations: list[tuple[str, float]] = []
    for name, value in values.items():
        raw = value.get("total_duration_seconds") or value.get("target_duration_seconds")
        if raw is None and name == "render_report":
            outputs = value.get("outputs") or []
            raw = outputs[0].get("duration_seconds") if outputs and isinstance(outputs[0], Mapping) else None
        if raw is None and name == "final_review":
            probe = ((value.get("checks") or {}).get("technical_probe") or {}) if isinstance(value.get("checks"), Mapping) else {}
            raw = probe.get("duration_seconds")
        try:
            if raw is not None:
                durations.append((name, float(raw)))
        except (TypeError, ValueError):
            errors.append(f"{name} declares a non-numeric duration")
    if durations:
        baseline_name, baseline = durations[0]
        for name, duration in durations[1:]:
            if abs(duration - baseline) > max(0.25, baseline * 0.02):
                errors.append(f"{name} duration {duration:g}s does not match {baseline_name} {baseline:g}s")

    render = values.get("render_report")
    final = values.get("final_review")
    if isinstance(render, Mapping) and isinstance(final, Mapping):
        if str(render.get("final_review_ref") or "").strip() not in {"artifacts/final_review.json", "final_review.json"}:
            errors.append("render_report.final_review_ref does not link to final_review")
        outputs = render.get("outputs") or []
        output_paths = {str(item.get("path")) for item in outputs if isinstance(item, Mapping) and item.get("path")}
        if final.get("output_path") and output_paths and not any(str(final["output_path"]).endswith(path) or str(path).endswith(str(final["output_path"])) for path in output_paths):
            errors.append("final_review.output_path does not match render_report output")
        if final.get("status") not in {"pass", "revise", "fail"}:
            errors.append("final_review.status is missing or invalid")
    elif render is not None or final is not None:
        errors.append("render_report and final_review must be supplied together")

    return {
        "valid": not errors,
        "checked": bool(values),
        "identity": canonical,
        "profile": canonical_profile,
        "runtime": canonical_runtime,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
    }


__all__ = ["validate_cross_artifact_consistency"]
