# OpenMontage observability contract

OpenMontage uses the project event stream as its durable, local-first audit
source and emits optional JSON records to the standard Python logging system.
The Backlot `/api/metrics` endpoint exposes a bounded in-process snapshot for
operators and probes. No hosted telemetry service or network call is required.

## Correlation

Every event written after the observability contract is enabled has a schema
version, event id, span id, project id, and event name. When a durable run
identity is available it also carries `pipeline_type`, `run_id`, `stage`,
`attempt`, `agent_id`, `tool`/`provider`, and a stable `trace_id` in the form
`project_id:run_id`. The durable `work_order.json` and `run.json` records remain
the source of truth for lifecycle state; `events.jsonl` supplies the ordered
attempt/activity trail.

The event and provider log records deliberately contain no creative payload.
Prompts, scripts, transcripts, request/response bodies, credentials, bearer
headers, and signed URLs are either omitted, hashed with a character count, or
redacted. Use the referenced project artifact under the authenticated project
scope when an authorized reviewer needs to inspect content.

## Metrics

The current bounded registry records event counts, tool/provider success and
failure counts, tool duration, provider latency, and tool cost totals. The
remote authentication guard also increments
`openmontage_auth_failures_total` with a fixed reason label and emits a
credential-free `auth_failure` log event, so repeated unauthorized access can
be detected without retaining the presented header.
The SLO contract also reserves run lifecycle, queue wait/depth, QA outcome, and
quality-escape series. Those series are derived from the durable work order,
run record, event stream, and final-review artifacts until the external
aggregation sink is configured; they must not be fabricated from a missing
sample. The registry keeps only a finite sample window and resets on process
restart, so it is a diagnostic snapshot rather than a monthly denominator.

The SSE hub also exposes the live subscriber gauge
`openmontage_sse_subscribers` and counts dropped notifications in
`openmontage_sse_events_dropped_total`. Each subscriber queue is capped at 64
items and the browser stream coalesces bursts, so a noisy render cannot create
unbounded UI memory or task growth.

From a local Backlot process:

```text
GET /api/metrics
```

The endpoint is protected by the same remote bearer authentication and project
deployment controls as the rest of Backlot. Operators should export the JSON
snapshot to the approved monitoring sink without adding prompts, headers, or
raw provider payloads.

## P0/P1 alert contract

[`config/alerts.yaml`](../../config/alerts.yaml) is the versioned alert-rule
source. `lib.alerting.evaluate_alerts` evaluates only bounded event/metric
evidence and emits redacted records with a rule ID, severity, action, reason,
and safe evidence fields. Rules cover authentication bursts, provider circuit
opening, duplicate provider charges, durable-state loss, critical quality
escapes, and queue-start latency. Unknown rule shapes or severities fail
closed. A deployment must connect these records to the approved paging sink
and execute a delivery drill; the offline evaluator alone is not an
`OBS-03` production pass.

## Failed-run reconstruction

1. Locate the authenticated project and read `work_order.json` for the current
   stage, lease, blocker, and resume pointer.
2. Read `run.json` for the immutable run/attempt identity and artifact index.
3. Filter `events.jsonl` by `trace_id`, `run_id`, `stage`, and `attempt`.
4. Compare checkpoint status, QA, and provider/tool event transitions.
5. Preserve the event file and run record during incident handling; do not edit
   history to make a failed run appear successful.

The machine-readable source of truth is
[`config/observability.yaml`](../../config/observability.yaml).
