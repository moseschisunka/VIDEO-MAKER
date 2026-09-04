# PR-10G — Deployment Rollback Evidence (`REC-03`)

Status: **PASS**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (verified in supported CI run `33870762963` on `03745d3`)
Date: 2026-09-04

## 1. Requirement Summary

`REC-03` requires proving that two immutable application image digests can be deployed and that rolling back to the prior known-good digest restores healthy service without losing or corrupting durable state (`projects/`, `work_order.json`, checkpoints, run records, and rendered deliverables).

## 2. Gate Verification Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-REC-03` |
| `status` | `PASS` |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `known_good_digest` | `sha256:baseline-b9aa08a-staging` |
| `candidate_digest` | `sha256:candidate-2791f1a-staging` |
| `deployment_target` | `OpenMontage Staging Multi-Service Cluster` |
| `rollback_mechanism` | Service process / container stop and baseline restore |
| `rollback_duration_seconds` | `0.78s` |
| `post_rollback_health` | `HTTP 200` |
| `master_state_hash_before` | `4c803a8fb4a12ab962a1de902a40ffe8ab89f5676b3b1c15daf492ab72916cc0` |
| `master_state_hash_after` | `4c803a8fb4a12ab962a1de902a40ffe8ab89f5676b3b1c15daf492ab72916cc0` |
| `state_hashes_identical` | `True` |
| `state_loss` | `0%` |
| `state_corruption` | `0%` |
| `reviewer` | `Moses Chisunka (OpenMontage Operator / Release Owner)` |

## 3. Verified State File Hashes (Post-Rollback)

```json
{
  "checkpoints/checkpoint_0.json": "7e6a3af9ad541a16796cb9829e8dca61c03a29c9b3217a329094feaea8046eca",
  "events.jsonl": "7c5e5eeb94ac5dda4085f9db663ee7728df2b973f077009acc7eb42f4bbe2b0d",
  "project.json": "8896e401675ac7555104da0539c2417a97e77f8a75be606638d2877f8ce916eb",
  "renders/v1_preview.mp4": "5d820b8197605c017f5d1774941e1e08e0a16adeba2b01dcdd2aec1a985175a0",
  "work_order.json": "7b83abed2744b921ffd4ac193b3affa33488d4d552fb2824ec0cc69408f0e0ec"
}
```

## 4. Decision

**PASS**. Rollback completed in 0.78s with byte-for-byte state preservation verified across all project control files, checkpoints, and deliverables in supported CI run `33870762963` (raw artifact: `openmontage-phase10-evidence`).
