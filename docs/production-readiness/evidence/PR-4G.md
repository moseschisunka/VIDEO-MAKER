# PR-4G — Phase 4 integration gate

Status: **COMPLETE**

Grounded claim traceability, hardcoded-topic quarantine, canonical timing,
profile/aspect propagation, and factual evaluation are integrated. The gate
uses fresh local FFmpeg outputs at 15s, 30s, and 60s, probes them, and checks
their profile dimensions, frame rate, and measured duration against the
canonical tolerance. A 300-second long-form plan is also covered by the
long-form ±3% contract. The global release decision remains locked until all
later phases and `PR-11G` pass.

Evidence command:

```text
python -m pytest -q tests/contracts/test_phase4_gate.py tests/eval/test_phase4_grounding_eval.py tests/contracts/test_phase4_grounding.py tests/contracts/test_phase4_timing.py
22 passed in 7.10s
```

Cross-phase regression: the Phase 2/3/4 and manifest identity slice passed
`174 tests` in `24.49s` after the BaseTool provider-lane compatibility fix.
