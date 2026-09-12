# PR-10G checkpoint — `9de5dc4`

- Date: 2026-09-12
- Ref: `codex/pr-10g-evidence-hardening`
- Exact tested SHA: `9de5dc460ad0e467d6f48765de7d0ac464d6f068`
- CI run: [34698345849](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/34698345849)
- Result: **PASS** for every enabled job.

| Job | Result |
|---|---|
| Release blockers (offline contracts) | **1,333 passed**, 5 skipped, 1 deselected, 1 warning; 179.25s |
| Offline regression suite | **1,792 passed**, 7 skipped, 3 deselected, 1 warning, 1 subtest passed; 249.90s |
| Clean-install smoke (PR-1002) | **PASS** — documented Python install, Remotion lockfile install/build, and package smoke |
| Container build and health contract (PR-1003) | **PASS** — image health, non-root runtime, and in-image Remotion render |
| Phase 10 SLO and load evidence | **PASS** — Linux SLO baseline, bounded load/soak, offline drills, and local-only operational simulation |

The workflow dispatch explicitly set `run_live_provider=false` and `run_hyperframes=false`; those two jobs were skipped. No live commercial-provider behavior or production infrastructure was exercised. The local Windows SLO file produced three near-threshold misses in one combined run (`PERF-01`, `PERF-04`, `PERF-06`); each corresponding test passed when rerun in isolation. The supported Linux SLO/load job passed on this exact code SHA.

This evidence verifies repository-owned CI on `9de5dc4`. It does not close the environment-owned `PR-10G` requirements for deployed rollback (`REC-03`), deployed trusted-edge enforcement (`SEC-06`), external durable metrics (`OBS-02`), or live external paging (`OBS-03`), and it does not certify production readiness.
