# PR-1G — Phase 1 integration gate

**Date:** 2026-09-02
**Gate status:** `COMPLETE` — Phase 1 exit conditions verified
**Reviewer:** OpenMontage execution agent (independent gate pass)
**Production status:** `INELIGIBLE` until `PR-11G`

## Gate decision

Phase 1 is complete for its defined contract boundary. The catalog, create
request, work-order, quarantine, identity, agent-contract, and thin-vertical
checks agree on one important truth: only the deliberately supported
`screen-demo`/`talking-head` launch candidates are creation-enabled, every
visible pipeline remains explicitly non-production, and held lanes expose their
incomplete runtime/agent contracts instead of pretending to execute.

The ordinary Backlot `/run` path now returns a manifest-derived agent handoff
for the certified `screen-demo` lane. It does not launch the generic
`lib.project_pipeline` runner. The legacy process is reachable only from an
explicit internal-demo marker used by its quarantined fixture. A golden
screen-demo project traverses the actual manifest stages, pauses at the human
gate, resumes after explicit approval, renders a new local FFmpeg output with
project-relative assets, and publishes through the durable work order.

## Exit-condition evidence

| Phase 1 condition | Evidence | Result |
|---|---|---|
| 100% of UI-visible manifests are schema-valid and honestly matured | `test_phase1_gate.py`, `test_phase1_agent_contract.py`, canonical catalog output | Pass; held lanes remain discoverable but creation-disabled and non-production |
| Rejected create requests leave no state | `test_phase1_create_validation.py` | Pass; all invalid/held selection cases leave the projects directory empty |
| Work order preserves selections and derives stages from the selected manifest | `test_phase1_work_order.py`, `test_phase1_claim_resume.py`, `test_phase1_gate.py` | Pass |
| Generic demo runner cannot execute unrelated pipelines | `test_phase1_demo_runner_quarantine.py`, `test_phase1_gate.py` | Pass; ordinary `/run` uses `execution_mode=manifest_agent` and `Popen` is not called |
| One supported pipeline completes a manifest-faithful golden path | `test_phase1_manifest_execution.py`, [`PR-109`](PR-109.md) | Pass; current output is probed and final work order is `completed` |

## Verification

Commands run from the repository root:

```text
python -m pytest -q tests/contracts/test_phase1_gate.py
2 passed, 9 warnings in 2.46s

python -m pytest -q tests/contracts/test_phase1_agent_contract.py tests/contracts/test_phase1_create_validation.py tests/contracts/test_phase1_manifest_validation.py tests/contracts/test_phase1_manifest_metadata.py tests/contracts/test_phase1_pipeline_catalog.py tests/backlot/test_server.py tests/tools/test_video_compose_vertical.py tests/tools/test_documentary_governance.py
56 passed, 105 warnings in 120.21s

python -m pytest -q tests/contracts --disable-warnings
824 passed, 6 skipped, 1 failed in 259.17s

python -m py_compile lib/work_order.py lib/manifest_executor.py lib/project_identity.py lib/checkpoint.py lib/events.py lib/project_pipeline.py backlot/server.py tools/video/video_compose.py
pass
```

The one full-contract failure is the pre-existing provider catalog expectation
drift in `tests/contracts/test_phase3_contracts.py::TestCapabilityMetadata::test_registry_catalog_views`:
the registry currently includes configured `azure`, `edge_tts`, and
`fish_audio` providers in addition to the test's stale expected set. It is
mapped to `PR-300`/`PR-306`/`PR-309`, was not weakened, and does not invalidate
the Phase 1-specific exit conditions. It remains an overall release blocker.

## Phase 1 residual risks carried forward

Provider execution/idempotency and cost controls, run-scoped staging and
stale-output prevention, media probing, grounded content, voice verification,
asset provenance, HyperFrames parity, QA fault coverage, packaging, security,
observability, recovery, and canary certification are still open roadmap work.
The phase gate therefore advances the program to `PR-200`; it does not change
the global release label.

Production cannot be declared until every later phase gate and `PR-11G` pass.
