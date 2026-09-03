# PR-10G — Phase 10 operational gate audit

Status: **BLOCKED — required external/RC proof remains outstanding**

This is an audit record, not a production approval. The release lock remains
`PR-11G`.

## Evidence completed

| Area | Result | Evidence |
|---|---|---|
| Offline release-blocker contracts | PASS (supported CI) | Latest supported run `33740027654` on `7646d46` — `.venv/bin/python -m pytest tests/contracts -m "release_blocker and not live_provider and not hyperframes_qa" -q` → **1058 passed, 5 skipped, 1 deselected, 1 warning**; Phase 10 raw artifact [`openmontage-phase10-evidence` (9887525457)](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33740027654/artifacts/9887525457); earlier runs remain historical evidence |
| Phase 10 targeted contracts | PASS | `python -m pytest --basetemp=tmp/pytest-phase10 -q` across all Phase 10 contract modules (clean install, static/container render, auth/security, privacy, observability, alerting, backup/restore, load/soak, operations drills, SLOs, dependencies, package data, and runbooks) → **70 passed, 2 warnings in 107.86s** on the current Windows diagnostic run; supported CI remains authoritative |
| UTF-8 authored text and wizard integrity | PASS (local Windows, live UI, and supported CI) | `tests/contracts/test_phase10_text_encoding.py` → **7 passed** in the current checkpoint; manifests, playbooks, and runtime config load explicit UTF-8, title-only briefs are rejected, wizard catalogs fail closed, run-start failures are visible, and dialog/labels/keyboard controls are verified |
| Library work-order state and progress precision | PASS (supported CI, local source/test, and read-only browser) | [`PR-1013.md`](PR-1013.md) — commit `ab01bcb` removes the hard-coded six-stage denominator, reports a queued handoff as `QUEUED · AGENT HANDOFF` at `0% Completed`, and derives rendered progress from actual checkpoint rails; supported run `33736220396` includes the focused **10-test** catalog/state set |
| Library aggregate/filter completion precision | PASS (supported CI, local source/test, and read-only browser) | [`PR-1014.md`](PR-1014.md) — commit `58439f9` removes the hard-coded five-stage threshold, preserves the five observed rendered outputs, and uses the manifest-aware completion predicate for metrics/filtering; supported run `33736220396` passes |
| Supported Phase 10 SLO/load checkpoint | PASS (supported CI) | Run `33740027654` on checkpoint `7646d46` — all `PERF-01`–`PERF-10`, provider-throttle, and queue checks pass; raw artifact [`openmontage-phase10-evidence` (9887525457)](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33740027654/artifacts/9887525457) |
| Backup/restore/migration | PASS | [`PR-1008.md`](PR-1008.md) |
| Operator runbooks | PASS (documentation contract) | [`PR-1010.md`](PR-1010.md) |
| Bounded load/soak | PASS (supported CI) | [`PR-1009.md`](PR-1009.md), [`PR-10G-load-soak-linux-ci.json`](PR-10G-load-soak-linux-ci.json), and latest supported run `33736220396` |
| Offline operational drills | PASS (supported CI; fake/no-network/no-spend) | [`PR-10G-operations-drill.md`](PR-10G-operations-drill.md), [`PR-10G-operations-drill-ci.json`](PR-10G-operations-drill-ci.json), and latest supported run `33736220396` |
| Alert rules and fake-sink drill | PASS (external delivery pending) | [`PR-10G-operations-drill.md`](PR-10G-operations-drill.md), [`PR-10G-operations-drill-ci.json`](PR-10G-operations-drill-ci.json), and [`config/alerts.yaml`](../../../config/alerts.yaml) |
| Current Windows SLO rerun | PASS (diagnostic only) | [`PR-10G-slo-windows-after-fast.json`](PR-10G-slo-windows-after-fast.json) — all measured gates pass after bounded FFmpeg preset tuning |
| HyperFrames CLI/browser QA | PASS (latest supported clean Ubuntu certification; frozen-RC proof pending) | [`PR-10G-hyperframes-offline-qa.md`](PR-10G-hyperframes-offline-qa.md) — latest run `33724899296` on `ac4fd9b` executes scaffold/lint/validate/inspect plus the real render (**2 passed, 1 skipped, 1 warning in 72.92s**); raw log retained as [artifact `openmontage-hyperframes-qa`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33724899296/artifacts/9881651965) |
| Shared CLI timeout boundary | PASS (local Windows and supported CI verification) | Commits `3f37200` and `671a8dc` bound process-tree cleanup for HyperFrames and every `BaseTool.run_command()` consumer; `VideoCompose.get_info()` returns in 5.447s when npm is unreachable, and the normal supported push run `33717170584` is green |
| Package data and clean Python smoke | PASS (supported CI) | [`PR-1001.md`](PR-1001.md), [`PR-1002.md`](PR-1002.md), and latest supported run `33736220396` — clean checkout package-data contract and PR-1002 smoke pass |
| Disposable clean Remotion install/build | PASS (supported CI) | [`PR-10G-remotion-clean-build-ci.json`](PR-10G-remotion-clean-build-ci.json) and [`PR-10G-remotion-compositions-ci.txt`](PR-10G-remotion-compositions-ci.txt) — lockfile install, browser ensure, TypeScript, bundle, and 13-composition enumeration pass in the latest supported run `33736220396` (artifact `9885902131`) |
| Remotion dependency vulnerability audit | PASS (supported CI) | `npm audit --audit-level=high` reports **0 vulnerabilities** in the current lock (browserslist 4.28.8, fast-uri 4.1.4, postcss 8.5.26); clean-install job in supported run `33736220396` passed the audit gate, which fails on any future high/critical advisory |
| Remotion default-props/container smoke | PASS (supported CI) | [`PR-10G-container-render-ci.json`](PR-10G-container-render-ci.json) and [`PR-10G-remotion-defaults.md`](PR-10G-remotion-defaults.md) — six asset-free/default-preview compositions render non-empty stills in the hardened image; latest container job in run `33736220396` passed (artifact `9885989279`) |
| Web security boundary | PARTIAL (policy/test pass; deployed edge pending) | [`config/security_policy.yaml`](../../../config/security_policy.yaml) and PR-1004 security contracts — same-origin/no-cookie bearer posture is explicit; reverse-proxy CORS/CSRF/rate-limit enforcement still needs a deployment drill |
| Security/auth/path/redaction contracts | PASS | [`PR-1004.md`](PR-1004.md), [`PR-1005.md`](PR-1005.md) |
| Full repository regression suite | PASS (supported offline CI) | Supported offline regression in run `33740027654` → **1516 passed, 6 skipped, 3 deselected, 1 warning, 1 subtest passed**; the default Windows temp-root run is diagnostic-only because the host denies pytest's per-user temp directory |
| Python dependency vulnerability audit | PASS (local) | `pip-audit -r requirements.txt` and `pip-audit -r requirements-dev.txt` both report **No known vulnerabilities found**; the local `openmontage` package is skipped because it is not published to PyPI |

## Supported CI feedback (2026-09-03)

The first run of the newly published checkpoint (`33708888800`) exposed
workflow defects before the supported gates could be trusted: jobs invoked the
system Python after installing into `.venv`, the container health step checked
too early, and the container cleanup trap deleted the service before the
in-image render step. Those issues were corrected in `ec51ea2`; its rerun
(`33709283910`) then exposed the independent missing `edge-tts` runtime
dependency during collection. `edge-tts>=7.2.0` is now declared in
`pyproject.toml` and covered by the dependency contract. A fresh CI run with
these corrections is required before any supported-environment row can be
promoted to PASS.

The full-suite run initially exposed a stale QA fixture that attempted to
complete manifest-gated proposal/script/scene/asset/publish stages with only
the deprecated `human_approved` boolean. The fixture now persists a pending
checkpoint, binds an immutable approval record to the exact artifact digest
and timestamp, and then completes the stage. This keeps the test representative
of the production gate; it does not weaken gate enforcement.

The same pass also removed the deprecated FastAPI `on_event` watcher
lifecycle. The remaining single warning is emitted by the installed
Starlette/httpx test-client compatibility layer and is environment-level
maintenance, not a production runtime failure.

The container cache contract was also checked against the installed Remotion
4.0.484 implementation: its browser download directory resolves to the
project-local `remotion-composer/node_modules/.remotion`, so the
`/home/node/.cache` tmpfs used for transient profiles does not hide the baked
browser. The static PR-1003 contract remains green (**7 passed**); this does
not replace the required in-image Docker build/render evidence.

The next supported run (`33709698659`) reached all the way through image
build, authenticated health, and browser bootstrap, then exposed three
independent release defects: the image lacked Chromium's Debian runtime
libraries (`libnspr4.so` was the first loader failure), the package-data test
mistook ignored user footage for a required release asset, and the black-video
fault report omitted the corpus's canonical `decoded black` signal when only
`blackdetect` found an interval. The Dockerfile now carries the complete
headless-browser dependency set; wheel mappings now ship only tracked curated
fixtures; and interval diagnostics now report decoded black/blank ranges. A
fresh supported CI run was required to validate those corrections; its result
is recorded below.

That fresh supported run is `33710765514` (commit `f6d8612`). It passed the
clean-install, release-blocker, offline-regression, container/browser, and
Phase 10 SLO/load/operations jobs. The supported raw evidence is retained in
[`PR-10G-slo-linux-ci.json`](PR-10G-slo-linux-ci.json),
[`PR-10G-container-render-ci.json`](PR-10G-container-render-ci.json),
[`PR-10G-remotion-clean-build-ci.json`](PR-10G-remotion-clean-build-ci.json),
and [`PR-10G-remotion-compositions-ci.txt`](PR-10G-remotion-compositions-ci.txt).
The immutable workflow record is [GitHub Actions run
33710765514](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33710765514).

The shared runtime-hardening checkpoint is commit `671a8dc`, followed by the
CI evidence-retention patch `2bf54ae`. Supported push run `33717170584` passed
clean install, release blockers, offline regression, container/browser
rendering, and Phase 10 SLO/load/operations. The explicitly dispatched
HyperFrames certification on `671a8dc` (run `33718138362`) completed the real
render path with **2 passed**; the evidence-retention rerun on `2bf54ae`
(`33718631193`) also completed **2 passed, 1 skipped**, uploaded the raw log,
and passed every other supported job. The earlier `33715260110` result remains
partial historical evidence because it omitted the render opt-in.

The current dependency/performance checkpoint is commit `3d368da` and
supported push run `33722648046`. All five required non-opt-in jobs completed
successfully: clean install (including `npm audit --audit-level=high`, which
reported **0 vulnerabilities**), release-blocker contracts, offline regression,
container/browser rendering, and Phase 10 SLO/load/operations evidence. The
two opt-in jobs (live-provider and HyperFrames) were intentionally skipped on
the push workflow, so this run validates the checkpoint but is not the frozen
release-candidate certification.

The follow-up workflow-dispatch run `33724899296` on commit `ac4fd9b` enabled
the HyperFrames opt-in. Its real render passed (**2 passed, 1 skipped, 1
warning in 72.92s**), while the release-blocker, clean-install, offline
regression, container/browser, and Phase 10 SLO/load/operations jobs also
passed. The live-provider opt-in remained intentionally skipped; frozen-RC and
external operational proof are still outstanding.

The subsequent supported push run `33726592541` on commit `6d23cf8` passed all
five required non-opt-in jobs: clean install (including the zero-vulnerability
npm audit), release-blocker contracts, offline regression, the hardened
container/browser matrix, and Phase 10 SLO/load/operations evidence. It also
executed the new release-blocking UTF-8 text-integrity contracts. Live-provider
and HyperFrames opt-ins were intentionally skipped on this push workflow; this
run validates the loader fix but is not frozen release-candidate certification.

The follow-up supported push run `33727762122` on commit `2562b67` also passed
all five required non-opt-in jobs. It includes the wizard guard and accessible
keyboard-selection changes, with the release-blocking UI contract suite green;
the live-provider and HyperFrames opt-ins remained intentionally skipped.

The latest supported push run `33736220396` on documentation checkpoint `0f3421d`
(including source checkpoint `58439f9`) passed all five
required non-opt-in jobs: clean install (including the zero-vulnerability npm
audit), **1057** release-blocker contracts, **1515** offline regression tests,
the hardened container/browser matrix, and Phase 10 SLO/load/operations
evidence. It also includes the catalog/launch reliability corrections: the
wizard consumes authoritative API catalogs, filters playbooks to the selected
pipeline contract, fails closed when options are unavailable, enforces input
limits, and surfaces a failed automatic run instead of claiming success. The
live-provider and HyperFrames opt-ins were intentionally skipped; this is a
supported checkpoint, not frozen release-candidate or production evidence.
The earlier Phase 10 raw evidence artifact is retained at [openmontage-phase10-evidence](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33730564062/artifacts/9883844320).
The current raw Phase 10 evidence artifact is [openmontage-phase10-evidence](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33736220396/artifacts/9886009100).

The `ab01bcb` and `58439f9` library checkpoints are included in this supported
run: queued work-order handoffs no longer appear to be rendering, card progress
uses each project's actual checkpoint rail, and aggregate/filter completion
uses the same state predicate. The read-only browser rerun and focused
**10-test** contract set pass. The SLO/load job was rerun after one transient
`PERF-07` p95 miss; the unchanged target and implementation passed on rerun.

The subsequent supported push run `33740027654` on commit `7646d46` verifies
the `PR-1015` project-relative caption final-review fix. Its release-blocker
job passed **1058** tests and its offline regression job passed **1516** tests;
clean install, container/browser, and Phase 10 SLO/load/operations also passed.
Live-provider and HyperFrames opt-ins were intentionally skipped. This
checkpoint satisfies the `PR-1015` integration prerequisite but is not a
frozen release-candidate or production certification.

The unpublished local follow-ups `PR-1016`, `PR-1017`, `PR-1018`, `PR-1019`,
`PR-1020`, and `PR-1021` are intentionally not represented as supported-CI
evidence. `PR-1016` adds the
source-footage talking-head manifest executor lane; `PR-1017` rejects
zero-duration or mismatched-stream media at ingestion; and `PR-1018` routes the
canonical Backlot screenshot/simulation fixtures through UUID-bound immutable
approval records and removes stale initializer arguments from the quarantined
internal demo runner. `PR-1019` adds a scrape-compatible Prometheus endpoint,
`PR-1020` rejects malformed voice-sample approvals, and `PR-1021` rejects
malformed provider spend approvals at request, bridge, and dry-run plan
boundaries. They have local regression evidence, but the supported workflow
must rerun from the published checkpoint before the corresponding tasks can
become `VERIFIED`; because PR-1017 covers a Phase 6 acceptance boundary, the
Phase 6 gate and `SEC-04` remain reopened/partial until that supported
verification is attached. `OPS-06` also remains partial until the corrected
fixture path is exercised in supported CI.

## Blocking proof

1. `REC-03` still requires an executed deployment/rollback drill on the
   approved deployment target. The local/fake recovery drill and supported
   container test do not prove rollback of a deployed service.
2. `OBS-03` still requires external alert delivery. The fake sink proves rule
   evaluation only; no paging/notification sink has been exercised.
3. `SEC-06` still requires a supported deployment to demonstrate same-origin
   CORS, bearer-only CSRF behavior, and distributed `429` request limiting at
   the trusted edge.
4. `OBS-02` still requires external metrics/log aggregation and durable SLO
   denominators. The service now exposes a scrape-compatible
   `/api/metrics/prometheus` endpoint (see [`PR-1019`](PR-1019.md)), but the
   in-process snapshot still resets on restart and no approved sink has been
   exercised.
5. HyperFrames' clean supported certification now passes, but the frozen
   release-candidate rerun must still repeat the complete matrix before
   `PR-10G` can close.

## Decision

`PR-10G` remains **BLOCKED**. Do not freeze a release candidate, start Phase 11
certification, or label any pipeline production-ready until the blockers above
are closed and the Phase 10 gate is rerun from the supported environment.
