"""Run the offline PR-10G operator drills.

The drills exercise the failure-handling paths that documentation alone cannot
prove: provider throttling/circuit opening, stuck-run cancellation and
manifest-derived restart, corrupt-artifact rejection, and bearer-token
rotation.  They use only temporary state and fake providers; no network,
credentials, or paid operation is used.

Usage::

    python scripts/run_operations_drill.py --output pr10g-operations-drill.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backlot import server as server_mod  # noqa: E402
from lib.alerting import evaluate_alerts, load_alert_config  # noqa: E402
from lib.providers.contracts import (  # noqa: E402
    ProviderArtifact,
    ProviderRequest,
    ProviderResult,
    ProviderResultStatus,
    ProviderErrorKind,
    stable_idempotency_key,
)
from lib.providers.executor import (  # noqa: E402
    ProviderExecutor,
    ProviderRateLimitError,
)


DRILL_RUN_ID = "12345678-1234-4234-8234-123456789abc"


def _request(*, provider: str, operation: str, capability: str, key: str, retries: int = 0) -> ProviderRequest:
    payload = {"drill": "pr-10g", "operation": operation, "key": key}
    return ProviderRequest(
        capability=capability,
        operation=operation,
        provider=provider,
        model="offline-fake-v1",
        payload=payload,
        idempotency_key=stable_idempotency_key(
            provider=provider,
            model="offline-fake-v1",
            capability=capability,
            operation=operation,
            payload=payload,
        ),
        project_id="pr10g-operations-drill",
        pipeline_type="screen-demo",
        run_id=DRILL_RUN_ID,
        attempt=1,
        stage="operations-drill",
        timeout_seconds=2,
        max_retries=retries,
        estimated_cost_usd=0,
        approved=True,
    )


def _provider_outage_drill() -> dict[str, Any]:
    """Prove bounded 429 retry and circuit-open behavior with a fake provider."""

    calls = 0
    events: list[dict[str, Any]] = []
    executor = ProviderExecutor(
        circuit_failure_threshold=2,
        circuit_cooldown_seconds=60,
        backoff_base_seconds=0.01,
        backoff_max_seconds=0.01,
        random_fn=lambda: 1.0,
        sleep_fn=lambda _seconds: None,
        event_sink=events.append,
    )

    def throttled(_request: ProviderRequest) -> Any:
        nonlocal calls
        calls += 1
        raise ProviderRateLimitError(
            "offline fake provider throttled",
            details={"retry_after_seconds": 1},
        )

    first = executor.execute(
        _request(
            provider="offline-fake-provider",
            operation="generate",
            capability="image_generation",
            key="outage-first",
            retries=1,
        ),
        throttled,
    )
    second = executor.execute(
        _request(
            provider="offline-fake-provider",
            operation="generate",
            capability="image_generation",
            key="outage-second",
        ),
        throttled,
    )
    first_kind = first.error.kind.value if first.error else None
    second_kind = second.error.kind.value if second.error else None
    passed = (
        first.status is ProviderResultStatus.FAILED
        and first_kind == ProviderErrorKind.RATE_LIMIT.value
        and first.attempt_count == 2
        and second.status is ProviderResultStatus.BLOCKED
        and second_kind == ProviderErrorKind.CIRCUIT_OPEN.value
        and calls == 2
        and any(event.get("event") == "backoff" for event in events)
        and not any(event.get("event") == "fallback" for event in events)
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "first_status": first.status.value,
        "first_error_kind": first_kind,
        "first_attempts": first.attempt_count,
        "second_status": second.status.value,
        "second_error_kind": second_kind,
        "provider_calls": calls,
        "event_count": len(events),
    }


def _corrupt_artifact_drill(root: Path) -> dict[str, Any]:
    """Prove a zero-byte provider artifact cannot be accepted or promoted."""

    root.mkdir(parents=True, exist_ok=True)
    candidate = root / "broken-candidate.mp4"
    candidate.touch()
    final = root / "final.mp4"
    request = _request(
        provider="offline-fake-runtime",
        operation="render",
        capability="video_render",
        key="corrupt-artifact",
    )

    def malformed_success(_request: ProviderRequest) -> ProviderResult:
        return ProviderResult.success(
            request,
            artifacts=[ProviderArtifact(path=str(candidate), size_bytes=0)],
        )

    result = ProviderExecutor().execute(
        request,
        malformed_success,
        require_artifacts=True,
    )
    error_code = result.error.code if result.error else None
    passed = (
        result.status is ProviderResultStatus.FAILED
        and error_code == "provider_partial_output"
        and candidate.is_file()
        and candidate.stat().st_size == 0
        and not final.exists()
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "result_status": result.status.value,
        "error_code": error_code,
        "candidate_preserved": candidate.is_file(),
        "candidate_size_bytes": candidate.stat().st_size,
        "final_promoted": final.exists(),
    }


@contextmanager
def _with_local_backlot(projects: Path):
    """Install a temporary Backlot root and disable its filesystem watcher."""

    original_projects = server_mod.PROJECTS_DIR
    original_root = server_mod._PROJECTS_ROOT_STR
    original_watch = server_mod._watch_projects

    async def no_watch() -> None:
        return None

    projects.mkdir(parents=True, exist_ok=True)
    server_mod.PROJECTS_DIR = projects
    server_mod._PROJECTS_ROOT_STR = str(projects.resolve()).lower()
    server_mod._watch_projects = no_watch
    try:
        yield
    finally:
        server_mod.PROJECTS_DIR = original_projects
        server_mod._PROJECTS_ROOT_STR = original_root
        server_mod._watch_projects = original_watch


def _stuck_job_drill(projects: Path, corrupt_root: Path) -> dict[str, Any]:
    """Exercise duplicate-claim conflict, cancel, and manifest-derived restart."""

    reclaimed = None
    with _with_local_backlot(projects):
        with TestClient(server_mod.create_app()) as client:
            created = client.post(
                "/api/project/create",
                json={
                    "project_id": "pr10g-operations-drill",
                    "title": "PR-10G operations drill",
                    "topic_prompt": "offline operator drill",
                    "pipeline_type": "screen-demo",
                    "source_mode": "synthetic_terminal",
                    "target_duration_seconds": 10,
                },
            )
            if created.status_code != 200:
                return {"status": "FAIL", "step": "create", "http_status": created.status_code}
            project_id = created.json()["project_id"]
            claimed = client.post(
                f"/api/project/{project_id}/claim",
                json={"agent_id": "drill-owner", "lease_seconds": 60},
            )
            conflict = client.post(
                f"/api/project/{project_id}/claim",
                json={"agent_id": "drill-recovery", "lease_seconds": 60},
            )
            cancelled = client.post(
                f"/api/project/{project_id}/cancel",
                json={"agent_id": "drill-owner", "reason": "PR-10G offline stuck-job drill"},
            )
            restarted = client.post(
                f"/api/project/{project_id}/restart",
                json={"agent_id": "drill-recovery", "reason": "PR-10G offline recovery drill"},
            )
            if restarted.status_code == 200:
                reclaimed = client.post(
                    f"/api/project/{project_id}/claim",
                    json={"agent_id": "drill-recovery", "lease_seconds": 60},
                )

    corrupt = _corrupt_artifact_drill(corrupt_root)
    restarted_order = restarted.json().get("work_order", {}) if restarted.is_success else {}
    reclaimed_order = reclaimed.json().get("work_order", {}) if reclaimed is not None and reclaimed.is_success else {}
    passed = (
        claimed.status_code == 200
        and conflict.status_code == 409
        and cancelled.status_code == 200
        and cancelled.json().get("status") == "cancelled"
        and restarted.status_code == 200
        and restarted_order.get("status") == "queued"
        and restarted_order.get("next_stage") == "idea"
        and reclaimed is not None
        and reclaimed.status_code == 200
        and reclaimed_order.get("status") == "running"
        and corrupt["status"] == "PASS"
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "claim_http_status": claimed.status_code,
        "duplicate_claim_http_status": conflict.status_code,
        "cancel_http_status": cancelled.status_code,
        "cancel_status": cancelled.json().get("status") if cancelled.is_success else None,
        "restart_http_status": restarted.status_code,
        "restart_status": restarted_order.get("status"),
        "restart_next_stage": restarted_order.get("next_stage"),
        "reclaim_http_status": reclaimed.status_code if reclaimed is not None else None,
        "reclaim_status": reclaimed_order.get("status"),
        "corrupt_artifact": corrupt,
    }


def _secret_rotation_drill() -> dict[str, Any]:
    """Prove that rotating the runtime bearer token revokes the old token."""

    names = ("BACKLOT_HOST", "BACKLOT_AUTH_REQUIRED", "BACKLOT_AUTH_TOKEN")
    previous = {name: os.environ.get(name) for name in names}
    os.environ["BACKLOT_HOST"] = "0.0.0.0"
    os.environ["BACKLOT_AUTH_REQUIRED"] = "1"
    os.environ["BACKLOT_AUTH_TOKEN"] = "old-drill-token"
    try:
        async def no_watch() -> None:
            return None

        original_watch = server_mod._watch_projects
        server_mod._watch_projects = no_watch
        try:
            with TestClient(server_mod.create_app()) as client:
                old_response = client.get(
                    "/api/health",
                    headers={"Authorization": "Bearer old-drill-token"},
                )
                os.environ["BACKLOT_AUTH_TOKEN"] = "new-drill-token"
                revoked_response = client.get(
                    "/api/health",
                    headers={"Authorization": "Bearer old-drill-token"},
                )
                new_response = client.get(
                    "/api/health",
                    headers={"Authorization": "Bearer new-drill-token"},
                )
        finally:
            server_mod._watch_projects = original_watch
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    old_text = old_response.text
    revoked_text = revoked_response.text
    new_text = new_response.text
    passed = (
        old_response.status_code == 200
        and revoked_response.status_code == 401
        and new_response.status_code == 200
        and "old-drill-token" not in old_text + revoked_text + new_text
        and "new-drill-token" not in old_text + revoked_text + new_text
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "old_token_before_rotation": old_response.status_code == 200,
        "old_token_after_rotation": revoked_response.status_code,
        "new_token_after_rotation": new_response.status_code == 200,
        "response_secret_free": "PASS" if passed else "FAIL",
    }


def _alert_delivery_drill() -> dict[str, Any]:
    """Evaluate P0/P1 evidence and deliver it to an in-process fake sink."""

    alerts = evaluate_alerts(
        load_alert_config(),
        events=[
            {"event": "auth_failure", "ts": 1_000.0},
            {"event": "auth_failure", "ts": 1_001.0},
            {"event": "auth_failure", "ts": 1_002.0},
            {"event": "circuit_open", "ts": 1_003.0},
        ],
        metrics_snapshot={
            "histograms": [{"name": "openmontage_queue_wait_seconds", "p95": 6.0}],
        },
        now=1_010.0,
    )
    delivered: list[dict[str, Any]] = []

    def fake_sink(alert: dict[str, Any]) -> None:
        delivered.append({key: alert[key] for key in ("rule_id", "severity", "action", "observed")})

    for alert in alerts:
        fake_sink(alert)
    ids = {item["rule_id"] for item in delivered}
    passed = (
        {"unauthorized-access-burst", "provider-circuit-open", "queue-start-latency"} <= ids
        and len(delivered) == len(alerts)
        and all(item["severity"] in {"P0", "P1"} for item in delivered)
        and all(item["action"] in {"page", "notify"} for item in delivered)
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "delivered_count": len(delivered),
        "rule_ids": sorted(ids),
        "severities": sorted({item["severity"] for item in delivered}),
        "sink": "in_process_fake_no_network",
    }


def run_operations_drill() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="openmontage-pr10g-") as raw_root:
        root = Path(raw_root)
        provider = _provider_outage_drill()
        stuck = _stuck_job_drill(root / "projects", root / "corrupt")
        rotation = _secret_rotation_drill()
        alerting = _alert_delivery_drill()
    drills = {
        "provider_outage": provider,
        "stuck_and_corrupt_job": stuck,
        "secret_rotation": rotation,
        "alerting": alerting,
    }
    return {
        "schema_version": "1.0",
        "status": "PASS" if all(item["status"] == "PASS" for item in drills.values()) else "FAIL",
        "mode": "offline_fake_no_network_no_spend",
        "python": platform.python_version(),
        "drills": drills,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PR-10G offline operational drills")
    parser.add_argument("--output", type=Path, help="write JSON evidence to this path")
    args = parser.parse_args()
    evidence = run_operations_drill()
    encoded = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
