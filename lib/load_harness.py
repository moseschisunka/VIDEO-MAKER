"""Deterministic, offline load and soak probes for the release gates.

The production-readiness load gate deliberately uses small local fixtures and
fake providers.  It exercises the same durable project, work-order, provider,
and SSE-boundary code used by the application without contacting a network or
creating billable work.  The helpers return JSON-serialisable evidence so CI
and operators can retain the raw measurement rather than only a pass/fail
assertion.
"""

from __future__ import annotations

import json
import tempfile
import time
import tracemalloc
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from lib.checkpoint import init_project
from lib.paths import run_paths
from lib.pipeline_loader import load_pipeline_readonly
from lib.providers.contracts import ProviderRequest
from lib.providers.executor import ProviderExecutor
from lib.work_order import (
    WorkOrderConflictError,
    build_work_order,
    claim_work_order,
    write_work_order,
)


DEFAULT_RUN_ID = "12345678-1234-4234-8234-123456789abc"
ORPHAN_MARKERS = (".tmp", ".part", ".lock")
SSE_QUEUE_LIMIT = 64


def _fixture_order(project_id: str, run_id: str) -> dict[str, Any]:
    """Build the smallest manifest-faithful queued order for load tests."""
    repo_root = Path(__file__).resolve().parents[1]
    manifest_path = repo_root / "pipeline_defs" / "screen-demo.yaml"
    manifest = load_pipeline_readonly("screen-demo", defs_dir=manifest_path.parent)
    return build_work_order(
        project_id=project_id,
        title=f"PR-1009 load fixture {project_id}",
        topic_prompt="Deterministic offline load fixture",
        target_duration_seconds=10,
        pipeline_type="screen-demo",
        manifest=manifest,
        manifest_path=manifest_path,
        selections={
            "playbook": "premium-minimalist",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "remotion",
            "output_profile": "youtube_landscape",
            "aspect_ratio": "16:9",
            "source_mode": "synthetic_terminal",
        },
        run_id=run_id,
    )


def orphan_inventory(root: Path | str) -> set[str]:
    """Return relative temporary/lock files under ``root``.

    The inventory is intentionally narrower than a generic hidden-file scan:
    durable dotfiles are allowed, while the temporary suffixes used by atomic
    writes, downloads, and renderer staging are treated as cleanup candidates.
    """
    base = Path(root).expanduser().resolve()
    if not base.exists():
        return set()
    result: set[str] = set()
    for item in base.rglob("*"):
        if not item.is_file():
            continue
        name = item.name.lower()
        if any(marker in name for marker in ORPHAN_MARKERS):
            result.add(item.relative_to(base).as_posix())
    return result


def run_concurrent_isolation_probe(
    root: Path | str,
    *,
    project_count: int = 4,
    workers: int = 4,
) -> dict[str, Any]:
    """Run independent work orders concurrently and verify run isolation."""
    if project_count < 1:
        raise ValueError("project_count must be positive")
    if workers < 1:
        raise ValueError("workers must be positive")
    if workers > project_count:
        raise ValueError("workers cannot exceed project_count for this probe")

    base = Path(root).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    fixtures: list[tuple[str, str, Path]] = []
    for index in range(project_count):
        project_id = f"pr1009-load-{index:02d}"
        run_id = str(uuid.uuid4())
        project = init_project(
            project_id,
            title=f"PR-1009 load {index}",
            pipeline_type="screen-demo",
            run_id=run_id,
            pipeline_dir=base,
        )
        write_work_order(project, _fixture_order(project_id, run_id))
        fixtures.append((project_id, run_id, project))

    started = time.perf_counter()
    tracemalloc.start()

    def execute_fixture(item: tuple[str, str, Path]) -> dict[str, Any]:
        project_id, run_id, project = item
        owner = f"pr1009-agent-{project_id[-2:]}"
        claimed = claim_work_order(project, owner, lease_seconds=60)
        paths = run_paths(project, run_id)
        marker = paths.work / "isolation-marker.json"
        marker.write_text(
            json.dumps({"project_id": project_id, "run_id": run_id, "owner": owner}) + "\n",
            encoding="utf-8",
        )
        observed = json.loads(marker.read_text(encoding="utf-8"))
        return {
            "project_id": project_id,
            "run_id": run_id,
            "owner": owner,
            "status": claimed["status"],
            "marker": observed,
            "run_root": str(paths.root),
        }

    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pr1009-load") as pool:
            results = list(pool.map(execute_fixture, fixtures))
        peak_memory = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    elapsed = time.perf_counter() - started

    project_ids = {item["project_id"] for item in results}
    run_ids = {item["run_id"] for item in results}
    contamination = [
        item
        for item in results
        if item["marker"].get("project_id") != item["project_id"]
        or item["marker"].get("run_id") != item["run_id"]
    ]
    return {
        "status": "PASS" if len(results) >= 4 and not contamination and len(project_ids) == len(results) and len(run_ids) == len(results) else "FAIL",
        "project_count": project_count,
        "workers": workers,
        "completed": len(results),
        "elapsed_seconds": round(elapsed, 6),
        "peak_python_memory_bytes": peak_memory,
        "contamination": contamination,
        "results": results,
    }


def run_same_project_queue_probe(root: Path | str, *, contenders: int = 8) -> dict[str, Any]:
    """Prove that one project has one live lease under concurrent claims."""
    base = Path(root).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    project_id = "pr1009-queue-fixture"
    run_id = str(uuid.uuid4())
    project = init_project(
        project_id,
        title="PR-1009 queue fixture",
        pipeline_type="screen-demo",
        run_id=run_id,
        pipeline_dir=base,
    )
    write_work_order(project, _fixture_order(project_id, run_id))

    def contender(index: int) -> dict[str, Any]:
        agent = f"pr1009-contender-{index:02d}"
        try:
            order = claim_work_order(project, agent, lease_seconds=60)
            return {"agent": agent, "outcome": "claimed", "owner": order["claim"]["claimed_by"]}
        except WorkOrderConflictError as exc:
            return {"agent": agent, "outcome": "conflict", "error": str(exc)}

    with ThreadPoolExecutor(max_workers=contenders, thread_name_prefix="pr1009-queue") as pool:
        outcomes = list(pool.map(contender, range(contenders)))
    winners = [item for item in outcomes if item["outcome"] == "claimed"]
    return {
        "status": "PASS" if len(winners) == 1 and len(outcomes) == contenders else "FAIL",
        "contenders": contenders,
        "winners": len(winners),
        "conflicts": len(outcomes) - len(winners),
        "outcomes": outcomes,
    }


def run_provider_throttle_probe(*, calls: int = 6, rate_per_second: float = 4.0) -> dict[str, Any]:
    """Measure deterministic fake-provider spacing without network or spend."""
    if calls < 2 or rate_per_second <= 0:
        raise ValueError("calls must be >= 2 and rate_per_second must be positive")
    current = [0.0]
    call_times: list[float] = []

    def clock() -> float:
        return current[0]

    def sleep(seconds: float) -> None:
        current[0] += max(0.0, float(seconds))

    executor = ProviderExecutor(
        clock=clock,
        sleep_fn=sleep,
        rate_limit_per_second=rate_per_second,
    )

    def request(index: int) -> ProviderRequest:
        return ProviderRequest(
            provider="fake",
            operation="load_probe",
            capability="video_post",
            payload={"index": index},
            project_id=f"pr1009-provider-{index:02d}",
            pipeline_type="screen-demo",
            run_id=DEFAULT_RUN_ID,
            attempt=1,
            stage="assets",
            idempotency_key=f"pr1009-throttle-{index:02d}",
            timeout_seconds=5,
            max_retries=0,
        )

    for index in range(calls):
        result = executor.execute(
            request(index),
            lambda _request: {"ok": True},
        )
        if not result.success:
            return {"status": "FAIL", "error": result.error.to_dict() if result.error else "provider failed"}
        call_times.append(current[0])
    interval = 1.0 / rate_per_second
    spacings = [right - left for left, right in zip(call_times, call_times[1:])]
    return {
        "status": "PASS" if all(value >= interval - 1e-9 for value in spacings) else "FAIL",
        "calls": calls,
        "rate_per_second": rate_per_second,
        "call_times": call_times,
        "minimum_spacing_seconds": min(spacings),
        "required_spacing_seconds": interval,
    }


def run_temporary_cleanup_probe(root: Path | str, *, iterations: int = 10) -> dict[str, Any]:
    """Run disposable temp workspaces and compare orphan inventories."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    base = Path(root).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    before = orphan_inventory(base)
    for index in range(iterations):
        with tempfile.TemporaryDirectory(prefix=f"pr1009-{index:02d}-", dir=base) as raw:
            workspace = Path(raw)
            (workspace / f".{index}.stage.tmp").write_bytes(b"temporary stage")
            (workspace / "download.part").write_bytes(b"partial download")
            (workspace / "render.lock").write_text("held", encoding="utf-8")
    after = orphan_inventory(base)
    added = sorted(after - before)
    return {
        "status": "PASS" if not added and after == before else "FAIL",
        "iterations": iterations,
        "before_orphans": sorted(before),
        "after_orphans": sorted(after),
        "added_orphans": added,
    }


def run_sse_stability_probe(*, subscriptions: int = 100, burst: int = 200) -> dict[str, Any]:
    """Exercise bounded ChangeHub queues across repeated connect/disconnect."""
    if subscriptions < 1 or burst < 1:
        raise ValueError("subscriptions and burst must be positive")
    from backlot.server import ChangeHub

    event_hub = ChangeHub()
    baseline = event_hub.subscriber_count()
    max_queue = 0
    dropped = 0
    for _index in range(subscriptions):
        queue = event_hub.subscribe("pr1009-sse-project")
        for _ in range(burst):
            event_hub.publish("pr1009-sse-project")
            event_hub.publish("unrelated-project")
        max_queue = max(max_queue, queue.qsize())
        dropped += max(0, burst - queue.qsize())
        event_hub.unsubscribe(queue)
    final = event_hub.subscriber_count()
    return {
        "status": "PASS" if max_queue <= SSE_QUEUE_LIMIT and final == baseline else "FAIL",
        "subscriptions": subscriptions,
        "burst": burst,
        "queue_limit": SSE_QUEUE_LIMIT,
        "max_queue": max_queue,
        "dropped_burst_events": dropped,
        "baseline_subscribers": baseline,
        "final_subscribers": final,
    }


def run_all_probes(root: Path | str, *, workers: int = 4, iterations: int = 10) -> dict[str, Any]:
    """Run all PR-1009 offline probes and return one evidence document."""
    base = Path(root).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results = {
        "PERF-08": run_concurrent_isolation_probe(base / "projects", workers=workers),
        "QUEUE": run_same_project_queue_probe(base / "queue"),
        "PROVIDER-THROTTLE": run_provider_throttle_probe(),
        "PERF-09": run_temporary_cleanup_probe(base / "temporary", iterations=iterations),
        "PERF-10": run_sse_stability_probe(),
    }
    return {
        "schema_version": "1.0",
        "gate": "PR-1009",
        "offline_only": True,
        "network_calls": 0,
        "paid_provider_calls": 0,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "results": results,
        "status": "PASS" if all(item.get("status") == "PASS" for item in results.values()) else "FAIL",
    }


__all__ = [
    "DEFAULT_RUN_ID",
    "ORPHAN_MARKERS",
    "SSE_QUEUE_LIMIT",
    "orphan_inventory",
    "run_all_probes",
    "run_concurrent_isolation_probe",
    "run_provider_throttle_probe",
    "run_same_project_queue_probe",
    "run_sse_stability_probe",
    "run_temporary_cleanup_probe",
]
