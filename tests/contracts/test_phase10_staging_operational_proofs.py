"""Contracts for staging operational proofs (REC-03, SEC-06, OBS-02, OBS-03)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from tools.staging.staging_edge_proxy import EdgeRateLimiter, StagingEdgeProxy
from tools.staging.staging_metrics_sink import StagingMetricsSink
from tools.staging.staging_alert_sink import StagingAlertSink
from scripts.run_staging_operational_proofs import (
    compute_directory_sha256,
    compute_master_hash,
)


def test_edge_rate_limiter_enforces_burst_capacity_and_refills():
    limiter = EdgeRateLimiter(burst_capacity=3, refill_rate_per_sec=10.0)
    # First 3 should succeed immediately
    assert limiter.acquire("127.0.0.1")[0] is True
    assert limiter.acquire("127.0.0.1")[0] is True
    assert limiter.acquire("127.0.0.1")[0] is True
    # 4th should be rejected
    allowed, retry_after = limiter.acquire("127.0.0.1")
    assert allowed is False
    assert retry_after > 0

    # Wait for refill
    time.sleep(0.15)
    assert limiter.acquire("127.0.0.1")[0] is True


def test_staging_metrics_sink_records_and_detects_durability(tmp_path: Path):
    db_file = tmp_path / "test_metrics.db"
    sink = StagingMetricsSink(db_path=db_file)

    sample_prom_text = (
        "# TYPE openmontage_auth_failures_total counter\n"
        'openmontage_auth_failures_total{host="0.0.0.0"} 3.0\n'
    )

    t0 = time.time()
    parsed = sink._parse_prometheus_text(sample_prom_text, t0)
    assert len(parsed) == 1
    assert parsed[0]["value"] == 3.0
    assert parsed[0]["metric_name"] == "openmontage_auth_failures_total"

    # Insert manual sample before restart
    import sqlite3
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute(
            "INSERT INTO metric_samples (scraped_at, metric_name, metric_type, labels_json, value) VALUES (?, ?, ?, ?, ?)",
            (t0 - 10, "openmontage_auth_failures_total", "counter", '{"host":"0.0.0.0"}', 2.0),
        )
        conn.execute(
            "INSERT INTO metric_samples (scraped_at, metric_name, metric_type, labels_json, value) VALUES (?, ?, ?, ?, ?)",
            (t0 + 10, "openmontage_auth_failures_total", "counter", '{"host":"0.0.0.0"}', 5.0),
        )
        conn.commit()

    durability = sink.verify_durability_across_restart("openmontage_auth_failures_total", restart_timestamp=t0)
    assert durability["status"] == "PASS"
    assert durability["samples_before_restart"] == 1
    assert durability["samples_after_restart"] == 1
    assert durability.get("slo_denominator_preserved", durability.get("denominator_preserved")) is True
    assert durability["max_value_overall"] == 5.0


def test_staging_alert_sink_records_and_acknowledges():
    sink = StagingAlertSink(host="127.0.0.1", port=0)
    actual_port = sink.start()
    assert actual_port > 0

    try:
        import requests
        base = f"http://127.0.0.1:{actual_port}"

        # Post alert
        resp = requests.post(
            f"{base}/api/v2/alerts",
            json={
                "rule_id": "unauthorized-access-burst",
                "severity": "P0",
                "action": "page",
                "reason": "simulated test",
            },
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        alert_id = data["alerts"][0]["alert_id"]

        # Acknowledge
        ack_resp = requests.post(
            f"{base}/api/v2/alerts/{alert_id}/ack",
            json={"operator": "test-operator"},
            timeout=5,
        )
        assert ack_resp.status_code == 200
        assert ack_resp.json()["status"] == "acknowledged"

        # Verify query
        list_resp = requests.get(f"{base}/api/v2/alerts", timeout=5)
        alerts = list_resp.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["acknowledged"] is True
        assert alerts[0]["acknowledged_by"] == "test-operator"
    finally:
        sink.stop()


def test_directory_sha256_is_deterministic(tmp_path: Path):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "b.txt").write_bytes(b"hello")
    (d / "a.txt").write_bytes(b"world")

    h1 = compute_directory_sha256(d)
    m1 = compute_master_hash(h1)

    h2 = compute_directory_sha256(d)
    m2 = compute_master_hash(h2)

    assert h1 == h2
    assert m1 == m2
    assert set(h1.keys()) == {"a.txt", "b.txt"}


def test_generated_simulations_cannot_claim_production_or_human_review(tmp_path, monkeypatch):
    from scripts import run_staging_operational_proofs as harness

    # The CLI runs in its own process normally; restore its module/environment
    # mutations when exercising it inside the shared pytest process.
    for name in ("BACKLOT_HOST", "BACKLOT_AUTH_REQUIRED", "BACKLOT_AUTH_TOKEN", "OPENMONTAGE_PROJECTS_DIR"):
        monkeypatch.setenv(name, "")
    for name in ("PROJECTS_DIR", "_PROJECTS_ROOT_STR", "_watch_projects"):
        monkeypatch.setattr(harness.server_mod, name, getattr(harness.server_mod, name))
    sha = "a" * 40
    historical = tmp_path / "PR-10G-rollback-b9aa08a.json"
    historical.write_text("historical record", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["harness", "--candidate-sha", sha, "--output-dir", str(tmp_path)])
    assert harness.main() == 0
    reports = list(tmp_path.glob("*-aaaaaaa.json"))
    assert len(reports) == 4
    for path in reports:
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["status"] == "SIMULATED_PASS"
        assert result["production_gate_satisfied"] is False
        assert result["evidence_kind"] == "localhost_simulation"
        assert result["reviewer"] is None
        assert result["candidate_sha"] == sha
        markdown = path.with_suffix(".md").read_text(encoding="utf-8")
        assert "PR-10G remains unproven" in markdown
        assert "Moses Chisunka" not in markdown
    assert historical.read_text(encoding="utf-8") == "historical record"
