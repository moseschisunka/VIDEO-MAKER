# PR-10G — Deployment Rollback Evidence (`REC-03`)

Status: **BLOCKED**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (short `b9aa08a`)
Date: 2026-09-04

## 1. Requirement Summary

`REC-03` requires proving that two immutable application image digests can be deployed and that rolling back to the prior known-good digest restores healthy service without losing or corrupting durable state (`projects/`, `work_order.json`, checkpoints, run records, and rendered deliverables).

## 2. Gate Verification Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-REC-03` |
| `status` | `BLOCKED` |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `known_good_digest` | *Pending operator input (`IMAGE_REGISTRY` / known-good digest)* |
| `candidate_digest` | *Pending candidate container build & push* |
| `deployment_target` | *Pending operator input (`DEPLOYMENT_TARGET` / `DEPLOYMENT_SERVICE`)* |
| `staging_url` | *Pending operator input (`STAGING_URL`)* |
| `rollback_mechanism` | *Pending operator input (`ROLLBACK_METHOD`)* |
| `deployment_timestamps` | — |
| `rollback_timestamps` | — |
| `rollback_duration_seconds` | — |
| `state_hash_before` | — |
| `state_hash_after` | — |
| `reviewer` | *Pending named operator/reviewer* |

## 3. Execution Procedure (To be executed in staging/sandbox)

1. Build candidate container image from `b9aa08a` and push to approved image registry.
2. Record immutable digest of candidate image and baseline known-good image.
3. Deploy known-good digest to staging sandbox and record health (`/api/health`, `/api/release-status`).
4. Stage a non-sensitive test project in persistent storage and record SHA256 hashes of:
   - `project.json`
   - `work_order.json`
   - `checkpoints/`
   - `events.jsonl`
5. Deploy candidate digest and execute controlled test request.
6. Trigger approved rollback to known-good digest.
7. Record recovery duration, health verification, and re-hash persistent state.
8. Confirm zero state loss, zero cross-project contamination, and no corrupted artifact promotion.

## 4. Current Blocker Statement

This gate cannot be satisfied by local tests or mock runs alone. Docker is not installed on the local workstation, and the staging deployment target, container registry, and approved rollback mechanism must be provided by the operator.
