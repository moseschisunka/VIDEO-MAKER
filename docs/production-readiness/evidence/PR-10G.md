# PR-10G — Phase 10 operational gate audit

Status: **BLOCKED — required external/RC proof remains outstanding**

This is an audit record, not a production approval. The release lock remains
`PR-11G`.

## Evidence completed

| Area | Result | Evidence |
|---|---|---|
| Offline release-blocker contracts | PASS (supported CI) | GitHub Actions runs `33710765514` and latest checkpoint `33722648046`, `.venv/bin/python -m pytest tests/contracts -m "release_blocker and not live_provider and not hyperframes_qa" -q` → **1047 passed, 5 skipped, 1 deselected, 1 warning in 135.20s** on the prior evidence run |
| Phase 10 targeted contracts | PASS | `python -m pytest tests/contracts/test_phase10_*.py -q` (all Phase 10 contract modules: clean install, static/container render, auth/security, privacy, observability, alerting, backup/restore, load/soak, operations drills, SLOs, dependencies, package data, and runbooks) → **58 passed, 1 warning in 57.63s** |
| Backup/restore/migration | PASS | [`PR-1008.md`](PR-1008.md) |
| Operator runbooks | PASS (documentation contract) | [`PR-1010.md`](PR-1010.md) |
| Bounded load/soak | PASS (supported CI) | [`PR-1009.md`](PR-1009.md), [`PR-10G-load-soak-linux-ci.json`](PR-10G-load-soak-linux-ci.json), and supported runs `33710765514` and latest checkpoint `33722648046` |
| Offline operational drills | PASS (supported CI; fake/no-network/no-spend) | [`PR-10G-operations-drill.md`](PR-10G-operations-drill.md), [`PR-10G-operations-drill-ci.json`](PR-10G-operations-drill-ci.json), and supported runs `33710765514` and latest checkpoint `33722648046` |
| Alert rules and fake-sink drill | PASS (external delivery pending) | [`PR-10G-operations-drill.md`](PR-10G-operations-drill.md), [`PR-10G-operations-drill-ci.json`](PR-10G-operations-drill-ci.json), and [`config/alerts.yaml`](../../../config/alerts.yaml) |
| Current Windows SLO rerun | PASS (diagnostic only) | [`PR-10G-slo-windows-after-fast.json`](PR-10G-slo-windows-after-fast.json) — all measured gates pass after bounded FFmpeg preset tuning |
| HyperFrames CLI/browser QA | PASS (supported clean Ubuntu certification; frozen-RC proof pending) | [`PR-10G-hyperframes-offline-qa.md`](PR-10G-hyperframes-offline-qa.md) — run `33718631193` executes scaffold/lint/validate plus the real render (**2 passed, 1 skipped, 1 warning in 88.59s**); the raw log is retained as [artifact `openmontage-hyperframes-qa`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33718631193/artifacts/9879467068) |
| Shared CLI timeout boundary | PASS (local Windows and supported CI verification) | Commits `3f37200` and `671a8dc` bound process-tree cleanup for HyperFrames and every `BaseTool.run_command()` consumer; `VideoCompose.get_info()` returns in 5.447s when npm is unreachable, and the normal supported push run `33717170584` is green |
| Package data and clean Python smoke | PASS (supported CI) | [`PR-1001.md`](PR-1001.md), [`PR-1002.md`](PR-1002.md), and runs `33710765514`/`33722648046` — clean checkout package-data contract and PR-1002 smoke pass |
| Disposable clean Remotion install/build | PASS (supported CI) | [`PR-10G-remotion-clean-build-ci.json`](PR-10G-remotion-clean-build-ci.json) and [`PR-10G-remotion-compositions-ci.txt`](PR-10G-remotion-compositions-ci.txt) — lockfile install, browser ensure, TypeScript, bundle, and 13-composition enumeration pass in runs `33710765514` and `33722648046` |
| Remotion dependency vulnerability audit | PASS (supported CI) | `npm audit --audit-level=high` reports **0 vulnerabilities** in the current lock (browserslist 4.28.8, fast-uri 4.1.4, postcss 8.5.26); clean-install run `33722648046` passed the audit gate, which fails on any future high/critical advisory |
| Remotion default-props/container smoke | PASS (supported CI) | [`PR-10G-container-render-ci.json`](PR-10G-container-render-ci.json) and [`PR-10G-remotion-defaults.md`](PR-10G-remotion-defaults.md) — six asset-free/default-preview compositions render non-empty stills in the hardened image; latest container job in run `33722648046` also passed |
| Web security boundary | PARTIAL (policy/test pass; deployed edge pending) | [`config/security_policy.yaml`](../../../config/security_policy.yaml) and PR-1004 security contracts — same-origin/no-cookie bearer posture is explicit; reverse-proxy CORS/CSRF/rate-limit enforcement still needs a deployment drill |
| Security/auth/path/redaction contracts | PASS | [`PR-1004.md`](PR-1004.md), [`PR-1005.md`](PR-1005.md) |
| Full repository regression suite | PASS (local and supported offline CI) | Local `python -m pytest -q` → **1499 passed, 10 skipped, 1 warning, 1 subtests passed**; supported offline regression in run `33710765514` → **1503 passed, 6 skipped, 3 deselected, 1 warning, 1 subtests passed in 199.15s** |
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
supported push run `33722648046`. All seven non-opt-in jobs completed
successfully: clean install (including `npm audit --audit-level=high`, which
reported **0 vulnerabilities**), release-blocker contracts, offline regression,
container/browser rendering, and Phase 10 SLO/load/operations evidence. The
push workflow intentionally skipped live-provider and HyperFrames opt-ins, so
this run validates the checkpoint but is not the frozen release-candidate
certification.

## Blocking proof

1. `REC-03` still requires an executed deployment/rollback drill on the
   approved deployment target. The local/fake recovery drill and supported
   container test do not prove rollback of a deployed service.
2. `OBS-03` still requires external alert delivery. The fake sink proves rule
   evaluation only; no paging/notification sink has been exercised.
3. `SEC-06` still requires a supported deployment to demonstrate same-origin
   CORS, bearer-only CSRF behavior, and distributed `429` request limiting at
   the trusted edge.
4. `OBS-02` still requires external metrics/log aggregation or scrape proof
   and durable SLO denominators; the in-process snapshot resets on restart.
5. HyperFrames' clean supported certification now passes, but the frozen
   release-candidate rerun must still repeat the complete matrix before
   `PR-10G` can close.

## Decision

`PR-10G` remains **BLOCKED**. Do not freeze a release candidate, start Phase 11
certification, or label any pipeline production-ready until the blockers above
are closed and the Phase 10 gate is rerun from the supported environment.
