"""PR-301--PR-304/PR-308 contracts for the deterministic provider kernel."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lib.providers.contracts import (
    ProviderArtifact,
    ProviderRequest,
    ProviderResult,
    ProviderResultStatus,
    stable_idempotency_key,
)
from lib.providers.fallback import classify_fallback, fallback_plan
from lib.providers.executor import (
    ProviderExecutor,
    ProviderPermanentError,
    ProviderTransientError,
)


def _request(tmp_path: Path, *, key_suffix: str = "one", cost: float = 0.0, approved: bool = True, retries: int = 0) -> ProviderRequest:
    payload = {"prompt": "fixture", "key_suffix": key_suffix}
    return ProviderRequest(
        capability="image_generation",
        operation="generate",
        provider="fake-provider",
        model="fake-v1",
        payload=payload,
        idempotency_key=stable_idempotency_key(
            provider="fake-provider",
            model="fake-v1",
            capability="image_generation",
            operation="generate",
            payload=payload,
        ),
        timeout_seconds=1,
        max_retries=retries,
        estimated_cost_usd=cost,
        approved=approved,
    )


def test_success_is_cached_without_reinvoking_provider(tmp_path: Path) -> None:
    calls = 0
    output = tmp_path / "asset.png"
    output.write_bytes(b"valid-image")
    request = _request(tmp_path)
    executor = ProviderExecutor(cache_dir=tmp_path / "cache")

    def operation(_request: ProviderRequest):
        nonlocal calls
        calls += 1
        return ProviderResult.success(
            request,
            artifacts=[ProviderArtifact(path=str(output), sha256=None, size_bytes=output.stat().st_size)],
            actual_cost_usd=0,
        )

    first = executor.execute(request, operation, require_artifacts=True)
    second = executor.execute(request, operation, require_artifacts=True)

    assert first.status is ProviderResultStatus.SUCCESS
    assert second.status is ProviderResultStatus.CACHED
    assert calls == 1
    assert second.metadata["cache_hit"] is True


def test_retryable_failure_uses_bounded_backoff_and_succeeds(tmp_path: Path) -> None:
    attempts = 0
    sleeps: list[float] = []
    request = _request(tmp_path, retries=2)
    executor = ProviderExecutor(
        sleep_fn=sleeps.append,
        random_fn=lambda: 1.0,
        backoff_base_seconds=0.25,
    )

    def operation(_request: ProviderRequest):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderTransientError("temporary upstream failure")
        return {"asset_id": "ok"}

    result = executor.execute(request, operation)
    assert result.status is ProviderResultStatus.SUCCESS
    assert result.attempt_count == 2
    assert sleeps == [pytest.approx(0.25)]


def test_timeout_returns_structured_failure_without_waiting_for_provider(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request = ProviderRequest(**{**request.to_dict(), "timeout_seconds": 0.02})
    executor = ProviderExecutor()
    started = time.monotonic()

    result = executor.execute(request, lambda _request: time.sleep(0.25))

    elapsed = time.monotonic() - started
    assert result.status is ProviderResultStatus.FAILED
    assert result.error is not None
    assert result.error.kind.value == "timeout"
    assert elapsed < 0.15


def test_circuit_breaker_blocks_after_repeated_provider_failures(tmp_path: Path) -> None:
    executor = ProviderExecutor(circuit_failure_threshold=2, circuit_cooldown_seconds=60)
    operation = lambda _request: (_ for _ in ()).throw(ProviderPermanentError("downstream unavailable"))

    first = executor.execute(_request(tmp_path, key_suffix="a"), operation)
    second = executor.execute(_request(tmp_path, key_suffix="b"), operation)
    third = executor.execute(_request(tmp_path, key_suffix="c"), operation)

    assert first.status is ProviderResultStatus.FAILED
    assert second.status is ProviderResultStatus.FAILED
    assert third.status is ProviderResultStatus.BLOCKED
    assert third.error is not None
    assert third.error.kind.value == "circuit_open"


def test_rate_limit_spaces_calls_per_provider(tmp_path: Path) -> None:
    current = [0.0]
    call_times: list[float] = []

    def clock() -> float:
        return current[0]

    def sleep(seconds: float) -> None:
        current[0] += seconds

    executor = ProviderExecutor(
        clock=clock,
        sleep_fn=sleep,
        rate_limit_per_second=2,
    )

    def operation(_request: ProviderRequest):
        call_times.append(current[0])
        return {"ok": True}

    executor.execute(_request(tmp_path, key_suffix="rate-a"), operation)
    executor.execute(_request(tmp_path, key_suffix="rate-b"), operation)
    assert call_times == [pytest.approx(0.0), pytest.approx(0.5)]


class _FakeCostTracker:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def approve_tool(self, tool: str) -> None:
        self.events.append(("approve", tool))

    def estimate(self, tool: str, operation: str, amount: float) -> str:
        self.events.append(("estimate", tool, operation, amount))
        return "entry-1"

    def reserve(self, entry_id: str) -> None:
        self.events.append(("reserve", entry_id))

    def reconcile(self, entry_id: str, amount: float, *, success: bool) -> None:
        self.events.append(("reconcile", entry_id, amount, success))


def test_cost_is_reserved_and_reconciled_once_per_request(tmp_path: Path) -> None:
    tracker = _FakeCostTracker()
    executor = ProviderExecutor(cost_tracker=tracker)
    request = _request(tmp_path, cost=0.04, approved=True)

    result = executor.execute(request, lambda _request: {"actual_cost_usd": 0.03})

    assert result.status is ProviderResultStatus.SUCCESS
    assert result.actual_cost_usd == pytest.approx(0.03)
    assert result.reserved_cost_usd == 0
    assert tracker.events == [
        ("approve", "fake-provider"),
        ("estimate", "fake-provider", "generate", 0.04),
        ("reserve", "entry-1"),
        ("reconcile", "entry-1", 0.03, True),
    ]


def test_paid_request_without_approval_is_blocked_before_provider_call(tmp_path: Path) -> None:
    request = _request(tmp_path, cost=0.01, approved=False)
    called = False

    def operation(_request: ProviderRequest):
        nonlocal called
        called = True
        return {}

    result = ProviderExecutor().execute(request, operation)
    assert result.status is ProviderResultStatus.BLOCKED
    assert result.error is not None
    assert result.error.kind.value == "approval_required"
    assert called is False


def test_material_fallback_requires_approval_and_unavailable_blocks(tmp_path: Path) -> None:
    calls = []
    request = ProviderRequest(
        capability="video_generation",
        operation="generate",
        provider="provider-a",
        payload={"prompt": "motion"},
        idempotency_key="e" * 64,
        fallback_class="material_provider_change",
        estimated_cost_usd=0,
        approved=False,
    )
    result = ProviderExecutor().execute(request, lambda _request: calls.append(True))
    assert result.status is ProviderResultStatus.BLOCKED
    assert result.error is not None
    assert result.error.kind.value == "approval_required"
    assert calls == []

    unavailable = classify_fallback(
        original_provider="provider-a",
        replacement_provider="provider-b",
        available=False,
    )
    assert unavailable.value == "unavailable"
    plan = fallback_plan(
        {"provider": "provider-a", "media_type": "video"},
        [{"provider": "provider-b", "media_type": "video", "available": True}],
    )
    assert plan["alternatives"][0]["requires_approval"] is True
    assert plan["execution"] == "blocked_until_approval"
