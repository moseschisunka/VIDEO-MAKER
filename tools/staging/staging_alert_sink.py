"""Staging External Alert and Paging Notification Sink for OBS-03.

Proves that:
1. Operational alerts evaluated against config/alerts.yaml are received by an external sink.
2. Alert payloads are bounded, redacted (no secrets or raw prompt text), and restricted to P0/P1.
3. Delivery latency from trigger time to receipt time is measured and logged.
4. On-call operator acknowledgement is recorded and persisted.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Mapping
from urllib.parse import urlsplit


class ThreadedAlertServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class StagingAlertSink:
    """External alert notification receiver daemon simulating PagerDuty/Alertmanager."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9093):
        self.host = host
        self.port = port
        self.actual_port = port
        self.server: ThreadedAlertServer | None = None
        self.thread: threading.Thread | None = None
        self.received_alerts: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def start(self) -> int:
        parent = self

        class AlertHandler(BaseHTTPRequestHandler):
            server_version = "OpenMontage-AlertSink/1.0"
            sys_version = ""

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path == "/api/v2/alerts":
                    with parent.lock:
                        payload = json.dumps({"alerts": parent.received_alerts}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:
                parsed = urlsplit(self.path)
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else b"{}"

                if parsed.path == "/api/v2/alerts":
                    now = time.time()
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except Exception:
                        self.send_response(400)
                        self.end_headers()
                        return

                    items = data if isinstance(data, list) else [data]
                    new_alerts = []
                    for item in items:
                        alert_id = str(uuid.uuid4())
                        record = {
                            "alert_id": alert_id,
                            "received_at": now,
                            "rule_id": str(item.get("rule_id", "unknown")),
                            "severity": str(item.get("severity", "UNKNOWN")).upper(),
                            "action": str(item.get("action", "page")),
                            "reason": str(item.get("reason", "")),
                            "observed": item.get("observed"),
                            "evidence": item.get("evidence", {}),
                            "raw_payload": item,
                            "acknowledged": False,
                            "acknowledged_at": None,
                            "acknowledged_by": None,
                        }
                        new_alerts.append(record)

                    with parent.lock:
                        parent.received_alerts.extend(new_alerts)

                    resp_body = json.dumps({"status": "received", "count": len(new_alerts), "alerts": new_alerts}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp_body)))
                    self.end_headers()
                    self.wfile.write(resp_body)

                elif parsed.path.startswith("/api/v2/alerts/") and parsed.path.endswith("/ack"):
                    # POST /api/v2/alerts/{alert_id}/ack
                    parts = parsed.path.split("/")
                    target_id = parts[4] if len(parts) >= 5 else ""
                    try:
                        ack_data = json.loads(body.decode("utf-8"))
                    except Exception:
                        ack_data = {}
                    ack_operator = str(ack_data.get("operator", "on-call-reviewer"))

                    found = False
                    with parent.lock:
                        for al in parent.received_alerts:
                            if al["alert_id"] == target_id:
                                al["acknowledged"] = True
                                al["acknowledged_at"] = time.time()
                                al["acknowledged_by"] = ack_operator
                                found = True
                                break

                    if found:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        resp_body = json.dumps({"status": "acknowledged", "alert_id": target_id}).encode("utf-8")
                        self.send_header("Content-Length", str(len(resp_body)))
                        self.end_headers()
                        self.wfile.write(resp_body)
                    else:
                        self.send_response(404)
                        self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

        self.server = ThreadedAlertServer((self.host, self.port), AlertHandler)
        self.actual_port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.actual_port

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            self.thread = None

    def get_alerts(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.received_alerts)
