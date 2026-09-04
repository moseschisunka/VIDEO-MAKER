# PR-10G — External Alert Delivery Evidence (`OBS-03`)

Status: **BLOCKED**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (short `b9aa08a`)
Date: 2026-09-04

## 1. Requirement Summary

`OBS-03` requires proving that operational alerts defined in `config/alerts.yaml` (covering P0/P1 symptoms such as unauthorized access bursts, provider circuit trips, duplicate provider charges, durable state loss, critical quality escapes, and queue start latency breaches) are delivered to an approved external paging/notification sink (such as PagerDuty, Opsgenie, Slack, or Alertmanager) and acknowledged, rather than only tested against in-memory or offline test harnesses.

## 2. Gate Verification Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-OBS-03` |
| `status` | `BLOCKED` |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `alert_sink_provider` | *Pending operator input (`PAGING_SINK`)* |
| `alert_sink_destination` | *Pending operator input (webhook/endpoint/service key)* |
| `fired_alert_rules` | *Pending live drill (e.g. `unauthorized-access-burst`, `provider-circuit-open`)* |
| `alert_payload_verification` | — (Expected: bounded, redacted, correlated by run/stage/attempt, zero secret leakage) |
| `trigger_timestamp` | — |
| `sink_receipt_timestamp` | — |
| `delivery_latency_seconds` | — |
| `acknowledgement_recorded` | — (Expected: true) |
| `reviewer` | *Pending named operations/on-call reviewer* |

## 3. Execution Procedure (To be executed with external notification sink)

1. Configure Backlot / monitoring pipeline with external paging sink credentials (`PAGING_SINK`).
2. Trigger a controlled, synthetic P0 or P1 event in the staging sandbox (e.g. simulated authentication failure burst or circuit breaker trip).
3. Confirm alert rule evaluator triggers the configured rule in `config/alerts.yaml`.
4. Verify external sink receives the notification payload with appropriate severity, rule ID, and sanitized diagnostic metadata.
5. Record latency from trigger emission to sink receipt.
6. Acknowledge alert through external paging interface and verify state update.
7. Archive redacted alert notification receipts and payload verification.

## 4. Current Blocker Statement

The offline operations drill passes and proves internal rule evaluation and fake-sink dispatch (`evidence/PR-10G-operations-drill.md`). However, `OBS-03` strictly mandates proving real delivery to an external paging/alerting sink. Because the external notification destination, webhook/API credentials, and designated on-call reviewer have not yet been provided by the operator, this gate remains blocked.
