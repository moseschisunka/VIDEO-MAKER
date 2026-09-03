"""Release SLO definitions and deterministic measurement helpers.

SLOs are intentionally kept separate from the metrics registry.  The registry
records bounded observations while this module defines how an operator turns a
set of observations into a release decision.  Keeping the calculation in one
place prevents each benchmark, runbook, and CI job from inventing a different
p95 or error-budget convention.

The default configuration lives in ``config/slo.yaml`` and is safe to load in
offline CI.  No provider calls, credentials, or network access are performed by
this module.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml


SLO_SCHEMA_VERSION = "1.0"
SLO_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "slo.yaml"


class SLOConfigError(ValueError):
    """Raised when the machine-readable SLO contract is malformed."""


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SLOConfigError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise SLOConfigError(f"{field} must be finite")
    return number


def load_slo_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the release SLO configuration.

    A caller may pass a temporary path for contract tests, but the default is
    always the repository's checked-in configuration.  The returned mapping is
    a fresh YAML object and may safely be read without mutating global state.
    """

    config_path = Path(path) if path is not None else SLO_CONFIG_PATH
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SLOConfigError(f"SLO configuration cannot be read: {config_path}") from exc
    validate_slo_config(payload)
    return dict(payload)


def validate_slo_config(config: Mapping[str, Any]) -> None:
    """Validate the stable shape required by measurement and operations."""

    if not isinstance(config, Mapping):
        raise SLOConfigError("SLO configuration must be an object")
    if str(config.get("version") or "") != SLO_SCHEMA_VERSION:
        raise SLOConfigError(f"SLO configuration version must be {SLO_SCHEMA_VERSION!r}")
    reference = config.get("reference_environment")
    if not isinstance(reference, Mapping):
        raise SLOConfigError("reference_environment must be an object")
    for field in ("id", "os", "python", "providers"):
        if not str(reference.get(field) or "").strip():
            raise SLOConfigError(f"reference_environment.{field} is required")
    measurement = reference.get("measurement")
    if not isinstance(measurement, Mapping):
        raise SLOConfigError("reference_environment.measurement must be an object")
    for field in ("warmups", "samples", "percentile"):
        if field not in measurement:
            raise SLOConfigError(f"reference_environment.measurement.{field} is required")
    warmups = measurement.get("warmups")
    samples = measurement.get("samples")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise SLOConfigError("measurement.warmups must be a non-negative integer")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise SLOConfigError("measurement.samples must be a positive integer")
    percentile_name = str(measurement.get("percentile") or "").lower()
    if percentile_name != "p95":
        raise SLOConfigError("measurement.percentile must be 'p95'")

    gates = config.get("performance_gates")
    if not isinstance(gates, list) or not gates:
        raise SLOConfigError("performance_gates must be a non-empty list")
    seen_ids: set[str] = set()
    for gate in gates:
        if not isinstance(gate, Mapping):
            raise SLOConfigError("each performance gate must be an object")
        gate_id = str(gate.get("id") or "").strip()
        if not gate_id or gate_id in seen_ids:
            raise SLOConfigError(f"performance gate id is missing or duplicated: {gate_id!r}")
        seen_ids.add(gate_id)
        for field in ("name", "category", "indicator", "measurement"):
            if not str(gate.get(field) or "").strip():
                raise SLOConfigError(f"{gate_id}.{field} is required")
        target = gate.get("target")
        if not isinstance(target, Mapping):
            raise SLOConfigError(f"{gate_id}.target must be an object")
        operator = str(target.get("operator") or "")
        if operator not in {"<=", ">=", "<", ">"}:
            raise SLOConfigError(f"{gate_id}.target.operator is invalid")
        _finite_number(target.get("value"), field=f"{gate_id}.target.value")
        if not str(target.get("unit") or "").strip():
            raise SLOConfigError(f"{gate_id}.target.unit is required")

    objectives = config.get("service_level_objectives")
    if not isinstance(objectives, list) or not objectives:
        raise SLOConfigError("service_level_objectives must be a non-empty list")
    seen_objectives: set[str] = set()
    for objective in objectives:
        if not isinstance(objective, Mapping):
            raise SLOConfigError("each service-level objective must be an object")
        objective_id = str(objective.get("id") or "").strip()
        if not objective_id or objective_id in seen_objectives:
            raise SLOConfigError(f"service-level objective id is missing or duplicated: {objective_id!r}")
        seen_objectives.add(objective_id)
        for field in ("name", "indicator", "window", "target"):
            if field not in objective:
                raise SLOConfigError(f"{objective_id}.{field} is required")
        _finite_number(objective.get("target"), field=f"{objective_id}.target")
        budget = objective.get("error_budget")
        if budget is not None:
            _finite_number(budget, field=f"{objective_id}.error_budget")


def percentile(samples: Iterable[float], quantile: float = 0.95) -> float:
    """Return a deterministic nearest-rank percentile.

    The index convention intentionally matches ``MetricsRegistry.snapshot``:
    ``sorted_values[min(n - 1, int(n * q))]``.  This makes values copied from
    ``/api/metrics`` comparable with a CI benchmark using this helper.
    """

    q = _finite_number(quantile, field="quantile")
    if not 0.0 < q <= 1.0:
        raise ValueError("quantile must be greater than 0 and at most 1")
    values = [_finite_number(value, field="sample") for value in samples]
    if not values:
        raise ValueError("at least one sample is required")
    values.sort()
    return values[min(len(values) - 1, int(len(values) * q))]


def summarize_samples(samples: Iterable[float], *, quantile: float = 0.95) -> dict[str, float | int]:
    """Summarize bounded numeric observations for an evidence artifact."""

    values = [_finite_number(value, field="sample") for value in samples]
    if not values:
        raise ValueError("at least one sample is required")
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "avg": sum(ordered) / len(ordered),
        "p50": percentile(ordered, 0.50),
        "p95": percentile(ordered, quantile),
    }


def evaluate_threshold(
    samples: Iterable[float],
    *,
    target: float,
    operator: str = "<=",
    quantile: float = 0.95,
) -> dict[str, Any]:
    """Evaluate a latency/size/throughput observation against one target."""

    summary = summarize_samples(samples, quantile=quantile)
    threshold = _finite_number(target, field="target")
    if operator == "<=":
        passed = float(summary["p95"]) <= threshold
    elif operator == ">=":
        passed = float(summary["p95"]) >= threshold
    elif operator == "<":
        passed = float(summary["p95"]) < threshold
    elif operator == ">":
        passed = float(summary["p95"]) > threshold
    else:
        raise ValueError(f"unsupported threshold operator: {operator!r}")
    return {
        "status": "PASS" if passed else "FAIL",
        "operator": operator,
        "target": threshold,
        "percentile": f"p{int(quantile * 100)}",
        **summary,
    }


def evaluate_ratio(
    successes: int,
    total: int,
    *,
    target: float,
) -> dict[str, Any]:
    """Evaluate an availability or quality-escape ratio.

    ``target`` and the returned ``ratio`` are fractions (for example ``0.995``
    for 99.5%).  The explicit zero-total state prevents an empty test window
    from being reported as healthy.
    """

    if isinstance(successes, bool) or not isinstance(successes, int) or successes < 0:
        raise ValueError("successes must be a non-negative integer")
    if isinstance(total, bool) or not isinstance(total, int) or total < 1:
        raise ValueError("total must be a positive integer")
    if successes > total:
        raise ValueError("successes cannot exceed total")
    threshold = _finite_number(target, field="target")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("target must be a fraction between 0 and 1")
    ratio = successes / total
    return {
        "status": "PASS" if ratio >= threshold else "FAIL",
        "successes": successes,
        "total": total,
        "ratio": ratio,
        "target": threshold,
        "error_rate": 1.0 - ratio,
    }


def measure_callable(
    callback: Callable[[], Any],
    *,
    samples: int = 11,
    warmups: int = 2,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Measure a local callback using monotonic wall-clock observations.

    Warmups are intentionally excluded from the reported sample set.  A
    callback exception is allowed to propagate: an operation that cannot be
    completed must fail the benchmark rather than becoming a missing sample.
    """

    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise ValueError("samples must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a non-negative integer")
    for _ in range(warmups):
        callback()
    observations: list[float] = []
    for _ in range(samples):
        started = clock()
        callback()
        elapsed = max(0.0, clock() - started)
        observations.append(elapsed)
    return {"samples_seconds": observations, "summary": summarize_samples(observations)}


__all__ = [
    "SLO_CONFIG_PATH",
    "SLO_SCHEMA_VERSION",
    "SLOConfigError",
    "evaluate_ratio",
    "evaluate_threshold",
    "load_slo_config",
    "measure_callable",
    "percentile",
    "summarize_samples",
    "validate_slo_config",
]
