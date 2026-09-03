# PR-10G — Phase 10 operational gate audit

Status: **BLOCKED — required external/RC proof remains outstanding**

This is an audit record, not a production approval. The release lock remains
`PR-11G`.

## Evidence completed

| Area | Result | Evidence |
|---|---|---|
| Offline release-blocker contracts | PASS | `python -m pytest tests/contracts/ -m "release_blocker and not live_provider and not hyperframes_qa" -q` → **1044 passed, 5 skipped, 1 deselected, 1 warning in 332.57s** |
| Phase 10 targeted contracts | PASS | `python -m pytest tests/contracts/test_phase10_*.py -q` (all Phase 10 contract modules: clean install, static/container render, auth/security, privacy, observability, alerting, backup/restore, load/soak, operations drills, SLOs, dependencies, package data, and runbooks) → **58 passed, 1 warning in 57.63s** |
| Bounded load/soak | PASS | [`PR-1009.md`](PR-1009.md) and [`PR-1009-load-soak.json`](PR-1009-load-soak.json) |
| Backup/restore/migration | PASS | [`PR-1008.md`](PR-1008.md) |
| Operator runbooks | PASS (documentation contract) | [`PR-1010.md`](PR-1010.md) |
| Offline operational drills | PASS (fake/no-network/no-spend) | [`PR-10G-operations-drill.md`](PR-10G-operations-drill.md) and [`PR-10G-operations-drill.json`](PR-10G-operations-drill.json) |
| Alert rules and fake-sink drill | PASS (external delivery pending) | [`PR-10G-operations-drill.md`](PR-10G-operations-drill.md), [`PR-10G-operations-drill.json`](PR-10G-operations-drill.json), and [`config/alerts.yaml`](../../../config/alerts.yaml) |
| Current Windows SLO rerun | PASS (diagnostic only) | [`PR-10G-slo-windows-after-fast.json`](PR-10G-slo-windows-after-fast.json) — all measured gates pass after bounded FFmpeg preset tuning |
| Current cached HyperFrames QA | PASS (local cached-runtime diagnostic; supported CI/RC proof pending) | [`PR-10G-hyperframes-offline-qa.md`](PR-10G-hyperframes-offline-qa.md) — opt-in scaffold/lint/validate/inspect/render completes with cached HyperFrames 0.8.25; the QA harness now supports explicit offline mode |
| Package data and clean Python smoke | PASS (local/disposable evidence) | [`PR-1001.md`](PR-1001.md), [`PR-1002.md`](PR-1002.md) |
| Disposable clean Remotion install/build | PARTIAL (local pass; supported browser bootstrap pending) | [`PR-10G-remotion-clean-install.json`](PR-10G-remotion-clean-install.json) — lockfile install, TypeScript, bundle, and 13-composition enumeration pass; standard browser ensure is blocked by restricted local egress |
| Remotion default-props smoke | PASS (local; container proof pending) | [`PR-10G-remotion-defaults.md`](PR-10G-remotion-defaults.md) and [`PR-10G-remotion-default-sweep.json`](PR-10G-remotion-default-sweep.json) — fixed four missing/empty-media default paths; all 13 compositions now render a non-empty local still |
| Web security boundary | PARTIAL (policy/test pass; deployed edge pending) | [`config/security_policy.yaml`](../../../config/security_policy.yaml) and PR-1004 security contracts — same-origin/no-cookie bearer posture is explicit; reverse-proxy CORS/CSRF/rate-limit enforcement still needs a deployment drill |
| Security/auth/path/redaction contracts | PASS | [`PR-1004.md`](PR-1004.md), [`PR-1005.md`](PR-1005.md) |
| Full repository regression suite | PASS (local) | `python -m pytest -q` → **1499 passed, 10 skipped, 1 warning, 1 subtests passed in 420.46s**; the executable end-to-end fixture also passes **38/38** via `python tests/qa/test_08_end_to_end.py` |
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
browser. The static PR-1003 contract remains green (**4 passed**); this does
not replace the required in-image Docker build/render evidence.

## Blocking proof

1. `PR-1003` cannot be promoted from `IMPLEMENTED` to `VERIFIED` on this
   workstation: neither the Docker CLI nor a Docker daemon is available. The
   checked-in GitHub Actions `container-build` job is the authoritative proof
   and must build, start, authenticate, health-check the pinned image, and
   render the asset-free/default-preview `EndTag`, `ProductReveal`,
   `SignalFromTomorrowWithMusic`, `TalkingHead`, `TitledVideo`, and
   `LyricOverlay` compositions with the baked browser.
2. `OPS-02` has a disposable local install/typecheck/bundle/enumeration pass,
   but the standard Remotion browser bootstrap is blocked by this session's
   restricted egress. CI now runs browser ensure, TypeScript, bundle, and
   composition enumeration on Ubuntu and writes `remotion-clean-build-evidence.json`;
   a passing CI run with that artifact attached is still required for
   supported-environment proof.
3. The documented Linux reference SLO run is not available in this local
   Windows session. After the bounded FFmpeg default-preset tuning, the
   current Windows diagnostic passes `PERF-06` at p95 **1.005×** (target
   ≤2.0×). The Ubuntu reference runner must still be measured; a local pass
   cannot substitute for the supported reference environment.
4. `REC-03` and `OBS-03` still require an executed deployment/rollback drill
   and alert-delivery drill. The bounded fake-provider, stuck-job,
   corrupt-artifact, and secret-rotation procedures now pass locally, but they
   do not substitute for those external drills.
5. `SEC-06` has an explicit fail-closed policy and local contract, but no
   supported deployment has yet demonstrated same-origin CORS, bearer-only
   CSRF behavior, and distributed `429` request limiting at the trusted edge.
6. `OBS-02` remains partial: the bounded runtime metrics snapshot resets on
   restart and no external aggregation/scrape proof is attached. The durable
   event stream supports reconstruction, but production alerting and SLO
   denominators still require the approved monitoring sink.

## Decision

`PR-10G` remains **BLOCKED**. Do not freeze a release candidate, start Phase 11
certification, or label any pipeline production-ready until the blockers above
are closed and the Phase 10 gate is rerun from the supported environment.
