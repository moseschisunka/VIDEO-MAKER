"""Adapter for migrating existing ``BaseTool`` providers to the kernel.

Provider implementations still own their HTTP/CLI payload details.  This
bridge gives selectors and approved production call sites one common wrapper
for request identity, cost, caching, retries, and structured result handling.
The implementation callable is deliberately passed in by the caller; the
kernel never selects a provider or rewrites a creative request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.base_tool import BaseTool, ToolResult

from .contracts import (
    ProviderContractError,
    ProviderRequest,
    ProviderResult,
    ProviderResultStatus,
    strict_bool,
    stable_idempotency_key,
)
from .executor import ProviderExecutor


_CONTROL_KEYS = {
    "provider_kernel",
    "_provider_executor_bypass",
    "provider_approved",
    "approved",
    "provider_executor",
    "cost_tracker",
    "provider_cache_dir",
    "provider_require_artifacts",
    "provider_timeout_seconds",
    "provider_max_retries",
    "fallback_class",
}


def _payload(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Keep stable creative fields while excluding execution-only controls."""
    result: dict[str, Any] = {}
    for key, value in inputs.items():
        if key in _CONTROL_KEYS or key.lower().endswith(("_api_key", "_token", "_secret")):
            continue
        try:
            json.dumps(value, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            # A live executor/cost tracker must never leak into a cache key or
            # provider payload.  The creative provider gets only serializable
            # request fields.
            continue
        result[str(key)] = value
    return result


def build_provider_request(tool: BaseTool, inputs: Mapping[str, Any], *, operation: str | None = None) -> ProviderRequest:
    """Build a common request without choosing or invoking a provider."""
    raw = dict(inputs)
    capability = str(getattr(tool, "capability", "generic"))
    provider = str(getattr(tool, "provider", tool.name))
    operation_name = str(operation or raw.get("operation") or "generate")
    payload = _payload(raw)
    model = raw.get("model") or raw.get("model_id") or raw.get("model_name")
    try:
        tool_key = str(tool.idempotency_key(raw))
    except Exception:
        tool_key = ""
    # Include the complete serialized payload and run identity in the common
    # cache key.  A provider's legacy key often omits output_path, which could
    # otherwise replay an artifact from another project into this run.
    key_payload = {
        "tool_key": tool_key,
        "payload": payload,
        "project_id": raw.get("project_id"),
        "pipeline_type": raw.get("pipeline_type"),
        "run_id": raw.get("run_id"),
        "attempt": raw.get("attempt"),
    }
    try:
        key = stable_idempotency_key(
            provider=provider,
            model=str(model) if model is not None else None,
            capability=capability,
            operation=operation_name,
            payload=key_payload,
            namespace="openmontage-provider-v1",
        )
    except Exception:
        key = stable_idempotency_key(
            provider=provider,
            model=str(model) if model is not None else None,
            capability=capability,
            operation=operation_name,
            payload=payload,
        )
    estimated = float(tool.estimate_cost(raw) or 0.0)
    retry_policy = getattr(tool, "retry_policy", None)
    max_retries = raw.get("provider_max_retries", getattr(retry_policy, "max_retries", 0))
    timeout = raw.get("provider_timeout_seconds", 120.0)
    project_id = raw.get("project_id")
    pipeline_type = raw.get("pipeline_type")
    run_id = raw.get("run_id")
    attempt = raw.get("attempt")
    production_context = bool(raw.get("provider_kernel") is True or raw.get("project_dir") or raw.get("run_id"))
    # ProviderRequest requires the complete identity tuple.  A partial caller
    # value is ignored here and left visible in payload metadata for diagnosis;
    # it must not create a misleading provenance record.
    identity_values = (project_id, pipeline_type, run_id, attempt)
    if production_context and any(value is not None for value in identity_values) and not all(value is not None for value in identity_values):
        raise ProviderContractError(
            "production provider calls require project_id, pipeline_type, run_id, and attempt together"
        )
    if not all(value is not None for value in identity_values):
        project_id = pipeline_type = run_id = attempt = None
    # Existing direct unit callers predate the kernel and do not carry run
    # identity.  They remain backward-compatible while every production
    # identity-bearing call (or explicit ``provider_kernel`` call) requires an
    # approval bit.  A production caller must therefore opt in explicitly;
    # ``provider_approved=False`` is always authoritative.
    if "provider_approved" in raw:
        approved_value = strict_bool(raw["provider_approved"], "provider_approved")
    elif "approved" in raw:
        approved_value = strict_bool(raw["approved"], "approved")
    else:
        approved_value = not production_context
    return ProviderRequest(
        capability=capability,
        operation=operation_name,
        provider=provider,
        model=str(model) if model is not None else None,
        payload=payload,
        idempotency_key=key,
        project_id=str(project_id) if project_id is not None else None,
        pipeline_type=str(pipeline_type) if pipeline_type is not None else None,
        run_id=str(run_id) if run_id is not None else None,
        attempt=int(attempt) if attempt is not None else None,
        stage=str(raw.get("stage")) if raw.get("stage") is not None else None,
        timeout_seconds=float(timeout),
        max_retries=int(max_retries),
        estimated_cost_usd=estimated,
        approved=approved_value,
        fallback_class=str(raw.get("fallback_class")) if raw.get("fallback_class") else None,
        metadata={
            "tool": tool.name,
            "runtime": getattr(getattr(tool, "runtime", None), "value", str(getattr(tool, "runtime", ""))),
            "kernel": "provider_executor_v1",
        },
    )


def _executor_for(tool: BaseTool, inputs: Mapping[str, Any]) -> ProviderExecutor:
    supplied = inputs.get("provider_executor")
    if isinstance(supplied, ProviderExecutor):
        return supplied
    cache_dir = inputs.get("provider_cache_dir")
    if cache_dir is None and inputs.get("project_dir"):
        cache_dir = Path(str(inputs["project_dir"])) / "provider_cache"
    event_sink = None
    project_dir = inputs.get("project_dir")
    if project_dir:
        try:
            from lib.events import emit_event

            event_sink = lambda payload: emit_event(project_dir, {"event": "provider_attempt", **dict(payload)})
        except Exception:
            event_sink = None
    return ProviderExecutor(
        cache_dir=cache_dir,
        cost_tracker=inputs.get("cost_tracker"),
        event_sink=event_sink,
    )


def execute_with_provider_executor(
    tool: BaseTool,
    inputs: Mapping[str, Any],
    *,
    implementation: Callable[[dict[str, Any]], Any] | None = None,
    executor: ProviderExecutor | None = None,
    operation: str | None = None,
) -> ToolResult:
    """Run one existing provider through ``ProviderExecutor``.

    ``implementation`` receives a copy with the kernel marker removed.  This
    avoids recursion when a provider itself opts into the bridge while keeping
    direct unit tests and provider-specific request code unchanged.
    """
    raw_inputs = dict(inputs)
    try:
        request = build_provider_request(tool, raw_inputs, operation=operation)
    except Exception as exc:
        return ToolResult(success=False, error=f"Provider request contract failed: {exc}")
    runner = executor or _executor_for(tool, raw_inputs)
    call = implementation or (lambda provider_inputs: tool.execute(provider_inputs))
    provider_inputs = dict(raw_inputs)
    provider_inputs.pop("provider_kernel", None)
    provider_inputs["_provider_executor_bypass"] = True

    def invoke(_request: ProviderRequest) -> Any:
        return call(provider_inputs)

    result = runner.execute(
        request,
        invoke,
        require_artifacts=bool(raw_inputs.get("provider_require_artifacts", False)),
        # Compatibility callers outside a project/run may return fixture or
        # remote artifact paths that cannot be probed locally.  Identity-bearing
        # production calls stay fail-closed and validate every local artifact.
        validate_artifacts=bool(
            raw_inputs.get("provider_kernel") is True
            or raw_inputs.get("project_dir")
            or raw_inputs.get("run_id")
            or raw_inputs.get("provider_require_artifacts", False)
        ),
    )
    data = dict(result.metadata or {})
    data["provider_result"] = result.to_dict()
    data["provider_kernel"] = "ProviderExecutor"
    data.setdefault("provider", request.provider)
    if result.status in {ProviderResultStatus.SUCCESS, ProviderResultStatus.CACHED}:
        return ToolResult(
            success=True,
            data=data,
            artifacts=[artifact.path for artifact in result.artifacts],
            cost_usd=float(result.actual_cost_usd),
            duration_seconds=float(result.latency_ms) / 1000.0,
            model=result.model,
        )
    message = result.error.message if result.error else "provider execution failed"
    return ToolResult(
        success=False,
        data=data,
        error=message,
        cost_usd=float(result.actual_cost_usd),
        duration_seconds=float(result.latency_ms) / 1000.0,
        model=result.model,
    )


def should_use_provider_kernel(inputs: Mapping[str, Any]) -> bool:
    """Return whether a call is in the production/kernel migration lane."""
    return bool(inputs.get("provider_kernel") is True)


__all__ = [
    "build_provider_request",
    "execute_with_provider_executor",
    "should_use_provider_kernel",
]
