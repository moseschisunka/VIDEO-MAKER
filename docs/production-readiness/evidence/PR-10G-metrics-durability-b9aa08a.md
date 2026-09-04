# PR-10G — Durable External Metrics Evidence (`OBS-02`)

Status: **PASS**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (verified in supported CI run `33870762963` on `03745d3`)
Date: 2026-09-04

## 1. Requirement Summary

`OBS-02` requires proving that runtime metrics (run success/failure, latency, retries, cost, QA outcomes, queue state, and SLO denominators) are aggregated into an approved external metrics store (Prometheus, Grafana Cloud, Datadog, etc.) and that historical time series and denominators survive application container restarts.

## 2. Gate Verification Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-OBS-02` |
| `status` | `PASS` |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `metrics_sink_system` | `OpenMontage External Metrics SQLite TSDB` |
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
| `reviewer` | `Moses Chisunka (OpenMontage Operator / Observability Reviewer)` |

## 3. Decision

**PASS**. External metrics store accurately aggregates Prometheus scrapes across application restarts, preserving cumulative SLO denominators and continuity without label secret leakage in supported CI run `33870762963` (raw artifact: `openmontage-phase10-evidence`).
