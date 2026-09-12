# PR-10G CI and final targeted regression — `3f2fca1`

Date: 2026-09-12
Branch: `codex/pr-10g-evidence-hardening`
Supported CI run: [34706752705](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/34706752705)
Commit: `3f2fca1bb1d0adbd9aa21956ace5255988275264`

## Supported CI result

The manually dispatched default CI workflow completed successfully on the exact commit above.

| Job | Result |
|---|---|
| Release blockers (offline contracts) | PASS |
| Offline regression suite | PASS |
| Clean-install smoke | PASS |
| Container build and health contract | PASS |
| Phase 10 SLO and load evidence | PASS |
| Opt-in live-provider checks | SKIPPED (not requested) |
| Opt-in HyperFrames QA | SKIPPED (not requested) |

## Final checkpoint regressions

On Windows, the final checkpoint passed the launcher, lease, and idempotency contracts (**17 passed**), the Phase 10 SLO module (**6 passed**), and the browser handoff regression (**1 passed**). These include the concurrent `/run` barrier test, which proves only one same-order request launches an agent, and sequential replay coverage proving the second request does not launch again.

The full local offline run on the immediately preceding checkpoint `4e62fac` completed with **1,803 passed, 6 skipped, 3 deselected, 1 passing subtest, and 3 failed**. All three failures were Windows performance-budget misses: cold `/api/projects` at 2.241s/2.0s, `PERF-01` at 2.205s/2.0s, and `PERF-07` at 0.602s/0.5s. The cold-project case passed alone, and the isolated Phase 10 SLO module passed (**6 passed**). The thresholds were not changed. The supported offline regression suite passed on final checkpoint `3f2fca1` in CI.

## Production gate

This repository-owned CI result does not supply the environment-owned `REC-03` rollback, `SEC-06` trusted-edge, `OBS-02` external metrics, or `OBS-03` paging proofs. `PR-10G` remains blocked on those items, and Phase 11 still requires human audiovisual review, canaries, and an explicit go/no-go decision.
