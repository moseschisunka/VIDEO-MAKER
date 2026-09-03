"""PR-310 provider fault-injection corpus."""

from __future__ import annotations

from pathlib import Path

from lib.providers.contracts import ProviderError, ProviderErrorKind, ProviderRequest, ProviderResultStatus
from lib.providers.executor import (
    ProviderExecutor,
    ProviderMalformedResponseError,
    ProviderPartialOutputError,
    ProviderRateLimitError,
    ProviderTransientError,
)
from tests.contracts.test_phase3_provider_executor import _request


def test_429_and_5xx_failures_are_retryable_and_bounded(tmp_path: Path) -> None:
    for exception in (ProviderRateLimitError("slow down"), ProviderTransientError("upstream 503")):
        attempts = 0

        def operation(_request):
            nonlocal attempts
            attempts += 1
            raise exception

        result = ProviderExecutor(
            backoff_base_seconds=0,
            sleep_fn=lambda _seconds: None,
        ).execute(_request(tmp_path, key_suffix=f"fault-{attempts}", retries=2), operation)
        assert result.status is ProviderResultStatus.FAILED
        assert result.error is not None
        assert result.error.retryable is True
        assert attempts == 3


def test_malformed_identity_and_partial_output_are_never_success(tmp_path: Path) -> None:
    malformed = ProviderExecutor().execute(
        _request(tmp_path, key_suffix="malformed"),
        lambda _request: {
            "status": "success",
            "provider": "different-provider",
            "operation": "generate",
            "idempotency_key": "z" * 64,
        },
    )
    assert malformed.status is ProviderResultStatus.FAILED
    assert malformed.error is not None
    assert malformed.error.kind.value in {"malformed_response", "unknown"}

    partial = ProviderExecutor().execute(
        _request(tmp_path, key_suffix="partial"),
        lambda _request: {"ok": True},
        require_artifacts=True,
    )
    assert partial.status is ProviderResultStatus.FAILED
    assert partial.error is not None
    assert partial.error.kind.value == "partial_output"


def test_retryable_structured_result_is_retried(tmp_path: Path) -> None:
    attempts = 0
    request = _request(tmp_path, key_suffix="structured-retry", retries=1)

    def operation(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {
                "status": "failed",
                "provider": request.provider,
                "operation": request.operation,
                "idempotency_key": request.idempotency_key,
                "error": ProviderError(
                    code="429",
                    message="rate limited",
                    kind=ProviderErrorKind.RATE_LIMIT,
                    retryable=True,
                    status_code=429,
                ).to_dict(),
            }
        return {"ok": True}

    result = ProviderExecutor(backoff_base_seconds=0).execute(request, operation)
    assert result.status is ProviderResultStatus.SUCCESS
    assert result.attempt_count == 2


def test_duplicate_request_after_executor_restart_uses_persisted_result(tmp_path: Path) -> None:
    output = tmp_path / "render.mp4"
    output.write_bytes(b"valid-render")
    request = _request(tmp_path, key_suffix="restart")
    calls = 0

    def operation(_request):
        nonlocal calls
        calls += 1
        return {"artifacts": [str(output)], "actual_cost_usd": 0.0}

    cache = tmp_path / "provider-cache"
    first = ProviderExecutor(cache_dir=cache).execute(request, operation, require_artifacts=True)
    second = ProviderExecutor(cache_dir=cache).execute(request, operation, require_artifacts=True)
    assert first.status is ProviderResultStatus.SUCCESS
    assert second.status is ProviderResultStatus.CACHED
    assert calls == 1
