# PR-6G — Phase 6 integration gate

Status: **COMPLETE**

The mixed-media gate assembles user footage, licensed stock, AI output, and a
diagram with current-run hashes/probes and provenance, binds candidates to a
manifest-hashed approval, and verifies every approved asset appears in the
edit decisions. Static motion, corrupt bytes, missing license data, unsafe
user paths, and unapproved batch work fail closed. The global production
decision remains locked until later phases and `PR-11G` pass.

Evidence command:

```text
python -m pytest -q tests/contracts/test_phase6_gate.py
7 passed in 0.93s
```
