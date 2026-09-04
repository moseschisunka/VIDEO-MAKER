# PR-10G — Trusted Edge Boundary Evidence (`SEC-06`)

Status: **BLOCKED**
Candidate Commit: `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` (short `b9aa08a`)
Date: 2026-09-04

## 1. Requirement Summary

`SEC-06` requires proving the deployed boundary, not only internal application configuration. Backlot must be deployed behind an approved reverse proxy / CDN / load balancer. Direct origin access must not be user-facing. The edge must enforce HTTPS/TLS, strict CORS (no wildcard origins with credentials), bearer token validation, and rate limiting (HTTP `429`).

## 2. Gate Verification Parameters

| Field | Value |
|---|---|
| `gate_id` | `PR-10G-SEC-06` |
| `status` | `BLOCKED` |
| `candidate_sha` | `b9aa08a8b5c3dcc95d5b7473bdb1ab003b0f3c9e` |
| `trusted_edge_provider` | *Pending operator input (`TRUSTED_EDGE`)* |
| `public_edge_url` | *Pending operator input* |
| `origin_address` | *Pending operator input (must not be directly reachable by users)* |
| `tls_configuration` | *Pending edge certificate / policy inspection* |
| `unauthenticated_health_result` | — |
| `missing_bearer_result` | — (Expected: HTTP 401) |
| `invalid_bearer_result` | — (Expected: HTTP 401) |
| `valid_bearer_result` | — (Expected: HTTP 200, no token reflected) |
| `cors_options_result` | — (Expected: fail-closed, no wildcard allowed origins) |
| `rate_limit_burst_result` | — (Expected: HTTP 429 with Retry-After) |
| `reviewer` | *Pending named security/operations reviewer* |

## 3. Execution Procedure (To be executed via public edge endpoint)

1. Deploy candidate behind approved reverse proxy/load balancer with remote binding enabled (`BACKLOT_HOST=0.0.0.0`).
2. Test unauthenticated `/api/health` and verify safe public health metadata.
3. Attempt control-plane and project API requests without Authorization header (verify HTTP 401).
4. Attempt requests with malformed/invalid bearer token (verify HTTP 401).
5. Attempt request with valid bearer token (verify HTTP 200, verify bearer token is never echoed in response body or headers).
6. Send cross-origin preflight requests (`OPTIONS`) from unapproved origin (verify origin is rejected).
7. Issue high-frequency burst traffic to trigger rate limiting at the trusted edge (verify HTTP 429 and standard rate-limit headers).
8. Record redacted headers and response payloads.

## 4. Current Blocker Statement

The application-level fail-closed security contracts pass in supported CI. However, `SEC-06` strictly mandates testing through the actual deployed edge infrastructure. Because edge routing, reverse proxy configuration, and public endpoints have not been provided by the operator, this gate remains blocked.
