# PR-10G — Durable External Metrics Evidence (`OBS-02`)

Status: **BLOCKED**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (short `b9aa08a`)
Date: 2026-09-04

## 1. Requirement Summary

`OBS-02` requires proving that runtime metrics (run success/failure, latency, retries, cost, QA outcomes, queue state, and SLO denominators) are aggregated into an approved external metrics store (Prometheus, Grafana Cloud, Datadog, etc.) and that historical time series and denominators survive application container restarts.

## 2. Gate Verification Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-OBS-02` |
| `status` | `BLOCKED` |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `metrics_sink_system` | *Pending operator input (`METRICS_SINK`)* |
| `scrape_endpoint` | `/api/metrics/prometheus` (behind authenticated edge) |
| `retention_policy` | *Pending operator input* |
| `pre_restart_scrape_hash` | — |
| `post_restart_scrape_hash` | — |
| `slo_denominator_preserved` | — (Expected: true; denominator not reset to 0) |
| `cardinality_check` | — (Expected: bounded label set, no prompt/token leakage) |
| `reviewer` | *Pending named observability/operations reviewer* |

## 3. Execution Procedure

1. Configure external scraper targeting the deployed Backlot `/api/metrics/prometheus` endpoint.
2. Execute a controlled synthesis run in the staging sandbox.
3. Record external query metrics covering run latency, provider attempts, and SLO denominators.
4. Restart the Backlot application container while leaving the external metrics store running.
5. Query the external metrics store post-restart.
6. Verify historical series continuity: timestamps remain contiguous, labels are valid, and cumulative request denominators have not been wiped.
7. Retain redacted query results and configuration fingerprint.

## 4. Current Blocker Statement

The application exposes scrape-ready Prometheus metrics at `/api/metrics/prometheus` which pass internal contract verification. However, proving durability across service restarts requires connecting to an external metrics sink, which has not yet been provided by the operator.
