# OpenMontage Production Readiness Acceptance Matrix

This matrix is the release contract. A feature may be called production-ready only when every mandatory row that applies to it has current evidence from the frozen release candidate.

## 1. Result states

Use only:

- `NOT_RUN`
- `PASS`
- `FAIL`
- `BLOCKED`
- `NOT_APPLICABLE` with written reason
- `EXCLUDED_FROM_RELEASE` with the corresponding UI/catalog change

Evidence must identify the commit/ref, environment, exact command or review procedure, result, artifacts, and reviewer. A result from an older commit becomes stale when relevant code or contracts change.

## 2. Release candidate identity

| Field | Value |
|---|---|
| Commit/ref | Not frozen |
| Version | Not assigned |
| Candidate date | — |
| Python | — |
| Node/npm | — |
| FFmpeg/ffprobe | — |
| Remotion | — |
| HyperFrames | — |
| Operating system | — |
| Container image digest | — |
| Certified provider set | — |
| Certified pipeline set | — |

## 3. Universal release invariants

| ID | Acceptance requirement | Method | Required | Status | Evidence |
|---|---|---|---|---|---|
| `INV-01` | UI selection equals manifest/work-order execution identity | automated cross-artifact test | Yes | `NOT_RUN` | — |
| `INV-02` | Provider/model/voice/runtime/profile changes cannot occur silently | contract + fault test | Yes | `NOT_RUN` | — |
| `INV-03` | Successful deliverable is produced and validated by current run | stale-output fault test | Yes | `NOT_RUN` | — |
| `INV-04` | Paid call retry/restart is idempotent | fake-provider spend counter | Yes | `NOT_RUN` | — |
| `INV-05` | Concurrent runs cannot share mutable workspace or output | concurrency test | Yes | `PASS` | [`evidence/PR-1009.md`](evidence/PR-1009.md) — four isolated local runs and run-scoped markers show zero cross-project contamination |
| `INV-06` | Human-gated stage pauses and resumes only after immutable approval | API/checkpoint integration test | Yes | `PASS` | [`evidence/PR-9G.md`](evidence/PR-9G.md) |
| `INV-07` | `revise`, `fail`, or missing final review blocks completion/publish | seeded QA test | Yes | `PASS` | [`evidence/PR-9G.md`](evidence/PR-9G.md) |
| `INV-08` | Factual claims are linked to approved sources or blocked | grounding eval | Factual workflows | `NOT_RUN` | — |
| `INV-09` | Actual output meets declared format and duration contract | ffprobe + golden matrix | Yes | `NOT_RUN` | — |
| `INV-10` | Every canonical asset has current-run provenance and validation | manifest validator | Yes | `NOT_RUN` | — |
| `INV-11` | Capability status distinguishes configured, available, degraded, unavailable, and untested | registry contract test | Yes | `NOT_RUN` | — |
| `INV-12` | One run is traceable across work order, checkpoints, provider attempts, costs, artifacts, render, QA | observability review | Yes | `NOT_RUN` | — |

## 4. Pipeline certification

Each manifest discovered by `lib.pipeline_loader` must appear here before release. `framework-smoke` must remain test-only. A missing or unclassified manifest is a release blocker.

| Pipeline | Intended release tier | Manifest/schema | Director/artifact contract | Thin E2E | Human AV review | Status | Evidence |
|---|---|---|---|---|---|---|---|
| `animated-explainer` | Experimental | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `animation` | Beta | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `avatar-spokesperson` | Experimental | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `character-animation` | Beta | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `cinematic` | Beta | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `clip-factory` | Beta | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `documentary-montage` | Experimental (held behind schema blocker) | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `hybrid` | Beta | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `localization-dub` | Experimental | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `podcast-repurpose` | Beta | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `screen-demo` | Launch candidate (not certified) | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `talking-head` | Launch candidate (source-footage only; not certified) | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| `framework-smoke` | Test only, never user-visible | `NOT_RUN` | `NOT_RUN` | `NOT_APPLICABLE` | `NOT_APPLICABLE` | `NOT_RUN` | — |

Pipeline certification requires:

1. schema-valid manifest and honest maturity;
2. every declared stage has a readable director skill;
3. canonical outputs have schemas or explicit specialized contracts;
4. tool/capability requirements match registry identities;
5. gated stages actually pause;
6. pipeline-specific source and structure are preserved;
7. one golden path completes without the generic demo runner impersonating it;
8. output passes technical and human audiovisual review.

## 5. Production input modes

| ID | Input mode | Required behavior | Status | Evidence |
|---|---|---|---|---|
| `SRC-01` | Topic/brief only | grounded research/artifact path; no unrelated filler | `NOT_RUN` | — |
| `SRC-02` | User images | safe ingestion, hash/probe/provenance, scene linkage | `NOT_RUN` | — |
| `SRC-03` | User video | source-media review, probe, consent/provenance, edit linkage | `NOT_RUN` | — |
| `SRC-04` | User audio/voice-over | decode/probe/transcript/timeline and no unauthorized replacement | `NOT_RUN` | — |
| `SRC-05` | Reference URL/video | video-reference analysis and differentiated concept; no carbon copy | `NOT_RUN` | — |
| `SRC-06` | Screen/terminal workflow | synthetic vs real capture decision is explicit and honored | `NOT_RUN` | — |
| `SRC-07` | Existing long-form content | derived clips preserve source meaning and timing | `NOT_RUN` | — |
| `SRC-08` | Mixed inputs | precedence, provenance, and scene mapping remain unambiguous | `NOT_RUN` | — |

## 6. Visual source matrix

| ID | Visual mode | Contract checks | Failure checks | Status | Evidence |
|---|---|---|---|---|---|
| `VIS-01` | User media | path confinement, hash, decode/probe, provenance | corrupt/unsupported/path traversal | `NOT_RUN` | — |
| `VIS-02` | Stock images | semantic/orientation/quality/license/source | partial download, bad MIME, duplicate, license gap | `NOT_RUN` | — |
| `VIS-03` | Stock video | duration/orientation/codec/license/source | partial/corrupt clip, wrong orientation, duplicate | `NOT_RUN` | — |
| `VIS-04` | AI image | approved provider/model, sample-first, seed/idempotency, decode | timeout, malformed response, rejected sample, restart | `NOT_RUN` | — |
| `VIS-05` | AI video | approved provider/model, actual motion, probe, seed/request ID | timeout, provider failure, still-image downgrade attempt | `NOT_RUN` | — |
| `VIS-06` | Diagram/chart/math/code | semantic data, labels, legibility, profile | overflow, wrong label, unreadable raster | `NOT_RUN` | — |
| `VIS-07` | Avatar/talking head | consent/source, voice/lip-sync identity, frame/audio quality | identity drift, sync failure, provider switch | `NOT_RUN` | — |
| `VIS-08` | Character animation/Ink Theater | rig/action contract, named motions, QA | broken rig, off-canvas, unsupported action | `NOT_RUN` | — |
| `VIS-09` | Mixed source | scene-level strategy and provenance | hidden substitution, duplicate/repetitive coverage | `NOT_RUN` | — |

## 7. Voice-over and language matrix

Provider rows must be populated dynamically from the release candidate registry. Do not hardcode a provider as certified because its module exists.

| ID | Scenario | Required behavior | Status | Evidence |
|---|---|---|---|---|
| `VOI-01` | Local/offline TTS path | selected local identity heard and recorded | `NOT_RUN` | — |
| `VOI-02` | One launch cloud TTS provider | authorized live smoke plus mocked fault matrix | `NOT_RUN` | — |
| `VOI-03` | Voice sample gate | batch blocked before required approval | `NOT_RUN` | — |
| `VOI-04` | Segment retry | only missing/failed segment reruns | `NOT_RUN` | — |
| `VOI-05` | Process restart | cached successful segments reused | `NOT_RUN` | — |
| `VOI-06` | Voice change | new decision entry; old cache not misused | `NOT_RUN` | — |
| `VOI-07` | Transcript comparison | ≥ configured match threshold and no known punctuation leak | `NOT_RUN` | — |
| `VOI-08` | Non-English/localized output | locale/provider/voice/captions remain consistent | `NOT_RUN` | — |
| `VOI-09` | Pronunciation exception | explicit lexicon/note and human sample approval | `NOT_RUN` | — |
| `VOI-10` | Audio assembly | decodable, ordered, gap/clipping checks pass | `NOT_RUN` | — |

## 8. Runtime certification

| Runtime | Environment check | Contract test | Real local render | Concurrency | Failure recovery | Human review | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| FFmpeg | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| Remotion | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |
| HyperFrames | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` | — |

Runtime-specific requirements:

| ID | Requirement | Applies to | Status | Evidence |
|---|---|---|---|---|
| `RUN-01` | Approved runtime persists proposal → edit → render → final review | all | `NOT_RUN` | — |
| `RUN-02` | Run-scoped workspace and candidate output | all | `NOT_RUN` | — |
| `RUN-03` | Actual media properties are probed | all | `NOT_RUN` | — |
| `RUN-04` | Unknown/unsupported edit feature fails before silent drop | all | `NOT_RUN` | — |
| `RUN-05` | Remotion TypeScript/build/composition check passes | Remotion | `PASS` | [`evidence/PR-10G-remotion-clean-build-ci.json`](evidence/PR-10G-remotion-clean-build-ci.json), [`evidence/PR-10G-remotion-compositions-ci.txt`](evidence/PR-10G-remotion-compositions-ci.txt), supported runs `33710765514` and `33722648046` |
| `RUN-06` | Remotion undefined-variable regression and golden render pass | Remotion | `PASS` | [`evidence/PR-200.md`](evidence/PR-200.md), [`evidence/PR-10G-container-render-ci.json`](evidence/PR-10G-container-render-ci.json) — regression and supported six-composition container still matrix pass |
| `RUN-07` | HyperFrames lint/validate/inspect/render all pass | HyperFrames | `PASS` | [`evidence/PR-10G-hyperframes-offline-qa.md`](evidence/PR-10G-hyperframes-offline-qa.md) — supported run `33718631193` passes scaffold/lint/validate/render; the cached production-mode diagnostic covers mandatory inspect before render |
| `RUN-08` | HyperFrames offline render passes | HyperFrames | `PASS` | [`evidence/PR-10G-hyperframes-offline-qa.md`](evidence/PR-10G-hyperframes-offline-qa.md) — explicit `HYPERFRAMES_QA_OFFLINE=1` cached-runtime render passes; frozen-RC repetition remains a separate gate |
| `RUN-09` | HyperFrames offsets/fades/ducking match canonical timeline | HyperFrames | `NOT_RUN` | — |
| `RUN-10` | FFmpeg concat/filter path rejects incompatible/corrupt media | FFmpeg | `NOT_RUN` | — |

## 9. Format and duration matrix

For short formats, acceptable duration is target ±5% or ±1 second, whichever is more permissive. For long-form, acceptable duration is target ±3%, unless the profile defines a stricter platform limit. The actual probed file is authoritative.

| ID | Target | Aspect/profile | Runtime coverage | Required checks | Status | Evidence |
|---|---:|---|---|---|---|---|
| `FMT-01` | 15s | 9:16 short | at least one launch runtime | duration, safe areas, captions, voice intelligibility | `NOT_RUN` | — |
| `FMT-02` | 30s | 16:9 explainer | Remotion launch path | duration, structured visuals, narration | `NOT_RUN` | — |
| `FMT-03` | 60s | 16:9 documentary | launch runtime | stock provenance, pacing, factual grounding | `NOT_RUN` | — |
| `FMT-04` | 60s | 9:16 social | launch runtime | reframe/layout/captions | `NOT_RUN` | — |
| `FMT-05` | 30–60s | 1:1 | launch runtime | layout/safe area | `NOT_RUN` | — |
| `FMT-06` | 30–60s | 4:5 if advertised | launch runtime | layout/safe area | `NOT_RUN` | — |
| `FMT-07` | 5min | 16:9 lesson | launch runtime | ±3%, section pacing, memory stability | `NOT_RUN` | — |
| `FMT-08` | Custom target | supported profile | applicable runtime | solver rejects impossible plan before asset spend | `NOT_RUN` | — |

## 10. Music, mix, and caption matrix

| ID | Requirement | Automated check | Human check | Status | Evidence |
|---|---|---|---|---|---|
| `AUD-01` | Music choice is explicit, including no music | schema/decision log | proposal review | `NOT_RUN` | — |
| `AUD-02` | Music provenance/license or generation metadata is complete | manifest validator | spot check | `NOT_RUN` | — |
| `AUD-03` | Narration/music/SFX stems remain separate until final mix | artifact contract | — | `NOT_RUN` | — |
| `AUD-04` | Integrated loudness and true peak meet selected profile | audio probe | listening review | `NOT_RUN` | — |
| `AUD-05` | Speech remains intelligible under music/ducking | speech/music level analysis | listening review | `NOT_RUN` | — |
| `AUD-06` | Silence, clipping, missing channel, and truncation are detected | fault tests | listening spot check | `BLOCKED` | Automated detection passes in [`evidence/PR-9G.md`](evidence/PR-9G.md); listening spot check remains Phase 11 |
| `CAP-01` | Captions derive from final verified transcript | cross-artifact test | content spot check | `NOT_RUN` | — |
| `CAP-02` | Caption timing aligns with speech | timing metric | playback review | `NOT_RUN` | — |
| `CAP-03` | Caption wrapping/safe area/contrast pass per aspect | layout tests | frame review | `NOT_RUN` | — |
| `CAP-04` | Sidecar and/or burn-in packaging matches profile | export test | — | `NOT_RUN` | — |

Output-specific loudness targets must come from the selected media profile. If a profile has no target, certification is blocked until one is approved and versioned.

## 11. Factual, editorial, and design quality

| ID | Requirement | Automated/eval evidence | Human evidence | Status | Evidence |
|---|---|---|---|---|---|
| `QLT-01` | Critical factual claims have approved source links | grounding validator/eval | domain spot check | `NOT_RUN` | — |
| `QLT-02` | Contradicted/unsupported critical claims block | seeded eval | reviewer confirmation | `NOT_RUN` | — |
| `QLT-03` | Script answers the approved brief without unrelated filler | semantic fixture assertions | editorial review | `NOT_RUN` | — |
| `QLT-04` | Scene plan visually explains rather than merely decorates | scene/asset coverage checks | storyboard review | `NOT_RUN` | — |
| `QLT-05` | Visual identity follows approved playbook/taste profile | artifact consistency | design review | `NOT_RUN` | — |
| `QLT-06` | Atelier work is distinct and does not reuse frozen looks | component-use/static checks where feasible | distinctness review | `NOT_RUN` | — |
| `QLT-07` | No black/frozen/duplicate/placeholder/low-resolution critical shots | frame/video QA | playback review | `BLOCKED` | Automated fault corpus passes in [`evidence/PR-9G.md`](evidence/PR-9G.md); playback review remains Phase 11 |
| `QLT-08` | Text is legible and inside safe areas | layout/frame QA | playback review | `NOT_RUN` | — |
| `QLT-09` | Voice, visuals, captions, music, and timing agree | cross-artifact validator | complete playback | `BLOCKED` | Automated consistency checks pass in [`evidence/PR-9G.md`](evidence/PR-9G.md); complete playback remains Phase 11 |
| `QLT-10` | Final review accurately selects pass/revise/fail and next action | seeded defect corpus | reviewer audit | `BLOCKED` | Automated corpus passes in [`evidence/PR-9G.md`](evidence/PR-9G.md); reviewer audit remains Phase 11 |

## 12. Fault-injection and recovery matrix

| ID | Injected condition | Expected result | Status | Evidence |
|---|---|---|---|---|
| `FLT-01` | Duplicate create/run request | one project/active run; idempotent response | `NOT_RUN` | — |
| `FLT-02` | Process dies after paid provider success but before checkpoint | result recovered by idempotency key; no second spend | `NOT_RUN` | — |
| `FLT-03` | Provider timeout | bounded retry then structured blocker/fallback proposal | `NOT_RUN` | — |
| `FLT-04` | Provider 429 | respects retry/backoff/rate state; no retry storm | `NOT_RUN` | — |
| `FLT-05` | Provider 4xx permanent error | no pointless retry; actionable failure | `NOT_RUN` | — |
| `FLT-06` | Provider 5xx/transient error | bounded policy and observable attempts | `NOT_RUN` | — |
| `FLT-07` | Malformed provider response | validation failure; no artifact promotion | `NOT_RUN` | — |
| `FLT-08` | Interrupted stock/media download | `.part` remains unapproved; safe retry | `NOT_RUN` | — |
| `FLT-09` | Wrong MIME/extension or corrupt media | decode/probe failure; rejected | `NOT_RUN` | — |
| `FLT-10` | Current render fails while old final exists | run fails; old file not claimed as new | `NOT_RUN` | — |
| `FLT-11` | Two projects render concurrently | isolated outputs/workspaces/events | `PASS` | [`evidence/PR-1009.md`](evidence/PR-1009.md) — bounded four-project local/fake isolation probe |
| `FLT-12` | Selected runtime unavailable at compose | block and request decision; no silent swap | `NOT_RUN` | — |
| `FLT-13` | Selected voice/provider unavailable | block/propose; no default voice substitution | `NOT_RUN` | — |
| `FLT-14` | Motion provider fails | no still-led downgrade without approval | `NOT_RUN` | — |
| `FLT-15` | Approval artifact changes after approval | approval invalidated; downstream blocked | `NOT_RUN` | — |
| `FLT-16` | Final review is missing/revise/fail | completion and publish blocked | `NOT_RUN` | — |
| `FLT-17` | Worker lease expires | safe reclaim/resume from checkpoint | `NOT_RUN` | — |
| `FLT-18` | Disk nearly full or write fails | atomic state retained; actionable failure; no corrupt promotion | `NOT_RUN` | — |
| `FLT-19` | Backlot restarts during active run | state reconstructed from durable records | `NOT_RUN` | — |
| `FLT-20` | Invalid checkpoint/schema version | fail closed or migrate through tested path | `NOT_RUN` | — |

## 13. Performance and scalability gates

Measure on a documented reference environment with local/fake providers unless explicitly testing a live provider.

| ID | Metric | Initial target | Status | Evidence |
|---|---|---:|---|---|
| `PERF-01` | Warm `provider_menu_summary()` | p95 ≤ 2 seconds | `PASS` | [`PR-1007`](evidence/PR-1007.md) — 11-sample offline baseline p95 0.457s |
| `PERF-02` | Cold local preflight without live probes | p95 ≤ 5 seconds | `PASS` | [`PR-1007`](evidence/PR-1007.md) — 11-sample local-only baseline p95 0.012s |
| `PERF-03` | Create-request validation | p95 ≤ 500 ms excluding disk contention | `PASS` | [`PR-1007`](evidence/PR-1007.md) — fingerprint-cached catalog baseline p95 0.045s |
| `PERF-04` | Duplicate `/run` idempotent response | p95 ≤ 500 ms | `PASS` | [`PR-1007`](evidence/PR-1007.md) and latest supported run `33722648046` — 11 duplicate responses p95 0.114s; no second run |
| `PERF-05` | Backlot state refresh for active project | p95 ≤ 400 ms | `PASS` | [`PR-1007`](evidence/PR-1007.md) — 11-sample active-project baseline p95 0.007s |
| `PERF-06` | Local render throughput | ≤ 2.0 wall seconds per output second (FFmpeg reference fixture) | `PASS` | [`PR-1007`](evidence/PR-1007.md), [`evidence/PR-10G-slo-windows-after-fast.json`](evidence/PR-10G-slo-windows-after-fast.json), and [`evidence/PR-10G-slo-linux-ci.json`](evidence/PR-10G-slo-linux-ci.json) — Windows diagnostic p95 1.005x; supported Ubuntu references p95 1.127x and latest checkpoint p95 1.159x, all ffprobe-validated |
| `PERF-07` | Restart-to-resume detection | p95 ≤ 500 ms | `PASS` | [`PR-1007`](evidence/PR-1007.md) — 11 durable restart/read cycles p95 0.115s |
| `PERF-08` | Bounded concurrent runs | ≥ 4 isolated local/fake runs without contamination/OOM | `PASS` | [`evidence/PR-1009.md`](evidence/PR-1009.md) — 4/4 isolated runs, unique UUIDs, 0 contamination, bounded memory |
| `PERF-09` | Temporary disk cleanup | 0 orphan files after the approved ten-run soak | `PASS` | [`evidence/PR-1009.md`](evidence/PR-1009.md) — 10 iterations, 0 added `.tmp`/`.part`/`.lock` files |
| `PERF-10` | UI event stream stability | 0 unbounded connection/task growth in soak | `PASS` | [`evidence/PR-1009.md`](evidence/PR-1009.md) — 100 connect/disconnect cycles, max queue 64, subscriber count returned to baseline |

Targets marked “defined after baseline” must be resolved by Phase 10; they cannot remain undefined at release.

## 14. Packaging, security, and operations

| ID | Requirement | Method | Status | Evidence |
|---|---|---|---|---|
| `OPS-01` | Clean supported Python environment installs successfully | clean-install CI/manual | `PASS` | [`evidence/PR-1002.md`](evidence/PR-1002.md) — disposable install and supported clean-install smoke pass in runs `33710765514` and `33722648046` |
| `OPS-02` | Remotion dependencies install/build from lockfile | clean Node install/build | `PASS` | [`evidence/PR-10G-remotion-clean-build-ci.json`](evidence/PR-10G-remotion-clean-build-ci.json), [`evidence/PR-10G-remotion-compositions-ci.txt`](evidence/PR-10G-remotion-compositions-ci.txt), supported runs `33710765514` and `33722648046` (current lock audit reports zero vulnerabilities) |
| `OPS-03` | HyperFrames setup/doctor/offline render is documented and passes | clean environment | `PASS` | [`evidence/PR-703.md`](evidence/PR-703.md) and [`evidence/PR-10G-hyperframes-offline-qa.md`](evidence/PR-10G-hyperframes-offline-qa.md) — cached offline doctor/strict render and supported opt-in full render pass; frozen-RC repetition remains required |
| `OPS-04` | Schemas/manifests/styles/skills/UI/templates ship with package/image | installed-package inspection | `PASS` | [`evidence/PR-1001.md`](evidence/PR-1001.md) — clean-checkout wheel and installed-target package-data checks pass in supported runs `33710765514` and `33722648046` |
| `OPS-05` | Container starts, reports health, and renders local smoke fixture | container test | `PASS` | [`evidence/PR-10G-container-render-ci.json`](evidence/PR-10G-container-render-ci.json) — supported runs `33710765514` and `33722648046` validate authenticated health and six non-empty in-image Remotion stills |
| `SEC-01` | Remote Backlot requires authentication | security integration test | `PASS` | [`PR-1004`](evidence/PR-1004.md) |
| `SEC-02` | User/project authorization prevents cross-project access | security test | `PASS` | [`PR-1004`](evidence/PR-1004.md) |
| `SEC-03` | Project/media path traversal is rejected | attack-case tests | `PASS` | [`PR-1004`](evidence/PR-1004.md) |
| `SEC-04` | Upload/media validation rejects unsafe or unsupported input | upload tests | `PASS` | [`evidence/PR-601.md`](evidence/PR-601.md), [`evidence/PR-6G.md`](evidence/PR-6G.md) — consent, symlink/path, magic/decode, MIME, size, partial-download, and provenance safeguards pass |
| `SEC-05` | Secrets and signed URLs are redacted from logs/events/errors | log scanning test | `PASS` | [`PR-1005`](evidence/PR-1005.md) |
| `SEC-06` | CORS/CSRF/rate-limit behavior is explicitly configured | config/test review | `PARTIAL` | [`config/security_policy.yaml`](../../config/security_policy.yaml) and `test_web_boundary_security_policy_is_explicit_and_fail_closed` define same-origin/no-cookie-bearer behavior and edge `429` rate limiting; deployed reverse-proxy enforcement still required |
| `SEC-07` | User-media retention/deletion and provider disclosure are documented | policy review | `PASS` | [`PR-1005`](evidence/PR-1005.md) |
| `OBS-01` | Project/run/stage/attempt correlation exists in logs/events | trace reconstruction | `PASS` | [`PR-1006`](evidence/PR-1006.md) |
| `OBS-02` | Metrics cover run success/failure, latency, retries, cost, QA, queues | metric inventory/test | `PARTIAL` | [`PR-1006`](evidence/PR-1006.md), [`PR-1007`](evidence/PR-1007.md) — runtime tool/provider series pass; run/QA/queue indicators and SLO thresholds are defined, with durable aggregation still required |
| `OBS-03` | Alerts exist for P0/P1 operational symptoms | alert drill | `PARTIAL` | [`evidence/PR-10G-operations-drill.md`](evidence/PR-10G-operations-drill.md) — versioned fail-closed rules and fake-sink drill pass; external paging delivery remains required |
| `REC-01` | Durable state backup and restore succeeds | recovery drill | `PASS` | [`PR-1008`](evidence/PR-1008.md) — manifest-hashed ZIP, staged restore, identity validation, tamper/secret safeguards |
| `REC-02` | Schema/state migration succeeds from supported prior version | migration fixture | `PASS` | [`PR-1008`](evidence/PR-1008.md) — legacy 0.9 dry-run, audited atomic promotion, validation, and idempotent NOOP |
| `REC-03` | Deployment rollback restores service without state loss | rollback drill | `NOT_RUN` | — |
| `RUNBOOK-01` | Provider outage runbook works | tabletop/live fake drill | `PASS` | [`evidence/PR-10G-operations-drill.md`](evidence/PR-10G-operations-drill.md) — bounded fake 429/retry/circuit drill |
| `RUNBOOK-02` | Stuck/corrupt job runbook works | fault drill | `PASS` | [`evidence/PR-10G-operations-drill.md`](evidence/PR-10G-operations-drill.md) — duplicate claim, cancel/restart, and corrupt-candidate drill |
| `RUNBOOK-03` | Secret-rotation and incident response are documented | review/drill | `PASS` | [`evidence/PR-10G-operations-drill.md`](evidence/PR-10G-operations-drill.md) — old-token revocation/new-token health drill; incident evidence contract remains documented |

## 15. Canary and launch gates

| ID | Gate | Pass condition | Status | Evidence |
|---|---|---|---|---|
| `REL-01` | Release candidate frozen | exact ref and capability inventory recorded | `NOT_RUN` | — |
| `REL-02` | Offline certification | every applicable mandatory offline row passes | `NOT_RUN` | — |
| `REL-03` | Live provider smoke | explicit authorization; every launch provider passes bounded smoke | `NOT_RUN` | — |
| `REL-04` | Human AV review | designated reviewers approve every launch golden output | `NOT_RUN` | — |
| `REL-05` | Security/recovery/rollback drill | all critical drills pass | `NOT_RUN` | — |
| `REL-06` | Internal canary | no unresolved P0/P1; SLO and quality thresholds met | `NOT_RUN` | — |
| `REL-07` | Limited canary | agreed volume/window passes with support coverage | `NOT_RUN` | — |
| `REL-08` | Go/no-go approval | release owner signs certified scope, risks, rollback | `NOT_RUN` | — |
| `REL-09` | Production label applied accurately | UI/docs/catalog show only certified scope | `NOT_RUN` | — |
| `REL-10` | Post-launch observation | agreed observation window passes or rollback executed | `NOT_RUN` | — |

## 16. Automatic rollback conditions

Any of these triggers immediate rollback or feature disablement:

- stale or wrong deliverable shown as current;
- cross-project/user data or asset contamination;
- duplicate paid generation caused by retry/restart;
- selected voice/provider/runtime/profile not honored;
- unsupported factual content presented as verified;
- human approval gate bypassed;
- critical QA defect marked complete;
- unauthorized access or secret exposure;
- unrecoverable durable-state corruption;
- error, latency, or quality-escape rate breaches the approved canary threshold.

## 17. Final go/no-go rule

`GO` is allowed only when:

1. all applicable universal invariants pass;
2. each advertised production pipeline passes its complete row;
3. every advertised runtime/provider/media source is certified or honestly labelled below production;
4. all mandatory fault, security, recovery, packaging, and operational rows pass;
5. no P0/P1 finding is open;
6. the canary gates pass;
7. a human release owner signs the decision.

If one feature fails but the core release can safely proceed, set that feature to `EXCLUDED_FROM_RELEASE`, hide or relabel it in the product, rerun affected catalog/UI tests, and document the exclusion in the go/no-go record.
