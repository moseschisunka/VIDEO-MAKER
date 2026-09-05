# OpenMontage codebase review and next moves

Review date: 2026-09-05. Baseline: `4339c3d` on `codex/pr-10g-evidence-hardening`.

## Verdict

Keep the agent-driven production model and the small certified execution surface. The project has substantial defensive infrastructure, but it is still an internal preview, not a finished self-service video studio. More providers, more pipelines, and more certification paperwork will not close the central product gap: a person must be able to move from a brief and source material to a reviewed, usable video, with a clear agent handoff and reliable recovery.

This review makes bounded repairs and deletions. It does not declare production readiness, publish changes, or replace the existing readiness ledger.

## Repository and publication state

The repository is nested in `Open Montage/OpenMontage`; its parent is not a Git checkout.

| Surface | Verified state at review start |
|---|---|
| Working tree and index | Clean; no pending tracked or untracked changes |
| Current branch | `codex/pr-10g-evidence-hardening`, HEAD `4339c3d` |
| Local main | `c66e981`, nine commits ahead of `origin/main` |
| GitHub main | `1d979524483872e81711e8c1059aea130a2a48c5` |
| Current branch vs GitHub | Ten additional commits, including provider alias fixes and corrected simulation classification |
| Open pull requests | None returned by the GitHub API |
| Latest GitHub CI | [33912669980](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33912669980), success for `1d97952`; does not validate the ten local commits or this patch |

The local-only commits were reviewed as a complete diff against `origin/main`: 22 files, 238 additions, 63 deletions before this review's edits. The provider identity corrections are justified. The simulation corrections are essential: a test agent cannot sign a human review, localhost test doubles do not establish a real external service, and fixture identifiers are not immutable image digests. Preserve these corrections when publishing.

## Coverage and limits

The tracked inventory contains 2,444 files, including 611 Python files. All tracked Python files were parsed for syntax. First-party Python in `lib`, `tools`, `backlot`, `scripts`, `styles`, and `schemas` was scanned for undefined names and syntax errors with Ruff. The review also covered the unpublished diff, application boundaries, frontend source and browser behavior, manifests, schemas, dependency declarations and lockfile, packaging, CI, and the readiness records.

This is a repository-wide, risk-weighted engineering review, not a claim that every line of vendored content received a manual security audit. `.agents` and `.claude` alone contain 1,414 tracked files, largely bundled instruction/tooling material. Provider calls, GPU generation, third-party service correctness, and deployed infrastructure were not exercised. No paid generation was initiated. Browser testing used an isolated project root under `tmp`, not real production projects.

| Area | Assessment |
|---|---|
| Backlot API and UI | Repaired live-library streaming and input compatibility; verified browser live refresh. Run Pipeline now launches a configured external agent or reports the missing setup without claiming a run started. |
| Work orders, checkpoints, approvals | Retain lease ownership, manifest order, artifact-bound approvals, and durable state. They protect against duplicate execution and stale approvals. Corrected HTTP conflict classification for human decisions. |
| Manifest executor and release scope | Preserve the explicit `screen-demo` and source-footage `talking-head` boundary. Schema-valid does not mean executable or certified. |
| Legacy teaching runner | Keep quarantined. It still supports intentional internal demo/voiceover-variant work; it cannot replace the generic executor. Deleted the separate unused script template from the server. |
| Provider tools and registry | Keep capability discovery and explicit choices. Local-only alias fixes remove identity drift. Do not expand certification to the entire catalog. |
| Media, caches, output promotion | Keep request identity, byte hashes, media probes, isolated run paths, candidate outputs, and atomic promotion. These are useful correctness mechanisms, not gratuitous complexity. |
| Composition | Type-check and bundle the retained Remotion tree. Preserve FFmpeg and existing runtime locks. Optional HyperFrames/GPU paths remain outside this review's runtime certification. |
| Packaging and dependencies | Removed five unused JavaScript dependencies through an offline lockfile update (17 lockfile package entries removed). Centralized source/wheel resource resolution and added a conventional virtualenv regression gate. |
| Observability and recovery | Keep application metrics, bounded labels, backup integrity, and offline drills. Real external metrics, alert delivery, edge security, rollback, and canary proofs remain absent. |
| Documentation and skills | Preserve one status ledger. Separate current fork launch claims from the much broader upstream capability descriptions. Avoid additional overlapping readiness documents as operating authorities. |

## Findings repaired in this patch

### P1 — Library event endpoint never returned its stream

`backlot/server.py` defined the generator for `/api/library/events` but omitted the `StreamingResponse`. The browser uses EventSource for automatic library refresh; a normal endpoint response could not provide that feed.

Returned the stream with event-stream and no-buffering headers. Also filtered changes using the configured project scope before emitting project identifiers. Added a behavioral async test for the response, scope filtering, event delivery, and subscription cleanup. In a running isolated Backlot instance, a project created via the API appeared in the already-open library without a reload.

### P2 — Compatibility aliases conflicted with implicit defaults

An older client sending only `tts_provider="open-ai"` collided with the model's default `voice_provider="edge_tts"`. Similarly, `profile="youtube_shorts"` collided with the default landscape profile. Neither request actually supplied two conflicting selections.

Use Pydantic's explicit-field set to distinguish omitted fields from user choices. Preserve rejection when both fields are explicitly supplied and disagree. New tests verify successful creation and persisted canonical selections.

### P2 — Human-decision conflicts became HTTP 400

The approve/revise handlers caught `WorkOrderValidationError` before its subclass `WorkOrderConflictError`, making their intended HTTP 409 branches unreachable. Catch the conflict first. Parameterized endpoint tests verify both human-decision routes return 409.

### Delete — Unreferenced production-script template

Removed `_PRODUCTION_SCRIPT_TEMPLATE`, approximately 273 lines of unused embedded Python in `backlot/server.py`. It had no caller, repeated a superseded workflow, hardcoded provider/runtime choices, attempted legacy approval bypasses, and contained invented render duration/size defaults. Removing it reduces misleading maintenance surface without removing a working production path. The separate quarantine launcher remains intact.

### Delete — Five unused direct JavaScript dependencies

Removed `@heyputer/puter.js`, `@remotion/player`, `d3-geo`, `topojson-client`, and `world-atlas` from the composer manifest and regenerated the lockfile using npm offline. Tracked application code has no imports or consumers for these packages. This is dependency reduction, not a claim that the remaining graph is vulnerability-free. The review did not perform an online vulnerability audit or a new clean container build.

### P3 — Undefined type annotation

Added the missing `typing.Any` import to `tools/video/hunyuan_video.py`. Deferred annotations concealed the issue during import, but type-hint evaluation and static checking encountered undefined names.

### P1 — Run Pipeline claimed work without starting an agent

The ordinary `/run` route used to claim a work order, return a manifest handoff, and let both Backlot buttons discard the useful execution details. That made the button look like a production start while no worker had been launched; the default `backlot-ui` lease could also block the real agent.

The route now treats an explicit `agent_id` as an already-running external agent and preserves the manifest handoff. A UI request without an agent id resolves `OPENMONTAGE_AGENT_COMMAND` and launches that trusted command with `shell=False`, a captured `agent.log`, durable `agent_process.json`, and project/run/stage identity in `OPENMONTAGE_*` variables. Missing or invalid configuration returns HTTP 503 before a fresh claim; a launch failure releases the lease for retry. Replays and competing callers return the existing run without spawning a duplicate. The library and board now surface launch status and structured setup errors.

No embedded fake executor was added. The repository remains agent-first: the configured command must point to the operator's actual coding/LLM agent or worker.

### P1 — Conventional wheel installs could not find config or Remotion

The wheel's root-level `data-files` are correctly installed below a virtual-environment prefix, but the application looked only beside `lib/` and `tools/`. `lib.paths.resource_path()` now searches an optional `OPENMONTAGE_RESOURCE_ROOT`, the source/package root, and `sys.prefix`; `runtime_root()` keeps writable projects and `.env` in the checkout or caller's working directory. Config loading, dotenv loading, Remotion/caption discovery, and HyperFrames vendor lookup use these helpers.

The package-data contract now builds and installs the wheel into a conventional virtualenv, runs outside the checkout with isolated imports, and verifies config, manifests, and Remotion resources resolve successfully.

## Remaining findings and next moves

### 1. Prove the configured agent against a real workflow

The launch boundary is now explicit and covered by contract tests, but this checkout has no external coding-agent command configured. Set `OPENMONTAGE_AGENT_COMMAND` to the actual local agent/worker, create a project from a clean browser session, and verify that the child process claims and heartbeats the same run, pauses at approval, resumes, and produces a playable deliverable. Keep the command in the operator's secret/runtime configuration rather than committing a machine-specific value.

Acceptance: the process recorded in `agent_process.json` is the real agent, the board shows its owner and current stage, and a missing command never implies production has started.

### 2. Publish and validate one coherent candidate

Preserve the ten unpublished commits and this review's patch together. Review the final diff, commit, push a candidate branch, and run supported Ubuntu CI against its exact SHA. Freeze the candidate only after the final patch passes. Do not cite an older successful run for a newer tree. Do not mechanically merge upstream or reset the customized fork.

Acceptance: one candidate SHA is shared by the branch, CI results, image digest, test outputs, and release record. This review is being published as one coherent candidate commit; CI and deployment proofs remain follow-up gates.

### 3. Prove the two workflows produce useful videos

Retain actual source fixtures, final media, hashes, runtime, cost, and an independent audiovisual review. Test real-capture and synthetic-terminal screen demos, plus supplied talking-head footage with transcription/captions/audio preserved. Test interruption, retry, resume, and an approval revision on the same candidate.

Measure useful outcomes: time from brief to acceptable video, operator intervention, failure/recovery rate, intelligible audio, readable captions, and cost per accepted deliverable. Test counts alone cannot measure any of these.

Acceptance: retained playable golden outputs and recorded review tied to the candidate, with no placeholder reports or inferred human approval.

### 4. Finish deployment proofs only on the intended deployment model

The existing `PR-10G` gate correctly remains blocked on real rollback (`REC-03`), trusted edge (`SEC-06`), durable external metrics (`OBS-02`), and external alert delivery (`OBS-03`). Then complete the human review and canary sequence in `PR-11G`.

Use established deployment/metrics/alerting services already chosen for the project; do not promote `tools/staging` test doubles into a custom production platform. If the actual first product is a single-operator local studio, explicitly scope a local release with applicable acceptance criteria instead of pretending it is a hosted service. That is a product/release-scope decision, not permission to silently weaken the current production gate.

Acceptance: environment-owned receipts, immutable deployment identity, restart/rollback state checks, and real operator acknowledgement where required. Keep simulations as integration tests.

### 5. Validate installed rendering on the intended deployment model

The resource lookup defect is repaired. The remaining release proof is an installed rendering smoke test: `pyproject.toml` still uses root-level `data-files`, so a wheel must be installed into a conventional virtual environment and exercised from outside the checkout. The new resolver now finds those files at the environment prefix; the prior isolated process had reported:

| Resource | At the code's `Lib/site-packages` root | At virtual-environment prefix |
|---|---|---|
| `config.yaml` | Missing | Present |
| `remotion-composer/package.json` | Missing | Present |

That lookup now passes in the conventional-install regression test. Checkout/container behavior remains a separate case.

Acceptance: install the wheel into a fresh conventional virtual environment, run from outside the checkout, load config/manifests/skills and the composer, and render a local fixture. Separate writable project/cache directories from installed resources. Add reproducible Python constraints for the supported deployment environment before release, without gratuitously upgrading providers.

### 6. Reduce maintenance surface after the product path works

- Put held workflows behind a secondary capability catalog. The create wizard currently displays long internal manifest descriptions, skill paths, and many disabled choices. Prefer two clear launch choices and short user-facing descriptions.
- Fix the voice menu and dashboard to reflect actual selected/configured providers instead of universally advertising Microsoft/Edge. Edge TTS is network-dependent even when it requires no API key.
- Keep the legacy demo runner isolated until a manifest-driven teaching workflow demonstrably replaces its useful behavior; then delete the replacement-obsolete code, not before.
- Audit duplicated `.agents`/`.claude` instruction bundles for a shared canonical source. Preserve tool compatibility and attribution. Their protected locations and different consumers make wholesale deletion inappropriate here.
- Replace source-string-only UI assertions with a small number of behavioral route/browser checks. The missing stream survived the existing large suite.
- Make `make lint` cover the first-party Python tree; currently it compiles only four files. The bounded Ruff scan used here found a missed undefined annotation without requiring a new architecture.

## Verification record

| Check | Result |
|---|---|
| Broad offline baseline: `python -m pytest tests -m "not live_provider and not hyperframes_qa" -q --tb=short` | **1,770 passed, 2 failed, 7 skipped, 3 deselected**, one warning and one passing subtest; 871.07 seconds. Started before the edits; this is a baseline, not an exact-final-tree CI certificate. |
| Broad-run failures | Both in `test_phase10_slos.py`: `PERF-01` provider-menu p95 **2.440s > 2.0s** and `PERF-04` duplicate-run p95 **0.544s > 0.5s**. Other review work ran concurrently, so these are contended workstation measurements. |
| Isolated SLO rerun | **6 passed**, one warning, 46.83 seconds. Thresholds unchanged. Passing in isolation does not erase the broad-run failures. |
| Final targeted API, live-feed, alias, conflict, quarantine and Backlot regression checks | **49 passed**, one warning, 62.08 seconds. Modules include `test_library_events.py`, `test_phase1_create_validation.py`, `test_phase1_demo_runner_quarantine.py`, `test_phase1_gate.py`, `test_phase2_run_idempotency.py`, `test_phase10_talking_head_executor.py`, `test_phase10_slos.py`, and `tests/backlot/test_server.py`. |
| Agent launcher and conventional wheel regression checks | **13 passed**, one warning, 43.17 seconds. Includes missing-command 503, a real short-lived process receiving launch identity, shell-free argv/metadata, lease release on launch failure, and a conventional virtualenv install executed outside the checkout. |
| Tracked Python syntax inventory | **611 Python files parsed** successfully. |
| First-party Ruff undefined-name/syntax scan | **Passed** with `F821,F822,F823,E9`. This is a bounded correctness scan, not a style or comprehensive security audit. |
| Remotion TypeScript | **Passed**, `tsc --noEmit`. |
| Remotion bundle | **Passed**; `tmp/review-remotion-bundle/index.html` created. Webpack reported an optional cache snapshot warning. Uses installed local dependencies; fresh `npm ci`/container validation remains for candidate CI. |
| Browser smoke | Library, loaded creation catalog, and unrefreshed live update verified at isolated `127.0.0.1:4765`; no production media or paid providers used. |
| Wheel build and conventional install | Installation succeeded; isolated conventional-virtualenv lookup **passed** for config, pipeline manifest, Remotion package data, and writable project-root selection. |
| Whitespace validation | `git diff --check` passed. |

Raw local logs are in `tmp/review-offline-tests.log`, `tmp/review-targeted-tests.log`, `tmp/review-isolated-slos.log`, `tmp/review-remotion-build.log`, `tmp/review-wheel-build.log`, and `tmp/review-wheel-install.log`. They are local review artifacts, not published release evidence. The installed FastAPI/Starlette test client emits an `httpx` deprecation warning; this review does not upgrade that dependency graph.

Local Windows results are diagnostic. Supported Ubuntu/container CI has not run against this patch, and no external infrastructure change or production certification was performed. A real external agent workflow remains to be exercised after `OPENMONTAGE_AGENT_COMMAND` is configured.
