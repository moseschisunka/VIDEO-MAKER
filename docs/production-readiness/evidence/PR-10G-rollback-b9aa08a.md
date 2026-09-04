# PR-10G — Deployment Rollback Evidence (`REC-03`)

Status: **SIMULATED INTEGRATION PASS / REAL DEPLOYMENT ROLLBACK PENDING**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (verified in supported CI run `33870762963` on `03745d3` and `33871877480` on `fe1d73a`)
Date: 2026-09-04

## 1. Requirement Summary

`REC-03` requires proving that two immutable application image digests can be deployed and that rolling back to the prior known-good digest restores healthy service without losing or corrupting durable state (`projects/`, `work_order.json`, checkpoints, run records, and rendered deliverables).

> [!WARNING]
> **Audit Finding (2026-09-04)**: The test harness `scripts/run_staging_operational_proofs.py` verified directory-level rollback mechanics inside a local temporary directory using simulated digest strings (`sha256:baseline-b9aa08a-staging`, `sha256:candidate-2791f1a-staging`). These are descriptive test tokens, not registry-generated immutable image digests. This proves integration logic, but does NOT satisfy real environment-owned deployment rollback.

## 2. Simulated Staging Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-REC-03` |
| `status` | `SIMULATED_PASS` (real deployment rollback pending) |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `simulated_baseline_digest` | `sha256:baseline-b9aa08a-staging` (local test token) |
| `simulated_candidate_digest` | `sha256:candidate-2791f1a-staging` (local test token) |
| `deployment_target` | Localhost temporary directory simulation |
| `rollback_mechanism` | Local subprocess restart and temporary directory state swap |
| `rollback_duration_seconds` | `0.78s` |
| `post_rollback_health` | `HTTP 200` |
| `master_state_hash_before` | `4c803a8fb4a12ab962a1de902a40ffe8ab89f5676b3b1c15daf492ab72916cc0` |
| `master_state_hash_after` | `4c803a8fb4a12ab962a1de902a40ffe8ab89f5676b3b1c15daf492ab72916cc0` |
| `state_hashes_identical` | `True` |
| `state_loss` | `0%` |
| `state_corruption` | `0%` |

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

## 4. Required Real Deployment Evidence for Gate Closure

To transition `REC-03` to `PASS`:
1. Provide actual image registry digests (e.g. `ghcr.io/moseschisunka/openmontage@sha256:...`).
2. Provide a deployment record from a container orchestration platform (Kubernetes / Cloud Run / ECS / Docker Swarm).
3. Record real service revision rollback timing and post-rollback health checks against a deployed external service.
