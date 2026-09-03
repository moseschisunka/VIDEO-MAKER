# OpenMontage Production Readiness Progress Tracker

This is the only authoritative implementation status ledger for the production-readiness program.

Do not mark a task complete because code exists. Link current test and review evidence. Do not erase historical blocker notes; append resolution evidence.

## Program control

| Field | Current value |
|---|---|
| Program status | `IN_PROGRESS` |
| Current phase | Phase 10 — Packaging, performance, security, and operations |
| Next task | Publish and verify `PR-1016`/`PR-1017`/`PR-1018` in supported CI, then close the external `PR-10G` blockers (deployment rollback, trusted edge, monitoring/paging, and frozen release candidate) |
| Current task owner | OpenMontage execution agent |
| Frozen release candidate | None |
| Production decision | Not eligible |
| Last tracker update | Local checkpoint `ccdf42f` implements `PR-1016`: the approved source-footage talking-head launch lane now has a deterministic manifest golden path, source-mode fail-closed guard, and ordinary manifest-agent `/run` handoff; its post-refinement local release-blocker validation was green (**1062** passed, 5 skipped, 1 deselected), while supported CI verification is pending publication of this commit. Local follow-up `PR-1017` adds fail-closed media timeline and declared stream-type validation after reproducing zero, non-finite, and negative ffprobe durations; the precision refinement now prefers the container timeline and ignores unrelated longer companion streams during fallback; Phase 6/9 adjacent local checks are green (**36** passed), the full offline release-blocker run is green (**1076** passed, 5 skipped, 1 deselected, 1 warning in 296.08s), and the broader offline regression is green (**1533** passed, 7 skipped, 3 deselected, 1 warning, 1 subtests passed in 382.69s). `PR-1018` aligns the canonical Backlot screenshot-stage, simulation, and quarantined internal demo runner with the immutable approval contract; focused fixture checks are green (**22** passed, 1 warning), while its supported CI verification remains pending. `PR-1019` adds an authenticated Prometheus text scrape endpoint with deterministic redaction/escaping; local observability checks pass, while external sink delivery and durable SLO aggregation remain open. The final working tree also passes local SLO, load/soak, and operations diagnostics with no network or paid-provider calls. The prior supported run `33740027654` (checkpoint `7646d46`) passed all required jobs: clean install with zero high/critical npm advisories, **1058** release-blocker contracts, **1516** offline regression tests, the hardened container/browser matrix, Ubuntu SLO/load/operations evidence, and the project-relative caption final-review regression; live-provider and HyperFrames opt-ins were skipped. `PR-1015` remains verified. `PR-10G` remains blocked by deployment rollback, external alert delivery/metrics aggregation, trusted-edge security enforcement, and frozen-RC evidence |

## Status rules

Allowed states: `NOT_STARTED`, `READY`, `IN_PROGRESS`, `BLOCKED`, `IMPLEMENTED`, `VERIFIED`, `COMPLETE`.

- A task needs an evidence link before `VERIFIED`.
- A task becomes `COMPLETE` only after its integration condition is satisfied.
- A phase gate becomes `COMPLETE` only with a phase-gate report and human decision where required.
- When a completed contract regresses, reopen the task and all dependent gates.

## Phase 0 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-000` | Capture repository and environment baseline | None | `VERIFIED` | Codex | [`evidence/PR-000.md`](evidence/PR-000.md) |
| `PR-001` | Establish registers and ownership | `PR-000` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-001.md`](evidence/PR-001.md) |
| `PR-002` | Define first-launch pipeline scope | `PR-001` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-002.md`](evidence/PR-002.md) — Option A approved; launch lane is `screen-demo` + source-footage `talking-head` |
| `PR-003` | Add honest non-production labelling | `PR-002` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-003.md`](evidence/PR-003.md) — catalog, API, persisted state, and UI are explicitly non-production |
| `PR-004` | Create golden scenarios | `PR-001`, `PR-002` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-004.md`](evidence/PR-004.md) — eight strict scenario contracts pass |
| `PR-005` | Record baseline quality/latency/reliability | `PR-000`, `PR-004` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-005.md`](evidence/PR-005.md) — baseline measurements and failure routing recorded |
| `PR-006` | Add release-blocker CI semantics | `PR-001` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-006.md`](evidence/PR-006.md) — known provider-catalog blocker remains intentionally red |
| `PR-0G` | Phase 0 gate | `PR-002`–`PR-006` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-0G.md`](evidence/PR-0G.md) — all Phase 0 exit conditions pass; known blockers remain mapped |

## Phase 1 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-100` | Extend manifest release metadata schema | `PR-0G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-100.md`](evidence/PR-100.md) — strict release metadata fields and fail-closed defaults |
| `PR-101` | Repair and validate all manifests | `PR-100` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-101.md`](evidence/PR-101.md) — all 13 manifests load; documentary category repaired |
| `PR-102` | Build canonical pipeline catalog | `PR-101` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-102.md`](evidence/PR-102.md) — loader, CLI, API, and UI consume one filtered catalog |
| `PR-103` | Validate create requests before mutation | `PR-102` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-103.md`](evidence/PR-103.md) — normalized pipeline/playbook/voice/profile/runtime/source validation; rejected requests leave no project |
| `PR-104` | Define durable manifest work-order contract | `PR-101` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-104.md`](evidence/PR-104.md) — atomic manifest-derived work order with run, selections, stages, approvals, claim, resume, and blocker state |
| `PR-105` | Quarantine hardcoded Studio/demo runner | `PR-102`, `PR-104` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-105.md`](evidence/PR-105.md) — ordinary `/run` cannot spawn the legacy runner; explicit internal fixture is separately marked |
| `PR-106` | Add manifest-to-agent-contract validator | `PR-101` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-106.md`](evidence/PR-106.md) — structured skill/artifact/tool/checkpoint/review/release contract report; launch candidates valid, held lanes blocked honestly |
| `PR-107` | Preserve pipeline identity across artifacts | `PR-104`, `PR-106` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-107.md`](evidence/PR-107.md) — read-only cross-artifact validator, fail-closed checkpoint merge, event enrichment, and runner propagation |
| `PR-108` | Implement agent claim/advance/resume semantics | `PR-104`, `PR-107` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-108.md`](evidence/PR-108.md) — atomic leases, manifest-derived advance, gated approval, failed-stage resume, and API contracts pass |
| `PR-109` | Certify one thin vertical pipeline path | `PR-103`, `PR-105`, `PR-108` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-109.md`](evidence/PR-109.md) — screen-demo agent artifact chain reaches current local FFmpeg output and publish without legacy runner |
| `PR-1G` | Phase 1 gate | `PR-100`–`PR-109` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-1G.md`](evidence/PR-1G.md) — Phase 1 exit conditions pass; full-suite provider-catalog drift remains mapped to Phase 3 |

## Phase 2 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-200` | Fix Remotion undefined-variable failure | `PR-1G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-200.md`](evidence/PR-200.md) — baseline NameError reproduced, fixed, diagnostics pass, and real local Remotion smoke renders |
| `PR-201` | Define run record and output provenance | `PR-1G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-201.md`](evidence/PR-201.md) — durable UUID-scoped run record, checkpoint/stage provenance, BaseTool result provenance, and artifact indexing pass |
| `PR-202` | Make `/run` idempotent | `PR-201` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-202.md`](evidence/PR-202.md) — same/different-agent retries reuse one live run and never spawn duplicate work |
| `PR-203` | Isolate per-run staging | `PR-201` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-203.md`](evidence/PR-203.md) — UUID-scoped work/inputs/props and HyperFrames/Remotion/FFmpeg isolation pass |
| `PR-204` | Publish outputs atomically | `PR-200`, `PR-203` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-204.md`](evidence/PR-204.md) — ffprobe-validated run candidates atomically promote and invalid candidates preserve prior finals |
| `PR-205` | Eliminate stale-success logic | `PR-201`, `PR-204` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-205.md`](evidence/PR-205.md) — current-run candidate hash/timestamp proof; stale final cannot rescue a failed renderer |
| `PR-206` | Add cancellation/restart/resume | `PR-202`, `PR-205` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-206.md`](evidence/PR-206.md) — cancellation, dead-lease recovery, restart, and run-record lifecycle evidence |
| `PR-207` | Probe actual media properties | `PR-204` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-207.md`](evidence/PR-207.md) — ffprobe facts and render-report mismatch rejection |
| `PR-208` | Add concurrency/crash fault tests | `PR-202`–`PR-207` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-208.md`](evidence/PR-208.md) — concurrent promotion, same-final serialization, and partial-candidate crash evidence |
| `PR-2G` | Phase 2 gate | `PR-200`–`PR-208` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-2G.md`](evidence/PR-2G.md) — 32 targeted contracts and full 45-test HyperFrames suite pass; production lock remains |

## Phase 3 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-300` | Define provider request/result contracts | `PR-2G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-300.md`](evidence/PR-300.md) |
| `PR-301` | Implement deterministic ProviderExecutor | `PR-300` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-301.md`](evidence/PR-301.md) |
| `PR-302` | Add idempotency keys and result cache | `PR-301` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-302.md`](evidence/PR-302.md) |
| `PR-303` | Enforce timeout/retry/backoff/jitter | `PR-301` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-303.md`](evidence/PR-303.md) |
| `PR-304` | Add rate limits and circuit breaker | `PR-303` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-304.md`](evidence/PR-304.md) |
| `PR-305` | Make fallback approval-aware | `PR-301`, `PR-304` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-305.md`](evidence/PR-305.md) |
| `PR-306` | Return selector ranked dry-run plans | `PR-300`, `PR-305` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-306.md`](evidence/PR-306.md) |
| `PR-307` | Split fast preflight from diagnostics | `PR-300` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-307.md`](evidence/PR-307.md) |
| `PR-308` | Integrate cost reserve/reconcile | `PR-301`, `PR-302` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-308.md`](evidence/PR-308.md) |
| `PR-309` | Migrate provider families incrementally | `PR-302`–`PR-308` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-309.md`](evidence/PR-309.md) |
| `PR-310` | Add provider fault-injection suite | `PR-309` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-310.md`](evidence/PR-310.md) |
| `PR-3G` | Phase 3 gate and contract freeze | `PR-300`–`PR-310` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-3G.md`](evidence/PR-3G.md) |

## Phase 4 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-400` | Define source/claim-grounding contracts | `PR-3G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-400.md`](evidence/PR-400.md) |
| `PR-401` | Implement grounding validator | `PR-400` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-401.md`](evidence/PR-401.md) |
| `PR-402` | Remove hardcoded/generic subject filler | `PR-400` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-402.md`](evidence/PR-402.md) |
| `PR-403` | Align research/script director skills | `PR-400`, `PR-401` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-403.md`](evidence/PR-403.md) |
| `PR-404` | Establish canonical duration solver | `PR-3G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-404.md`](evidence/PR-404.md) |
| `PR-405` | Enforce profile/aspect propagation | `PR-404` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-405.md`](evidence/PR-405.md) |
| `PR-406` | Calibrate script length to voice rate | `PR-404` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-406.md`](evidence/PR-406.md) |
| `PR-407` | Add format/timing regression suite | `PR-405`, `PR-406` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-407.md`](evidence/PR-407.md) |
| `PR-408` | Add factual accuracy eval set | `PR-401`, `PR-403` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-408.md`](evidence/PR-408.md) |
| `PR-4G` | Phase 4 gate | `PR-400`–`PR-408` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-4G.md`](evidence/PR-4G.md) |

## Phase 5 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-500` | Normalize voice identity model | `PR-3G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-500.md`](evidence/PR-500.md) |
| `PR-501` | Persist voice selection across artifacts | `PR-500` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-501.md`](evidence/PR-501.md) |
| `PR-502` | Implement voice-sample approval gate | `PR-501` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-502.md`](evidence/PR-502.md) |
| `PR-503` | Segment narration deterministically | `PR-501` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-503.md`](evidence/PR-503.md) |
| `PR-504` | Cache/resume per narration segment | `PR-302`, `PR-503` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-504.md`](evidence/PR-504.md) |
| `PR-505` | Assemble audio safely | `PR-503`, `PR-504` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-505.md`](evidence/PR-505.md) |
| `PR-506` | Verify transcript/pronunciation | `PR-505` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-506.md`](evidence/PR-506.md) |
| `PR-507` | Add voice provider/failure matrix | `PR-502`–`PR-506` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-507.md`](evidence/PR-507.md) |
| `PR-5G` | Phase 5 gate | `PR-500`–`PR-507` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-5G.md`](evidence/PR-5G.md) |

## Phase 6 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-600` | Define asset request/result contracts | `PR-3G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-600.md`](evidence/PR-600.md) |
| `PR-601` | Harden user-media ingestion | `PR-600` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-601.md`](evidence/PR-601.md) |
| `PR-602` | Harden stock downloads | `PR-600` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-602.md`](evidence/PR-602.md) |
| `PR-603` | Normalize stock license/provenance | `PR-602` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-603.md`](evidence/PR-603.md) |
| `PR-604` | Improve stock ranking/diversity | `PR-602`, `PR-603` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-604.md`](evidence/PR-604.md) |
| `PR-605` | Route AI images through executor | `PR-600`, `PR-309` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-605.md`](evidence/PR-605.md) |
| `PR-606` | Route AI video through executor | `PR-600`, `PR-309` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-606.md`](evidence/PR-606.md) |
| `PR-607` | Integrate diagram/structured visuals | `PR-600` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-607.md`](evidence/PR-607.md) |
| `PR-608` | Add contact-sheet/sample approval | `PR-601`–`PR-607` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-608.md`](evidence/PR-608.md) |
| `PR-609` | Add asset cache/partial resume | `PR-302`, `PR-600`–`PR-607` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-609.md`](evidence/PR-609.md) |
| `PR-610` | Add multi-source/corruption tests | `PR-601`–`PR-609` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-610.md`](evidence/PR-610.md) |
| `PR-6G` | Phase 6 gate | `PR-600`–`PR-610`, `PR-1017` | `IN_PROGRESS` | OpenMontage execution agent | [`evidence/PR-6G.md`](evidence/PR-6G.md) — historical mixed-media evidence remains retained; PR-1017 reopens strict-ingestion acceptance until supported CI verification |

## Phase 7 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-700` | Define edit-to-HyperFrames mapping | `PR-2G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-700.md`](evidence/PR-700.md) |
| `PR-701` | Honor narration offsets/timing | `PR-700` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-701.md`](evidence/PR-701.md) |
| `PR-702` | Implement fades/stems/ducking | `PR-700`, `PR-701` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-702.md`](evidence/PR-702.md) |
| `PR-703` | Remove runtime CDN dependency | `PR-700` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-703.md`](evidence/PR-703.md) — offline HyperFrames 0.8.25 + vendored GSAP render passed |
| `PR-704` | Enforce lint/validate/inspect/render | `PR-700` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-704.md`](evidence/PR-704.md) |
| `PR-705` | Enforce keyframe/motion quality | `PR-704` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-705.md`](evidence/PR-705.md) |
| `PR-706` | Apply resource-safe worker policy | `PR-704` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-706.md`](evidence/PR-706.md) |
| `PR-707` | Isolate workspace/cleanup | `PR-203`, `PR-700` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-707.md`](evidence/PR-707.md) |
| `PR-708` | Add offline/concurrency/timing tests | `PR-701`–`PR-707` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-708.md`](evidence/PR-708.md) |
| `PR-709` | Run HyperFrames golden review | `PR-708` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-709.md`](evidence/PR-709.md) — sampled frames, audio probe, and standing-approval review passed |
| `PR-7G` | Phase 7 gate | `PR-700`–`PR-709` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-7G.md`](evidence/PR-7G.md) |

## Phase 8 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-800` | Enforce proposal-stage music decision | `PR-5G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-800.md`](evidence/PR-800.md) |
| `PR-801` | Normalize music provenance | `PR-800` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-801.md`](evidence/PR-801.md) |
| `PR-802` | Preserve separate audio stems | `PR-801` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-802.md`](evidence/PR-802.md) |
| `PR-803` | Enforce loudness/peak/clipping/ducking | `PR-802` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-803.md`](evidence/PR-803.md) |
| `PR-804` | Generate captions from verified transcript | `PR-506` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-804.md`](evidence/PR-804.md) |
| `PR-805` | Certify caption rendering per runtime | `PR-804`, `PR-2G`, `PR-7G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-805.md`](evidence/PR-805.md) |
| `PR-806` | Add audio/caption failure tests | `PR-803`–`PR-805` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-806.md`](evidence/PR-806.md) |
| `PR-807` | Add accessibility/profile checks | `PR-805` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-807.md`](evidence/PR-807.md) |
| `PR-8G` | Phase 8 gate | `PR-800`–`PR-807` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-8G.md`](evidence/PR-8G.md) |

## Phase 9 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-900` | Define immutable approval record | `PR-3G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-900.md`](evidence/PR-900.md) |
| `PR-901` | Make gated stages pause | `PR-900` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-901.md`](evidence/PR-901.md) |
| `PR-902` | Implement approve/revise/reject transitions | `PR-901` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-902.md`](evidence/PR-902.md) |
| `PR-903` | Persist/link final review | `PR-2G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-903.md`](evidence/PR-903.md) |
| `PR-904` | Implement technical video QA | `PR-903` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-904.md`](evidence/PR-904.md) |
| `PR-905` | Implement audio/content QA | `PR-5G`, `PR-903` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-905.md`](evidence/PR-905.md) |
| `PR-906` | Add cross-artifact consistency validator | `PR-4G`, `PR-5G`, `PR-6G`, `PR-7G`, `PR-8G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-906.md`](evidence/PR-906.md) |
| `PR-907` | Block completion on revise/fail | `PR-902`–`PR-906` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-907.md`](evidence/PR-907.md) |
| `PR-908` | Add QA fault-injection corpus | `PR-904`–`PR-907` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-908.md`](evidence/PR-908.md) |
| `PR-909` | Surface QA evidence in Backlot | `PR-902`, `PR-903`, `PR-908` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-909.md`](evidence/PR-909.md) |
| `PR-9G` | Phase 9 gate | `PR-900`–`PR-909` | `COMPLETE` | OpenMontage execution agent | [`evidence/PR-9G.md`](evidence/PR-9G.md) |

## Phase 10 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-1000` | Establish dependency truth | `PR-3G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1000.md`](evidence/PR-1000.md) — pyproject authority, requirements wrappers, clean disposable install, wheel metadata, and lock coherence; Remotion lock now carries fixed transitive security overrides |
| `PR-1001` | Include non-Python package data | `PR-1000` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1001.md`](evidence/PR-1001.md) — tracked curated Remotion fixtures are packaged; generated/user media are excluded; supported run `33710765514` passes |
| `PR-1002` | Add clean-install smoke test | `PR-1000`, `PR-1001` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1002.md`](evidence/PR-1002.md) — disposable install, manifest/registry/health checks, local FFmpeg render, and CI job |
| `PR-1003` | Harden container/deployment build | `PR-1002` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1003.md`](evidence/PR-1003.md) — pinned non-root image, complete Chromium runtime libraries, authenticated health, and six in-image stills pass in supported run `33710765514` |
| `PR-1004` | Add auth/authorization/path controls | `PR-3G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1004.md`](evidence/PR-1004.md) — remote bearer auth, project scope, traversal, and symlink contracts pass |
| `PR-1005` | Define secrets/privacy/retention/deletion | `PR-1004` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1005.md`](evidence/PR-1005.md) — policy/config contract and secret-redaction persistence tests pass; automated purge remains a later task |
| `PR-1006` | Add logs/metrics/traces | `PR-3G` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1006.md`](evidence/PR-1006.md) — correlated JSON events/logs, bounded metrics, and privacy-preserving reconstruction tests pass |
| `PR-1007` | Define and measure SLOs | `PR-1006` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1007.md`](evidence/PR-1007.md) — versioned SLO contract, 11-sample offline measurements, create-validation cache fix; PR-1009 owns load/soak gates |
| `PR-1008` | Add backup/restore/migrations | `PR-2G`, `PR-1006` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1008.md`](evidence/PR-1008.md) — integrity-checked backup/restore, secret/path safeguards, and audited state migration drill |
| `PR-1009` | Add bounded load/soak tests | `PR-1003`, `PR-1006`, `PR-1007` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1009.md`](evidence/PR-1009.md) — four-run isolation, queue contention, fake-provider throttling, ten-run cleanup, bounded SSE soak, and dynamic Remotion worker policy |
| `PR-1010` | Write operator/incident runbooks | `PR-1003`–`PR-1009` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1010.md`](evidence/PR-1010.md) — deploy, rollback, provider outage, stuck/corrupt job, restore, secret rotation, and incident procedures |
| `PR-1011` | Preserve authored text and wizard interaction integrity | `PR-1000` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-10G.md`](evidence/PR-10G.md) — Windows mojibake, title-only submit, keyboard-selection, dialog accessibility, catalog availability, and run-start visibility regressions fixed; 7 release-blocking tests pass locally and in supported run `33736220396` |
| `PR-1012` | Align creation catalogs with manifest contracts and surface launch failures | `PR-1000`, `PR-102`, `PR-103` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-10G.md`](evidence/PR-10G.md) — compatible playbooks are derived from each manifest, malformed identifiers are ignored, unavailable catalogs fail closed, input limits are enforced, and non-OK `/run` responses are visible; focused catalog/wizard contracts and supported run `33736220396` pass |
| `PR-1013` | Make library state/progress truthful for work-order handoffs | `PR-1006`, `PR-1007` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1013.md`](evidence/PR-1013.md) — removes the hard-coded six-stage denominator, labels queued/running/approval states honestly, and reports a queued handoff at 0%; 10 focused contracts, read-only browser evidence, and supported run `33736220396` pass on the `0f3421d` checkpoint |
| `PR-1014` | Derive library completion metrics from truthful project state | `PR-1013` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1014.md`](evidence/PR-1014.md) — removes the hard-coded five-stage completion threshold, preserves rendered outputs, and uses one state predicate for metrics/filtering; 10 focused contracts, a read-only browser filter check, and supported run `33736220396` pass on `58439f9` |
| `PR-1015` | Preserve project-relative caption evidence through final review | `PR-1006`, `PR-1011` | `VERIFIED` | OpenMontage execution agent | [`evidence/PR-1015.md`](evidence/PR-1015.md) — commit `7646d46`; focused, adjacent, and full offline release-blocker regressions pass locally; supported run `33740027654` passes the release-blocker/offline, clean-install, container/browser, and SLO/load/operations jobs |
| `PR-1016` | Certify the approved source-footage talking-head launch path | `PR-1012`, `PR-1015` | `IMPLEMENTED` | OpenMontage execution agent | [`evidence/PR-1016.md`](evidence/PR-1016.md) — local focused, adjacent, compile, and post-refinement full offline release-blocker gates pass; supported CI verification is pending publication of checkpoint `ccdf42f` |
| `PR-1017` | Enforce positive media timelines and declared stream types at ingestion | `PR-610`, `PR-1016` | `IMPLEMENTED` | OpenMontage execution agent | [`evidence/PR-1017.md`](evidence/PR-1017.md) — zero/non-finite/negative-duration and missing-stream regressions are fixed; container duration now takes precedence over unrelated companion streams; Phase 6/9 adjacent checks **36 passed**, full offline release blockers **1074 passed**, and broader offline regression **1531 passed**; supported CI verification is pending |
| `PR-1018` | Align Backlot visual, simulation, and quarantined demo fixtures with immutable approvals | `PR-1016`, `PR-1017` | `IMPLEMENTED` | OpenMontage execution agent | [`evidence/PR-1018.md`](evidence/PR-1018.md) — canonical screenshot staging, simulation, and internal demo gates now use UUID-bound approval records; focused fixture checks **22 passed**, full offline release blockers **1072 passed**, and broader offline regression **1529 passed**; supported CI verification is pending |
| `PR-1019` | Add scrape-compatible metrics export | `PR-1006`, `PR-10G` | `IMPLEMENTED` | OpenMontage execution agent | [`evidence/PR-1019.md`](evidence/PR-1019.md) — Prometheus text exposition at `/api/metrics/prometheus` with redacted labels and bounded summaries; focused observability/config checks pass locally, full release blockers **1076 passed**, and broader offline **1533 passed**, while external sink delivery and durable SLO aggregation remain open |
| `PR-10G` | Phase 10 gate | `PR-9G`, `PR-1000`–`PR-1019` | `BLOCKED` | OpenMontage execution agent | [`evidence/PR-10G.md`](evidence/PR-10G.md) — supported run `33740027654` passes the current non-opt-in checkpoint, including PR-1015 integration; unpublished PR-1016/1017/1018/1019 checks still need supported CI, and deployment rollback, external monitoring/alert delivery, trusted-edge enforcement, and frozen-RC evidence remain required |

## Phase 11 tracker

| ID | Task | Depends on | Status | Owner | Evidence/blocker |
|---|---|---|---|---|---|
| `PR-1100` | Freeze release candidate/inventory | `PR-4G`–`PR-10G` | `NOT_STARTED` | — | — |
| `PR-1101` | Run full offline acceptance matrix | `PR-1100` | `NOT_STARTED` | — | — |
| `PR-1102` | Run clean-environment certification | `PR-1100` | `NOT_STARTED` | — | — |
| `PR-1103` | Run authorized live-provider smoke | `PR-1100`, explicit approval | `NOT_STARTED` | — | — |
| `PR-1104` | Run human audiovisual review | `PR-1101`–`PR-1103` | `NOT_STARTED` | — | — |
| `PR-1105` | Perform security/recovery/rollback drill | `PR-1100` | `NOT_STARTED` | — | — |
| `PR-1106` | Launch internal canary | `PR-1104`, `PR-1105` | `NOT_STARTED` | — | — |
| `PR-1107` | Expand limited canary | `PR-1106` observation pass | `NOT_STARTED` | — | — |
| `PR-1108` | Conduct go/no-go review | `PR-1107` | `NOT_STARTED` | — | — |
| `PR-1109` | Apply production label/release | `PR-1108` approval | `NOT_STARTED` | — | — |
| `PR-1110` | Complete post-launch observation | `PR-1109` | `NOT_STARTED` | — | — |
| `PR-11G` | Close readiness program | `PR-1110` | `NOT_STARTED` | — | — |

## Confirmed finding register

Severity meanings:

- `P0`: can expose/corrupt user data, spend money incorrectly, cross-contaminate projects, bypass approval, or deliver a stale/wrong artifact as current.
- `P1`: blocks a core advertised workflow or makes identity, factual accuracy, format, voice, runtime, or completion materially false.
- `P2`: significant reliability, speed, diagnostics, packaging, or operational weakness without immediate P0 impact.
- `P3`: non-blocking polish or maintainability improvement.

| Finding | Severity | Phase | Remediation task(s) | Owner role | Regression/eval | Status | Evidence |
|---|---:|---:|---|---|---|---|---|
| Backlot launches one hardcoded runner for multiple advertised pipelines | P1 | 1 | `PR-104`–`PR-106` | Pipeline/backend | Pipeline contract tests | `RESOLVED at PR-105` | [`evidence/PR-105.md`](evidence/PR-105.md) |
| Advertised manifests cannot follow the fixed generic stage list | P1 | 1 | `PR-101`, `PR-106`, `PR-108` | Pipeline/backend | Manifest compatibility tests | `RESOLVED at PR-106/PR-108` | [`evidence/PR-106.md`](evidence/PR-106.md), [`evidence/PR-108.md`](evidence/PR-108.md) |
| Documentary manifest/schema mismatch | P1 | 1 | `PR-101` | Pipeline/backend | Manifest schema suite | `RESOLVED at PR-101` | [`evidence/PR-101.md`](evidence/PR-101.md) |
| Pipeline/playbook/runtime/profile selections may be ignored | P1 | 1, 4 | `PR-103`, `PR-107` | Pipeline/frontend | Cross-artifact identity tests | `RESOLVED at PR-103/PR-107` | [`evidence/PR-103.md`](evidence/PR-103.md), [`evidence/PR-107.md`](evidence/PR-107.md) |
| Normal Remotion path has an undefined-variable failure | P1 | 2 | `PR-200` | Media/render | Remotion regression fixture | `RESOLVED at PR-200` | [`evidence/PR-200.md`](evidence/PR-200.md) |
| Shared staging can contaminate concurrent projects | P0 | 2 | `PR-203`, `PR-208` | Media/render | Concurrency fault suite | `RESOLVED at PR-203/PR-208` | [`evidence/PR-203.md`](evidence/PR-203.md), [`evidence/PR-208.md`](evidence/PR-208.md) |
| Duplicate `/run` calls can launch duplicate work | P0 | 2 | `PR-202`, `PR-208` | Pipeline/backend | Same-project idempotency test | `RESOLVED at PR-202/PR-208` | [`evidence/PR-202.md`](evidence/PR-202.md), [`evidence/PR-208.md`](evidence/PR-208.md) |
| Old output may be accepted after a failed current render | P0 | 2 | `PR-204`, `PR-205` | Media/render | Stale-output fault test | `RESOLVED at PR-204/PR-205` | [`evidence/PR-204.md`](evidence/PR-204.md), [`evidence/PR-205.md`](evidence/PR-205.md) |
| Declared retry/fallback/idempotency policies are not centrally enforced | P1 | 3 | `PR-300`–`PR-310` | Provider/QA | Provider fault suite | `RESOLVED at PR-3G` | [`evidence/PR-3G.md`](evidence/PR-3G.md) |
| Preflight may take roughly a minute or more | P2 | 3 | `PR-307` | Provider/operations | Cold/warm performance benchmark | `RESOLVED at PR-307` | [`evidence/PR-307.md`](evidence/PR-307.md) |
| Generic or epidemiology filler can enter unrelated topics | P1 | 4 | `PR-400`–`PR-403` | Content/pipeline | Topic contamination tests | `RESOLVED at PR-4G` | [`evidence/PR-4G.md`](evidence/PR-4G.md) |
| Declared short durations may be structurally impossible | P1 | 4 | `PR-404`–`PR-407` | Content/media | Duration golden matrix | `RESOLVED at PR-4G` | [`evidence/PR-4G.md`](evidence/PR-4G.md) |
| UI voice identity can execute a different provider/default | P1 | 5 | `PR-500`, `PR-501` | Audio/provider | Voice identity contract test | `RESOLVED at PR-5G` | [`evidence/PR-5G.md`](evidence/PR-5G.md) |
| TTS reruns serially and may assemble MP3 unsafely | P1 | 5 | `PR-503`–`PR-505` | Audio/render | Segment resume/audio decode tests | `RESOLVED at PR-5G` | [`evidence/PR-5G.md`](evidence/PR-5G.md) |
| Stock, AI, user media, and diagrams are not one coherent Studio flow | P1 | 6 | `PR-600`–`PR-610` | Media/provider | Mixed-media golden test | `RESOLVED at PR-6G` | [`evidence/PR-6G.md`](evidence/PR-6G.md) |
| Partial/invalid stock downloads can be trusted | P1 | 6 | `PR-602` | Media/provider | Interrupted/corrupt download tests | `RESOLVED at PR-6G` | [`evidence/PR-6G.md`](evidence/PR-6G.md) |
| HyperFrames can ignore offsets/fades/ducking | P1 | 7 | `PR-700`–`PR-702` | Media/render | Canonical timing/audio parity tests | `RESOLVED at PR-7G (contract and real audio render verified)` | [`evidence/PR-7G.md`](evidence/PR-7G.md) |
| HyperFrames has inspection/keyframe/worker/offline gaps | P1 | 7 | `PR-703`–`PR-709` | Media/operations | Offline and inspection fault suite | `RESOLVED at PR-7G (real runtime and concurrent render verified)` | [`evidence/PR-7G.md`](evidence/PR-7G.md) |
| Studio path lacks a complete music/caption contract | P1 | 8 | `PR-800`–`PR-807` | Audio/frontend | Audio/caption matrix | `RESOLVED at PR-8G` | [`evidence/PR-8G.md`](evidence/PR-8G.md) |
| Human gates can be auto-approved | P0 | 9 | `PR-900`, `PR-901` | Pipeline/frontend | Gate bypass tests | `RESOLVED at PR-9G` | [`evidence/PR-9G.md`](evidence/PR-9G.md) |
| Approve endpoint may not complete/resume the gated stage | P1 | 9 | `PR-902` | Pipeline/backend | Approval transition integration tests | `RESOLVED at PR-9G` | [`evidence/PR-9G.md`](evidence/PR-9G.md) |
| QA does not reliably inspect visual/audio correctness | P1 | 9 | `PR-903`–`PR-906`, `PR-908` | QA/media | Seeded defect corpus | `RESOLVED at PR-9G (automated/local evidence; human AV review remains Phase 11)` | [`evidence/PR-9G.md`](evidence/PR-9G.md) |
| `revise` may still be treated as success | P0 | 9 | `PR-907` | Pipeline/QA | Revise/fail block tests | `RESOLVED at PR-9G` | [`evidence/PR-9G.md`](evidence/PR-9G.md) |
| Render report may assume instead of probe media properties | P1 | 2, 9 | `PR-207` | Media/QA | Probe/report consistency test | `RESOLVED at PR-207` | [`evidence/PR-207.md`](evidence/PR-207.md) |
| Mandatory dependencies/package data are incomplete | P1 | 10 | `PR-1000`–`PR-1002` | DevOps | Clean-install certification | `RESOLVED — supported run 33710765514` | [`evidence/PR-1000.md`](evidence/PR-1000.md), [`evidence/PR-1001.md`](evidence/PR-1001.md), [`evidence/PR-1002.md`](evidence/PR-1002.md), [`evidence/PR-10G.md`](evidence/PR-10G.md) |
| Zero-duration or mismatched-stream media can pass strict ingestion | P1 | 6, 10 | `PR-1017` | Media/QA | ffprobe duration/stream-type regression | `IMPLEMENTED locally; supported CI pending` | [`evidence/PR-1017.md`](evidence/PR-1017.md) |
| Canonical Backlot visual, simulation, and quarantined demo fixtures used deprecated mutable approval state | P1 | 10 | `PR-1018` | QA/pipeline | Fixture staging and simulation through the immutable approval contract | `IMPLEMENTED locally; supported CI pending` | [`evidence/PR-1018.md`](evidence/PR-1018.md) |
| Production SLO, recovery, and canary evidence is missing | P1 | 10, 11 | `PR-1007`–`PR-1110` | Operations/release | Operational and release gates | `OPEN` | — |

## Human decisions register

Append decisions; never rewrite history.

| Decision ID | Date | Subject | Options | Decision | Approver | Consequence/evidence |
|---|---|---|---|---|---|---|
| — | — | First-launch pipeline scope | Pending Phase 0 inventory | Pending | — | — |
| `PR-002-SCOPE-2026-09-02` | 2026-09-02 | First-launch pipeline scope | A: `screen-demo` + source-footage `talking-head` (recommended); B: add source-footage `cinematic`/`clip-factory`/`podcast-repurpose`; C: broad launch after all certification | **Approved: A — reliability-first** | Project release owner (standing approval in this chat) | [`evidence/PR-002.md`](evidence/PR-002.md); classification is not pipeline certification |
| — | — | Supported production environments | Pending Phase 10 design | Pending | — | — |
| — | — | Canary size and observation window | Pending Phase 11 | Pending | — | — |

## Blocker log

| Date | Task | Blocker | Classification | Required decision/change | Owner | Status |
|---|---|---|---|---|---|---|
| 2026-09-02 | `PR-101` | `documentary-montage` manifest used schema-rejected category `documentary` | Contract/schema | Normalize category or explicitly exclude the pipeline; add regression test | Pipeline/backend | Resolved — [`evidence/PR-101.md`](evidence/PR-101.md) |
| 2026-09-02 | `PR-200` | Remotion diagnostics tests failed with undefined `edit_decisions` at `video_compose.py:1900` | Code defect | Add failing regression fix and render-path verification | Media/render | Resolved — [`evidence/PR-200.md`](evidence/PR-200.md) |
| 2026-09-02 | `PR-300`/`PR-306` | Registry catalog test expects stale TTS provider set; registry exposes `azure`, `edge_tts`, and `fish_audio` | Contract drift | Make catalog contract dynamic/authoritative and retain provider coverage | Provider/QA | Resolved — [`evidence/PR-306.md`](evidence/PR-306.md) |
| 2026-09-02 | `PR-307` | Capability preflight is 104.35s cold and 29.20s warm | Performance | Split cached summary from deep/live probes and measure p95 | Provider/operations | Resolved — [`evidence/PR-307.md`](evidence/PR-307.md) |
| 2026-09-02 | `PR-703` | Historical HyperFrames npm package probe timed out; runtime was unavailable at that moment | Environment/provider | Install or explicitly exclude HyperFrames from launch scope; no silent runtime swap | Media/operations | Superseded by cached offline certification — [`evidence/PR-703.md`](evidence/PR-703.md) |
| 2026-09-02 | `PR-703` | Historical npm probe superseded by cached HyperFrames 0.8.25 and vendored GSAP 3.15.0; offline strict render passed | Environment/provider | Retain the pinned package/cache and rerun the offline gate on clean setup | Media/operations | Resolved — [`evidence/PR-703.md`](evidence/PR-703.md) |
| 2026-09-02 | `PR-1000` | `.venv` is POSIX layout on Windows; FFmpeg/ffprobe versions differ; npm reports five extraneous packages | Packaging/environment | Define supported environment and clean-install contract | DevOps | Open |
| 2026-09-02 | `PR-1009`/`PERF-06` | Pre-tuning Windows diagnostic measured local FFmpeg p95 at 2.0875× output duration against the 2.0× target | Performance/environment | Repeat on the documented Linux reference runner; investigate encoder/preset tuning if the reference gate also fails | Media/operations | Superseded by bounded `fast` preset rerun; supported Ubuntu reference now passes at 1.127x in run `33710765514` |
| 2026-09-02 | `PR-10G`/`PERF-06` | Bounded `fast` FFmpeg compose default reduced the current Windows diagnostic p95 to 1.005× with all seven SLO measurements passing | Performance | Attach and review the Ubuntu reference run; retain explicit preset overrides for quality/size trade-offs | Media/operations | Resolved — supported Ubuntu reference now passes at 1.127× in run `33710765514` |
| 2026-09-02 | `PR-10G`/`SEC-06` | Same-origin/bearer-only web policy is explicit, but trusted-edge CORS/CSRF/rate-limit enforcement has not been exercised on a supported deployment | Security/operations | Execute the reverse-proxy boundary test and retain a redacted `429`/CORS evidence artifact | DevOps/security | Open |
| 2026-09-02 | `PR-10G`/`OBS-02` | Runtime metrics are bounded and reset on restart; external aggregation/scrape proof and durable SLO denominators are not attached | Observability | Connect the approved monitoring sink and retain a redacted scrape/alert evidence artifact | Operations | Open |
| 2026-09-02 | `PR-10G`/`Remotion default previews` | `ProductReveal` referenced an absent image; three video-led defaults passed empty sources to `staticFile("")`; Signal fixture referenced unshipped demo media | Code defect | Use explicit assetless preview props/branches; retain strict failure for declared missing assets and run the container smoke | Media/render | Resolved — [`evidence/PR-10G-remotion-defaults.md`](evidence/PR-10G-remotion-defaults.md); supported container proof passed in run `33710765514` |
| 2026-09-02 | `PR-000` | Full suite emitted FastAPI `on_event` and Starlette/httpx deprecation warnings | Maintenance | Replace deprecated server lifecycle; track the remaining test-client compatibility warning | Backend/operations | Resolved in server; one Starlette/httpx environment warning remains |
| 2026-09-02 | Full repository regression | End-to-end QA fixture attempted to complete manifest-gated stages with only the deprecated `human_approved` boolean, aborting test collection | Test/governance contract | Route gated fixture stages through pending checkpoint plus immutable approval record | QA/pipeline | Resolved — `evidence/PR-10G.md`; full suite **1499 passed** and executable fixture **38/38** |
| 2026-09-02 | `PR-002` | First-launch lane was a material release decision; inventory recommended option A before approval was recorded | Release governance | Approve option A, B, or a documented alternative; then continue `PR-003`/`PR-004` | Project release owner | Resolved — Option A recorded in [`evidence/PR-002.md`](evidence/PR-002.md) |
| 2026-09-02 | `PR-002` | Scope decision resolved: Option A approved; held lanes remain subject to their phase gates | Release governance | Use the approved launch boundary; retain all held workflows for later certification | Project release owner | Resolved — [`evidence/PR-002.md`](evidence/PR-002.md) |
| 2026-09-03 | `PR-10G`/HyperFrames | Direct CLI-backed operations did not consistently honor the public `offline` flag; empty lint JSON reports could raise `TypeError` while counting findings | Code defect / reliability | Apply offline mode at the operation boundary and normalize absent finding arrays to `[]`; retain focused regression evidence | Media/operations | Resolved locally and in supported render certification — [`evidence/PR-10G-hyperframes-offline-qa.md`](evidence/PR-10G-hyperframes-offline-qa.md), focused suite (**46 passed**), and run `33718631193`; frozen-RC proof remains pending |
| 2026-09-03 | `PR-10G`/CI | Supported workflow invoked the system Python after installing dependencies into `.venv`; clean Ubuntu jobs could not import pytest | CI/environment | Run all post-install pytest/scripts commands through `.venv/bin/python` and retain a successful run | DevOps | Resolved in commit `ec51ea2`; supported run `33710765514` passed |
| 2026-09-03 | `PR-10G`/Remotion dependencies | Supported npm install reported three high-severity advisories in transitive `browserslist`, `fast-uri`, and `postcss` packages | Security/dependency | Lock fixed versions and fail CI on future high/critical npm advisories | DevOps/security | Resolved — current lock audit and clean-install gate passed in supported run `33722648046` (commit `3d368da`) |
| 2026-09-03 | `PR-10G`/CI | `lib.project_pipeline` imports `edge_tts` at module load, but the authoritative runtime dependency list omitted `edge-tts` | Packaging/code defect | Declare `edge-tts` in `pyproject.toml` and assert it in the dependency contract | DevOps/provider | Resolved in `8190dbb`; supported run `33710765514` passed |
| 2026-09-03 | `PR-10G`/PR-1003 | Container start step removed `openmontage-backlot-ci` on shell exit, so the following in-image render step saw `No such container` | CI/container lifecycle | Keep the container through all verification steps and clean it in a final `always()` step | DevOps | Resolved in `8190dbb`; supported run `33710765514` passed |
| 2026-09-03 | `PR-10G`/PR-1003 | Baked Remotion Chromium could not launch in the supported image because Debian headless-browser libraries were absent (`libnspr4.so` was the first loader failure) | Container/runtime | Install and contract-test the complete Chromium runtime dependency set; rerun the in-image still matrix | DevOps/media | Resolved in `f6d8612`; supported run `33710765514` passed |
| 2026-09-03 | `PR-10G`/PR-1001 | Package-data test required ignored `remotion-composer/public/talking-head/in.mp4`, creating a local-only false positive and a clean-checkout CI failure | Packaging/privacy | Ship only tracked curated fixtures; exclude generated/user media and assert the exclusion | DevOps | Resolved in `f6d8612`; supported run `33710765514` passed |
| 2026-09-03 | `PR-10G`/PR-903 | Black-video corpus required `decoded black`, but interval-only detection reported only the filter name | QA/diagnostics | Emit canonical decoded black/blank interval evidence with precise time ranges | Media/QA | Resolved in `f6d8612`; supported offline regression run `33710765514` passed |
| 2026-09-03 | `PR-10G`/CLI runtime | Windows npm/npx `.CMD` descendants could retain stdout/stderr pipes after a timeout, making provider/runtime probes block for more than one minute; the same shared boundary covered all `BaseTool.run_command()` consumers | Reliability/performance | Terminate process trees in a bounded process group and capture diagnostics through temporary files; retain timeout regression evidence | Media/operations | Resolved in `3f37200`/`671a8dc`; supported push run `33717170584` and full HyperFrames run `33718631193` are green validation records |
| 2026-09-03 | `PR-10G`/HyperFrames CI | Opt-in workflow set `HYPERFRAMES_QA` but omitted `HYPERFRAMES_QA_RENDER`, so the green run exercised only partial QA and produced no raw artifact | CI/evidence contract | Pin Node 22, enable the real-render switch, tee the result, and upload the log; retain a static workflow contract | Media/operations | Resolved in `671a8dc`/`2bf54ae`; supported run `33718631193` passed the render path and uploaded artifact `9879467068` |
| 2026-09-03 | `PR-10G`/HyperFrames certification | The latest opt-in workflow-dispatch run needed to repeat real render coverage on the current dependency/performance checkpoint | QA/evidence | Run HyperFrames with both QA switches and retain the raw log | Media/operations | Resolved — run `33724899296` on `ac4fd9b` passed **2 tests** with the real render and uploaded artifact `9881651965`; frozen-RC repetition remains pending |
| 2026-09-03 | `PR-10G`/wizard integrity | Windows' default cp1252 decoding changed authored UTF-8 punctuation (for example `—`) into `â€”`; the wizard also allowed a direct title-only submit path and exposed non-semantic selection cards | Open manifests, playbooks, generated playbooks, and config with explicit UTF-8; reject empty concept prompts; add dialog labels, keyboard selection, focus, and Escape behavior; retain regression tests and live UI evidence | Backend/frontend/QA | Resolved in `6d23cf8`/`2562b67`; local **5/5** contracts, live Backlot modal/keyboard checks, and supported run `33727762122` pass |
| 2026-09-03 | `PR-10G`/wizard/catalog launch reliability | Wizard fallback arrays could present stale or incompatible options; a selected pipeline could show playbooks its manifest does not permit; failed `/run` responses could be mistaken for successful project creation; oversized title/topic input had no UI limit | Derive compatible playbooks from manifest metadata; remove fallback catalogs; fail closed with retry; normalize selections; enforce bounded inputs; check run response status and `ok` before redirecting; retain focused contracts and supported CI evidence | Backend/frontend/QA | Resolved in `004a2b6`/`f911341`/`30804ae`/`1d4860c`/`2a2af84`; focused **10-test** catalog/wizard set and supported run `33730564062` pass; external Phase 10 blockers remain |
| 2026-09-03 | `PR-10G`/library state precision | Library cards used a hard-coded six-stage denominator and labelled a persisted queued agent handoff `AWAITING RENDER`, producing a false `17% Completed` state | Derive progress from manifest-backed stage rails, clamp counts, and map work-order states to truthful placeholders; retain focused contracts and read-only browser evidence | Frontend/QA | Resolved in `ab01bcb`; supported run `33733699579` on `6b59d8e` passes — [`evidence/PR-1013.md`](evidence/PR-1013.md) |
| 2026-09-03 | `PR-10G`/library aggregate precision | Metrics and the completed filter retained a hard-coded `completed_count >= 5` threshold after card progress became manifest-aware | Share the manifest-aware completion predicate, preserve observed rendered outputs, and add source/browser regression coverage | Frontend/QA | Resolved in `58439f9`; supported run `33736220396` on `0f3421d` passes — [`evidence/PR-1014.md`](evidence/PR-1014.md) |
| 2026-09-03 | `PR-10G`/source-footage caption review | FFmpeg burn-in resolves a project-relative subtitle source against the project root, but final review re-checks the same declaration from the process working directory; a repository-root Backlot caller can therefore mark a valid captioned output as revise | Resolve caption evidence against the canonical project root (or the already-normalized source), add a root-caller regression, and retain render/final-review proof | Media/QA | Resolved in `7646d46`; focused and full offline regressions plus supported run `33740027654` pass — [`evidence/PR-1015.md`](evidence/PR-1015.md) |
| 2026-09-03 | `PR-1017`/media ingestion | ffprobe-readable audio/video inputs with zero/non-finite duration or a mismatched stream type could be admitted as decoded assets | Require a finite positive timeline and the declared audio/video stream before asset acceptance; retain stream-duration fallback and partial-file cleanup | Media/QA | Implemented locally; Phase 6/9 adjacent checks **36 passed** and full offline release blockers **1074 passed** — [`evidence/PR-1017.md`](evidence/PR-1017.md); supported CI pending |
| 2026-09-03 | `PR-1017`/timeline precision | A valid container duration could be replaced by a longer companion audio-stream duration because fallback selected the maximum across all streams | Prefer a finite positive container duration; when absent, select the declared stream type before any legacy fallback; retain regressions for mixed audio/video probes | Media/QA | Implemented locally; focused **16 passed**, Phase 6/9 adjacent **36 passed**, release-blocker **1074 passed**, and broader offline **1531 passed** — [`evidence/PR-1017.md`](evidence/PR-1017.md); supported CI pending |
| 2026-09-03 | `PR-1018`/Backlot fixtures | Canonical screenshot-stage, simulation, and quarantined internal demo fixtures attempted to complete human-gated stages with only the deprecated mutable `human_approved` bit; the internal runner also passed stale `init_project` arguments | Route fixture gates through `awaiting_human`, UUID-bound immutable approval, and completed transitions; use the canonical project initializer; retain approval-log and state regression coverage | QA/pipeline | Implemented locally; focused **22 passed**, full offline release blockers **1072 passed**, and broader offline regression **1529 passed** — [`evidence/PR-1018.md`](evidence/PR-1018.md); supported CI pending |
| 2026-09-03 | `PR-1019`/observability export | Operators had only a JSON in-process snapshot, making standard external scraping dependent on an adapter and easy to misconfigure | Expose an authenticated Prometheus text endpoint with deterministic escaping and explicit summary semantics; retain the existing JSON endpoint and do not fabricate histogram buckets | Operations/backend | Implemented locally; focused observability/config checks pending; external sink, durable retention, and SLO denominator proof remain open |
| 2026-09-03 | `PR-1016`/`PR-1017`/`PR-1018` supported publication | Local checkpoints are ready, but supported CI cannot verify unpublished commits; the sandbox network push failed and shared-branch publication requires exact destination authorization | Publish the reviewed commits to the approved CI branch without rewriting history, then retain the workflow run and artifacts | Release/DevOps | Open — local evidence is not substituted for supported CI |

## Phase gate summary

| Gate | Status | Evidence | Human decision |
|---|---|---|---|
| Phase 0 | `COMPLETE` | [`evidence/PR-0G.md`](evidence/PR-0G.md) | Option A approved; production remains locked until `PR-11G` |
| Phase 1 | `COMPLETE` | [`evidence/PR-1G.md`](evidence/PR-1G.md) | — |
| Phase 2 | `COMPLETE` | [`evidence/PR-2G.md`](evidence/PR-2G.md) | — |
| Phase 3 | `COMPLETE` | [`evidence/PR-3G.md`](evidence/PR-3G.md) | — |
| Phase 4 | `COMPLETE` | [`evidence/PR-4G.md`](evidence/PR-4G.md) | — |
| Phase 5 | `COMPLETE` | [`evidence/PR-5G.md`](evidence/PR-5G.md) | — |
| Phase 6 | `IN_PROGRESS` | [`evidence/PR-6G.md`](evidence/PR-6G.md), [`evidence/PR-1017.md`](evidence/PR-1017.md) | PR-1017 strict-ingestion fix is locally green; supported CI verification pending |
| Phase 7 | `COMPLETE` | [`evidence/PR-7G.md`](evidence/PR-7G.md) | — |
| Phase 8 | `COMPLETE` | [`evidence/PR-8G.md`](evidence/PR-8G.md) | — |
| Phase 9 | `COMPLETE` | [`evidence/PR-9G.md`](evidence/PR-9G.md) | Automated/local gate passed; human AV review remains Phase 11 |
| Phase 10 | `IN_PROGRESS` | [`evidence/PR-1018.md`](evidence/PR-1018.md) | PR-1016/1017/1018 local follow-ups are green; supported CI and external deployment/observability/trusted-edge/frozen-RC proof remain required |
| Phase 11 | `NOT_STARTED` | — | — |
