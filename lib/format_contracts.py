"""Immutable output-profile propagation and validation across artifacts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from lib.media_profiles import get_profile, profile_contract, validate_duration


PROFILE_KEYS = ("output_profile", "profile")
ARTIFACT_ORDER = ("brief", "proposal_packet", "script", "scene_plan", "edit_decisions", "render_report")


def _profile_from(value: Mapping[str, Any]) -> str | None:
    for key in PROFILE_KEYS:
        raw = value.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip().lower()
    return None


def _profile_aliases(value: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(value[key]).strip().lower()
        for key in PROFILE_KEYS
        if value.get(key) is not None and str(value.get(key)).strip()
    )


def _duration_from(value: Mapping[str, Any]) -> float | None:
    for key in ("target_duration_seconds", "requested_duration_seconds", "total_duration_seconds"):
        raw = value.get(key)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
    return None


def build_format_contract(
    profile: str,
    *,
    target_duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Build the canonical profile/aspect/dimension contract."""

    normalized = str(profile).strip().lower()
    facts = profile_contract(normalized)
    if target_duration_seconds is not None:
        validate_duration(normalized, float(target_duration_seconds))
    return {
        "profile": normalized,
        "output_profile": normalized,
        "aspect_ratio": facts["aspect_ratio"],
        "width": facts["width"],
        "height": facts["height"],
        "fps": facts["fps"],
        "target_duration_seconds": (
            float(target_duration_seconds) if target_duration_seconds is not None else None
        ),
    }


def propagate_profile(
    artifacts: Mapping[str, Mapping[str, Any]],
    profile: str,
    *,
    target_duration_seconds: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Return copied artifacts carrying one immutable profile contract.

    Existing artifact fields are preserved.  This helper is intentionally
    pure: agents can preview the propagated handoff before writing it.
    """

    contract = build_format_contract(profile, target_duration_seconds=target_duration_seconds)
    normalized_duration = contract["target_duration_seconds"]
    result: dict[str, dict[str, Any]] = {
        str(name): deepcopy(dict(value))
        for name, value in artifacts.items()
        if isinstance(value, Mapping)
    }
    for name in ARTIFACT_ORDER:
        artifact = result.get(name)
        if artifact is None:
            continue
        artifact["output_profile"] = contract["output_profile"]
        artifact["profile"] = contract["profile"]
        artifact["aspect_ratio"] = contract["aspect_ratio"]
        if normalized_duration is not None and name not in {"render_report"}:
            artifact["target_duration_seconds"] = normalized_duration
        if name == "proposal_packet":
            plan = artifact.get("production_plan")
            if isinstance(plan, dict):
                plan["output_profile"] = contract["output_profile"]
                plan["profile"] = contract["profile"]
                plan["aspect_ratio"] = contract["aspect_ratio"]
                if normalized_duration is not None:
                    plan["target_duration_seconds"] = normalized_duration
        if name == "render_report":
            for output in artifact.get("outputs", []) or []:
                if isinstance(output, dict):
                    output["profile"] = contract["profile"]
                    output["output_profile"] = contract["output_profile"]
                    output["aspect_ratio"] = contract["aspect_ratio"]
    return result


def validate_profile_propagation(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    expected_profile: str | None = None,
    expected_duration_seconds: float | None = None,
    require_all: bool = False,
) -> dict[str, Any]:
    """Check profile, aspect, and target-duration consistency end to end."""

    errors: list[str] = []
    observed: list[tuple[str, str]] = []
    durations: list[tuple[str, float]] = []
    for name in ARTIFACT_ORDER:
        value = artifacts.get(name)
        if not isinstance(value, Mapping):
            continue
        profile = _profile_from(value)
        if profile:
            aliases_for_artifact = _profile_aliases(value)
            if len(set(aliases_for_artifact)) > 1:
                errors.append(
                    f"{name} declares conflicting profile aliases: "
                    + ", ".join(dict.fromkeys(aliases_for_artifact))
                )
            observed.append((name, profile))
            try:
                facts = profile_contract(profile)
            except ValueError as exc:
                errors.append(f"{name} declares unknown profile {profile!r}: {exc}")
                continue
            aspect = value.get("aspect_ratio")
            if aspect is None and (require_all or expected_profile):
                errors.append(f"{name} is missing aspect_ratio for profile {profile!r}")
            elif aspect is not None and str(aspect) != str(facts["aspect_ratio"]):
                errors.append(
                    f"{name}.aspect_ratio={aspect!r} does not match {profile!r} "
                    f"({facts['aspect_ratio']})"
                )
            duration = _duration_from(value)
            if duration is not None:
                durations.append((name, duration))
                try:
                    validate_duration(profile, duration)
                except ValueError as exc:
                    errors.append(f"{name}: {exc}")
        elif require_all or (expected_profile and isinstance(value.get("aspect_ratio"), str)):
            errors.append(f"{name} is missing output_profile/profile")

        if name == "proposal_packet":
            plan = value.get("production_plan")
            if isinstance(plan, Mapping):
                plan_profile = _profile_from(plan)
                if plan_profile and profile and plan_profile != profile:
                    errors.append(
                        f"proposal_packet.production_plan profile {plan_profile!r} does not match {profile!r}"
                    )
        if name == "render_report":
            for index, output in enumerate(value.get("outputs", []) or []):
                if not isinstance(output, Mapping):
                    continue
                output_profile = _profile_from(output)
                if output_profile:
                    observed.append((f"render_report.outputs[{index}]", output_profile))
                if output_profile and profile and output_profile != profile:
                    errors.append(
                        f"render_report.outputs[{index}] profile {output_profile!r} does not match {profile!r}"
                    )
                output_aspect = output.get("aspect_ratio")
                if output_aspect is not None and profile:
                    try:
                        expected_aspect = profile_contract(profile)["aspect_ratio"]
                    except ValueError:
                        expected_aspect = None
                    if expected_aspect and str(output_aspect) != str(expected_aspect):
                        errors.append(
                            f"render_report.outputs[{index}].aspect_ratio={output_aspect!r} "
                            f"does not match {profile!r} ({expected_aspect})"
                        )

    canonical_profile = str(expected_profile).strip().lower() if expected_profile else None
    if canonical_profile:
        try:
            profile_contract(canonical_profile)
        except ValueError as exc:
            errors.append(f"expected profile {canonical_profile!r} is invalid: {exc}")
        for name, profile in observed:
            if profile != canonical_profile:
                errors.append(f"{name} profile {profile!r} does not match expected {canonical_profile!r}")
    elif observed:
        canonical_profile = observed[0][1]
        for name, profile in observed[1:]:
            if profile != canonical_profile:
                errors.append(f"{name} profile {profile!r} does not match {canonical_profile!r}")

    if expected_duration_seconds is not None:
        expected_duration = float(expected_duration_seconds)
        for name, duration in durations:
            if name == "render_report":
                continue
            if abs(duration - expected_duration) > 0.001:
                errors.append(
                    f"{name} target duration {duration:g}s does not match expected {expected_duration:g}s"
                )

    contract_facts = None
    if canonical_profile:
        try:
            contract_facts = profile_contract(canonical_profile)
        except ValueError:
            contract_facts = None
    return {
        "valid": not errors,
        "profile": canonical_profile,
        "profile_contract": contract_facts,
        "observed": [{"artifact": name, "profile": profile} for name, profile in observed],
        "errors": list(dict.fromkeys(errors)),
    }


def assert_profile_propagation(
    artifacts: Mapping[str, Mapping[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Raise ``ValueError`` when profile propagation is not valid."""

    report = validate_profile_propagation(artifacts, **kwargs)
    if not report["valid"]:
        raise ValueError("profile propagation failed: " + "; ".join(report["errors"]))
    return report


__all__ = [
    "ARTIFACT_ORDER",
    "PROFILE_KEYS",
    "assert_profile_propagation",
    "build_format_contract",
    "propagate_profile",
    "validate_profile_propagation",
]
