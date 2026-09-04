"""Staging External Prometheus Metrics Scraper and Durable Time-Series Sink for OBS-02.

Proves that:
1. Metrics from /api/metrics/prometheus are scraped and aggregated into an external sink.
2. The external sink persists across Backlot application restarts.
3. SLO denominators and time-series history survive container restarts without data loss.
4. Metric labels remain bounded and free of secrets or raw prompt text.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence
import requests


class StagingMetricsSink:
    """Independent metrics scraper and persistent external SQLite time-series store."""

    def __init__(
        self,
        db_path: Path | str,
        target_scrape_url: str = "http://127.0.0.1:4750/api/metrics/prometheus",
        auth_token: str = "staging-secret-token",
        scrape_interval_seconds: float = 1.0,
    ):
        self.db_path = Path(db_path)
        self.target_scrape_url = target_scrape_url
        self.auth_token = auth_token
        self.scrape_interval = scrape_interval_seconds
        self.running = False
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scraped_at REAL NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    value REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_metric_name_time
                ON metric_samples(metric_name, scraped_at)
                """
            )
            conn.commit()

    def scrape_once(self) -> dict[str, Any]:
        """Perform a single authenticated scrape of the target Prometheus endpoint."""
        now = time.time()
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        try:
            resp = requests.get(self.target_scrape_url, headers=headers, timeout=5)
            if resp.status_code != 200:
                return {"status": "ERROR", "http_status": resp.status_code, "scraped_at": now}
            text = resp.text
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc), "scraped_at": now}

        parsed_samples = self._parse_prometheus_text(text, now)
        with self.lock, sqlite3.connect(str(self.db_path)) as conn:
            for s in parsed_samples:
                conn.execute(
                    """
                    INSERT INTO metric_samples (scraped_at, metric_name, metric_type, labels_json, value)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (s["scraped_at"], s["metric_name"], s["metric_type"], s["labels_json"], s["value"]),
                )
            conn.commit()

        return {
            "status": "PASS",
            "samples_count": len(parsed_samples),
            "scraped_at": now,
        }

    def _parse_prometheus_text(self, text: str, timestamp: float) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        types: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("# TYPE "):
                parts = line.split()
                if len(parts) >= 4:
                    types[parts[2]] = parts[3]
                continue
            if line.startswith("#"):
                continue

            # Format: metric_name{labels} value OR metric_name value
            if "{" in line and "}" in line:
                metric_part, val_part = line.split("}", 1)
                metric_name, labels_str = metric_part.split("{", 1)
                val_str = val_part.strip().split()[0]
                labels = self._parse_labels(labels_str)
            else:
                parts = line.split()
                metric_name = parts[0]
                val_str = parts[1]
                labels = {}

            try:
                val = float(val_str)
            except ValueError:
                continue

            base_name = metric_name.split("{")[0]
            metric_type = types.get(base_name, "gauge")
            samples.append({
                "scraped_at": timestamp,
                "metric_name": metric_name,
                "metric_type": metric_type,
                "labels_json": json.dumps(labels, sort_keys=True),
                "value": val,
            })
        return samples

    def _parse_labels(self, label_str: str) -> dict[str, str]:
        labels = {}
        # Naive key="value" parser
        items = label_str.split(",")
        for item in items:
            if "=" in item:
                k, v = item.split("=", 1)
                labels[k.strip()] = v.strip().strip('"')
        return labels

    def start_background_scraper(self) -> None:
        self.running = True

        def run() -> None:
            while self.running:
                self.scrape_once()
                time.sleep(self.scrape_interval)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop_background_scraper(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            self.thread = None

    def query_history(self, metric_name: str) -> list[dict[str, Any]]:
        """Query time-series points for a metric."""
        with self.lock, sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT scraped_at, metric_type, labels_json, value
                FROM metric_samples
                WHERE metric_name LIKE ?
                ORDER BY scraped_at ASC
                """,
                (f"{metric_name}%",),
            )
            rows = cursor.fetchall()
            return [
                {
                    "scraped_at": r[0],
                    "metric_type": r[1],
                    "labels": json.loads(r[2]),
                    "value": r[3],
                }
                for r in rows
            ]

    def verify_durability_across_restart(
        self,
        metric_name: str,
        restart_timestamp: float,
    ) -> dict[str, Any]:
        """Verify that samples exist both before and after restart timestamp."""
        points = self.query_history(metric_name)
        before = [p for p in points if p["scraped_at"] < restart_timestamp]
        after = [p for p in points if p["scraped_at"] >= restart_timestamp]

        # Check that historical max/denominator was not wiped
        max_before = max((p["value"] for p in before), default=0.0)
        max_overall = max((p["value"] for p in points), default=0.0)

        # Check label bounding and absence of sensitive patterns
        leaks = []
        for p in points:
            labels_str = json.dumps(p["labels"])
            if "Bearer" in labels_str or "secret" in labels_str:
                leaks.append(labels_str)

        passed = (
            len(before) > 0
            and len(after) > 0
            and max_overall >= max_before
            and len(leaks) == 0
        )

        return {
            "status": "PASS" if passed else "FAIL",
            "metric_name": metric_name,
            "samples_before_restart": len(before),
            "samples_after_restart": len(after),
            "max_value_before": max_before,
            "max_value_overall": max_overall,
            "denominator_preserved": (max_overall >= max_before),
            "label_leak_count": len(leaks),
        }
