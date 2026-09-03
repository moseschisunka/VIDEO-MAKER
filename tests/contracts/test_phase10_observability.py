"""PR-1006 structured logs, metrics, traces, and reconstruction contracts."""

from __future__ import annotations

import json
import logging
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
import yaml

from backlot import server as server_mod
from lib.events import emit_event, read_events
from lib.observability import MetricsRegistry, correlation_fields, structured_log


def test_metrics_registry_is_bounded_and_reports_latency_percentiles():
    registry = MetricsRegistry(max_samples=32)
    registry.increment("runs_total", labels={"status": "success"})
    registry.increment("runs_total", labels={"status": "success"})
    registry.observe("run_duration_seconds", 1.0, labels={"runtime": "ffmpeg"})
    registry.observe("run_duration_seconds", 3.0, labels={"runtime": "ffmpeg"})

    snapshot = registry.snapshot()

    assert snapshot["schema_version"] == "1.0"
    assert {item["name"] for item in snapshot["counters"]} == {"runs_total"}
    assert snapshot["counters"][0]["value"] == 2.0
    histogram = snapshot["histograms"][0]
    assert histogram["count"] == 2
    assert histogram["min"] == 1.0
    assert histogram["max"] == 3.0
    assert histogram["p95"] == 3.0


def test_metrics_registry_exports_deterministic_prometheus_text_without_raw_labels():
    registry = MetricsRegistry(max_samples=32)
    registry.increment("runs_total", labels={"status": 'ok\n"quoted"'})
    registry.set_gauge("queue_depth", 2)
    registry.observe("run_duration_seconds", 1.5, labels={"runtime": "ffmpeg"})

    output = registry.prometheus_text()

    assert "# TYPE runs_total counter" in output
    assert 'runs_total{status="ok\\n\\\"quoted\\\""} 1' in output
    assert "# TYPE queue_depth gauge" in output
    assert "# TYPE run_duration_seconds summary" in output
    assert 'run_duration_seconds{quantile="0.5",runtime="ffmpeg"} 1.5' in output
    assert "run_duration_seconds_count{runtime=\"ffmpeg\"} 1" in output
    assert output.endswith("\n")


def test_event_stream_contains_end_to_end_correlation_without_prompt_content(tmp_path: Path):
    project = tmp_path / "lesson"
    project.mkdir()
    run_id = "run-123"
    (project / "project.json").write_text(
        json.dumps(
            {
                "project_id": "lesson",
                "pipeline_type": "screen-demo",
                "run_id": run_id,
                "attempt": 2,
                "current_stage": "compose",
            }
        ),
        encoding="utf-8",
    )
    emit_event(
        project,
        {
            "event": "finish",
            "tool": "video_compose",
            "prompt": "private learner recording about a sensitive topic",
            "success": True,
            "duration_s": 2.5,
        },
    )

    event = read_events(project)[0]

    assert event["schema_version"] == "1.0"
    assert event["project_id"] == "lesson"
    assert event["run_id"] == run_id
    assert event["pipeline_type"] == "screen-demo"
    assert event["stage"] == "compose"
    assert event["attempt"] == 2
    assert event["trace_id"] == f"lesson:{run_id}"
    assert event["event_id"] and event["span_id"]
    assert "private learner recording" not in json.dumps(event)
    assert event["prompt_sha256"]
    assert event["prompt_chars"] > 0


def test_structured_log_is_json_correlated_and_redacts_sensitive_content(caplog):
    logger = logging.getLogger("openmontage.test-observability")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        structured_log(
            logger,
            logging.INFO,
            "provider failed with Bearer test-secret",
            context=correlation_fields(
                project_id="p1",
                run_id="r1",
                pipeline_type="screen-demo",
                stage="assets",
                attempt=1,
                provider="test-provider",
            ),
            prompt="do not persist this prompt",
            error="Authorization: Bearer test-secret",
        )

    record = json.loads(caplog.records[-1].message)
    assert record["project_id"] == "p1"
    assert record["run_id"] == "r1"
    assert record["trace_id"] == "p1:r1"
    assert record["prompt_sha256"]
    assert "do not persist this prompt" not in caplog.text
    assert "test-secret" not in caplog.text


def test_correlation_fields_omit_unknown_context_without_inventing_identity():
    assert correlation_fields(project_id="p1", stage="research") == {
        "project_id": "p1",
        "stage": "research",
    }


def test_backlot_metrics_endpoint_exposes_bounded_snapshot(monkeypatch: pytest.MonkeyPatch):
    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BACKLOT_AUTH_TOKEN", raising=False)
    with TestClient(server_mod.create_app()) as client:
        response = client.get("/api/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert isinstance(body["counters"], list)
    assert isinstance(body["histograms"], list)


def test_backlot_metrics_endpoint_exposes_scrape_compatible_snapshot(monkeypatch: pytest.MonkeyPatch):
    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BACKLOT_AUTH_TOKEN", raising=False)
    with TestClient(server_mod.create_app()) as client:
        response = client.get("/api/metrics/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert response.headers["cache-control"] == "no-store"
    assert response.text.startswith("# OpenMontage bounded metrics")


def test_backlot_scrape_endpoint_inherits_remote_bearer_boundary(monkeypatch: pytest.MonkeyPatch):
    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    monkeypatch.setenv("BACKLOT_HOST", "0.0.0.0")
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "metrics-token")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    with TestClient(server_mod.create_app()) as client:
        missing = client.get("/api/metrics/prometheus")
        valid = client.get(
            "/api/metrics/prometheus",
            headers={"Authorization": "Bearer metrics-token"},
        )

    assert missing.status_code == 401
    assert "metrics-token" not in missing.text
    assert valid.status_code == 200


def test_observability_config_and_operator_doc_match_runtime_contract():
    config = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config" / "observability.yaml").read_text(
            encoding="utf-8"
        )
    )
    docs = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "operations"
        / "OBSERVABILITY.md"
    ).read_text(encoding="utf-8")

    assert "run_id" in config["event_stream"]["correlation_fields"]
    assert config["event_stream"]["sensitive_content_policy"]["prompts_and_scripts"] == "hash_and_record_length_only"
    assert config["metrics"]["endpoint"] == "/api/metrics"
    assert config["metrics"]["scrape_endpoint"] == "/api/metrics/prometheus"
    assert config["metrics"]["scrape_format"] == "prometheus_text_0.0.4"
    assert config["metrics"]["alert_contract"] == "config/alerts.yaml"
    assert "openmontage_auth_failures_total" in config["metrics"]["names"]
    assert "openmontage_auth_failures_total" in docs
    assert "Failed-run reconstruction" in docs
    assert "P0/P1 alert contract" in docs
    assert "raw provider payloads" in docs
