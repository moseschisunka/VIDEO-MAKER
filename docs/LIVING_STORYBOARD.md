# Backlot — The Living Storyboard Architecture & Design Contract

> **Core Philosophy**: Observation, not reporting. All state derives from the disk files that the agent and tools already write to `projects/<project-id>/`. The agent never updates the UI, sends heartbeat payloads, or manages socket state.

---

## 1. System Identity & Goals

Backlot is the local observer dashboard for OpenMontage. When an automated video production run begins, Backlot provides a live, visual filmstrip of the production lifecycle without interfering with the pipeline execution engine.

```
+--------------------------------------------------------------------------+
| Backlot UI (Browser)                                                     |
|                                                                          |
| [Stages Rail]   [Script Card]   [Filmstrip Cards]   [Decisions / Spend]  |
+--------------------------------------------------------------------------+
                                    ▲
                                    │ SSE / HTTP JSON
+--------------------------------------------------------------------------+
| Backlot Server (FastAPI / Starlette)                                     |
|                                                                          |
|   watches projects/<id>/ via watchfiles                                  |
|   derives state on-the-fly                                               |
+--------------------------------------------------------------------------+
                                    ▲
                                    │ File writes
+--------------------------------------------------------------------------+
| Agent Control Plane & Production Tools                                   |
|                                                                          |
|   projects/<id>/project.json                                             |
|   projects/<id>/work_order.json                                          |
|   projects/<id>/checkpoint_<stage>.json                                  |
|   projects/<id>/artifacts/*.json                                         |
|   projects/<id>/events.jsonl                                             |
|   projects/<id>/renders/*.mp4                                            |
+--------------------------------------------------------------------------+
```

---

## 2. Core Invariants

1. **Observation, Not Reporting**:
   * The pipeline agent has exactly one duty toward Backlot: execute `python -m backlot open <project-id>` at initialization.
   * If Backlot is not running, that command starts the server in the background and opens the user's browser.
   * If the command fails or Backlot cannot start, production continues without interruption—Backlot is an observer, never a blocker.

2. **Never Block, Never Break**:
   * Malformed, missing, or partially written files degrade gracefully in the UI.
   * Corrupted JSON or un-flushed files must never cause the server to crash or return a 500 error.

3. **Disk as Single Source of Truth**:
   * State is derived strictly from disk artifacts:

| UI Component | Disk Source | Derivation Logic |
|---|---|---|
| **Identity & Pipeline Rail** | `project.json` + `pipeline_defs/<type>.yaml` | Project title, target pipeline, stages sequence |
| **Execution State & Lease** | `work_order.json` | Manifest hash, run ID, selections, active lease, resume pointers |
| **Stage States & Gates** | `checkpoint_<stage>.json` + `history/` | `completed`, `in_progress`, `awaiting_human`, `failed` |
| **Script Display** | `artifacts/script.json` | Screenplay view with formatted sections and timing |
| **Filmstrip View** | `scene_plan` × `script` × `asset_manifest` | Scene cards populated with visuals, narration text, and duration |
| **Spend & Budget Meter** | Checkpoint `cost_snapshot` | Current stage cost vs project budget reserve |
| **Activity Feed** | `events.jsonl` | Append-only tool invocation logs with millisecond timestamps |
| **Final Deliverable** | `renders/*.mp4` | Video player for rendered output |

---

## 3. Human Approval Gates on the Board

When a gated stage finishes (e.g. `idea`, `script`, `scene_plan`, `assets`):
1. The checkpoint writer persists `status: "awaiting_human"` with an immutable artifact digest.
2. Backlot lights up the stage in amber ("Approval Required").
3. The board presents the stage artifact summary, quality review findings, and cost log.
4. When the user approves via the board, an immutable `approval_record` is written to the work order.

---

## 4. Replay System

Every completed run can be scrubbed end-to-end using the **Replay Run** control on the board:
* Checkpoints stored in `projects/<id>/history/` provide snapshots of previous states.
* Events recorded in `events.jsonl` provide exact timing to animate the filmstrip and progress indicators retrospectively.

---

## 5. Security & Isolation

* Backlot binds to `127.0.0.1:4750` by default.
* For container or remote deployments (`BACKLOT_HOST=0.0.0.0`), `BACKLOT_AUTH_REQUIRED=true` and a bearer token (`BACKLOT_AUTH_TOKEN`) are mandatory to prevent unauthorized access.
* Projects are strictly constrained to `PROJECTS_DIR` to prevent path traversal.
