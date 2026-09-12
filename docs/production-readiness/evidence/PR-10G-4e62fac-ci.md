# PR-10G CI and Windows diagnostic — `4e62fac`

Date: 2026-09-12
Branch: `codex/pr-10g-evidence-hardening`
Supported CI run: [34706044328](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/34706044328)

## Supported CI result

The manually dispatched default CI workflow completed successfully on commit `4e62fac8dd0443f72ddb2daab2aa0d0170797c88`.

| Job | Result |
|---|---|
| Release blockers (offline contracts) | PASS |
| Offline regression suite | PASS |
| Clean-install smoke | PASS |
| Container build and health contract | PASS |
| Phase 10 SLO and load evidence | PASS |
| Opt-in live-provider checks | SKIPPED (not requested) |
| Opt-in HyperFrames QA | SKIPPED (not requested) |

## Windows diagnostic

The full local offline command completed with **1,803 passed, 6 skipped, 3 deselected, 1 passing subtest, and 3 failed** in 909.51 seconds. The three failures were performance thresholds under the combined Windows run:

| Check | Observed | Target |
|---|---:|---:|
| Cold `/api/projects` budget | 2.241s | < 2.0s |
| `PERF-01` warm provider menu p95 | 2.205s | ≤ 2.0s |
| `PERF-07` restart-resume p95 | 0.602s | ≤ 0.5s |

The cold `/api/projects` budget passed when rerun alone (**1 passed**). The isolated Phase 10 SLO module passed (**6 passed**), including `PERF-01` and `PERF-07`; `PERF-04` also passed in the full local run. The local result remains partial rather than green; no thresholds were relaxed.

The run that exposed transient Windows `PermissionError` on work-order replacement was followed by a bounded retry at the atomic-write boundary. The final full run did not reproduce that error. Targeted atomic-retry, lease, idempotent replay, and launcher contracts passed (**16 passed**), and the browser handoff regression passed in the final full run.

## Production gate

This CI result does not supply the environment-owned `REC-03` rollback, `SEC-06` trusted-edge, `OBS-02` external metrics, or `OBS-03` paging proofs. `PR-10G` remains blocked on those items, and Phase 11 still requires human audiovisual review, canaries, and an explicit go/no-go decision.
