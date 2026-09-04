# PR-10G — Durable External Metrics Evidence (`OBS-02`)

Status: **SIMULATED INTEGRATION PASS / EXTERNAL SERVICE PENDING**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (verified in supported CI run `33870762963` on `03745d3` and `33871877480` on `fe1d73a`)
Date: 2026-09-04

## 1. Requirement Summary

`OBS-02` requires proving that runtime metrics (run success/failure, latency, retries, cost, QA outcomes, queue state, and SLO denominators) are aggregated into an approved external metrics store (Prometheus, Grafana Cloud, Datadog, etc.) and that historical time series and denominators survive application container restarts.

> [!WARNING]
> **Audit Finding (2026-09-04)**: The test harness `scripts/run_staging_operational_proofs.py` executed a local scraper process writing to a SQLite TSDB (`OpenMontage External Metrics SQLite TSDB`) against `http://127.0.0.1`. While this verified the application's `/api/metrics/prometheus` exposition, restart survivability logic, bounded cardinality, and lack of secret leakage, it executed on localhost inside CI rather than scraping into an external, durable metrics infrastructure service.

## 2. Simulated Staging Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-OBS-02` |
| `status` | `SIMULATED_PASS` (external metrics service pending) |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `simulated_sink_system` | `OpenMontage External Metrics SQLite TSDB` (localhost test double) |
| `scrape_endpoint` | `http://127.0.0.1:41953/api/metrics/prometheus` |
| `pre_restart_scrape_status` | `PASS` |
| `post_restart_scrape_status` | `PASS` |
| `samples_before_restart` | `1` |
| `samples_after_restart` | `1` |
| `slo_denominator_before` | `1.0` |
| `slo_denominator_overall` | `1.0` |
| `slo_denominator_preserved` | `True` |
| `cardinality_bounded` | `True` |
| `zero_secret_or_prompt_leaks` | `True` |

## 3. Required Real Deployment Evidence for Gate Closure

To transition `OBS-02` to `PASS`:
1. Connect Backlot to an external durable metrics store (e.g. standalone Prometheus server, Grafana Cloud Mimir, or Datadog agent).
2. Execute a container/application restart while actively ingesting metrics.
3. Retain query results proving historical time series continuity and preservation of cumulative SLO denominators across the restart boundary.
