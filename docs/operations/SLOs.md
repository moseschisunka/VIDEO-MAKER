# OpenMontage service-level objectives

This document is the operator contract for speed, availability, recovery, and
quality. The machine-readable source is [`config/slo.yaml`](../../config/slo.yaml).
It defines release-candidate thresholds now; it does not by itself certify the
product for production. Certification still requires every applicable
acceptance row and the `PR-11G` gate.

## Reference measurement

The comparable baseline is produced on the `openmontage-ci-linux` reference:
Ubuntu 24.04, Python 3.11, Node 22.13.1, the supported apt FFmpeg/ffprobe pair,
and the documented minimum 2-vCPU/7-GB runner. Measurements use local or fake
providers only, a disposable temporary directory, a monotonic wall clock, two
warmups, and eleven reported samples. Live provider calls, credentials, network
health probes, and paid generation are excluded.

The p95 convention is the same one used by `lib.observability.MetricsRegistry`:
sort the samples and select `min(n - 1, int(n * 0.95))`. Warm and cold samples
must never be combined. Every evidence record must include the commit/ref,
environment versions, raw sample count, summary, and any skipped gate with its
owner task.

Run the deterministic SLO contract with:

```text
python -m pytest tests/contracts/test_phase10_slos.py -q
```

To emit a JSON baseline artifact (including observed OS/runtime versions), run
the same offline measurements directly:

```text
python scripts/measure_slos.py > slo-baseline.json
python scripts/measure_slos.py --output slo-baseline.json
```

The Phase 10 bounded load/soak gates use a separate disposable harness:

```text
python scripts/measure_load_soak.py --output pr1009-load-soak.json
```

It runs four isolated local work orders, same-project lease contention, fake
provider throttling, a ten-run temporary-directory cleanup soak, and repeated
bounded SSE connect/disconnect bursts. The harness is capped at four workers,
uses no network or paid provider, and removes its scratch root before exit.

The checked-in reference environment is Linux CI. A workstation result is a
diagnostic baseline and must be labelled with its observed environment; it must
not be substituted for the Linux release-candidate measurement.

The test measures the warm provider menu, cold local preflight, validation,
duplicate-run response, Backlot refresh, local render ratio, and restart/resume
detection. It never contacts a paid provider. The bounded concurrency, temporary
disk soak, and SSE stability measurements are intentionally implemented by
`PR-1009`; their thresholds are already fixed in `config/slo.yaml`.

## Release-candidate performance gates

| Gate | Indicator | Target | Measurement boundary | Owner |
|---|---|---:|---|---|
| `PERF-01` | warm `provider_menu_summary()` p95 | ≤ 2.0 s | registry already discovered; no live probes | PR-1007 |
| `PERF-02` | cold local `fast_preflight()` p95 | ≤ 5.0 s | dependency inspection only; sockets forbidden | PR-1007 |
| `PERF-03` | create-request validation p95 | ≤ 0.5 s | validation/catalog only; no disk mutation | PR-1007 |
| `PERF-04` | duplicate `/run` response p95 | ≤ 0.5 s | one live lease; no second subprocess | PR-1007 |
| `PERF-05` | active-project state refresh p95 | ≤ 0.4 s | Backlot state endpoint and temporary project | PR-1007 |
| `PERF-06` | local render wall time / output second p95 | ≤ 2.0x | one-second 320×180 FFmpeg fixture, ffprobe checked | PR-1007 |
| `PERF-07` | restart-to-resume detection p95 | ≤ 0.5 s | durable restart and read of resume pointer | PR-1007 |
| `PERF-08` | bounded concurrent runs | ≥ 4 isolated runs | no contamination or OOM | PR-1009 |
| `PERF-09` | temporary orphan growth after soak | 0 files | ten-run disposable soak | PR-1009 |
| `PERF-10` | SSE connection/task growth | 0 unbounded growth | bounded connect/disconnect soak | PR-1009 |

An offline gate is `PASS` only when its required samples exist and its target
is met. A missing sample is a measurement failure, not a zero-latency sample.
`PR-1009` must attach its own raw load/soak evidence before `PR-10G` can pass.

## Operational SLOs and error budgets

The rolling production window is 30 days and is aggregated in an external log
and metrics sink. The local `/api/metrics` endpoint is bounded and useful for a
diagnostic snapshot, but it resets on process restart and is not a durable
monthly denominator.

| SLO | Indicator | Objective | Error budget | Page condition |
|---|---|---:|---:|---|
| `SLO-AVAILABILITY` | successful health checks / total checks | ≥ 99.5% | 0.5% | <99.5% for 15 minutes or 3 consecutive failed probes |
| `SLO-QUEUE-START` | p95(queue claim start − queued time) ≤ 5 s | ≥ 99.5% of starts | 0.5% | p95 >5 s for 15 minutes |
| `SLO-FAILURE-RECOVERY` | recoveries without duplicate spend / recoverable failures | 100% | 0 | any duplicate charge or durable-state loss |
| `SLO-QUALITY-ESCAPE` | 1 − critical defects after release / released deliverables | 100% | 0 | any critical defect in canary or public release |
| `SLO-LOCAL-RENDER` | p95(render wall seconds / output seconds) | ≤2.0x | 0 | target missed in two consecutive RC runs |

For availability and queue-start, the denominator must contain only eligible
requests; planned maintenance and operator-paused or human-approval-gated work
are recorded as exclusions, never silently dropped. For quality, a critical
escape includes black/frozen video, wrong or missing voice, caption timing or
legibility failure, factual-grounding failure, wrong format/duration, and a
licensing/provenance gap. Editorial preferences are not counted as escapes.

## Measuring from runtime telemetry

1. Capture the release candidate identity and reference-environment values.
2. Run the offline contract command and retain the test output plus raw summary.
3. For a deployed instance, export structured events and the bounded metrics
   snapshot to the external sink. Correlate by `project_id`, `run_id`, `stage`,
   and `attempt`; do not use prompts, scripts, transcripts, signed URLs, or
   credentials as dimensions.
4. Calculate each indicator with the definitions above. Compare the p95 or ratio
   to the exact target in `config/slo.yaml`.
5. Subtract eligible exclusions explicitly and record them. Never turn an
   absent observation into a passing denominator.
6. Burn the error budget only for an attributable failure. Freeze expansion,
   investigate, and attach an incident reference when the page condition is
   reached. Rollback or pause the affected pipeline/provider if the quality or
   recovery budget reaches zero.

The provider menu is a capability-planning operation, not a provider SLA. A
passing warm-menu benchmark does not prove a cloud provider is reachable or
quota-ready. Live-provider smoke, human audiovisual review, backup/restore,
rollback, and canary evidence remain separate release gates.
