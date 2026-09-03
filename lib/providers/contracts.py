"""Provider execution contracts shared by all paid/local provider families.

The creative agent still chooses the provider and authors the payload.  These
types define the deterministic boundary around that decision: every attempt
has a stable idempotency key, bounded execution policy, cost fields, structured
errors, and artifact provenance.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from lib.secrets import redact_mapping, redact_text


REPO_ROOT = Path(__file__).resolve().parents[2]
REQUEST_SCHEMA_PATH = REPO_ROOT / "schemas" / "providers" / "provider_request.schema.json"
RESULT_SCHEMA_PATH = REPO_ROOT / "schemas" / "providers" / "provider_result.schema.json"


class ProviderContractError(ValueError):
    """Raised when provider request/result data violates the common contract."""


class ProviderResultStatus(str, Enum):
    SUCCESS = "success"
    CACHED = "cached"
    FAILED = "failed"
    BLOCKED = "blocked"


class ProviderErrorKind(str, Enum):
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    MALFORMED_RESPONSE = "malformed_response"
    PARTIAL_OUTPUT = "partial_output"
    CANCELLED = "cancelled"
    APPROVAL_REQUIRED = "approval_required"
    CIRCUIT_OPEN = "circuit_open"
    UNKNOWN = "unknown"


class FallbackClass(str, Enum):
    EQUIVALENT_NON_MATERIAL = "equivalent_non_material"
    MATERIAL_PROVIDER_CHANGE = "material_provider_change"
    MATERIAL_MEDIA_CHANGE = "material_media_change"
    UNAVAILABLE = "unavailable"


def _nonempty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_nonempty(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _nonempty(value, field_name)


def _normalise_uuid(value: Any, field_name: str) -> str:
    raw = _nonempty(value, field_name)
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise ProviderContractError(f"{field_name} must be a UUID") from exc
    return str(parsed)


def stable_idempotency_key(
    *,
    provider: str,
    model: str | None,
    capability: str,
    operation: str,
    payload: Mapping[str, Any],
    namespace: str | None = None,
) -> str:
    """Derive a stable SHA-256 key from the complete provider request."""
    envelope = {
        "namespace": namespace or "openmontage-provider-v1",
        "provider": provider,
        "model": model,
        "capability": capability,
        "operation": operation,
        "payload": payload,
    }
    try:
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ProviderContractError(f"provider payload is not JSON serializable: {exc}") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderError:
    code: str
    message: str
    kind: ProviderErrorKind = ProviderErrorKind.UNKNOWN
    retryable: bool = False
    status_code: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty(self.code, "error.code")
        _nonempty(self.message, "error.message")
        if self.status_code is not None and (
            isinstance(self.status_code, bool) or not isinstance(self.status_code, int)
            or self.status_code < 100 or self.status_code > 599
        ):
            raise ProviderContractError("error.status_code must be an HTTP status code")
        if not isinstance(self.details, Mapping):
            raise ProviderContractError("error.details must be an object")
        # Provider responses and exception strings are persisted by the
        # executor.  Sanitize them at the immutable error boundary so a
        # provider cannot accidentally disclose credentials or signed URLs.
        object.__setattr__(self, "message", redact_text(self.message))
        object.__setattr__(self, "details", redact_mapping(dict(self.details)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "kind": self.kind.value,
            "retryable": bool(self.retryable),
            "status_code": self.status_code,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderError":
        return cls(
            code=str(value.get("code") or "provider_error"),
            message=str(value.get("message") or "provider execution failed"),
            kind=ProviderErrorKind(str(value.get("kind") or ProviderErrorKind.UNKNOWN.value)),
            retryable=bool(value.get("retryable", False)),
            status_code=value.get("status_code"),
            details=dict(value.get("details") or {}),
        )


@dataclass(frozen=True)
class ProviderArtifact:
    path: str
    artifact_type: str = "file"
    sha256: str | None = None
    size_bytes: int | None = None
    media_probe: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty(self.path, "artifact.path")
        _nonempty(self.artifact_type, "artifact.artifact_type")
        if self.sha256 is not None and (
            not isinstance(self.sha256, str) or len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise ProviderContractError("artifact.sha256 must be a lowercase SHA-256 digest")
        if self.size_bytes is not None and (
            isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ProviderContractError("artifact.size_bytes must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "artifact_type": self.artifact_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_probe": dict(self.media_probe) if self.media_probe is not None else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderArtifact":
        return cls(
            path=str(value.get("path") or ""),
            artifact_type=str(value.get("artifact_type") or "file"),
            sha256=value.get("sha256"),
            size_bytes=value.get("size_bytes"),
            media_probe=value.get("media_probe"),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ProviderRequest:
    capability: str
    operation: str
    provider: str
    payload: Mapping[str, Any]
    idempotency_key: str
    model: str | None = None
    project_id: str | None = None
    pipeline_type: str | None = None
    run_id: str | None = None
    attempt: int | None = None
    stage: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 0
    estimated_cost_usd: float = 0.0
    approved: bool = False
    fallback_class: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty(self.capability, "capability")
        _nonempty(self.operation, "operation")
        _nonempty(self.provider, "provider")
        _nonempty(self.idempotency_key, "idempotency_key")
        if not isinstance(self.payload, Mapping):
            raise ProviderContractError("payload must be an object")
        try:
            json.dumps(dict(self.payload), sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ProviderContractError(f"payload is not JSON serializable: {exc}") from exc
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)):
            raise ProviderContractError("timeout_seconds must be numeric")
        if not 0 < float(self.timeout_seconds) <= 86_400:
            raise ProviderContractError("timeout_seconds must be between 0 and 86400")
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 10:
            raise ProviderContractError("max_retries must be an integer between 0 and 10")
        if isinstance(self.estimated_cost_usd, bool) or not isinstance(self.estimated_cost_usd, (int, float)) or float(self.estimated_cost_usd) < 0:
            raise ProviderContractError("estimated_cost_usd must be non-negative")
        identity = (self.project_id, self.pipeline_type, self.run_id, self.attempt, self.stage)
        if any(value is not None for value in identity[:4]) and not all(value is not None for value in identity[:4]):
            raise ProviderContractError("project_id, pipeline_type, run_id, and attempt must be supplied together")
        if self.run_id is not None:
            _normalise_uuid(self.run_id, "run_id")
        if self.attempt is not None and (isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1):
            raise ProviderContractError("attempt must be a positive integer")
        if self.stage is not None:
            _nonempty(self.stage, "stage")
        if self.fallback_class is not None:
            try:
                FallbackClass(str(self.fallback_class))
            except ValueError as exc:
                raise ProviderContractError(
                    f"fallback_class must be one of: {', '.join(item.value for item in FallbackClass)}"
                ) from exc
        if not isinstance(self.metadata, Mapping):
            raise ProviderContractError("metadata must be an object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "operation": self.operation,
            "provider": self.provider,
            "model": self.model,
            "payload": dict(self.payload),
            "idempotency_key": self.idempotency_key,
            "project_id": self.project_id,
            "pipeline_type": self.pipeline_type,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "stage": self.stage,
            "timeout_seconds": float(self.timeout_seconds),
            "max_retries": self.max_retries,
            "estimated_cost_usd": round(float(self.estimated_cost_usd), 8),
            "approved": bool(self.approved),
            "fallback_class": self.fallback_class,
            "metadata": dict(self.metadata),
        }


@dataclass
class ProviderResult:
    status: ProviderResultStatus
    provider: str
    operation: str
    idempotency_key: str
    model: str | None = None
    attempt_count: int = 0
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    reserved_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    refunded_cost_usd: float = 0.0
    artifacts: list[ProviderArtifact] = field(default_factory=list)
    error: ProviderError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, ProviderResultStatus):
            self.status = ProviderResultStatus(str(self.status))
        _nonempty(self.provider, "provider")
        _nonempty(self.operation, "operation")
        _nonempty(self.idempotency_key, "idempotency_key")
        if isinstance(self.attempt_count, bool) or not isinstance(self.attempt_count, int) or self.attempt_count < 0:
            raise ProviderContractError("attempt_count must be a non-negative integer")
        for name in ("latency_ms", "estimated_cost_usd", "reserved_cost_usd", "actual_cost_usd", "refunded_cost_usd"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < 0:
                raise ProviderContractError(f"{name} must be non-negative")
        if self.status in {ProviderResultStatus.FAILED, ProviderResultStatus.BLOCKED} and self.error is None:
            raise ProviderContractError(f"{self.status.value} provider results require an error")
        if self.status in {ProviderResultStatus.SUCCESS, ProviderResultStatus.CACHED} and self.error is not None:
            raise ProviderContractError(f"{self.status.value} provider results cannot carry an error")

    @classmethod
    def success(
        cls,
        request: ProviderRequest,
        *,
        artifacts: list[ProviderArtifact] | None = None,
        attempt_count: int = 1,
        latency_ms: float = 0.0,
        actual_cost_usd: float | None = None,
        metadata: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> "ProviderResult":
        return cls(
            status=ProviderResultStatus.SUCCESS,
            provider=request.provider,
            operation=request.operation,
            idempotency_key=request.idempotency_key,
            model=request.model,
            attempt_count=attempt_count,
            latency_ms=latency_ms,
            estimated_cost_usd=float(request.estimated_cost_usd),
            reserved_cost_usd=float(request.estimated_cost_usd),
            actual_cost_usd=float(request.estimated_cost_usd if actual_cost_usd is None else actual_cost_usd),
            artifacts=list(artifacts or []),
            metadata=dict(metadata or {}),
            provenance=dict(provenance or {}),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderResult":
        raw_error = value.get("error")
        return cls(
            status=ProviderResultStatus(str(value.get("status") or ProviderResultStatus.FAILED.value)),
            provider=str(value.get("provider") or ""),
            operation=str(value.get("operation") or ""),
            idempotency_key=str(value.get("idempotency_key") or ""),
            model=value.get("model"),
            attempt_count=int(value.get("attempt_count") or 0),
            latency_ms=float(value.get("latency_ms") or 0),
            estimated_cost_usd=float(value.get("estimated_cost_usd") or 0),
            reserved_cost_usd=float(value.get("reserved_cost_usd") or 0),
            actual_cost_usd=float(value.get("actual_cost_usd") or 0),
            refunded_cost_usd=float(value.get("refunded_cost_usd") or 0),
            artifacts=[
                ProviderArtifact.from_dict(item)
                for item in (value.get("artifacts") or [])
                if isinstance(item, Mapping)
            ],
            error=ProviderError.from_dict(raw_error) if isinstance(raw_error, Mapping) else None,
            metadata=dict(value.get("metadata") or {}),
            provenance=dict(value.get("provenance") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "provider": self.provider,
            "operation": self.operation,
            "idempotency_key": self.idempotency_key,
            "model": self.model,
            "attempt_count": self.attempt_count,
            "latency_ms": round(float(self.latency_ms), 3),
            "estimated_cost_usd": round(float(self.estimated_cost_usd), 8),
            "reserved_cost_usd": round(float(self.reserved_cost_usd), 8),
            "actual_cost_usd": round(float(self.actual_cost_usd), 8),
            "refunded_cost_usd": round(float(self.refunded_cost_usd), 8),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "error": self.error.to_dict() if self.error else None,
            "metadata": dict(self.metadata),
            "provenance": dict(self.provenance),
        }


def provider_artifact_from_path(path: str | Path, *, artifact_type: str = "file", metadata: Mapping[str, Any] | None = None) -> ProviderArtifact:
    """Create an artifact record with a local SHA-256/size when available."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        return ProviderArtifact(path=str(path), artifact_type=artifact_type, metadata=dict(metadata or {}))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return ProviderArtifact(
        path=str(path),
        artifact_type=artifact_type,
        sha256=digest,
        size_bytes=source.stat().st_size,
        metadata=dict(metadata or {}),
    )


__all__ = [
    "ProviderArtifact",
    "ProviderContractError",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderRequest",
    "ProviderResult",
    "ProviderResultStatus",
    "RESULT_SCHEMA_PATH",
    "REQUEST_SCHEMA_PATH",
    "provider_artifact_from_path",
    "stable_idempotency_key",
]
