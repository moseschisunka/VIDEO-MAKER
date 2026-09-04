"""Staging Trusted Edge Reverse Proxy for SEC-06 operational verification.

Implements the edge boundary defined in config/security_policy.yaml:
- Origin cloaking: clients connect only to the edge listener.
- Strict CORS: fail-closed on unapproved origins; exact allow-list; no credentials with wildcard.
- Bearer authentication enforcement: public safe /api/health; 401 on missing/invalid token.
- Edge burst rate limiting: token-bucket rate limiter returning HTTP 429 with Retry-After.
"""

from __future__ import annotations

import collections
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Mapping, Sequence
from urllib.parse import urlsplit
import requests


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class EdgeRateLimiter:
    """Thread-safe token-bucket rate limiter keyed by client IP."""

    def __init__(self, burst_capacity: int = 5, refill_rate_per_sec: float = 2.0):
        self.burst_capacity = burst_capacity
        self.refill_rate = refill_rate_per_sec
        self.buckets: dict[str, float] = collections.defaultdict(lambda: float(burst_capacity))
        self.last_refill: dict[str, float] = collections.defaultdict(time.time)
        self.lock = threading.Lock()

    def acquire(self, client_ip: str) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_refill[client_ip]
            self.last_refill[client_ip] = now
            # Refill tokens up to burst capacity
            self.buckets[client_ip] = min(
                float(self.burst_capacity),
                self.buckets[client_ip] + elapsed * self.refill_rate,
            )
            if self.buckets[client_ip] >= 1.0:
                self.buckets[client_ip] -= 1.0
                return True, 0.0
            else:
                needed = 1.0 - self.buckets[client_ip]
                retry_after = max(1.0, needed / self.refill_rate)
                return False, retry_after


class StagingEdgeProxy:
    """Manages an in-process or background threaded reverse proxy server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8443,
        upstream_url: str = "http://127.0.0.1:4750",
        auth_token: str = "staging-secret-token",
        allowed_origins: Sequence[str] = ("https://studio.openmontage.internal",),
        rate_limit_burst: int = 5,
        rate_limit_refill: float = 2.0,
    ):
        self.host = host
        self.port = port
        self.upstream_url = upstream_url.rstrip("/")
        self.auth_token = auth_token
        self.allowed_origins = set(allowed_origins)
        self.rate_limiter = EdgeRateLimiter(rate_limit_burst, rate_limit_refill)
        self.server: ThreadedHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.actual_port: int = port

    def start(self) -> int:
        parent = self

        class ProxyHandler(BaseHTTPRequestHandler):
            server_version = "OpenMontage-Edge/1.0"
            sys_version = ""

            def log_message(self, format: str, *args: object) -> None:
                # Suppress noisy standard HTTP access logs
                pass

            def _handle_cors(self) -> bool:
                """Return True if CORS policy passed; False if rejected."""
                origin = self.headers.get("Origin")
                if not origin:
                    return True
                if origin in parent.allowed_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Request-ID")
                    self.send_header("Access-Control-Max-Age", "86400")
                    # Policy: allow_credentials is false, never allow wildcard credentials
                    return True
                else:
                    # Fail-closed: unapproved origins get no allow-origin headers
                    return False

            def do_OPTIONS(self) -> None:
                origin = self.headers.get("Origin")
                if origin and origin not in parent.allowed_origins:
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"detail":"CORS origin forbidden by edge policy"}')
                    return

                self.send_response(204)
                self._handle_cors()
                self.end_headers()

            def _check_rate_limit(self) -> bool:
                client_ip = self.client_address[0]
                allowed, retry_after = parent.rate_limiter.acquire(client_ip)
                if not allowed:
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Retry-After", str(int(retry_after)))
                    self.send_header("X-RateLimit-Remaining", "0")
                    self._handle_cors()
                    self.end_headers()
                    payload = json.dumps({
                        "detail": "Too Many Requests: edge rate limit exceeded",
                        "retry_after_seconds": int(retry_after),
                    }).encode("utf-8")
                    self.wfile.write(payload)
                    return False
                return True

            def _forward(self, method: str) -> None:
                if not self._check_rate_limit():
                    return

                path = self.path
                parsed = urlsplit(path)

                # Public unauthenticated health check exception
                is_public_health = (parsed.path == "/api/health" and method == "GET")

                auth_header = self.headers.get("Authorization", "")
                if not is_public_health:
                    if not auth_header or not auth_header.startswith("Bearer "):
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("WWW-Authenticate", "Bearer")
                        self._handle_cors()
                        self.end_headers()
                        self.wfile.write(b'{"detail":"Missing or malformed Bearer token"}')
                        return

                    token = auth_header[7:].strip()
                    if token != parent.auth_token:
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("WWW-Authenticate", "Bearer")
                        self._handle_cors()
                        self.end_headers()
                        self.wfile.write(b'{"detail":"Invalid Bearer token"}')
                        return

                # Read body if present
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else None

                # Forward upstream
                target_url = f"{parent.upstream_url}{path}"
                forward_headers: dict[str, str] = {}
                for k, v in self.headers.items():
                    if k.lower() not in {"host", "content-length"}:
                        forward_headers[k] = v

                # For public health, edge provides internal upstream auth
                if is_public_health and not forward_headers.get("Authorization"):
                    forward_headers["Authorization"] = f"Bearer {parent.auth_token}"

                forward_headers["X-Forwarded-For"] = self.client_address[0]
                forward_headers["X-Forwarded-Proto"] = "https"
                forward_headers["X-Forwarded-Host"] = self.headers.get("Host", f"{parent.host}:{parent.actual_port}")

                try:
                    upstream_resp = requests.request(
                        method=method,
                        url=target_url,
                        headers=forward_headers,
                        data=body,
                        timeout=10,
                        allow_redirects=False,
                    )
                except Exception as exc:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self._handle_cors()
                    self.end_headers()
                    self.wfile.write(json.dumps({"detail": f"Bad Gateway: {exc}"}).encode("utf-8"))
                    return

                # Send upstream response back
                self.send_response(upstream_resp.status_code)
                for hk, hv in upstream_resp.headers.items():
                    # Filter internal/hop-by-hop headers
                    if hk.lower() not in {"server", "transfer-encoding", "content-encoding", "content-length"}:
                        self.send_header(hk, hv)

                self._handle_cors()
                resp_bytes = upstream_resp.content

                # Defense-in-depth: assert no bearer token is reflected in response
                if parent.auth_token.encode("utf-8") in resp_bytes:
                    resp_bytes = resp_bytes.replace(parent.auth_token.encode("utf-8"), b"[REDACTED_SECRET]")

                self.send_header("Content-Length", str(len(resp_bytes)))
                self.end_headers()
                self.wfile.write(resp_bytes)

            def do_GET(self) -> None:
                self._forward("GET")

            def do_POST(self) -> None:
                self._forward("POST")

            def do_PUT(self) -> None:
                self._forward("PUT")

            def do_DELETE(self) -> None:
                self._forward("DELETE")

        self.server = ThreadedHTTPServer((self.host, self.port), ProxyHandler)
        self.actual_port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        for _ in range(50):
            try:
                with socket.create_connection((self.host, self.actual_port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.02)

        return self.actual_port

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            self.thread = None
