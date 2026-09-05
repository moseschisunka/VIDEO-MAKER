# PR-10G — Trusted Edge Boundary Evidence (`SEC-06`)

Status: **SIMULATED INTEGRATION PASS / DEPLOYED TRUSTED EDGE PENDING**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (verified in supported CI run `33870762963` on `03745d3` and `33871877480` on `fe1d73a`)
Date: 2026-09-04

## 1. Requirement Summary

`SEC-06` requires proving the deployed boundary, not only internal application configuration. Backlot must be deployed behind an approved reverse proxy / CDN / load balancer. Direct origin access must not be user-facing. The edge must enforce HTTPS/TLS, strict CORS (no wildcard origins with credentials), bearer token validation, and rate limiting (HTTP `429`).

> [!WARNING]
> **Audit Finding (2026-09-04)**: The test harness `scripts/run_staging_operational_proofs.py` executed `tools/staging/staging_edge_proxy.py` bound to `127.0.0.1`. While this verified reverse-proxy logic (origin cloaking, CORS rejection, token validation, and HTTP 429 burst rate limiting), it was executed locally in CI rather than in front of a live deployed infrastructure target with real TLS certificates.

## 2. Simulated Staging Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-SEC-06` |
| `status` | `SIMULATED_PASS` (deployed trusted edge pending) |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `simulated_edge_provider` | `OpenMontage-Edge/1.0` (localhost test double) |
| `simulated_edge_url` | `http://127.0.0.1:36895` |
| `origin_cloaked` | `Not established for a deployed origin` (local proxy simulation only) |
| `unauthenticated_health_result` | `HTTP 200` (safe public health, token not leaked) |
| `missing_bearer_result` | `HTTP 401` (WWW-Authenticate: Bearer) |
| `invalid_bearer_result` | `HTTP 401` (WWW-Authenticate: Bearer) |
| `valid_bearer_result` | `HTTP 200` (Authorized, token not echoed) |
| `cors_unapproved_result` | `True` (Fail-closed, rejected) |
| `cors_approved_result` | `True` (Allowed origin accepted without credentials wildcard) |
| `rate_limit_burst_result` | `HTTP 429` triggered (7 rejections with Retry-After) |

## 3. Required Real Deployment Evidence for Gate Closure

To transition `SEC-06` to `PASS`:
1. Deploy Backlot behind an approved cloud reverse proxy or CDN (e.g. Cloudflare, Envoy, Cloud Armor, or NGINX ingress).
2. Exercise the public HTTPS endpoint and record non-loopback network telemetry.
3. Retain public CORS rejection and real `429` rate-limit responses with sanitized request headers.
