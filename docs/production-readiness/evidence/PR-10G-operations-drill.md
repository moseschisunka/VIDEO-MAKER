# PR-10G offline operations-drill evidence

Status: **PASS for the bounded offline/fake drill; not a Phase 10 gate pass**

## Scope

This evidence covers the locally executable portions of the Phase 10 operator
procedures. It uses temporary project state, a fake provider, and fake runtime
artifacts. It makes no network request, uses no credentials, and incurs no
provider spend.

## Exact verification

From the repository root:

```text
python scripts/run_operations_drill.py --output docs/production-readiness/evidence/PR-10G-operations-drill.json
python -m pytest tests/contracts/test_phase10_operations_drill.py tests/contracts/test_phase10_runbooks.py -q
```

Result: **PASS**. The contract run reported `5 passed` (Python 3.13.2; the
FastAPI/httpx deprecation warnings are non-failing environment warnings).

## Drill results

| Drill | Result | Proof |
|---|---|---|
| Provider outage/throttle | PASS | fake HTTP-429 classified as `rate_limit`, exactly two bounded attempts, then a new request was blocked by `circuit_open`; no fallback was selected |
| Stuck/duplicate job | PASS | first claim succeeded, competing claim returned HTTP 409, cancellation preserved state, restart returned the manifest-derived `idea` stage, and recovery reclaim returned `running` |
| Corrupt/partial artifact | PASS | zero-byte candidate returned `provider_partial_output`, remained preserved for evidence, and no final artifact was promoted |
| Secret rotation | PASS | old token worked before rotation, returned HTTP 401 after rotation, new token worked, and neither token appeared in response bodies |
| P0/P1 alert evaluation | PASS (fake sink) | authentication burst, provider circuit, and queue-latency signals produced three bounded page records with only P0/P1 severities |

Raw machine-readable output is attached at
[`PR-10G-operations-drill.json`](PR-10G-operations-drill.json).

## Limitations and remaining blockers

The identical offline/fake drill also passed in supported GitHub Actions run
`33710765514` under Python 3.11. It produced the same bounded provider outage,
stuck/corrupt job, secret-rotation, and three-rule alert-evaluation evidence.
The exact supported result is committed at
[`PR-10G-operations-drill-ci.json`](PR-10G-operations-drill-ci.json).

This is not evidence of a real deployment rollback, live-provider recovery, or
external alert delivery. The Linux-reference SLO and clean Remotion build are
now supported-CI passes, but those external controls remain required for
`PR-10G`; the release lock remains `PR-11G`.
