# PR-10G — External Alert Delivery Evidence (`OBS-03`)

Status: **SIMULATED INTEGRATION PASS / REAL PAGING PENDING**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (verified in supported CI run `33870762963` on `03745d3` and `33871877480` on `fe1d73a`)
Date: 2026-09-04

## 1. Requirement Summary

`OBS-03` requires proving that operational alerts defined in `config/alerts.yaml` (covering P0/P1 symptoms such as unauthorized access bursts, provider circuit trips, duplicate provider charges, durable state loss, critical quality escapes, and queue start latency breaches) are delivered to an approved external paging/notification sink (such as PagerDuty, Opsgenie, Slack, or Alertmanager) and acknowledged, rather than only tested against in-memory or offline test harnesses.

> [!WARNING]
> **Audit Finding (2026-09-04)**: The test harness `scripts/run_staging_operational_proofs.py` executed `tools/staging/staging_alert_sink.py` bound to `127.0.0.1`. The source code for `staging_alert_sink.py` explicitly notes that it simulates PagerDuty/Alertmanager. While this confirmed alert evaluation logic, P0/P1 symptom mapping, payload schemas, and operator acknowledgment flow, it was executed locally in CI rather than delivering alerts to a live external paging provider.

## 2. Simulated Staging Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-OBS-03` |
| `status` | `SIMULATED_PASS` (real paging pending) |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `simulated_sink_provider` | `OpenMontage External Paging Receiver` (localhost test double) |
| `simulated_sink_destination` | `http://127.0.0.1:50225/api/v2/alerts` |
| `fired_alert_rules` | `provider-circuit-open, unauthorized-access-burst` |
| `severities_present` | `P0, P1` |
| `delivery_latency_seconds` | `0.0018s` |
| `sink_receipt_count` | `2` |
| `acknowledgement_recorded` | `True` for automated simulation-agent only; human acknowledgement pending |
| `acknowledged_alert_id` | `c632a8ea-cd68-406f-84c9-9a416b031233` |

## 3. Required Real Deployment Evidence for Gate Closure

To transition `OBS-03` to `PASS`:
1. Configure an approved live paging or webhook sink (e.g. PagerDuty Events API v2, Opsgenie, or Alertmanager cluster).
2. Trigger an authorized synthetic P0 or P1 operational alert from a deployed OpenMontage instance.
3. Retain the external provider delivery receipt and on-call incident acknowledgment record.
