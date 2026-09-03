"""Fail-closed operational alert rules and deterministic evaluation.

The evaluator deliberately has no network or notification dependency. It turns
durable event/metric evidence into bounded, redacted alert records; a runtime
adapter can deliver those records to the approved paging sink. Unknown rule
shapes and unsupported severities fail closed instead of silently disabling a
critical alert.
"""

from __future__ import annotations

import math
import operator
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml


ALERT_SCHEMA_VERSION = "1.0"
ALERT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "alerts.yaml"
_SEVERITIES = {"P0", "P1"}
_ACTIONS = {"page", "notify"}
_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


class AlertConfigError(ValueError):
    """Raised when the checked-in alert contract is malformed."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AlertConfigError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise AlertConfigError(f"{field} must be finite")
    return number


def load_alert_config(path: Path | str | None = None) -> dict[str, Any]:
    config_path = Path(path) if path is not None else ALERT_CONFIG_PATH
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AlertConfigError(f"alert configuration cannot be read: {config_path}") from exc
    validate_alert_config(payload)
    return dict(payload)


def validate_alert_config(config: Mapping[str, Any]) -> None:
    if not isinstance(config, Mapping):
        raise AlertConfigError("alert configuration must be an object")
    if str(config.get("version") or "") != ALERT_SCHEMA_VERSION:
        raise AlertConfigError(f"alert configuration version must be {ALERT_SCHEMA_VERSION!r}")
    rules = config.get("rules")
    if not isinstance(rules, list) or not rules:
        raise AlertConfigError("alert configuration must contain rules")
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        prefix = f"rules[{index}]"
        if not isinstance(rule, Mapping):
            raise AlertConfigError(f"{prefix} must be an object")
        rule_id = str(rule.get("id") or "").strip()
        if not rule_id or rule_id in seen:
            raise AlertConfigError(f"{prefix}.id is missing or duplicated")
        seen.add(rule_id)
        severity = str(rule.get("severity") or "").strip().upper()
        if severity not in _SEVERITIES:
            raise AlertConfigError(f"{prefix}.severity must be P0 or P1")
        action = str(rule.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            raise AlertConfigError(f"{prefix}.action must be page or notify")
        trigger = rule.get("trigger")
        if not isinstance(trigger, Mapping):
            raise AlertConfigError(f"{prefix}.trigger must be an object")
        trigger_type = str(trigger.get("type") or "").strip().lower()
        if trigger_type == "event":
            if not str(trigger.get("name") or "").strip():
                raise AlertConfigError(f"{prefix}.trigger.name is required")
            count = trigger.get("count_at_least", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise AlertConfigError(f"{prefix}.trigger.count_at_least must be positive")
            window = _finite_number(trigger.get("window_seconds", 300), f"{prefix}.trigger.window_seconds")
            if window <= 0:
                raise AlertConfigError(f"{prefix}.trigger.window_seconds must be positive")
        elif trigger_type == "metric":
            if not str(trigger.get("name") or "").strip():
                raise AlertConfigError(f"{prefix}.trigger.name is required")
            field = str(trigger.get("field") or "value").strip()
            if field not in {"value", "p50", "p95", "min", "max", "avg"}:
                raise AlertConfigError(f"{prefix}.trigger.field is unsupported")
            if str(trigger.get("operator") or "") not in _OPERATORS:
                raise AlertConfigError(f"{prefix}.trigger.operator is unsupported")
            _finite_number(trigger.get("threshold"), f"{prefix}.trigger.threshold")
        else:
            raise AlertConfigError(f"{prefix}.trigger.type must be event or metric")


def _event_timestamp(event: Mapping[str, Any]) -> float | None:
    raw = event.get("ts")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(float(raw)) else None
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None
    return None


def _event_matches(events: Iterable[Mapping[str, Any]], trigger: Mapping[str, Any], now: float) -> tuple[int, int]:
    name = str(trigger["name"])
    window = float(trigger.get("window_seconds", 300))
    matched = 0
    considered = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        timestamp = _event_timestamp(event)
        if timestamp is not None and (timestamp < now - window or timestamp > now + 1):
            continue
        considered += 1
        if str(event.get("event") or event.get("name") or "") == name:
            matched += 1
    return matched, considered


def _metric_values(snapshot: Mapping[str, Any], name: str, field: str) -> list[float]:
    values: list[float] = []
    for collection_name in ("counters", "gauges", "histograms"):
        collection = snapshot.get(collection_name, [])
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, Mapping) or str(item.get("name") or "") != name:
                continue
            raw = item.get(field)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            value = float(raw)
            if math.isfinite(value):
                values.append(value)
    return values


def evaluate_alerts(
    config: Mapping[str, Any] | None = None,
    *,
    events: Iterable[Mapping[str, Any]] = (),
    metrics_snapshot: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return active, redacted alert records from one bounded evidence window."""

    active_config = config if config is not None else load_alert_config()
    validate_alert_config(active_config)
    current = time.time() if now is None else _finite_number(now, "now")
    event_values = list(events)
    snapshot = metrics_snapshot or {}
    alerts: list[dict[str, Any]] = []
    for rule in active_config["rules"]:
        trigger = rule["trigger"]
        trigger_type = str(trigger["type"]).lower()
        observed: float
        reason: str
        if trigger_type == "event":
            matched, considered = _event_matches(event_values, trigger, current)
            threshold = int(trigger.get("count_at_least", 1))
            if matched < threshold:
                continue
            observed = float(matched)
            reason = f"event {trigger['name']} observed {matched} time(s) in the configured window"
            evidence = {"matched_events": matched, "considered_events": considered}
        else:
            field = str(trigger.get("field") or "value")
            values = _metric_values(snapshot, str(trigger["name"]), field)
            if not values:
                continue
            observed = max(values)
            threshold = float(trigger["threshold"])
            if not _OPERATORS[str(trigger["operator"])](observed, threshold):
                continue
            reason = f"metric {trigger['name']} {field}={observed:g} {trigger['operator']} {threshold:g}"
            evidence = {"metric_values": values, "field": field}
        alerts.append({
            "schema_version": ALERT_SCHEMA_VERSION,
            "rule_id": str(rule["id"]),
            "severity": str(rule["severity"]).upper(),
            "action": str(rule["action"]).lower(),
            "reason": reason,
            "observed": observed,
            "evidence": evidence,
        })
    return alerts


__all__ = [
    "ALERT_CONFIG_PATH",
    "ALERT_SCHEMA_VERSION",
    "AlertConfigError",
    "evaluate_alerts",
    "load_alert_config",
    "validate_alert_config",
]
