# PR-2G — Phase 2 integration gate

Status: **COMPLETE**

## Gate decision

Phase 2 passes its local integration gate. The supported `screen-demo` golden
path has durable run identity, isolated workspaces, fresh/probed output
promotion, idempotent `/run`, explicit lifecycle recovery, and concurrency /
crash evidence. Production is still not eligible: the global `PR-11G` lock
remains active and later phases are required.

## Evidence

```text
python -m pytest -q tests/contracts/test_phase2_run_record.py tests/contracts/test_phase2_run_idempotency.py tests/contracts/test_phase2_run_isolation.py tests/contracts/test_phase2_output_promotion.py tests/contracts/test_phase2_stale_output.py tests/contracts/test_phase2_media_probe.py tests/contracts/test_phase2_faults.py tests/contracts/test_phase1_claim_resume.py tests/contracts/test_phase1_manifest_execution.py tests/tools/test_remotion_diagnostics.py tests/contracts/test_phase1_gate.py
32 passed in 29.16s

python -m pytest -q tests/tools/test_hyperframes_compose.py
45 passed in 165.67s

python -m py_compile lib/output_promotion.py lib/work_order.py lib/run_record.py lib/manifest_executor.py backlot/server.py tools/video/video_compose.py tools/video/hyperframes_compose.py
git diff --check
```

## Conditions carried forward

- Provider execution, cost, retry, capability honesty, and fault contracts are
  still Phase 3 work (`PR-300`–`PR-310`).
- No paid provider call may be treated as production-ready until `PR-3G`.
- Final production certification remains blocked until every roadmap phase and
  `PR-11G` pass.
