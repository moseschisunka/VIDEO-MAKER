"""Deterministic provider execution kernel.

The executor owns reliability and accounting mechanics only.  It never picks a
provider, invents a prompt, changes a media type, or advances a pipeline stage.
The caller supplies an already-approved :class:`ProviderRequest` and a single
provider operation callable.
"""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from lib.providers.contracts import (
    ProviderArtifact,
    ProviderContractError,
    ProviderError,
    ProviderErrorKind,
    FallbackClass,
    ProviderRequest,
    ProviderResult,
    ProviderResultStatus,
    provider_artifact_from_path,
)
from lib.observability import correlation_fields, event_id, metrics, structured_log, trace_id
from lib.secrets import redact_mapping, redact_text


_logger = logging.getLogger("openmontage.providers")


class ProviderExecutionError(RuntimeError):
    """Base exception that can carry retry classification."""

    kind = ProviderErrorKind.UNKNOWN
    retryable = False
    status_code: int | None = None
    code = "provider_execution_error"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class ProviderTransientError(ProviderExecutionError):
    kind = ProviderErrorKind.TRANSIENT
    retryable = True
    code = "provider_transient_error"


class ProviderRateLimitError(ProviderExecutionError):
    kind = ProviderErrorKind.RATE_LIMIT
    retryable = True
    status_code = 429
    code = "provider_rate_limit"


class ProviderPermanentError(ProviderExecutionError):
    kind = ProviderErrorKind.PERMANENT
    retryable = False
    code = "provider_permanent_error"


class ProviderMalformedResponseError(ProviderExecutionError):
    kind = ProviderErrorKind.MALFORMED_RESPONSE
    retryable = False
    code = "provider_malformed_response"


class ProviderPartialOutputError(ProviderExecutionError):
    kind = ProviderErrorKind.PARTIAL_OUTPUT
    retryable = False
    code = "provider_partial_output"


@dataclass
class CircuitState:
    consecutive_failures: int = 0
    opened_at: float | None = None


def _now_monotonic() -> float:
    return time.monotonic()


def _normalise_error(exc: BaseException) -> ProviderError:
    if isinstance(exc, ProviderExecutionError):
        return ProviderError(
            code=exc.code,
            message=redact_text(str(exc)),
            kind=exc.kind,
            retryable=bool(exc.retryable),
            status_code=exc.status_code,
            details=exc.details,
        )
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        status_code = None
    retryable = isinstance(exc, (TimeoutError, ConnectionError)) or (
        status_code is not None and (status_code == 429 or status_code >= 500)
    )
    kind = ProviderErrorKind.TIMEOUT if isinstance(exc, TimeoutError) else (
        ProviderErrorKind.RATE_LIMIT if status_code == 429 else (
            ProviderErrorKind.TRANSIENT if retryable else ProviderErrorKind.UNKNOWN
        )
    )
    return ProviderError(
        code="provider_timeout" if isinstance(exc, TimeoutError) else "provider_call_failed",
        message=redact_text(str(exc)) or exc.__class__.__name__,
        kind=kind,
        retryable=retryable,
        status_code=status_code,
            details=redact_mapping({"exception_type": exc.__class__.__name__}),
    )


class ProviderExecutor:
    """Run one approved provider operation with bounded, observable mechanics."""

    def __init__(
        self,
        *,
        cache_dir: Path | str | None = None,
        cost_tracker: Any | None = None,
        clock: Callable[[], float] = _now_monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] | None = None,
        event_sink: Callable[[Mapping[str, Any]], None] | None = None,
        rate_limit_per_second: float | Mapping[str, float] | None = None,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_seconds: float = 60.0,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 30.0,
    ) -> None:
        self.cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cost_tracker = cost_tracker
        self.clock = clock
        self.sleep_fn = sleep_fn
        self.random_fn = random_fn or (lambda: random.uniform(0.8, 1.2))
        self.event_sink = event_sink
        self.rate_limit_per_second = rate_limit_per_second
        if isinstance(circuit_failure_threshold, bool) or circuit_failure_threshold < 1:
            raise ValueError("circuit_failure_threshold must be positive")
        self.circuit_failure_threshold = int(circuit_failure_threshold)
        self.circuit_cooldown_seconds = max(0.0, float(circuit_cooldown_seconds))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))
        self.backoff_max_seconds = max(self.backoff_base_seconds, float(backoff_max_seconds))
        self._lock = threading.RLock()
        self._rate_calls: dict[str, deque[float]] = defaultdict(deque)
        self._circuits: dict[str, CircuitState] = defaultdict(CircuitState)

    # ---- public API -------------------------------------------------

    def execute(
        self,
        request: ProviderRequest,
        operation: Callable[[ProviderRequest], Any],
        *,
        require_artifacts: bool = False,
        artifact_validator: Callable[[ProviderArtifact], None] | None = None,
        validate_artifacts: bool = True,
    ) -> ProviderResult:
        """Execute one request, returning a structured result in all outcomes."""
        if not isinstance(request, ProviderRequest):
            raise ProviderContractError("ProviderExecutor requires a ProviderRequest")
        if not callable(operation):
            raise ProviderContractError("provider operation must be callable")

        fallback_class = request.fallback_class
        if fallback_class in {
            FallbackClass.MATERIAL_PROVIDER_CHANGE.value,
            FallbackClass.MATERIAL_MEDIA_CHANGE.value,
        } and not request.approved:
            error = ProviderError(
                code="material_fallback_approval_required",
                message=f"{fallback_class} requires explicit approval before execution",
                kind=ProviderErrorKind.APPROVAL_REQUIRED,
                retryable=False,
                details={"fallback_class": fallback_class},
            )
            return self._blocked(request, error)
        if fallback_class == FallbackClass.UNAVAILABLE.value:
            error = ProviderError(
                code="capability_unavailable",
                message="no honest provider/media substitute is available",
                kind=ProviderErrorKind.PERMANENT,
                retryable=False,
            )
            return self._blocked(request, error)

        cached = self._load_cached(request)
        if cached is not None:
            cached.status = ProviderResultStatus.CACHED
            cached.metadata = {**cached.metadata, "cache_hit": True}
            self._emit(request, "cache_hit", {"attempt_count": cached.attempt_count})
            return cached

        if request.estimated_cost_usd > 0 and not request.approved:
            result = self._blocked(
                request,
                ProviderError(
                    code="approval_required",
                    message="paid provider execution requires an approved request",
                    kind=ProviderErrorKind.APPROVAL_REQUIRED,
                    retryable=False,
                ),
            )
            self._emit(request, "blocked", {"error": result.error.to_dict() if result.error else None})
            return result

        circuit_error = self._circuit_error(request.provider)
        if circuit_error is not None:
            result = self._blocked(request, circuit_error)
            self._emit(request, "circuit_open", {"error": circuit_error.to_dict()})
            return result

        try:
            cost_entry_id = self._reserve_cost(request)
        except ProviderExecutionError as exc:
            error = ProviderError(
                code="cost_reservation_blocked",
                message=redact_text(str(exc)),
                kind=ProviderErrorKind.APPROVAL_REQUIRED
                if "approval" in str(exc).lower()
                else ProviderErrorKind.PERMANENT,
                retryable=False,
                details=redact_mapping(exc.details),
            )
            result = self._blocked(request, error)
            self._emit(request, "blocked", {"error": error.to_dict()})
            return result
        started = self.clock()
        self._emit(request, "start", {"estimated_cost_usd": request.estimated_cost_usd})
        last_error: ProviderError | None = None
        attempt_count = 0
        for attempt_index in range(request.max_retries + 1):
            attempt_count = attempt_index + 1
            self._wait_for_rate_limit(request.provider)
            self._emit(request, "attempt_start", {"attempt": attempt_count})
            try:
                raw_result = self._call_with_timeout(operation, request)
                result = self._normalise_result(request, raw_result, attempt_count)
                self._validate_result(
                    request,
                    result,
                    require_artifacts,
                    artifact_validator,
                    validate_artifacts=validate_artifacts,
                )
                result.latency_ms = max(0.0, (self.clock() - started) * 1000.0)
                result.estimated_cost_usd = float(request.estimated_cost_usd)
                result.reserved_cost_usd = float(request.estimated_cost_usd)
                if result.actual_cost_usd < 0:
                    result.actual_cost_usd = 0.0
                result.reserved_cost_usd = 0.0
                self._record_circuit_success(request.provider)
                self._reconcile_cost(cost_entry_id, result.actual_cost_usd, success=True)
                result.metadata.update({
                    "cache_hit": False,
                    "attempts": attempt_count,
                    "executor": "ProviderExecutor",
                })
                self._store_cached(request, result)
                self._emit(request, "success", {
                    "attempt": attempt_count,
                    "latency_ms": result.latency_ms,
                    "actual_cost_usd": result.actual_cost_usd,
                })
                return result
            except Exception as exc:  # provider boundaries must become structured results
                last_error = _normalise_error(exc)
                self._record_circuit_failure(request.provider)
                self._emit(request, "attempt_error", {
                    "attempt": attempt_count,
                    "error": last_error.to_dict(),
                })
                if not last_error.retryable or attempt_index >= request.max_retries:
                    break
                delay = min(
                    self.backoff_max_seconds,
                    self.backoff_base_seconds * (2 ** attempt_index),
                ) * max(0.0, float(self.random_fn()))
                if delay:
                    self._emit(request, "backoff", {"attempt": attempt_count, "delay_seconds": delay})
                    self.sleep_fn(delay)

        if last_error is None:
            last_error = ProviderError(
                code="provider_no_result",
                message="provider returned no result",
                kind=ProviderErrorKind.MALFORMED_RESPONSE,
                retryable=False,
            )
        self._reconcile_cost(cost_entry_id, 0.0, success=False)
        result = ProviderResult(
            status=ProviderResultStatus.FAILED,
            provider=request.provider,
            operation=request.operation,
            idempotency_key=request.idempotency_key,
            model=request.model,
            attempt_count=attempt_count,
            latency_ms=max(0.0, (self.clock() - started) * 1000.0),
            estimated_cost_usd=float(request.estimated_cost_usd),
            reserved_cost_usd=0.0,
            actual_cost_usd=0.0,
            error=last_error,
            metadata={"cache_hit": False, "attempts": attempt_count, "executor": "ProviderExecutor"},
            provenance=self._provenance(request),
        )
        self._emit(request, "failed", {"attempt": attempt_count, "error": last_error.to_dict()})
        return result

    # ---- normalisation/validation ----------------------------------

    def _normalise_result(self, request: ProviderRequest, raw: Any, attempt_count: int) -> ProviderResult:
        if isinstance(raw, ProviderResult):
            result = raw
            if result.provider != request.provider or result.operation != request.operation or result.idempotency_key != request.idempotency_key:
                raise ProviderMalformedResponseError(
                    "provider result identity does not match request",
                    details={"expected_provider": request.provider},
                )
            result.attempt_count = attempt_count
            return result

        if hasattr(raw, "success"):
            success = bool(getattr(raw, "success"))
            data = getattr(raw, "data", {}) or {}
            artifacts = [
                provider_artifact_from_path(path)
                for path in (getattr(raw, "artifacts", []) or [])
            ]
            if success:
                return ProviderResult.success(
                    request,
                    artifacts=artifacts,
                    attempt_count=attempt_count,
                    actual_cost_usd=float(getattr(raw, "cost_usd", request.estimated_cost_usd) or 0),
                    metadata=dict(data) if isinstance(data, Mapping) else {"data": data},
                    provenance=self._provenance(request),
                )
            return ProviderResult(
                status=ProviderResultStatus.FAILED,
                provider=request.provider,
                operation=request.operation,
                idempotency_key=request.idempotency_key,
                model=request.model,
                attempt_count=attempt_count,
                error=ProviderError(
                    code="provider_tool_failed",
                    message=str(getattr(raw, "error", None) or "provider tool returned failure"),
                    kind=ProviderErrorKind.UNKNOWN,
                    retryable=False,
                ),
                metadata=dict(data) if isinstance(data, Mapping) else {"data": data},
                provenance=self._provenance(request),
            )

        if isinstance(raw, Mapping):
            if "status" in raw:
                result = ProviderResult.from_dict(raw)
                result.attempt_count = attempt_count
                return result
            if raw.get("success") is False:
                return ProviderResult(
                    status=ProviderResultStatus.FAILED,
                    provider=request.provider,
                    operation=request.operation,
                    idempotency_key=request.idempotency_key,
                    model=request.model,
                    attempt_count=attempt_count,
                    error=ProviderError(
                        code="provider_mapping_failed",
                        message=str(raw.get("error") or "provider returned failure"),
                    ),
                    metadata=dict(raw),
                    provenance=self._provenance(request),
                )
            raw_artifacts = raw.get("artifacts") or []
            artifacts = [
                item if isinstance(item, ProviderArtifact) else (
                    ProviderArtifact.from_dict(item) if isinstance(item, Mapping)
                    else provider_artifact_from_path(item)
                )
                for item in raw_artifacts
            ]
            return ProviderResult.success(
                request,
                artifacts=artifacts,
                attempt_count=attempt_count,
                actual_cost_usd=float(raw.get("actual_cost_usd", raw.get("cost_usd", request.estimated_cost_usd)) or 0),
                metadata={str(k): v for k, v in raw.items() if k != "artifacts"},
                provenance=self._provenance(request),
            )

        return ProviderResult.success(
            request,
            attempt_count=attempt_count,
            actual_cost_usd=float(request.estimated_cost_usd),
            metadata={"value": raw},
            provenance=self._provenance(request),
        )

    @staticmethod
    def _validate_result(
        request: ProviderRequest,
        result: ProviderResult,
        require_artifacts: bool,
        artifact_validator: Callable[[ProviderArtifact], None] | None,
        *,
        validate_artifacts: bool = True,
    ) -> None:
        if result.provider != request.provider or result.idempotency_key != request.idempotency_key:
            raise ProviderMalformedResponseError("provider result identity does not match request")
        if result.status not in {ProviderResultStatus.SUCCESS, ProviderResultStatus.CACHED}:
            if result.error and result.error.retryable:
                retry_error = result.error
                if retry_error.kind is ProviderErrorKind.RATE_LIMIT:
                    raise ProviderRateLimitError(retry_error.message, details=retry_error.details)
                if retry_error.kind is ProviderErrorKind.TIMEOUT:
                    raise TimeoutError(retry_error.message)
                raise ProviderTransientError(retry_error.message, details=retry_error.details)
            raise ProviderMalformedResponseError(
                result.error.message if result.error else "provider returned a non-success result"
            )
        if require_artifacts and not result.artifacts:
            raise ProviderPartialOutputError("provider returned no required artifacts")
        if not validate_artifacts:
            return
        for artifact in result.artifacts:
            if artifact_validator:
                artifact_validator(artifact)
            if artifact.path.startswith(("http://", "https://", "s3://", "gs://")):
                continue
            source = Path(artifact.path).expanduser()
            if not source.is_file() or source.stat().st_size <= 0:
                raise ProviderPartialOutputError(f"provider artifact is missing or empty: {artifact.path}")
            if artifact.size_bytes is not None and source.stat().st_size != artifact.size_bytes:
                raise ProviderPartialOutputError(f"provider artifact size changed: {artifact.path}")
            if artifact.sha256:
                from lib.providers.contracts import provider_artifact_from_path

                current = provider_artifact_from_path(source)
                if current.sha256 != artifact.sha256:
                    raise ProviderPartialOutputError(f"provider artifact hash changed: {artifact.path}")

    # ---- cache ------------------------------------------------------

    def _cache_path(self, request: ProviderRequest) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{request.idempotency_key}.json"

    def _load_cached(self, request: ProviderRequest) -> ProviderResult | None:
        path = self._cache_path(request)
        if path is None or not path.is_file():
            return None
        try:
            result = ProviderResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if result.status != ProviderResultStatus.SUCCESS:
                return None
            self._validate_result(request, result, False, None)
            return result
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def _store_cached(self, request: ProviderRequest, result: ProviderResult) -> None:
        path = self._cache_path(request)
        if path is None or result.status != ProviderResultStatus.SUCCESS:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
            temporary = Path(raw)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(result.to_dict(), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError):
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    # ---- policy/telemetry ------------------------------------------

    def _call_with_timeout(self, operation: Callable[[ProviderRequest], Any], request: ProviderRequest) -> Any:
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-exec")
        future = pool.submit(operation, request)
        try:
            result = future.result(timeout=float(request.timeout_seconds))
            pool.shutdown(wait=True)
            return result
        except FutureTimeoutError as exc:
            future.cancel()
            # Do not wait for an uncooperative provider after the caller's
            # deadline. Python cannot kill a running thread, but the executor
            # returns a bounded result and no retry is launched in that thread.
            pool.shutdown(wait=False, cancel_futures=True)
            raise TimeoutError(
                f"provider {request.provider!r} exceeded {request.timeout_seconds:g}s timeout"
            ) from exc
        except Exception:
            pool.shutdown(wait=True)
            raise

    def _wait_for_rate_limit(self, provider: str) -> None:
        configured = self.rate_limit_per_second
        if configured is None:
            return
        if isinstance(configured, Mapping):
            rate = configured.get(provider)
        else:
            rate = configured
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            return
        if rate <= 0:
            return
        interval = 1.0 / rate
        while True:
            now = self.clock()
            with self._lock:
                calls = self._rate_calls[provider]
                while calls and now - calls[0] >= interval:
                    calls.popleft()
                if not calls:
                    calls.append(now)
                    return
                wait = interval - (now - calls[0])
            self.sleep_fn(max(0.0, wait))

    def _circuit_error(self, provider: str) -> ProviderError | None:
        with self._lock:
            state = self._circuits[provider]
            if state.opened_at is None:
                return None
            if self.clock() - state.opened_at >= self.circuit_cooldown_seconds:
                state.opened_at = None
                state.consecutive_failures = 0
                return None
            return ProviderError(
                code="circuit_open",
                message=f"provider circuit for {provider!r} is open",
                kind=ProviderErrorKind.CIRCUIT_OPEN,
                retryable=False,
                details={"consecutive_failures": state.consecutive_failures},
            )

    def _record_circuit_failure(self, provider: str) -> None:
        with self._lock:
            state = self._circuits[provider]
            state.consecutive_failures += 1
            if state.consecutive_failures >= self.circuit_failure_threshold:
                state.opened_at = self.clock()

    def _record_circuit_success(self, provider: str) -> None:
        with self._lock:
            self._circuits[provider] = CircuitState()

    def _reserve_cost(self, request: ProviderRequest) -> str | None:
        if self.cost_tracker is None or request.estimated_cost_usd <= 0:
            return None
        try:
            if request.approved and hasattr(self.cost_tracker, "approve_tool"):
                self.cost_tracker.approve_tool(request.provider)
            try:
                entry_id = self.cost_tracker.estimate(
                    request.provider,
                    request.operation,
                    float(request.estimated_cost_usd),
                    idempotency_key=request.idempotency_key,
                    metadata={
                        "capability": request.capability,
                        "project_id": request.project_id,
                        "run_id": request.run_id,
                        "stage": request.stage,
                    },
                )
            except TypeError:
                # Preserve compatibility with lightweight tracker adapters
                # used by older integrations while the common contract rolls
                # out across the repository.
                entry_id = self.cost_tracker.estimate(
                    request.provider,
                    request.operation,
                    float(request.estimated_cost_usd),
                )
            self.cost_tracker.reserve(entry_id)
            return entry_id
        except Exception as exc:
            # Approval/budget errors are returned as a structured blocked
            # result by the public method rather than leaking tracker details.
            raise ProviderExecutionError(
                str(exc) or "provider cost reservation failed",
                details={"exception_type": exc.__class__.__name__},
            ) from exc

    def _reconcile_cost(self, entry_id: str | None, actual: float, *, success: bool) -> None:
        if not entry_id or self.cost_tracker is None:
            return
        try:
            self.cost_tracker.reconcile(entry_id, float(actual), success=success)
        except Exception:
            pass

    @staticmethod
    def _provenance(request: ProviderRequest) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "project_id": request.project_id,
                "pipeline_type": request.pipeline_type,
                "run_id": request.run_id,
                "attempt": request.attempt,
                "stage": request.stage,
                "provider": request.provider,
                "model": request.model,
                "idempotency_key": request.idempotency_key,
            }.items()
            if value is not None
        }

    @staticmethod
    def _blocked(request: ProviderRequest, error: ProviderError) -> ProviderResult:
        return ProviderResult(
            status=ProviderResultStatus.BLOCKED,
            provider=request.provider,
            operation=request.operation,
            idempotency_key=request.idempotency_key,
            model=request.model,
            error=error,
            estimated_cost_usd=float(request.estimated_cost_usd),
            provenance=ProviderExecutor._provenance(request),
        )

    def _emit(self, request: ProviderRequest, event: str, data: Mapping[str, Any] | None = None) -> None:
        payload = {
            "schema_version": "1.0",
            "event_id": event_id(),
            "span_id": event_id()[:16],
            "event": event,
            "provider": request.provider,
            "model": request.model,
            "capability": request.capability,
            "operation": request.operation,
            "idempotency_key": request.idempotency_key,
            **self._provenance(request),
        }
        current_trace = trace_id(request.project_id, request.run_id)
        if current_trace:
            payload["trace_id"] = current_trace
        if data:
            payload.update(redact_mapping(dict(data)))
        try:
            metrics.increment(
                "openmontage_provider_events_total",
                labels={"provider": request.provider, "event": event},
            )
            if isinstance(payload.get("latency_ms"), (int, float)):
                metrics.observe(
                    "openmontage_provider_latency_seconds",
                    float(payload["latency_ms"]) / 1000.0,
                    labels={"provider": request.provider},
                )
            structured_log(
                _logger,
                logging.DEBUG,
                f"provider event: {event}",
                context=correlation_fields(
                    project_id=request.project_id,
                    run_id=request.run_id,
                    pipeline_type=request.pipeline_type,
                    stage=request.stage,
                    attempt=request.attempt,
                    provider=request.provider,
                ),
                event=event,
                operation=request.operation,
                capability=request.capability,
                success=event in {"success", "cache_hit"},
            )
        except Exception:
            pass
        if self.event_sink is None:
            return
        try:
            self.event_sink(payload)
        except Exception:
            pass


__all__ = [
    "ProviderExecutionError",
    "ProviderExecutor",
    "ProviderMalformedResponseError",
    "ProviderPartialOutputError",
    "ProviderPermanentError",
    "ProviderRateLimitError",
    "ProviderTransientError",
]
