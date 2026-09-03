"""PR-10G alert-rule and offline delivery-drill contracts."""

from __future__ import annotations

from lib.alerting import evaluate_alerts, load_alert_config, validate_alert_config


def test_alert_contract_is_versioned_and_p0_p1_only() -> None:
    config = load_alert_config()
    validate_alert_config(config)
    assert config["version"] == "1.0"
    assert {rule["severity"] for rule in config["rules"]} <= {"P0", "P1"}
    assert {rule["action"] for rule in config["rules"]} <= {"page", "notify"}


def test_alert_evaluator_pages_bounded_p0_and_p1_signals_without_payloads() -> None:
    config = load_alert_config()
    alerts = evaluate_alerts(
        config,
        events=[
            {"event": "auth_failure", "ts": 1_000.0, "prompt": "must not persist"},
            {"event": "auth_failure", "ts": 1_001.0},
            {"event": "auth_failure", "ts": 1_002.0},
            {"event": "circuit_open", "ts": 1_003.0},
        ],
        metrics_snapshot={
            "histograms": [{"name": "openmontage_queue_wait_seconds", "p95": 6.0}],
        },
        now=1_010.0,
    )
    ids = {item["rule_id"] for item in alerts}
    assert {"unauthorized-access-burst", "provider-circuit-open", "queue-start-latency"} <= ids
    assert all(item["severity"] in {"P0", "P1"} for item in alerts)
    encoded = str(alerts)
    assert "must not persist" not in encoded
