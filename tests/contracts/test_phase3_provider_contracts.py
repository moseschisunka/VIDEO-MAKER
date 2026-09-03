"""PR-300 provider request/result contract tests."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import jsonschema
import pytest

from lib.providers.contracts import (
    ProviderArtifact,
    ProviderContractError,
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResult,
    ProviderResultStatus,
    REQUEST_SCHEMA_PATH,
    RESULT_SCHEMA_PATH,
    stable_idempotency_key,
)


def test_request_and_result_are_structured_and_schema_valid() -> None:
    run_id = str(uuid.uuid4())
    request = ProviderRequest(
        capability="tts",
        operation="generate",
        provider="elevenlabs",
        model="eleven_multilingual_v2",
        payload={"text": "hello", "voice_id": "Rachel"},
        idempotency_key=stable_idempotency_key(
            provider="elevenlabs",
            model="eleven_multilingual_v2",
            capability="tts",
            operation="generate",
            payload={"text": "hello", "voice_id": "Rachel"},
        ),
        project_id="provider-contracts",
        pipeline_type="screen-demo",
        run_id=run_id,
        attempt=1,
        stage="voice",
        timeout_seconds=30,
        max_retries=2,
        estimated_cost_usd=0.002,
        approved=True,
    )
    result = ProviderResult.success(
        request,
        artifacts=[ProviderArtifact(path="assets/audio/voice.mp3", artifact_type="audio")],
        attempt_count=1,
        latency_ms=42.5,
        actual_cost_usd=0.0018,
        provenance={"project_id": request.project_id, "run_id": run_id, "stage": "voice"},
    )

    request_schema = json.loads(REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    result_schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(request.to_dict(), request_schema, format_checker=jsonschema.FormatChecker())
    jsonschema.validate(result.to_dict(), result_schema, format_checker=jsonschema.FormatChecker())
    assert result.to_dict()["attempt_count"] == 1
    assert result.to_dict()["actual_cost_usd"] == pytest.approx(0.0018)


def test_failed_result_requires_structured_error() -> None:
    request = ProviderRequest(
        capability="image_generation",
        operation="generate",
        provider="test-provider",
        payload={"prompt": "a diagram"},
        idempotency_key="a" * 64,
    )
    error = ProviderError(
        code="provider_timeout",
        message="provider did not respond",
        kind=ProviderErrorKind.TIMEOUT,
        retryable=True,
    )
    result = ProviderResult(
        status=ProviderResultStatus.FAILED,
        provider=request.provider,
        operation=request.operation,
        idempotency_key=request.idempotency_key,
        error=error,
    )
    assert result.to_dict()["error"]["kind"] == "timeout"
    with pytest.raises(ProviderContractError):
        ProviderResult(
            status=ProviderResultStatus.FAILED,
            provider="test",
            operation="generate",
            idempotency_key="b" * 64,
        )


def test_request_rejects_partial_identity_and_invalid_policy() -> None:
    with pytest.raises(ProviderContractError, match="must be supplied together"):
        ProviderRequest(
            capability="tts",
            operation="generate",
            provider="edge_tts",
            payload={"text": "hi"},
            idempotency_key="c" * 64,
            project_id="partial",
        )
    with pytest.raises(ProviderContractError, match="max_retries"):
        ProviderRequest(
            capability="tts",
            operation="generate",
            provider="edge_tts",
            payload={"text": "hi"},
            idempotency_key="d" * 64,
            max_retries=99,
        )


@pytest.mark.parametrize("approval", ["false", "true", 0, 1, None])
def test_request_rejects_non_boolean_approval(approval: object) -> None:
    with pytest.raises(ProviderContractError, match="approved must be boolean"):
        ProviderRequest(
            capability="image_generation",
            operation="generate",
            provider="test-provider",
            payload={"prompt": "a diagram"},
            idempotency_key="e" * 64,
            approved=approval,  # type: ignore[arg-type]
        )
