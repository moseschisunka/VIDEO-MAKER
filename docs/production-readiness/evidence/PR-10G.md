# PR-10G — Phase 10 operational gate audit

Status: **BLOCKED**

This audit record tracks Phase 10 operational gate certification. Supported CI validates the repository-owned
offline, packaging, container, and SLO checks, as well as the simulated multi-service staging integration harness.
However, Phase 10 remains blocked on four real environment-owned operational proofs:
deployed rollback (`REC-03`), deployed trusted-edge enforcement (`SEC-06`), real external alert paging (`OBS-03`),
and real external durable metrics aggregation (`OBS-02`).

## Evidence status

| Area | Result | Evidence |
|---|---|---|
| Offline release-blocker contracts | PASS (supported CI) | Supported run `33871877480` on `fe1d73a` — `.venv/bin/python -m pytest tests/contracts -m "release_blocker and not live_provider and not hyperframes_qa" -q` → **1,309 passed, 5 skipped, 1 deselected, 1 warning** in 175s; Phase 10 raw artifact [`openmontage-phase10-evidence`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33871877480) |
| Phase 10 targeted contracts | PASS | `python -m pytest --basetemp=tmp/pytest-phase10 -q` across all Phase 10 contract modules (clean install, static/container render, auth/security, privacy, observability, alerting, backup/restore, load/soak, operations drills, SLOs, dependencies, package data, runbooks, staging operational proofs) → **74 passed**; supported CI remains authoritative |
| UTF-8 authored text and wizard integrity | PASS (local Windows, live UI, and supported CI) | `tests/contracts/test_phase10_text_encoding.py` → **7 passed** in the current checkpoint; manifests, playbooks, and runtime config load explicit UTF-8, title-only briefs are rejected, wizard catalogs fail closed, run-start failures are visible, and dialog/labels/keyboard controls are verified |
| Library work-order state and progress precision | PASS (supported CI, local source/test, and read-only browser) | [`PR-1013.md`](PR-1013.md) — commit `ab01bcb` removes the hard-coded six-stage denominator, reports a queued handoff as `QUEUED · AGENT HANDOFF` at `0% Completed`, and derives rendered progress from actual checkpoint rails; supported run `33736220396` includes the focused **10-test** catalog/state set |
| Library aggregate/filter completion precision | PASS (supported CI, local source/test, and read-only browser) | [`PR-1014.md`](PR-1014.md) — commit `58439f9` removes the hard-coded five-stage threshold, preserves the five observed rendered outputs, and uses the manifest-aware completion predicate for metrics/filtering; supported run `33736220396` passes |
| Supported Phase 10 SLO/load checkpoint | PASS (supported CI) | Run `33871877480` on checkpoint `fe1d73a` — all `PERF-01`–`PERF-10`, provider-throttle, and queue checks pass; raw artifact [`openmontage-phase10-evidence`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33871877480) |
| Backup/restore/migration | PASS | [`PR-1008.md`](PR-1008.md) |
| Operator runbooks | PASS (documentation contract) | [`PR-1010.md`](PR-1010.md) |
| Bounded load/soak | PASS (supported CI) | [`PR-1009.md`](PR-1009.md), [`PR-10G-load-soak-linux-ci.json`](PR-10G-load-soak-linux-ci.json), and supported run `33871877480` |
| Offline operational drills | PASS (supported CI; fake/no-network/no-spend) | [`PR-10G-operations-drill.md`](PR-10G-operations-drill.md), [`PR-10G-operations-drill-ci.json`](PR-10G-operations-drill-ci.json), and supported run `33871877480` |
| Alert rules and external sink delivery (`OBS-03`) | PARTIAL (simulated harness in CI) | [`PR-10G-alert-delivery-b9aa08a.md`](PR-10G-alert-delivery-b9aa08a.md) — verified synthetic alert generation and delivery to local mock sink; delivery to a real external paging sink (e.g. PagerDuty / Opsgenie) remains required |
| Durable external metrics across restart (`OBS-02`) | PARTIAL (simulated harness in CI) | [`PR-10G-metrics-durability-b9aa08a.md`](PR-10G-metrics-durability-b9aa08a.md) — verified Prometheus scrape persistence in temporary SQLite TSDB; proof against an external durable metrics service remains required |
| Deployment rollback with state integrity (`REC-03`) | NOT RUN (local directory simulation in CI) | [`PR-10G-rollback-b9aa08a.md`](PR-10G-rollback-b9aa08a.md) — local file swapping passed; timed rollback between two real immutable image digests in a deployed target environment remains required |
| Current Windows SLO rerun | PASS (diagnostic only) | [`PR-10G-slo-windows-after-fast.json`](PR-10G-slo-windows-after-fast.json) — all measured gates pass after bounded FFmpeg preset tuning |
| Corrective FFmpeg compose rerun | PASS (local Windows diagnostic) | Profile dimensions and frame rate applied during segment normalization, avoiding redundant re-encode; `test_perf_06_local_render_and_perf_07_restart_resume` plus adjacent tests pass (**20 passed**) |
| HyperFrames CLI/browser QA | PASS (latest supported clean Ubuntu certification; frozen-RC proof pending) | [`PR-10G-hyperframes-offline-qa.md`](PR-10G-hyperframes-offline-qa.md) — supported run `33810441833` on `beec14f` executes scaffold/lint/validate/inspect plus real render in 2m43s; raw log retained as [artifact `openmontage-hyperframes-qa`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33810441833/artifacts/openmontage-hyperframes-qa) |
| Shared CLI timeout boundary | PASS (local Windows and supported CI verification) | Commits `3f37200` and `671a8dc` bound process-tree cleanup for HyperFrames and every `BaseTool.run_command()` consumer; `VideoCompose.get_info()` returns in 5.447s when npm is unreachable, and push run `33717170584` is green |
| Package data and clean Python smoke | PASS (supported CI) | [`PR-1001.md`](PR-1001.md), [`PR-1002.md`](PR-1002.md), and latest supported run `33871877480` — clean checkout package-data contract and PR-1002 smoke pass |
| Disposable clean Remotion install/build | PASS (supported CI) | [`PR-10G-remotion-clean-build-ci.json`](PR-10G-remotion-clean-build-ci.json) and [`PR-10G-remotion-compositions-ci.txt`](PR-10G-remotion-compositions-ci.txt) — lockfile install, browser ensure, TypeScript, bundle, and 13-composition enumeration pass in supported run `33871877480` |
| Remotion dependency vulnerability audit | PASS (supported CI) | `npm audit --audit-level=high` reports **0 vulnerabilities** in the current lock; clean-install job in supported run `33871877480` passed the audit gate |
| Remotion default-props/container smoke | PASS (supported CI) | [`PR-10G-container-render-ci.json`](PR-10G-container-render-ci.json) and [`PR-10G-remotion-defaults.md`](PR-10G-remotion-defaults.md) — six asset-free/default-preview compositions render non-empty stills in the hardened image; container job in run `33871877480` passed |
| Web security and trusted-edge boundary (`SEC-06`) | PARTIAL (simulated harness in CI) | [`PR-10G-trusted-edge-b9aa08a.md`](PR-10G-trusted-edge-b9aa08a.md) — local reverse proxy double verified origin cloaking, health 200, bearer 401, strict CORS, and 429 burst rate limiting; proof against a deployed production trusted edge remains required |
| Security/auth/path/redaction contracts | PASS | [`PR-1004.md`](PR-1004.md), [`PR-1005.md`](PR-1005.md) |
| Full repository regression suite | PASS (supported offline CI) | Supported offline regression in run `33871877480` on `fe1d73a` → **1,768 passed, 6 skipped, 3 deselected, 1 warning, 1 subtest passed** in 192s |
| Python dependency vulnerability audit | PASS (local) | `pip-audit -r requirements.txt` and `pip-audit -r requirements-dev.txt` both report **No known vulnerabilities found**; the local `openmontage` package is skipped because it is not published to PyPI |

## Supported CI feedback (2026-09-04)

Supported CI runs [`33870762963`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33870762963) and [`33871877480`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33871877480) verify:
- **Release blockers (offline contracts)**: **1,309 passed**, 5 skipped, 1 deselected, 1 warning in 175s.
- **Offline regression suite**: **1,768 passed**, 6 skipped, 3 deselected, 1 warning, 1 subtest passed in 192s.
- **Clean-install smoke (PR-1002)**: **PASSED** (1m14s).
- **Container build and health contract (PR-1003)**: **PASSED** (2m18s; non-root user, healthcheck, still render).
- **Phase 10 SLO and load evidence**: **PASSED** (2m44s; Linux SLO baseline, load/soak, operations drills, and staging proof harness).
- **Artifacts retained**: `openmontage-phase10-evidence`, `openmontage-container-render`, `openmontage-remotion-compositions`.

## Simulated staging integration evidence vs. Environment-owned proofs remaining

The staging verification harness (`scripts/run_staging_operational_proofs.py`) and mock services (`tools/staging/`) execute as a **simulated localhost integration test harness**:
- Services bind to `127.0.0.1` inside one Python process.
- `run_staging_operational_proofs.py` creates the proxy and alert receiver locally in a temporary directory.
- `staging_alert_sink.py` explicitly acts as a test double simulating PagerDuty/Alertmanager.
- The rollback digests (`sha256:baseline-b9aa08a-staging` and `sha256:candidate-2791f1a-staging`) are simulated local file state markers, not registry-generated immutable image digests.

These tests prove strong integration plumbing, but **cannot be credited as real environment-owned production evidence**:

1. **`REC-03`**: Deploy two immutable image digests to a real target environment, execute a timed rollback, and verify post-rollback state integrity.
2. **`SEC-06`**: Exercise a real deployed trusted edge (Cloudflare / Envoy / cloud load balancer) and retain CORS, bearer, and rate-limit (`429`) results.
3. **`OBS-02`**: Retain proof that an external durable metrics service (Prometheus/TSDB/Mimir/Datadog) survives application restarts and preserves cumulative SLO denominators.
4. **`OBS-03`**: Deliver synthetic P0/P1 alerts through a real external paging sink (PagerDuty / Opsgenie) and retain the redacted receipt.

## Decision

**BLOCKED.** Phase 10 Gate (`PR-10G`) is not complete. Repository-owned packaging, security, recovery, render, and SLO checks, as well as the simulated multi-service staging integration harness, have fully verified supported-CI evidence. However, the four environment-owned operational requirements listed above remain mandatory before Phase 10 can close and Phase 11 can begin.
