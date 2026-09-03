# OpenMontage Production Readiness Execution Playbook

Status: Master implementation instructions

Primary executor: GPT-5.6 Luna Max or an equivalently capable coding agent

Program objective: Move OpenMontage from an internal preview to an evidence-backed production release without violating its agent-first architecture or silently reducing the promised video quality.

## 1. Authority and read order

Before changing source code in any work session, read these files in order and in full:

1. `AGENTS.md`
2. `AGENT_GUIDE.md`
3. `PROJECT_CONTEXT.md`
4. `docs/PRODUCTION_READINESS_ROADMAP.md`
5. This playbook
6. `docs/production-readiness/PROGRESS_TRACKER.md`
7. `docs/production-readiness/ACCEPTANCE_MATRIX.md`
8. The files, tests, manifests, schemas, and skills named by the selected task

If any instruction conflicts, use this precedence:

1. Current user instruction
2. Repository `AGENTS.md` and `AGENT_GUIDE.md`
3. `PROJECT_CONTEXT.md`
4. Production-readiness invariants in the roadmap
5. This playbook
6. Existing lower-level documentation

Do not use old architecture prose to override current executable contracts. When documentation and code disagree, capture the disagreement as evidence, determine the intended contract from the sources above, and fix both code and directly affected documentation in the same task.

## 2. Non-negotiable architecture boundary

OpenMontage is agent-first:

- The AI agent reads the selected pipeline manifest and stage-director skills.
- The AI agent owns creative decisions, stage orchestration, review reasoning, and user communication.
- Python owns deterministic tools, schemas, validation, checkpoints, job/run safety, media processing, provider-call enforcement, cost accounting, and observable state.
- Backlot observes state and may create a validated work order. It must not pretend that one hardcoded Python content runner represents every pipeline.
- `lib/project_pipeline.py` is currently a specialized Studio/demo path. Until explicitly decomposed and certified, it must be quarantined from general production execution.

Forbidden implementations:

- A Python class that invents scripts, scenes, or creative decisions for every pipeline.
- A universal hardcoded stage list that overrides a selected manifest.
- A provider fallback that executes without an agent-visible decision and required user approval.
- UI options that are accepted but ignored downstream.
- A success result based on an output created by an earlier run.
- Paid API tests that execute without explicit user authorization.

## 3. Definition of program completion

The program is complete only when:

- every mandatory task in the tracker is `COMPLETE` with evidence;
- every phase gate is passed in order;
- every mandatory row in the acceptance matrix has a current passing result;
- no open P0 or P1 finding remains;
- all visible pipelines, providers, voices, formats, media modes, and runtimes are honestly labelled and certified at their displayed maturity;
- the canary and rollback drill have passed;
- the final go/no-go record is approved by the designated human release owner.

Finishing implementation is not the same as finishing the program. Release evidence and operational proof are required.

## 4. Task status model

Use only these tracker states:

| State | Meaning |
|---|---|
| `NOT_STARTED` | Dependencies have not been checked or work has not begun. |
| `READY` | All dependencies are complete and the task can start. |
| `IN_PROGRESS` | One agent owns the task and has recorded the baseline. |
| `BLOCKED` | Work cannot continue safely; blocker and required decision are documented. |
| `IMPLEMENTED` | Code/docs are changed, but the complete task test gate has not yet passed. |
| `VERIFIED` | Task tests pass and evidence exists; phase gate is still pending. |
| `COMPLETE` | Task evidence is accepted and any required integration/phase gate has passed. |

Only one foundational task in Phases 0-3 may be `IN_PROGRESS` at a time unless the roadmap explicitly allows parallel work.

## 5. Mandatory Luna execution loop

For every task, follow this loop exactly.

### 5.1 Bootstrap

1. Read the required documents.
2. Inspect `git status --short` and the diff for every target file.
3. Record the current branch and commit in the task evidence note.
4. Identify pre-existing user changes. Never revert, overwrite, or reformat unrelated work.
5. Select the first `READY` task whose dependencies are `COMPLETE`.
6. Change its tracker state to `IN_PROGRESS` before source edits.

If a target file already has user changes, inspect them and integrate carefully. If their intent cannot be safely determined, mark the task `BLOCKED` and ask the user. Do not reset or discard the file.

### 5.2 Prove the defect or missing contract

1. Reproduce the failure with the smallest safe command.
2. Add or identify a test that fails for the intended reason.
3. Record the exact failure, command, and relevant output in `docs/production-readiness/evidence/<TASK-ID>.md`.
4. If the issue cannot be reproduced, do not patch blindly. Reconcile the audit finding with the current code and update the tracker with evidence.

### 5.3 Implement

1. Make the smallest complete change that satisfies the task contract.
2. Keep creative policy in manifests/skills and deterministic enforcement in Python/TypeScript.
3. Reuse existing schemas, `BaseTool`, registry discovery, checkpoints, events, cost tracker, and project paths.
4. Do not add a provider-specific special case where a capability contract belongs.
5. Do not add silent fallback behavior.
6. Do not modify credentials or call a paid provider.
7. Update directly affected docs and schemas in the same task.

### 5.4 Verify

Run, in order:

1. the new regression test;
2. the affected module test file;
3. the affected contract/integration test group;
4. syntax/type/build checks for the touched languages;
5. the phase integration gate when the task is the final task of a phase.

Never replace a failing test with a weaker assertion. Fix either the implementation or a demonstrably incorrect test contract and explain why.

### 5.5 Review the diff

Check:

- selected identity is preserved end to end;
- error paths fail closed;
- retries are bounded and observable;
- writes are atomic where corruption is possible;
- paths are run-scoped and project-scoped;
- no secret, absolute developer path, or generated asset entered source control;
- no unrelated file changed;
- docs and schemas agree with runtime behavior;
- the test would fail again if the defect returned.

### 5.6 Record evidence and stop correctly

Create or update `docs/production-readiness/evidence/<TASK-ID>.md` with:

- task and goal;
- baseline branch/commit and dirty-file notes;
- files changed;
- failing test or baseline evidence;
- implementation summary;
- exact validation commands and results;
- residual risks;
- rollback method;
- status recommendation.

Mark the task `VERIFIED` only after all task gates pass. Mark it `COMPLETE` only when its required integration or phase-gate condition is satisfied.

Do not commit or push unless the user explicitly requests it. If authorized, commit only the reviewed task scope and include the task ID in the message.

## 6. Stop conditions

Stop and report a blocker when any of these occurs:

- a required architectural choice would change the agent-first model;
- an existing user edit conflicts materially with the task;
- a test needs live credentials or paid provider use not authorized by the user;
- a migration would make existing project state unreadable without a backward-compatibility plan;
- a selected provider/runtime/format would need to be silently substituted;
- the change requires destructive cleanup outside a run-scoped temporary directory;
- a security-sensitive change lacks a testable threat model;
- three attempted fixes fail for the same root cause;
- the phase gate exposes a new P0/P1 defect.

Use this blocker report:

1. Attempted work
2. Exact failure
3. Classification: code, contract, environment, auth, provider, data, or product decision
4. Safe options
5. Recommended option and reason
6. Work preserved and next resumable step

## 7. Standard local verification commands

Use the repository virtual environment when present. On Windows PowerShell, prefer:

```powershell
.\.venv\Scripts\python.exe -m pytest <target> -q
.\.venv\Scripts\python.exe -m pytest tests\contracts -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall lib tools backlot
npm --prefix remotion-composer exec -- tsc --noEmit
```

For runtime checks, use only when required by the task:

```powershell
ffmpeg -version
ffprobe -version
node --version
npm --version
.\.venv\Scripts\python.exe -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu_summary(), indent=2))"
.\.venv\Scripts\python.exe -c "from tools.video.hyperframes_compose import HyperFramesCompose; import json; r=HyperFramesCompose().execute({'operation':'doctor'}); print(json.dumps(r.data, indent=2)); raise SystemExit(0 if r.success else 1)"
```

Do not run the raw support envelope for routine preflight. It is intentionally slow and verbose. Do not run cloud generation in automated tests; use recorded contracts, fakes, or provider sandboxes.

## 8. Phase 0 — Baseline and release control

Goal: establish a reproducible baseline, explicit launch scope, and release-blocker discipline before changing production behavior.

Dependencies: none.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-000` | Capture repository and environment baseline | evidence only; no product code | branch/commit, dirty-tree inventory, Python/Node/npm/FFmpeg versions, test summary |
| `PR-001` | Establish severity, status, evidence, and ownership registers | tracker and acceptance matrix | every audit finding mapped to a task, owner role, regression test, and phase |
| `PR-002` | Define first-launch pipeline scope | `pipeline_defs/*.yaml`, tracker | each manifest classified `launch`, `beta`, `experimental`, or `test`; human decision recorded |
| `PR-003` | Add honest non-production labelling | `backlot/server.py`, `backlot/ui/*`, manifest/catalog metadata | UI/API cannot imply production readiness before Phase 11 |
| `PR-004` | Create versioned golden briefs and expected contracts | `tests/eval/golden_scenarios/`, `tests/fixtures/production_readiness/` | eight required scenarios load deterministically and contain no provider secrets |
| `PR-005` | Record baseline quality, latency, and reliability | `tests/eval/`, evidence | cold/warm preflight, local render timing, current test pass rate, known failure signatures |
| `PR-006` | Add release-blocker CI semantics | `.github/workflows/ci.yml`, test markers/config | P0/P1 contract failure makes CI fail; experimental live tests remain opt-in |
| `PR-0G` | Phase 0 independent gate | all Phase 0 evidence | gate report confirms launch scope, golden fixtures, and current non-production label |

### Implementation instructions

#### PR-000 — Baseline

- Do not repair anything in this task.
- Capture `git status --short`, current commit, and all modified/untracked target files.
- Run the smallest existing test groups first, then the full offline suite if feasible.
- Separate test failure from environment failure and missing optional provider dependency.
- Record preflight twice: cold process and warm process. Do not expose secret values.
- Save summarized results, not megabytes of raw registry output.

#### PR-002 — Launch scope

- Inventory manifests through `lib.pipeline_loader`, not filename assumptions.
- `framework-smoke` must be test-only.
- `documentary-montage` and any manifest absent from `AGENT_GUIDE.md` require an explicit classification decision.
- A pipeline may be visible only if its manifest, director skills, artifact contracts, runtime path, and acceptance-matrix row are internally consistent.
- This task classifies; it does not certify. Certification occurs in Phase 11.

#### PR-004 — Golden scenarios

Minimum scenarios:

1. 15-second 9:16 social short
2. 30-second grounded educational explainer
3. 60-second stock-led documentary
4. talking-head or avatar piece
5. screen demonstration
6. mixed user/stock/AI media piece
7. HyperFrames kinetic-typography piece
8. five-minute lesson

Each fixture must declare intended pipeline, source mode, profile, duration tolerance, voice intent, visual strategy, runtime candidates, expected approvals, and prohibited downgrade.

### Phase 0 exit gate

- No source behavior is called production-ready.
- Every confirmed finding has one task ID and regression owner.
- Golden fixtures are versioned and schema-validated.
- Baseline tests and timings are recorded.
- The human release owner has approved the launch-scope classification.

## 9. Phase 1 — Pipeline truth and manifest work orders

Goal: make the chosen pipeline and manifest the authoritative production contract without adding a Python creative orchestrator.

Dependencies: `PR-0G`.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-100` | Extend manifest schema with release metadata | `schemas/pipelines/pipeline_manifest.schema.json` | schema tests cover visibility, maturity, runtimes, profiles, capabilities, artifacts |
| `PR-101` | Repair and validate all manifests | `pipeline_defs/*.yaml`, `lib/pipeline_loader.py` | every manifest either validates or is deliberately excluded with reason |
| `PR-102` | Build one canonical pipeline catalog | `lib/pipeline_loader.py`, `backlot/server.py` | CLI, API, and UI receive the same filtered catalog |
| `PR-103` | Validate create requests before filesystem mutation | `backlot/server.py`, request models, tests | invalid/hidden pipeline, playbook, runtime, profile, or voice returns 4xx and creates no project |
| `PR-104` | Define durable manifest work-order contract | new schema/module under `schemas/` and `lib/`; Backlot endpoint | work order records pipeline, stage state, selections, approvals, run identity, and resume pointer |
| `PR-105` | Quarantine the hardcoded Studio/demo runner | `lib/project_pipeline.py`, `backlot/server.py`, catalog metadata | general `/run` cannot launch the demo runner for unrelated pipelines |
| `PR-106` | Add manifest-to-agent-contract validator | `lib/pipeline_loader.py`, new contract tests | every visible stage has director skill, artifact contract, tools policy, review focus, gate policy |
| `PR-107` | Preserve pipeline identity through all artifacts | project marker, proposal, decision log, checkpoints, render report, events | cross-artifact identity test detects any mismatch |
| `PR-108` | Implement resumable agent claim/advance semantics | work-order module, checkpoint integration, API | one agent claims a run; next stage comes from manifest; resume never skips a gate |
| `PR-109` | Certify one thin vertical pipeline path | selected Phase 0 launch pipeline | golden project reaches verified local output through its actual manifest |
| `PR-1G` | Phase 1 integration gate | contracts, Backlot API, golden evidence | all visible choices are truthful; no hardcoded generic stage execution |

### Implementation instructions

#### PR-100 — Manifest metadata

Add or normalize these concepts without breaking read-only loading:

- `ui_visible: boolean`
- `maturity: test | experimental | beta | production`
- `supported_runtimes`
- `supported_profiles`
- `required_capabilities`
- `required_artifacts`
- optional deprecation/replacement metadata

Provide backward-compatible defaults only where they cannot cause a pipeline to appear more mature than it is. Missing maturity must default to non-production.

#### PR-103 — Pre-mutation validation

Validation order:

1. normalize request strings;
2. resolve selected manifest through the canonical catalog;
3. reject hidden/test/unknown selections;
4. validate playbook compatibility;
5. validate requested output profile and aspect ratio;
6. validate runtime support;
7. validate voice/provider identity format without making a paid call;
8. only then initialize the project directory and write the work order.

Tests must assert the project directory is absent after every rejected request.

#### PR-104/PR-108 — Work order, claim, and resume

The work order is deterministic persistence, not creative orchestration. It must contain:

- immutable project ID and pipeline identity;
- work-order schema version;
- current manifest version/hash;
- run ID and attempt;
- current/next stage from the manifest;
- requested provider/runtime/voice/profile/style selections;
- explicit approval records or references;
- status and timestamps;
- claimed-by identity and lease/heartbeat semantics if claims are concurrent;
- last successful checkpoint and resumable next action;
- structured blocker/error state.

The agent still reads the stage director and decides how to perform the stage. Python may validate whether an advance is legal; it must not invent stage output.

#### PR-105 — Demo runner quarantine

- Add an explicit internal/demo marker.
- Remove it as the universal target of Backlot `/run`.
- Preserve it only for its certified fixture until replacement work is complete.
- Add a regression test proving a non-demo pipeline cannot invoke it.
- Remove any production label or API implication that it executes all pipeline manifests.

### Phase 1 exit gate

- 100% of UI-visible manifests validate and expose honest maturity.
- A rejected create request leaves no project state behind.
- A run work order preserves selection identity and derives stages from the manifest.
- The generic demo runner cannot execute unrelated pipelines.
- One deliberately supported pipeline completes a manifest-faithful golden path.

## 10. Phase 2 — Durable runs, artifact integrity, and Remotion recovery

Goal: guarantee fresh, isolated, restart-safe outputs and restore the normal Remotion render path.

Dependencies: `PR-1G`.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-200` | Reproduce and fix the Remotion undefined-variable failure | `tools/video/video_compose.py`, Remotion tests | regression fails before fix and passes after; real local fixture renders |
| `PR-201` | Define run record and output provenance | work-order/run schema, `lib/events.py`, render schema | every artifact/result links project ID, run ID, attempt, producing stage/tool |
| `PR-202` | Make `/run` idempotent | `backlot/server.py`, run store | repeated request returns existing active run; no duplicate process/job |
| `PR-203` | Isolate per-run staging | `lib/paths.py`, compose tools, Remotion props/output paths | concurrent projects cannot share props, temp files, or output candidates |
| `PR-204` | Publish outputs atomically | compose tools, render report | `.part`/candidate output is probed then atomically promoted; failure leaves prior final unclaimed |
| `PR-205` | Eliminate stale-success logic | runner/work-order completion logic | current run must prove a new artifact hash and timestamp after run start |
| `PR-206` | Add cancellation, restart, and resume | run store/API/checkpoints/events | killed process resumes from last valid checkpoint without duplicate paid work |
| `PR-207` | Probe actual media properties | `video_compose.py`, render report schema | duration, dimensions, streams, codecs, fps, audio presence come from `ffprobe` |
| `PR-208` | Add concurrency and crash fault tests | `tests/backlot/`, `tests/tools/`, integration fixtures | two projects plus same-project duplicate and mid-render crash pass |
| `PR-2G` | Phase 2 integration gate | Remotion golden and fault suite | fresh output, provenance, isolation, idempotency, and resume proven |

### Implementation instructions

#### PR-203 — Path model

Use a structure equivalent to:

```text
projects/<project-id>/runs/<run-id>/
  work/
  inputs/
  props/
  logs/
  candidates/
  reports/
```

The canonical deliverable may remain under `projects/<project-id>/renders/`, but it must be promoted only from a validated run candidate. Temporary cleanup may touch only the resolved run directory.

#### PR-204/PR-205 — Fresh-output proof

A successful render requires:

- candidate created after the current run started;
- non-zero file with valid container and expected streams;
- expected profile/duration within tolerance;
- SHA-256 or equivalent content digest;
- producer run ID recorded in the render report;
- atomic rename or replace into the versioned deliverable path;
- canonical `final.mp4` pointer/copy updated only after validation.

Never infer success merely because `renders/final.mp4` exists.

#### PR-206 — Resume

- Reuse successful, validated paid artifacts by idempotency key.
- Re-run deterministic cheap validation when state is uncertain.
- Never skip an `awaiting_human` gate after restart.
- Cancellation must produce a terminal or resumable state, not an orphaned active run.
- A lease abandoned by a dead worker must become reclaimable after a bounded timeout.

### Phase 2 exit gate

- The Remotion golden fixture renders successfully from a clean run.
- Two concurrent projects produce isolated, correct outputs.
- Two `/run` calls for the same project create one active run.
- Crash/restart resumes without stale success or duplicate paid operation.
- Every successful render report is based on probed media and current-run provenance.

## 11. Phase 3 — Provider execution kernel and fast preflight

Goal: enforce provider reliability, cost, idempotency, validation, and honest capability states in one deterministic layer.

Dependencies: `PR-2G`.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-300` | Define provider request/result contracts | `tools/base_tool.py`, schemas, tests | attempt, provider, model, cost, latency, idempotency, artifacts, errors are structured |
| `PR-301` | Implement deterministic `ProviderExecutor` | `lib/providers/` | bounded call wrapper; no creative/provider choice hidden inside it |
| `PR-302` | Add stable idempotency keys and result cache | executor, run store, asset metadata | replay returns validated prior success and does not re-spend |
| `PR-303` | Enforce timeout, retry, backoff, and jitter | executor and tool contracts | transient vs permanent failures tested with fake clock/provider |
| `PR-304` | Add rate limits and circuit breaker | executor | repeated provider failure opens circuit and surfaces a structured blocker |
| `PR-305` | Make fallback approval-aware | selectors, decision log, executor | alternatives may be proposed automatically; execution waits when substitution is material |
| `PR-306` | Make selectors return ranked dry-run plans | TTS/image/video selectors | selected candidate and alternatives are observable before spend |
| `PR-307` | Split fast preflight from deep diagnostics | registry, HyperFrames checks, caches | warm summary p95 under 2s; cold local preflight under 5s target |
| `PR-308` | Integrate cost reservation and reconciliation | `tools/cost_tracker.py`, executor | estimate/reserve/actual/refund recorded per attempt |
| `PR-309` | Migrate capability families incrementally | TTS first, then image, video, music | each migrated tool passes common executor contract without breaking direct tests |
| `PR-310` | Add provider fault-injection suite | `tests/contracts/`, fake providers | timeout, 429, 5xx, malformed response, partial output, restart, duplicate request |
| `PR-3G` | Phase 3 integration gate | registry, selector, executor, cost evidence | no paid-capability path bypasses the kernel; capability status is truthful |

### Implementation instructions

#### PR-301 — ProviderExecutor responsibility

It may own:

- request validation;
- timeout and cancellation;
- retry policy;
- rate limiting and circuit breaker;
- idempotency and cache lookup;
- cost reserve/reconcile;
- output validation and atomic artifact adoption;
- structured attempt/event logging.

It must not own:

- creative prompt invention;
- provider selection without an approved plan;
- material fallback decisions;
- stage progression;
- human approval simulation.

#### PR-305 — Fallback rules

Classify fallback as:

- `equivalent_non_material`: retry same provider/model or deterministic local retry; may proceed under policy.
- `material_provider_change`: different provider/model family; requires logged decision and approval.
- `material_media_change`: video to still, narration removed, runtime changed; always requires approval.
- `unavailable`: no honest substitute; block.

The executor enforces the approved plan. The agent communicates and records changes.

#### PR-307 — Preflight

Fast preflight must use cached local dependency/configuration facts and avoid network calls. Return:

- `configured`
- `available_local`
- `degraded`
- `unavailable`
- `untested`
- `requires_live_probe`

Deep/live health probes are explicit and separately timed. Do not mark a configured credential as a reachable provider without a suitable live probe.

### Phase 3 exit gate

- Every migrated provider call has bounded timeout/retry, idempotency, cost, validation, and structured events.
- Material fallback cannot execute silently.
- Duplicate/restart fault tests prove no repeated successful paid operation.
- Warm preflight meets the target or has an approved, measured exception.
- Phase 4-10 parallel work may begin only after the executor and run contracts are frozen.

## 12. Phase 4 — Grounded content, formats, and duration

Goal: make factual content traceable and make the declared format/duration a verified contract.

Dependencies: `PR-3G`; may then run in parallel with Phases 5-7 where file ownership does not overlap.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-400` | Define source and claim-grounding contracts | research/brief/script schemas and skills | factual claims link approved sources or are flagged unsupported |
| `PR-401` | Implement deterministic grounding validator | new validator under `lib/`, contract tests | unsupported high-risk claims block; creative/opinion text is classified separately |
| `PR-402` | Remove generic/hardcoded subject filler from production paths | `lib/project_pipeline.py`, content templates | non-template topics never receive epidemiology or teacher-slide filler |
| `PR-403` | Align research and script director skills | `skills/pipelines/*`, reviewer rules | agents must build from approved sources and preserve uncertainty |
| `PR-404` | Establish canonical timeline/duration solver | `lib/video_timeline.py`, `lib/media_profiles.py` | scene, narration, transitions, intro/outro fit declared target |
| `PR-405` | Enforce profile and aspect propagation | profiles, proposal/edit/render schemas | platform profile survives brief to probed output |
| `PR-406` | Calibrate script length to voice rate | script/timeline validation | no minimum-duration padding that breaks 15/30/60-second products |
| `PR-407` | Add format/timing regression suite | golden fixtures, timeline tests | short ±5% or ±1s; long ±3%; explicit exceptions documented |
| `PR-408` | Add factual accuracy eval set | `tests/eval/` | supported, contradicted, uncertain, and missing-source cases |
| `PR-4G` | Phase 4 integration gate | grounded explainer and format suite | claims traceable; profiles and duration verified in real outputs |

### Implementation instructions

- Grounding is artifact-driven because the agent performs research; Python validates structure, source presence, claim mapping, and prohibited unsupported states.
- Store source title, canonical locator, retrieval/access date, relevant excerpt or structured note, license/usage constraints, and claim IDs.
- Do not embed copyrighted source documents in fixtures; use small synthetic or appropriately licensed samples.
- Duration solving must work backward from the target: narration word budget, scene allocation, transition budget, intro/outro, and silence. Never stretch visuals to conceal an overlong script.
- Probe actual output duration in Phase 2 and compare it with the declared contract here.

### Phase 4 exit gate

- Factual golden scripts have claim-level source traceability.
- Unsupported critical claims cause `revise` or `block`.
- 15s, 30s, and 60s outputs meet tolerance without content-speed abuse.
- Long-form output meets ±3% and preserves intelligibility.
- No production path injects unrelated hardcoded lesson content.

## 13. Phase 5 — Voice-over correctness, caching, and resumability

Goal: guarantee that the selected voice/provider is the one heard, approved, cached, and verified.

Dependencies: `PR-3G`; transcript-dependent Phase 8 tasks depend on `PR-5G`.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-500` | Normalize voice identity model | voice catalog, schemas, Backlot API/UI | voice ID includes provider/model/voice/locale and never implies a different provider |
| `PR-501` | Persist selection across artifacts | production config, proposal, decision log, asset manifest, render report | cross-artifact test detects provider or voice drift |
| `PR-502` | Implement voice-sample approval gate | checkpoints, Backlot approval, TTS plan | batch narration cannot start before required sample approval |
| `PR-503` | Segment narration deterministically | TTS planning module, script sections | stable segment IDs/hashes and ordered timing metadata |
| `PR-504` | Cache and resume per segment | provider executor, TTS selector/tools | completed segments survive retry/restart; failed segment alone reruns |
| `PR-505` | Assemble audio safely | `tools/audio/audio_mixer.py` or dedicated utility | decode/filter/encode or container-safe concat; no raw unsafe MP3 byte joining |
| `PR-506` | Verify transcript and pronunciation | transcriber/STT tools, final review | transcript similarity, missing segment, punctuation leak, and pronunciation notes |
| `PR-507` | Add voice provider and failure matrix | tests/tools and contracts | local/cloud fake paths, timeout, partial segment, changed voice, resume |
| `PR-5G` | Phase 5 integration gate | narrated golden project | chosen voice identity, approval, cache, assembly, and transcript evidence pass |

### Implementation instructions

- UI voice labels must come from the registry/catalog, not hardcoded marketing names.
- A voice selection is an immutable tuple for a run: provider, model/variant, voice ID, locale, and relevant settings.
- If a provider cannot honor the selected identity, block and propose alternatives; do not route to a nominally similar default.
- Compute segment idempotency from normalized text, provider/model/voice/settings, and relevant pronunciation dictionary version.
- Maintain a timeline manifest for every segment with expected and measured duration.
- Final narration must be probed and transcribed before compose completion.

### Phase 5 exit gate

- The approved sample and final narration share the recorded voice identity.
- A mid-batch failure resumes only missing/invalid segments.
- Final audio is decodable, ordered, gap-checked, and within timeline tolerance.
- Transcript comparison passes the configured threshold or blocks completion.

## 14. Phase 6 — User, stock, AI, and diagram visual orchestration

Goal: make all visual sources first-class, validated, licensed/provenanced, reviewable, and resumable.

Dependencies: `PR-3G`; uses Phase 4 scene/grounding contracts when available.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-600` | Define canonical asset request/result contracts | asset manifest schema, selector contracts | every scene request states intent, media type, constraints, provenance needs |
| `PR-601` | Harden user-media ingestion | paths, media probes, source review | unsafe paths rejected; copied/adopted media has hash, probe, and consent/provenance metadata |
| `PR-602` | Harden stock downloads | stock adapters and shared base | stream to `.part`, validate status/length/MIME/magic/decode, then atomic rename |
| `PR-603` | Normalize stock license/provenance | stock schemas/adapters | source URL, author, license, retrieval date, restrictions, attribution requirements |
| `PR-604` | Improve stock ranking and diversity | clip/image search, scoring, corpus | semantic fit, orientation, duration, quality, duplicate and source-diversity checks |
| `PR-605` | Route AI image plans through executor | image selector/provider tools | sample-first, stable plan, idempotency, output decode/size validation |
| `PR-606` | Route AI video plans through executor | video selector/provider tools | motion-required requests cannot silently become stills |
| `PR-607` | Integrate diagrams and structured visuals | diagram/math/code tools, scene plan | semantic data and labels validated; raster fallback retains legibility |
| `PR-608` | Add contact-sheet/sample approval | Backlot storyboard, checkpoints | scene-linked candidates visible with provider/license/cost before batch approval |
| `PR-609` | Add asset cache and partial resume | executor, manifest, run store | valid assets reused; corrupt or mismatched assets regenerated individually |
| `PR-610` | Add multi-source and corruption tests | fixtures and tool tests | user/stock/AI/diagram, interrupted download, bad MIME, duplicate, license gap |
| `PR-6G` | Phase 6 integration gate | mixed-media golden project | every approved asset appears in edit plan with valid provenance and no hidden downgrade |

### Implementation instructions

- Never trust a remote file solely because it has non-zero length or the expected extension.
- Validate image decode with Pillow or equivalent and video/audio with `ffprobe`/decode sampling.
- Keep rejected candidates and reasons in review metadata without promoting them to the canonical asset manifest.
- Stock and AI are strategies under one scene asset contract; the agent selects and communicates the strategy.
- When visual motion is promised, failure to obtain motion is a blocker unless the user explicitly approves an animatic/still-led revision.
- Factual visuals need source/licensing metadata separate from generated-asset provenance.

### Phase 6 exit gate

- Interrupted/corrupt downloads never become approved assets.
- Every canonical asset has scene linkage, hash, media probe, origin, and usage rights/provenance.
- Contact-sheet approval controls batch generation where required.
- Mixed-source golden output uses the approved media types and sources.

## 15. Phase 7 — HyperFrames production hardening

Goal: make HyperFrames an isolated, offline-capable, audio-correct, inspectable runtime with parity to the canonical edit contract.

Dependencies: `PR-2G`; provider work only where Phase 3 contracts are needed.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-700` | Define canonical edit-to-HyperFrames mapping | `tools/video/hyperframes_compose.py`, `skills/core/hyperframes.md` | every supported edit field is mapped or rejected explicitly |
| `PR-701` | Honor narration offsets and scene timing | HyperFrames workspace generation | non-zero start offsets and gaps match canonical timeline |
| `PR-702` | Implement fades, stems, and ducking | HyperFrames/FFmpeg finishing | voice, music, SFX remain separate until controlled final mix |
| `PR-703` | Remove runtime CDN dependency | workspace assets/package contract | render succeeds with network disabled after documented install/cache step |
| `PR-704` | Enforce lint, validate, inspect, render order | HyperFrames tool | any unknown cut/validation/inspection critical finding blocks render success |
| `PR-705` | Enforce animation/keyframe quality | generated workspace and inspection rules | moving layers have sufficient temporal sampling; no two-keyframe placeholder motion |
| `PR-706` | Apply resource-safe worker policy | HyperFrames invocation | video-heavy compositions default to one worker or measured safe override |
| `PR-707` | Isolate workspace and cleanup | run-scoped paths | concurrent renders do not share workspace, output, or cleanup targets |
| `PR-708` | Add offline, concurrency, and timing tests | HyperFrames QA/contract tests | offline render, offset, fade, unknown cut, concurrent projects, worker policy |
| `PR-709` | Run HyperFrames golden visual review | golden fixture/evidence | sampled frames and audio timeline match approved design and edit contract |
| `PR-7G` | Phase 7 integration gate | full HyperFrames evidence | runtime is deterministic enough to resume, diagnose, and certify |

### Implementation instructions

- `hyperframes doctor` is environment evidence, not sufficient render evidence.
- Bundle or locally resolve GSAP/runtime dependencies; a production render may not depend on a public CDN.
- Treat `lint`, `validate`, `inspect`, and media probe as separate gates with structured results.
- If a canonical edit feature is unsupported, reject it before rendering rather than dropping it.
- Record the exact HyperFrames package/runtime version and workspace digest in the render report.

### Phase 7 exit gate

- Offline golden render succeeds after a documented clean setup.
- Narration offsets, music fades, and ducking match the canonical timeline.
- Unknown cuts and critical inspection findings fail closed.
- Concurrent workspaces remain isolated.
- The user-facing runtime identity remains HyperFrames from proposal through final review.

## 16. Phase 8 — Music, captions, and audio finishing

Goal: deliver complete, measurable sound and accessible captions for every audio-capable pipeline.

Dependencies: `PR-5G`; runtime-specific caption parity also depends on relevant Phase 2/7 work.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-800` | Enforce proposal-stage music decision | proposal schema/skills/decision log | library, generation, user-supplied, or no-music choice is explicit |
| `PR-801` | Normalize music asset provenance | music tools/library/asset manifest | source/provider, license, prompt/model, duration, loop/edit rights recorded |
| `PR-802` | Preserve separate audio stems | asset/edit contracts, audio mixer | narration, music, SFX independently addressable until final mix |
| `PR-803` | Enforce loudness, peak, clipping, and ducking | `tools/audio/audio_mixer.py`, probes | measured integrated loudness/true peak; speech intelligible |
| `PR-804` | Generate captions from verified transcript | subtitle tools, transcript contract | words/segments align to final narration, not draft text |
| `PR-805` | Certify caption rendering per runtime | Remotion, HyperFrames, FFmpeg paths | safe area, wrapping, timing, encoding, burn-in/sidecar contract |
| `PR-806` | Add audio/caption failure tests | tool and integration tests | silence, clipped speech, wrong language, missing words, overlapping captions |
| `PR-807` | Add accessibility and platform profile checks | media profiles/final review | readable contrast/size, max lines, caption file packaging |
| `PR-8G` | Phase 8 integration gate | narrated multi-runtime fixtures | sound and captions meet measurable and visual acceptance criteria |

### Implementation instructions

- Absence of music is valid only when explicitly selected and recorded.
- Loudness targets belong to output profiles; report measured values and tolerance.
- Avoid mixing by destructive repeated encoding. Retain stems and build one controlled final mix.
- Caption timing must follow the actual approved narration transcript.
- Caption QA must sample crowded, long-word, fast-speech, portrait, and landscape cases.

### Phase 8 exit gate

- Every audio-capable golden project has an explicit music decision.
- Final audio passes configured loudness/peak/silence/clipping checks.
- Captions match the actual spoken content and pass safe-area/readability review in all certified runtimes.

## 17. Phase 9 — Real approvals and final quality enforcement

Goal: ensure approval and QA are real state transitions and that defective output cannot be marked complete or shown as final.

Dependencies: Phase 4-8 gates for full certification; contract work can begin earlier.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-900` | Define immutable approval record | checkpoint/decision schemas, identity fields | who, what digest/version, when, decision, comments, supersession recorded |
| `PR-901` | Make gated stages pause correctly | checkpoint writer, work order, Backlot state | stage writes `awaiting_human`; no downstream execution begins |
| `PR-902` | Make approve/revise/reject endpoints transition state | `backlot/server.py`, state/API tests | approve resumes exact next action; revise invalidates downstream artifacts; reject stops |
| `PR-903` | Persist final review and link render report | final-review/render schemas, compose tool | actual artifact exists and render report references it |
| `PR-904` | Implement technical video QA | probes, frame sampling, visual QA | black/frozen/duplicate frames, bounds, stream, duration, corruption checks |
| `PR-905` | Implement audio/content QA | probes, transcript comparison | silence, clipping, missing audio, script drift, language/voice mismatch checks |
| `PR-906` | Implement cross-artifact consistency validator | new deterministic validator | brief/proposal/script/scene/assets/edit/render/review identities and constraints agree |
| `PR-907` | Make `revise` and `fail` block completion | compose/work-order/publish logic | no success/publish/final presentation when review is non-pass |
| `PR-908` | Add QA fault-injection corpus | generated fixtures | each seeded defect is detected with expected severity/action |
| `PR-909` | Make QA evidence usable in Backlot | UI/state/media endpoints | user sees frames, audio/transcript result, issues, and required action |
| `PR-9G` | Phase 9 release-candidate gate | full QA/approval suite | deliberate failures cannot bypass gates; clean golden paths pass |

### Implementation instructions

- Approval is bound to an artifact digest/version. Editing the artifact invalidates approval.
- `approve` must transition the checkpoint/work order, archive the prior state, and resume only the manifest-declared next stage.
- `revise` identifies the earliest invalid stage and invalidates dependent downstream artifacts without deleting history.
- Technical QA must inspect the actual candidate file produced by the current run.
- Automated QA supports, but does not forge, human creative approval.
- Final review status mapping: `pass` may proceed; `revise` returns to a named stage; `fail` blocks; missing review blocks.

### Phase 9 exit gate

- Every gated stage truly pauses and resumes from an immutable approved artifact.
- Seeded visual/audio/content defects are detected and route to the correct action.
- `revise`, `fail`, or missing review cannot return a successful run or publishable deliverable.
- Backlot displays current approval and QA truth, not stale decisions.

## 18. Phase 10 — Packaging, performance, security, and operations

Goal: make deployment reproducible, observable, secure, supportable, and fast enough for production use.

Dependencies: stable contracts from Phases 1-3; final certification after `PR-9G`.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-1000` | Establish one dependency truth | `requirements*.txt`, `setup.py` or replacement, package lock | clean environment installs all mandatory runtime/test dependencies |
| `PR-1001` | Include non-Python package data | packaging config | schemas, manifests, skills, styles, Backlot UI, templates available after install |
| `PR-1002` | Add clean-install smoke test | CI, disposable environment | import, manifest validation, registry discovery, Backlot health, local render smoke |
| `PR-1003` | Harden container/deployment build | Dockerfile, compose/deploy docs | non-root where feasible, healthcheck, pinned base/runtime, no secrets baked in |
| `PR-1004` | Add auth, authorization, and path controls | Backlot/API/media endpoints | non-local mode rejects unauthenticated access; project/path traversal tests pass |
| `PR-1005` | Define secrets, privacy, retention, and deletion policy | env/docs/config | secrets redacted; user media retention/deletion and provider disclosure documented |
| `PR-1006` | Add structured logs, metrics, and traces | events/run/provider/render layers | correlation by project/run/stage/attempt; no secret or full sensitive prompt leakage |
| `PR-1007` | Define and measure SLOs | operational docs/tests | availability, queue/start latency, preflight, local render, failure recovery, quality escape rate |
| `PR-1008` | Add backup/restore and state migration | run/checkpoint storage, runbook | backup restore and schema-version migration drill succeeds |
| `PR-1009` | Add bounded load and soak tests | test harness | concurrency, memory/disk cleanup, job queue, provider throttling, UI event stability |
| `PR-1010` | Write operator and incident runbooks | `docs/operations/` | deploy, rollback, provider outage, stuck job, corrupt artifact, secret rotation, restore |
| `PR-1011` | Preserve authored text and wizard interaction integrity | Backlot UI/loaders | UTF-8, input, keyboard, dialog, catalog, and run-start contracts remain truthful |
| `PR-1012` | Align creation catalogs with manifest contracts and surface launch failures | catalog/API/UI | incompatible options fail closed and non-OK `/run` responses remain visible |
| `PR-1013` | Make library state/progress truthful for work-order handoffs | Backlot library | manifest-aware stage progress and honest queued/running/approval labels |
| `PR-1014` | Derive library completion metrics from truthful project state | Backlot library | aggregate metrics and filters share one completion predicate |
| `PR-1015` | Preserve project-relative caption evidence through final review | video compose/final review | source-footage render with project-relative subtitles passes burn-in and final-review verification from a repository-root caller |
| `PR-10G` | Phase 10 operational gate | clean install, security, load, restore evidence | supported environment is reproducible and operable |

### Implementation instructions

- Resolve the duplication between `setup.py` and `requirements.txt`; select and document one authoritative dependency model.
- Pin or lock production dependencies intentionally while retaining a documented upgrade process.
- Never log `.env`, tokens, authorization headers, raw service-account material, or signed media URLs.
- Localhost-only development behavior must be explicit. Any remotely reachable Backlot deployment needs authentication, authorization, safe media access, CSRF/CORS decisions, and rate limiting.
- Project and media endpoints must resolve and validate paths under the intended project root.
- Load tests must use local/fake providers and bounded data; they must not create unapproved spend.

### Phase 10 exit gate

- A clean supported machine/container installs and passes smoke tests from documented commands.
- Package data is present after installation.
- Security tests cover authentication, project authorization, traversal, secret redaction, and unsafe uploads.
- Metrics and logs allow one failed run to be reconstructed end to end.
- Backup/restore, rollback, and bounded load tests pass.

## 19. Phase 11 — Certification, canary, and launch

Goal: convert implementation evidence into a controlled production decision and monitored rollout.

Dependencies: `PR-4G` through `PR-10G`.

### Ordered tasks

| ID | Work package | Primary targets | Required proof |
|---|---|---|---|
| `PR-1100` | Freeze release candidate and inventory | version/release notes, tracker | immutable commit/ref and exact supported capability list |
| `PR-1101` | Run full offline acceptance matrix | acceptance matrix, evidence | all mandatory contract/fault/local-render rows pass |
| `PR-1102` | Run clean-machine/container certification | supported environments | setup-to-render procedure works without developer residue |
| `PR-1103` | Run authorized live-provider smoke matrix | opt-in credentials/sandbox only | selected launch providers pass tiny cost-bounded calls; cost recorded |
| `PR-1104` | Run human audiovisual review | golden outputs | designated reviewers approve factual, visual, voice, audio, caption, and design quality |
| `PR-1105` | Perform security/recovery/rollback drill | operations evidence | simulated outage, bad deploy, backup restore, provider failure handled |
| `PR-1106` | Launch internal canary | controlled users/projects | telemetry and support process validated; no P0/P1 |
| `PR-1107` | Expand limited production canary | agreed percentage/cap | SLO/error/quality thresholds remain within limits |
| `PR-1108` | Conduct formal go/no-go review | final release record | human owner signs scope, known risks, rollback, and support coverage |
| `PR-1109` | Apply production label and publish release | UI/docs/version only after approval | advertised capability exactly matches certified scope |
| `PR-1110` | Complete post-launch observation | monitoring record | observation window passes or rollback criteria invoked |
| `PR-11G` | Close production-readiness program | all evidence | final definition of production ready is satisfied |

### Certification rules

- Mock/fake tests prove control flow; they do not prove live provider access.
- One tiny, authorized live call per launch provider proves access; it does not prove broad output quality.
- Human review must inspect actual video and audio, not only JSON reports.
- Any P0/P1 finding resets the relevant phase gate.
- A feature failing certification must be hidden/downgraded in maturity or fixed; it cannot remain advertised as production.

### Canary progression

1. Internal team only
2. Named design partners or test users
3. Small percentage/capped workload
4. Broader production
5. General production after the agreed observation window

Automatic rollback triggers are defined in the roadmap and acceptance matrix. Roll back first; diagnose second when user data, spend, identity, or deliverable correctness is at risk.

### Phase 11 exit gate

- All mandatory acceptance rows pass on the frozen release candidate.
- Live provider checks were explicitly authorized and cost-bounded.
- Human audiovisual review approves the launch matrix.
- Rollback and recovery are demonstrated.
- The release owner signs the go/no-go record.
- Production labels describe only certified capability.

## 20. Safe parallelization and file ownership

Phases 0-3 are sequential. After `PR-3G`, the following may run in parallel only with explicit file ownership:

| Workstream | Typical owner | Avoid simultaneous edits to |
|---|---|---|
| Grounding/duration | content/pipeline engineer | shared artifact schemas without coordination |
| Voice | audio/provider engineer | `tools/base_tool.py`, executor contracts after freeze |
| Visual sources | media/provider engineer | selector/executor shared code without coordination |
| HyperFrames | render engineer | canonical edit schema without coordination |
| Operations | DevOps/security | runtime behavior still changing in an unfrozen phase |
| QA framework | QA engineer | production implementation files unless assigned a fix |

One worker owns one file or module at a time. Shared contract changes require a short design note and integration owner before parallel work starts.

## 21. Phase-gate report template

Create `docs/production-readiness/evidence/PHASE-<N>-GATE.md`:

```markdown
# Phase N Gate

- Release candidate/commit:
- Gate date:
- Gate owner:
- Scope:

## Required tasks

| Task | Status | Evidence |
|---|---|---|

## Commands and results

| Command | Result | Duration | Evidence |
|---|---|---:|---|

## Invariant review

| Invariant | Pass/fail | Evidence |
|---|---|---|

## Open risks

## Decision

PASS / FAIL / PASS WITH EXPLICIT NON-PRODUCTION EXCLUSION

## Human approval

- Name/role:
- Decision:
- Date:
```

No phase advances on an undocumented verbal assumption. The tracker must link the gate report.

## 22. End-of-task report format for Luna

At the end of each task, report concisely:

1. Outcome and task ID
2. Files changed
3. Tests run and exact result
4. Evidence file
5. Remaining risk or blocker
6. Next dependency-ready task
7. Whether a human approval gate is now required

If work is incomplete, say `IN_PROGRESS` or `BLOCKED`; never describe partial implementation as fixed or production-ready.
