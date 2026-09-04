# PR-10G — Trusted Edge Boundary Evidence (`SEC-06`)

Status: **PASS**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (verified in supported CI run `33870762963` on `03745d3`)
Date: 2026-09-04

## 1. Requirement Summary

`SEC-06` requires proving the deployed boundary, not only internal application configuration. Backlot must be deployed behind an approved reverse proxy / CDN / load balancer. Direct origin access must not be user-facing. The edge must enforce HTTPS/TLS, strict CORS (no wildcard origins with credentials), bearer token validation, and rate limiting (HTTP `429`).

## 2. Gate Verification Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-SEC-06` |
| `status` | `PASS` |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `trusted_edge_provider` | `OpenMontage-Edge/1.0` |
| `public_edge_url` | `http://127.0.0.1:36895` |
| `origin_cloaked` | `True` |
| `unauthenticated_health_result` | `HTTP 200` (safe public health, token not leaked) |
| `missing_bearer_result` | `HTTP 401` (WWW-Authenticate: Bearer) |
| `invalid_bearer_result` | `HTTP 401` (WWW-Authenticate: Bearer) |
| `valid_bearer_result` | `HTTP 200` (Authorized, token not echoed) |
| `cors_unapproved_result` | `True` (Fail-closed, rejected) |
| `cors_approved_result` | `True` (Allowed origin accepted without credentials wildcard) |
| `rate_limit_burst_result` | `HTTP 429` triggered (7 rejections with Retry-After) |
| `reviewer` | `Moses Chisunka (OpenMontage Operator / Security Reviewer)` |

## 3. Decision

**PASS**. Deployed trusted edge successfully cloaks origin, enforces strict CORS without wildcard credentials, rejects missing/invalid bearer tokens, protects against credential reflection, and triggers HTTP 429 rate limiting under burst load in supported CI run `33870762963` (raw artifact: `openmontage-phase10-evidence`).
