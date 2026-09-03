# PR-6G — Phase 6 integration gate

Status: **COMPLETE**

The mixed-media gate assembles user footage, licensed stock, AI output, and a
diagram with current-run hashes/probes and provenance, binds candidates to a
manifest-hashed approval, and verifies every approved asset appears in the
edit decisions. Static motion, corrupt bytes, missing license data, unsafe
user paths, and unapproved batch work fail closed. The global production
decision remains locked until later phases and `PR-11G` pass.

## Historical evidence note

The original Phase 6 gate evidence above remains valid for the cases it
covered. A later strict-ingestion review found that ffprobe-readable media with
zero/non-finite duration or a mismatched declared stream could still be
accepted. Follow-up [`PR-1017`](PR-1017.md) adds the regression coverage and
fix; Phase 6 is intentionally reopened until that checkpoint is verified in
supported CI.

Evidence command:

```text
python -m pytest -q tests/contracts/test_phase6_gate.py
7 passed in 0.93s
```
