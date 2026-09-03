"""Structured observability primitives shared by tools, providers, and Backlot.

The repository already has a durable per-project event stream.  This module
adds a small dependency-free metrics registry and JSON log helper around that
stream so one failed run can be reconstructed by project/run/stage/attempt
without introducing a hosted telemetry dependency or leaking creative payloads.
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Mapping

from lib.secrets import redact_mapping, redact_text


OBSERVABILITY_SCHEMA_VERSION = "1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_id() -> str:
    """Create an opaque event/span identifier suitable for log correlation."""
    return uuid.uuid4().hex


def trace_id(project_id: Any, run_id: Any) -> str | None:
    """Return a stable, non-path trace id for one project/run pair."""
    if project_id in (None, "") or run_id in (None, ""):
        return None
    return f"{str(project_id)}:{str(run_id)}"


def correlation_fields(
    *,
    project_id: Any = None,
    run_id: Any = None,
    pipeline_type: Any = None,
    stage: Any = None,
    attempt: Any = None,
    agent_id: Any = None,
    tool: Any = None,
    provider: Any = None,
) -> dict[str, Any]:
    """Build a flat correlation envelope, omitting unknown values."""
    fields = {
        "project_id": project_id,
        "run_id": run_id,
        "pipeline_type": pipeline_type,
        "stage": stage,
        "attempt": attempt,
        "agent_id": agent_id,
        "tool": tool,
        "provider": provider,
    }
    result = {key: value for key, value in fields.items() if value not in (None, "")}
    current_trace = trace_id(result.get("project_id"), result.get("run_id"))
    if current_trace:
        result["trace_id"] = current_trace
    return result


_SENSITIVE_OBSERVATION_KEYS = {
    "prompt",
    "prompts",
    "text",
    "script",
    "transcript",
    "content",
    "payload",
    "request",
    "response",
    "raw_body",
    "input",
    "inputs",
}


def sanitize_observation(value: Any) -> Any:
    """Keep telemetry useful without persisting full creative/user content."""
    if isinstance(value, Mapping):
        output: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_OBSERVATION_KEYS or normalized.endswith("_prompt"):
                raw = json.dumps(item, sort_keys=True, default=str, ensure_ascii=False)
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
                output[f"{key}_sha256"] = digest
                output[f"{key}_chars"] = len(raw)
            else:
                output[key] = sanitize_observation(item)
        return output
    if isinstance(value, list):
        return [sanitize_observation(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_observation(item) for item in value)
    return value


class MetricsRegistry:
    """Bounded in-process counters, gauges, and latency observations."""

    def __init__(self, *, max_samples: int = 4096) -> None:
        self._lock = threading.RLock()
        self._max_samples = max(32, int(max_samples))
        self._counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._observations: defaultdict[
            tuple[str, tuple[tuple[str, str], ...]], deque[float]
        ] = defaultdict(lambda: deque(maxlen=self._max_samples))

    @staticmethod
    def _key(name: str, labels: Mapping[str, Any] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        normalized_name = str(name).strip()
        if not normalized_name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:" for char in normalized_name):
            raise ValueError("metric name must contain only letters, digits, underscore, or colon")
        normalized_labels = tuple(
            sorted((str(key), redact_text(value)) for key, value in (labels or {}).items())
        )
        return normalized_name, normalized_labels

    @staticmethod
    def _labels(key: tuple[str, tuple[tuple[str, str], ...]]) -> dict[str, str]:
        return dict(key[1])

    def increment(self, name: str, value: float = 1.0, *, labels: Mapping[str, Any] | None = None) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("metric increment must be numeric")
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += float(value)

    def set_gauge(self, name: str, value: float, *, labels: Mapping[str, Any] | None = None) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("metric gauge must be numeric")
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = float(value)

    def observe(self, name: str, value: float, *, labels: Mapping[str, Any] | None = None) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("metric observation must be numeric")
        key = self._key(name, labels)
        with self._lock:
            self._observations[key].append(float(value))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = [
                {"name": name, "labels": self._labels(key), "value": round(value, 6)}
                for key, value in sorted(self._counters.items(), key=lambda item: item[0])
                for name in [key[0]]
            ]
            gauges = [
                {"name": name, "labels": self._labels(key), "value": round(value, 6)}
                for key, value in sorted(self._gauges.items(), key=lambda item: item[0])
                for name in [key[0]]
            ]
            histograms = []
            for key, values in sorted(self._observations.items(), key=lambda item: item[0]):
                samples = sorted(values)
                if not samples:
                    continue
                p50 = samples[(len(samples) - 1) // 2]
                p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
                histograms.append({
                    "name": key[0],
                    "labels": self._labels(key),
                    "count": len(samples),
                    "sum": round(sum(samples), 6),
                    "min": round(samples[0], 6),
                    "max": round(samples[-1], 6),
                    "avg": round(sum(samples) / len(samples), 6),
                    "p50": round(p50, 6),
                    "p95": round(p95, 6),
                })
        return {
            "schema_version": OBSERVABILITY_SCHEMA_VERSION,
            "generated_at": _now(),
            "counters": counters,
            "gauges": gauges,
            "histograms": histograms,
        }

    @staticmethod
    def _prometheus_label_name(value: Any) -> str:
        """Return a valid, deterministic Prometheus label name."""
        name = re.sub(r"[^a-zA-Z0-9_]", "_", str(value))
        if not name:
            name = "label"
        if name[0].isdigit():
            name = f"label_{name}"
        return name

    @staticmethod
    def _prometheus_label_value(value: Any) -> str:
        """Escape a label value for the Prometheus text exposition format."""
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace('"', '\\"')
        )

    @classmethod
    def _prometheus_labels(cls, labels: Mapping[str, Any] | None = None) -> str:
        """Serialize labels without allowing malformed keys or values."""
        normalized: dict[str, str] = {}
        for key, value in sorted((labels or {}).items(), key=lambda item: str(item[0])):
            label_name = cls._prometheus_label_name(key)
            # Registry labels are normally already unique and safe.  Preserve
            # both values deterministically if a caller supplied colliding
            # names such as ``scene-id`` and ``scene_id``.
            if label_name in normalized:
                suffix = 2
                candidate = f"{label_name}_{suffix}"
                while candidate in normalized:
                    suffix += 1
                    candidate = f"{label_name}_{suffix}"
                label_name = candidate
            normalized[label_name] = cls._prometheus_label_value(value)
        if not normalized:
            return ""
        return "{" + ",".join(
            f'{key}="{value}"' for key, value in sorted(normalized.items())
        ) + "}"

    @staticmethod
    def _prometheus_number(value: Any) -> str:
        """Format finite metric values without locale-dependent output."""
        number = float(value)
        if number == float("inf"):
            return "+Inf"
        if number == float("-inf"):
            return "-Inf"
        if number != number:
            return "NaN"
        return format(number, ".12g")

    def prometheus_text(self) -> str:
        """Render the bounded snapshot as Prometheus text exposition.

        Counters and gauges preserve their metric names.  Bounded latency
        observations are exported as Prometheus summaries (quantiles plus
        ``_count``/``_sum``), which is explicit about the fact that this
        process does not retain bucket data.  The endpoint is scrape-ready;
        durability and long-window aggregation remain deployment concerns.
        """
        snapshot = self.snapshot()
        lines = [
            "# OpenMontage bounded metrics (diagnostic snapshot)",
        ]
        declared_types: set[str] = set()

        def declare(name: str, metric_type: str) -> None:
            if name not in declared_types:
                lines.append(f"# TYPE {name} {metric_type}")
                declared_types.add(name)

        for item in snapshot["counters"]:
            name = str(item["name"])
            declare(name, "counter")
            lines.append(
                f"{name}{self._prometheus_labels(item.get('labels'))} "
                f"{self._prometheus_number(item['value'])}"
            )
        for item in snapshot["gauges"]:
            name = str(item["name"])
            declare(name, "gauge")
            lines.append(
                f"{name}{self._prometheus_labels(item.get('labels'))} "
                f"{self._prometheus_number(item['value'])}"
            )
        for item in snapshot["histograms"]:
            name = str(item["name"])
            declare(name, "summary")
            labels = item.get("labels") or {}
            for quantile, field in (("0.5", "p50"), ("0.95", "p95")):
                quantile_labels = dict(labels)
                quantile_labels["quantile"] = quantile
                lines.append(
                    f"{name}{self._prometheus_labels(quantile_labels)} "
                    f"{self._prometheus_number(item[field])}"
                )
            lines.append(
                f"{name}_count{self._prometheus_labels(labels)} "
                f"{self._prometheus_number(item['count'])}"
            )
            lines.append(
                f"{name}_sum{self._prometheus_labels(labels)} "
                f"{self._prometheus_number(item['sum'])}"
            )
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._observations.clear()


metrics = MetricsRegistry()


def structured_log(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    context: Mapping[str, Any] | None = None,
    **fields: Any,
) -> None:
    """Emit one redacted JSON log record with correlation context."""
    payload: dict[str, Any] = {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "ts": _now(),
        "event_id": event_id(),
        "message": redact_text(message),
    }
    if context:
        allowed = {
            key: context.get(key)
            for key in (
                "project_id", "run_id", "pipeline_type", "stage", "attempt",
                "agent_id", "tool", "provider",
            )
        }
        payload.update(correlation_fields(**allowed))
    payload.update(fields)
    safe_payload = sanitize_observation(redact_mapping(payload))
    logger.log(level, json.dumps(safe_payload, sort_keys=True, default=str))


__all__ = [
    "MetricsRegistry",
    "OBSERVABILITY_SCHEMA_VERSION",
    "correlation_fields",
    "event_id",
    "metrics",
    "sanitize_observation",
    "structured_log",
    "trace_id",
]
