"""Stage-scoped MCP bridge for local OpenMontage directors.

The local model process never receives provider approval controls or API keys.
This small stdio server binds tool calls to the one claimed Backlot project and
run, then delegates them to the existing registry and manifest executor.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from jsonschema import ValidationError, validate

from lib.local_director import LOCAL_MCP_SERVER_NAME
from lib.manifest_executor import (
    ManifestExecutionError,
    is_certified_executor_order,
    load_manifest_stage_context,
    submit_manifest_stage,
)
from lib.secrets import redact_mapping, redact_text
from lib.work_order import (
    WorkOrderConflictError,
    WorkOrderStateError,
    WorkOrderValidationError,
    heartbeat_work_order,
    read_work_order,
)
from lib.providers.contracts import (
    FallbackClass,
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResultStatus,
)
from lib.providers.executor import ProviderExecutor


_BOUND_FIELDS = frozenset({
    "project_dir",
    "project_id",
    "pipeline_type",
    "run_id",
    "attempt",
    "agent_id",
    "stage",
})
_PROTECTED_CONTROL_FIELDS = frozenset({
    "provider_executor",
    "cost_tracker",
    "provider_cache_dir",
    "provider_kernel",
    "_provider_executor_bypass",
    "provider_require_artifacts",
    "provider_timeout_seconds",
    "provider_max_retries",
    "fallback_class",
})
_NON_AUTHORITY_APPROVAL_FLAGS = frozenset({"required_approval"})
_MCP_TOOLS = (
    {
        "name": "openmontage_get_context",
        "title": "Get OpenMontage run context",
        "description": (
            "Return the current manifest stage, artifacts, human gate, and the "
            "stage-scoped production-tool schemas. Call this before directing work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "openmontage_execute_tool",
        "title": "Run an OpenMontage production tool",
        "description": (
            "Run one registered tool that is available to the current manifest stage. "
            "Use its exact name and input schema from openmontage_get_context. "
            "Project, run, stage, and agent identity are bound by Backlot. "
            "Provider approval fields are not accepted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "minLength": 1},
                "inputs": {"type": "object", "additionalProperties": True},
            },
            "required": ["tool_name", "inputs"],
            "additionalProperties": False,
        },
    },
    {
        "name": "openmontage_submit_stage",
        "title": "Submit a validated stage checkpoint",
        "description": (
            "Submit the manifest-declared artifacts for the current stage. "
            "A human-gated stage pauses for Backlot review; this tool cannot approve it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifacts": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                },
                "status": {
                    "type": "string",
                    "enum": ["in_progress", "awaiting_human", "completed", "failed"],
                    "default": "completed",
                },
                "error": {"type": ["string", "null"]},
            },
            "required": ["artifacts"],
            "additionalProperties": False,
        },
    },
)


class _BacklotCapabilityClient:
    """Revalidate a short-lived run bearer with the local Backlot process."""

    def __init__(self, base_url: str, token: str) -> None:
        value = str(base_url or "").strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise AgentBridgeError("Backlot capability endpoint must be on this device")
        if not token or len(token) > 256:
            raise AgentBridgeError("a short-lived Backlot run capability is required")
        self.endpoint = value.rstrip("/") + "/api/local-director/capability/validate"
        self.token = token

    def validate(
        self,
        *,
        project_dir: Path | str,
        project_id: str,
        run_id: str,
        agent_id: str,
    ) -> None:
        try:
            with requests.Session() as session:
                # Do not route a local bearer through proxy variables inherited
                # from the operator shell or model CLI environment.
                session.trust_env = False
                response = session.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={
                        "project_dir": str(Path(project_dir).expanduser().resolve()),
                        "project_id": project_id,
                        "run_id": run_id,
                        "agent_id": agent_id,
                    },
                    timeout=5,
                    allow_redirects=False,
                )
        except requests.RequestException as exc:
            raise AgentBridgeError(
                "Backlot could not validate this run capability; check that Backlot is still running"
            ) from exc
        if response.status_code != 200:
            raise AgentBridgeError("Backlot rejected this local director run capability")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AgentBridgeError("Backlot returned an invalid capability response") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("project_id") != project_id
            or payload.get("run_id") != run_id
            or payload.get("agent_id") != agent_id
        ):
            raise AgentBridgeError("Backlot returned a mismatched run capability")


class AgentBridgeError(RuntimeError):
    """Raised when a local director call violates the run boundary."""


def _normalized_field_name(name: Any) -> str:
    """Normalize JSON field spelling before comparing trust-boundary names."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name))
    return re.sub(r"[\s-]+", "_", snake).strip("_").lower()


def _protected_input_field(name: Any) -> bool:
    normalized = _normalized_field_name(name)
    if normalized in _NON_AUTHORITY_APPROVAL_FLAGS:
        return False
    if normalized in _BOUND_FIELDS or normalized in _PROTECTED_CONTROL_FIELDS:
        return True
    # Approval-bearing fields can be hidden in open-ended nested objects such
    # as asset_request. Only accept the one reviewed boolean that asks a local
    # contact sheet to label an item as requiring approval; it grants none.
    return any(token in normalized for token in ("approval", "approved", "authorized"))


def _sanitize_input_schema(value: Any) -> Any:
    """Recursively remove identity and approval controls from tool schemas."""
    if isinstance(value, list):
        return [_sanitize_input_schema(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "properties" and isinstance(item, Mapping):
            result[key] = {
                str(field): _sanitize_input_schema(field_schema)
                for field, field_schema in item.items()
                if not _protected_input_field(field)
            }
        elif key == "required" and isinstance(item, list):
            result[key] = [
                field for field in item
                if not _protected_input_field(field)
            ]
        else:
            result[str(key)] = _sanitize_input_schema(item)
    return result


def _find_protected_input(value: Any, *, prefix: str = "inputs") -> str | None:
    """Return the first nested trust-boundary field supplied by the model."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}"
            if _protected_input_field(key):
                return path
            found = _find_protected_input(child, prefix=path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_protected_input(child, prefix=f"{prefix}[{index}]")
            if found:
                return found
    return None


def public_input_schema(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """Hide server-bound identity and approval controls from model schemas."""
    result = _sanitize_input_schema(json.loads(json.dumps(dict(schema or {}))))
    if result.get("type") != "object":
        return {"type": "object", "properties": {}, "additionalProperties": False}
    return result


def _path_field(name: str) -> bool:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name)).lower()
    return normalized in {"path", "paths", "file", "files"} or normalized.endswith(
        ("_path", "_paths", "_file", "_files", "filepath", "filepaths")
    )


def _project_path_value(value: Any, project_root: Path) -> Any:
    if isinstance(value, (list, tuple)):
        return [_project_path_value(item, project_root) for item in value]
    if not isinstance(value, str) or not value.strip():
        return value
    cleaned = value.strip()
    if cleaned.lower().startswith(("http://", "https://", "data:")):
        return cleaned
    path = Path(cleaned).expanduser()
    candidate = path if path.is_absolute() else project_root / path
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(project_root)
    except ValueError as exc:
        raise AgentBridgeError("tool file paths must stay inside the active project") from exc
    if any(part.lower() in {".env", ".env.local", ".git", ".openmontage"} for part in relative.parts):
        raise AgentBridgeError("tool file paths cannot target OpenMontage control or credential files")
    return str(resolved)


def _bind_project_paths(value: Any, project_root: Path, *, key: str = "") -> Any:
    if isinstance(value, Mapping):
        return {
            str(child_key): (
                _project_path_value(child_value, project_root)
                if _path_field(str(child_key))
                else _bind_project_paths(child_value, project_root, key=str(child_key))
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_bind_project_paths(item, project_root, key=key) for item in value]
    return value


def _tool_result_dict(result: Any) -> dict[str, Any]:
    payload = {
        "success": bool(getattr(result, "success", False)),
        "data": getattr(result, "data", {}) or {},
        "artifacts": getattr(result, "artifacts", []) or [],
        "error": getattr(result, "error", None),
        "cost_usd": getattr(result, "cost_usd", 0.0),
        "duration_seconds": getattr(result, "duration_seconds", 0.0),
        "model": getattr(result, "model", None),
    }
    return redact_mapping(payload)


class LocalDirectorBridge:
    """Execute stage-approved registry tools for one claimed run only."""

    def __init__(
        self,
        *,
        project_dir: Path | str,
        project_id: str,
        run_id: str,
        agent_id: str,
        capability_validator: Callable[[], Any] | None = None,
        tool_registry: Any | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.project_id = str(project_id).strip()
        self.run_id = str(run_id).strip()
        self.agent_id = str(agent_id).strip()
        self.capability_validator = capability_validator
        self.registry = tool_registry
        if not self.project_id or not self.run_id or not self.agent_id:
            raise AgentBridgeError("project_id, run_id, and agent_id are required")
        if not callable(self.capability_validator):
            raise AgentBridgeError("a short-lived Backlot run capability is required")
        if not self.project_dir.is_dir():
            raise AgentBridgeError("active project directory is missing")
        self._current_context()

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "LocalDirectorBridge":
        source = os.environ if env is None else env
        required = {
            "project_dir": "OPENMONTAGE_PROJECT_DIR",
            "project_id": "OPENMONTAGE_PROJECT_ID",
            "run_id": "OPENMONTAGE_RUN_ID",
            "agent_id": "OPENMONTAGE_AGENT_ID",
            "backlot_url": "OPENMONTAGE_BACKLOT_URL",
            "capability_token": "OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN",
        }
        missing = [env_name for env_name in required.values() if not str(source.get(env_name, "")).strip()]
        if missing:
            raise AgentBridgeError("local tool bridge context is incomplete: " + ", ".join(missing))
        context = {field: str(source[env_name]) for field, env_name in required.items()}
        capability_client = _BacklotCapabilityClient(
            context.pop("backlot_url"),
            context.pop("capability_token"),
        )
        context["capability_validator"] = lambda: capability_client.validate(
            project_dir=context["project_dir"],
            project_id=context["project_id"],
            run_id=context["run_id"],
            agent_id=context["agent_id"],
        )
        return cls(**context)

    def _current_context(self):
        # The run capability is checked by the Backlot process before the
        # bridge touches the project's work order or executes any tool.
        self.capability_validator()
        try:
            context = load_manifest_stage_context(self.project_dir)
            order = read_work_order(self.project_dir)
        except (ManifestExecutionError, WorkOrderStateError, WorkOrderValidationError, OSError) as exc:
            raise AgentBridgeError(f"Backlot execution context is unavailable: {redact_text(exc)}") from exc
        if str(order.get("project_id") or "") != self.project_id:
            raise AgentBridgeError("project_id does not match the active project")
        if str(order.get("run_id") or "") != self.run_id:
            raise AgentBridgeError("run_id no longer matches the active work order")
        if not is_certified_executor_order(order):
            raise AgentBridgeError("this pipeline lane is not certified for local-agent execution")
        claim = order.get("claim") or {}
        if str(claim.get("claimed_by") or "") != self.agent_id:
            raise AgentBridgeError("this local director no longer owns the work-order lease")
        expires_raw = claim.get("lease_expires_at")
        try:
            expires = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError) as exc:
            raise AgentBridgeError("work-order lease expiry is invalid") from exc
        if expires <= datetime.now(timezone.utc):
            raise AgentBridgeError("work-order lease expired; start the director again to resume")
        return context

    def _get_registry(self):
        if self.registry is None:
            from tools.tool_registry import registry

            registry.ensure_discovered("tools")
            self.registry = registry
        return self.registry

    def _stage_tool_contracts(self, context: Any) -> list[dict[str, Any]]:
        stage_definition = context.stage_definition or {}
        declared = [str(name) for name in stage_definition.get("tools_available", []) or []]
        required = {str(name) for name in stage_definition.get("required_tools", []) or []}
        missing = sorted(required - set(declared))
        registry = self._get_registry()
        contracts: list[dict[str, Any]] = []
        for name in declared:
            tool = registry.get(name)
            if tool is None:
                contracts.append({
                    "name": name,
                    "available": False,
                    "required": name in required,
                    "status": "missing",
                    "reason": "manifest-listed tool is not registered in this installation",
                })
                continue
            try:
                status = tool.get_status().value
            except Exception as exc:
                status = "unavailable"
                reason = redact_text(exc)
            else:
                reason = None if status == "available" else "one or more declared tool dependencies are missing"
            contracts.append({
                "name": name,
                "description": next(iter(str(getattr(tool, "__doc__", "") or "").strip().splitlines()), ""),
                "provider": str(getattr(tool, "provider", "")),
                "capability": str(getattr(tool, "capability", "")),
                "runtime": str(getattr(getattr(tool, "runtime", None), "value", getattr(tool, "runtime", ""))),
                "required": name in required,
                "available": status == "available",
                "status": status,
                "reason": reason,
                "input_schema": public_input_schema(getattr(tool, "input_schema", {})),
            })
        if missing:
            raise AgentBridgeError(
                "manifest marks required tools unavailable to directors: " + ", ".join(missing)
            )
        return contracts

    def get_context(self) -> dict[str, Any]:
        context = self._current_context()
        order = context.order
        tools = self._stage_tool_contracts(context) if context.stage else []
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "execution": context.as_dict(),
            "required_artifacts_in": list(context.required_artifacts_in),
            "produces": list(context.produced_artifacts),
            "stage_tools": tools,
            "work_order_status": order.get("status"),
            "next_action": order.get("resume", {}).get("next_action"),
            "rules": [
                "Use only the listed tools for the current stage.",
                "Do not provide project, run, stage, agent, or approval controls; Backlot binds the run identity.",
                "Provider approvals are never supplied by this bridge; obey any blocked/approval-required result.",
                "The bridge renews the work-order lease while this session is connected.",
                "Stop after submitting an awaiting-human checkpoint; wait for the user to approve it in Backlot.",
            ],
        }

    def _execute_tool(self, tool_name: str, raw_inputs: Mapping[str, Any]) -> dict[str, Any]:
        context = self._current_context()
        order = context.order
        if order.get("status") in {"awaiting_approval", "completed", "cancelled"}:
            raise AgentBridgeError("the work order is paused or complete; no production tools may run")
        available = {
            item["name"]: item for item in self._stage_tool_contracts(context)
        }
        contract = available.get(str(tool_name).strip())
        if contract is None:
            raise AgentBridgeError(f"tool {tool_name!r} is not available to stage {context.stage!r}")
        if not contract.get("available"):
            reason = contract.get("reason") or "tool dependencies are unavailable"
            raise AgentBridgeError(f"tool {tool_name!r} cannot run: {reason}")
        if not isinstance(raw_inputs, Mapping):
            raise AgentBridgeError("tool inputs must be a JSON object")
        supplied = {str(key): value for key, value in raw_inputs.items()}
        protected = _find_protected_input(supplied)
        if protected:
            raise AgentBridgeError(
                "tool inputs cannot set server-bound identity or approval fields: "
                + protected
            )
        public_schema = contract["input_schema"]
        try:
            validate(instance=supplied, schema=public_schema)
        except ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "inputs"
            raise AgentBridgeError(f"tool input does not match its schema at {location}") from exc
        supplied = _bind_project_paths(supplied, self.project_dir)
        tool = self._get_registry().get(str(tool_name).strip())
        if tool is None:
            raise AgentBridgeError(f"registered tool {tool_name!r} disappeared")
        trusted = {
            "project_dir": str(self.project_dir),
            "project_id": self.project_id,
            "pipeline_type": str(order.get("pipeline_type") or ""),
            "run_id": self.run_id,
            "attempt": order.get("attempt"),
            "agent_id": self.agent_id,
            "stage": context.stage,
        }
        request = {**supplied, **trusted}
        # A model-authored tool call can never make the production approval bit true.
        runtime = str(getattr(getattr(tool, "runtime", None), "value", "")).lower()
        if str(getattr(tool, "provider", "")) == "selector" or runtime in {"api", "hybrid"}:
            request["provider_approved"] = False
            request["provider_executor"] = LocalDirectorProviderExecutor(self)
        try:
            result = tool.execute(request)
        except Exception as exc:
            raise AgentBridgeError(redact_text(exc)) from exc
        return _tool_result_dict(result)

    def submit_stage(
        self,
        artifacts: Mapping[str, Any],
        *,
        status: str = "completed",
        error: str | None = None,
    ) -> dict[str, Any]:
        context = self._current_context()
        if context.stage is None:
            raise AgentBridgeError("there is no stage left to submit")
        if context.order.get("status") in {"awaiting_approval", "completed", "cancelled"}:
            raise AgentBridgeError("the work order is paused or complete")
        if not isinstance(artifacts, Mapping):
            raise AgentBridgeError("artifacts must be an object keyed by declared artifact name")
        if status not in {"in_progress", "awaiting_human", "completed", "failed"}:
            raise AgentBridgeError("invalid stage status")
        try:
            result = submit_manifest_stage(
                self.project_dir,
                self.agent_id,
                context.stage,
                artifacts,
                status=status,
                human_approved=False,
                error=error,
                producing_tool="local-director-mcp",
            )
        except ManifestExecutionError as exc:
            raise AgentBridgeError(redact_text(exc)) from exc
        return redact_mapping(result)

    def heartbeat(self) -> dict[str, Any]:
        self._current_context()
        try:
            order = heartbeat_work_order(self.project_dir, self.agent_id)
        except (WorkOrderConflictError, WorkOrderStateError, WorkOrderValidationError) as exc:
            raise AgentBridgeError(redact_text(exc)) from exc
        return {
            "ok": True,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "lease_expires_at": (order.get("claim") or {}).get("lease_expires_at"),
        }


class LocalDirectorProviderExecutor(ProviderExecutor):
    """Require a fresh Backlot decision before a local director spends API quota."""

    def __init__(
        self,
        bridge: LocalDirectorBridge,
        *,
        store_dir: Path | str | None = None,
        sleep_fn=time.sleep,
    ) -> None:
        from lib.provider_approvals import provider_approval_store_dir

        self.bridge = bridge
        self.approval_store_dir = provider_approval_store_dir(
            bridge.project_dir,
            store_dir=store_dir,
        )
        super().__init__(
            cache_dir=bridge.project_dir / "provider_cache",
            sleep_fn=sleep_fn,
            event_sink=self._provider_event,
        )

    def _provider_event(self, payload: Mapping[str, Any]) -> None:
        try:
            from lib.events import emit_event

            emit_event(
                self.bridge.project_dir,
                {"event": "provider_attempt", **dict(payload)},
            )
        except Exception:
            pass

    def _context_matches(self, request: ProviderRequest) -> None:
        context = self.bridge._current_context()
        order = context.order
        if (
            order.get("status") != "running"
            or str(order.get("pipeline_type") or "") != str(request.pipeline_type or "")
            or str(order.get("run_id") or "") != str(request.run_id or "")
            or order.get("attempt") != request.attempt
            or str(order.get("current_stage") or "") != str(request.stage or "")
            or str(context.stage or "") != str(request.stage or "")
            or str((order.get("claim") or {}).get("claimed_by") or "") != self.bridge.agent_id
        ):
            raise AgentBridgeError("provider request no longer matches the active work-order stage")
        stage = next(
            (item for item in order.get("stages", []) if item.get("name") == request.stage),
            None,
        )
        if not stage or stage.get("status") != "running":
            raise AgentBridgeError("provider request stage is no longer running")

    @staticmethod
    def _requires_backlot_approval(request: ProviderRequest) -> bool:
        runtime = str(request.metadata.get("runtime") or "").lower()
        material_fallbacks = {
            FallbackClass.MATERIAL_PROVIDER_CHANGE.value,
            FallbackClass.MATERIAL_MEDIA_CHANGE.value,
        }
        return (
            runtime in {"api", "hybrid"}
            or request.metadata.get("requires_external_media_approval") is True
            or request.fallback_class in material_fallbacks
        )

    def _blocked_result(self, request: ProviderRequest, code: str, message: str):
        error = ProviderError(
            code=code,
            message=message,
            kind=ProviderErrorKind.APPROVAL_REQUIRED,
            retryable=False,
        )
        result = self._blocked(request, error)
        self._emit(request, "blocked", {"error": error.to_dict()})
        return result

    def _request_board_refresh(self, record: Mapping[str, Any]) -> None:
        try:
            from lib.events import emit_event

            emit_event(self.bridge.project_dir, {
                "event": "provider_approval_requested",
                "request_id": record.get("request_id"),
                "run_id": record.get("run_id"),
                "attempt": record.get("attempt"),
                "stage": record.get("stage"),
                "agent_id": record.get("agent_id"),
                "provider": record.get("provider"),
                "model": record.get("model"),
                "capability": record.get("capability"),
                "operation": record.get("operation"),
            })
        except Exception:
            pass

    def execute(
        self,
        request: ProviderRequest,
        operation,
        *,
        require_artifacts: bool = False,
        artifact_validator=None,
        validate_artifacts: bool = True,
    ):
        if not self._requires_backlot_approval(request):
            return super().execute(
                request,
                operation,
                require_artifacts=require_artifacts,
                artifact_validator=artifact_validator,
                validate_artifacts=validate_artifacts,
            )

        # A cache hit cannot spend quota or transfer media, so keep it usable
        # without a new approval. Do the lookup once to avoid a check/use race.
        cached = self._load_cached(request)
        if cached is not None:
            cached.status = ProviderResultStatus.CACHED
            cached.metadata = {**cached.metadata, "cache_hit": True}
            self._emit(request, "cache_hit", {"attempt_count": cached.attempt_count})
            return cached

        from lib.provider_approvals import (
            DEFAULT_TTL_SECONDS,
            POLL_INTERVAL_SECONDS,
            ProviderApprovalError,
            consume_provider_approval_request,
            create_provider_approval_request,
            expire_provider_approval_request,
            get_provider_approval_request,
        )

        # Approval is always obtained from Backlot. Never trust a truthy bit
        # from an agent, including one supplied by an internal selector.
        request = replace(request, approved=False)
        try:
            self._context_matches(request)
            ticket = create_provider_approval_request(
                self.bridge.project_dir,
                request,
                agent_id=self.bridge.agent_id,
                store_dir=self.approval_store_dir,
                ttl_seconds=DEFAULT_TTL_SECONDS,
            )
            self._request_board_refresh(ticket)
        except (AgentBridgeError, ProviderApprovalError) as exc:
            return self._blocked_result(
                request,
                "provider_approval_unavailable",
                f"Provider approval could not be created; no provider call was sent: {redact_text(exc)}",
            )

        while True:
            try:
                self._context_matches(request)
                current = get_provider_approval_request(
                    self.bridge.project_dir,
                    str(ticket["request_id"]),
                    store_dir=self.approval_store_dir,
                )
            except (AgentBridgeError, ProviderApprovalError) as exc:
                try:
                    expire_provider_approval_request(
                        self.bridge.project_dir,
                        str(ticket["request_id"]),
                        str(ticket["request_digest"]),
                        store_dir=self.approval_store_dir,
                    )
                except ProviderApprovalError:
                    pass
                return self._blocked_result(
                    request,
                    "provider_approval_context_stale",
                    f"The active run changed before approval; no provider call was sent: {redact_text(exc)}",
                )
            if current is None:
                return self._blocked_result(
                    request,
                    "provider_approval_missing",
                    "The provider approval request disappeared; no provider call was sent.",
                )

            status = current.get("status")
            if status == "rejected":
                return self._blocked_result(
                    request,
                    "provider_approval_rejected",
                    "You rejected this provider call in Backlot; no provider call was sent.",
                )
            if status == "expired":
                return self._blocked_result(
                    request,
                    "provider_approval_expired",
                    "This provider request expired before approval; no provider call was sent.",
                )
            if status == "approved":
                try:
                    self._context_matches(request)
                    consumed = consume_provider_approval_request(
                        self.bridge.project_dir,
                        str(ticket["request_id"]),
                        str(ticket["request_digest"]),
                        store_dir=self.approval_store_dir,
                    )
                    if not consumed:
                        return self._blocked_result(
                            request,
                            "provider_approval_replayed",
                            "This one-time approval was already used or expired; no provider call was sent.",
                        )
                    self._context_matches(request)
                except (AgentBridgeError, ProviderApprovalError) as exc:
                    return self._blocked_result(
                        request,
                        "provider_approval_context_stale",
                        f"The active run changed before provider execution; no provider call was sent: {redact_text(exc)}",
                    )
                return super().execute(
                    replace(request, approved=True),
                    operation,
                    require_artifacts=require_artifacts,
                    artifact_validator=artifact_validator,
                    validate_artifacts=validate_artifacts,
                )
            if status != "pending":
                return self._blocked_result(
                    request,
                    "provider_approval_invalid_state",
                    "The provider approval request is no longer active; no provider call was sent.",
                )
            try:
                expires = datetime.fromisoformat(str(current["expires_at"]).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
            except (KeyError, TypeError, ValueError):
                return self._blocked_result(
                    request,
                    "provider_approval_invalid_expiry",
                    "Provider approval expiry is invalid; no provider call was sent.",
                )
            remaining = (expires - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                try:
                    expire_provider_approval_request(
                        self.bridge.project_dir,
                        str(ticket["request_id"]),
                        str(ticket["request_digest"]),
                        store_dir=self.approval_store_dir,
                    )
                except ProviderApprovalError:
                    pass
                return self._blocked_result(
                    request,
                    "provider_approval_expired",
                    "This provider request expired before approval; no provider call was sent.",
                )
            self.sleep_fn(min(POLL_INTERVAL_SECONDS, remaining))


class _LeaseKeeper:
    def __init__(self, bridge: LocalDirectorBridge, interval_seconds: int = 60) -> None:
        self.bridge = bridge
        self.interval_seconds = max(15, int(interval_seconds))
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="openmontage-lease", daemon=True)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self.bridge.heartbeat()
            except AgentBridgeError as exc:
                sys.stderr.write(f"OpenMontage lease heartbeat failed: {redact_text(exc)}\n")
                return

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


class OpenMontageMCPServer:
    """Small protocol adapter, imported lazily so help/tests need no SDK startup."""

    def __init__(self, bridge: LocalDirectorBridge) -> None:
        from mcp.server.mcpserver import MCPServer
        from mcp.types import CallToolResult, TextContent, Tool

        class _Server(MCPServer):
            async def list_tools(self):
                return [Tool(**item) for item in _MCP_TOOLS]

            async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None):
                try:
                    schema_entry = next((item for item in _MCP_TOOLS if item["name"] == name), None)
                    if schema_entry is None:
                        raise AgentBridgeError("unknown OpenMontage MCP tool")
                    try:
                        validate(instance=arguments or {}, schema=schema_entry["inputSchema"])
                    except ValidationError as exc:
                        location = ".".join(str(part) for part in exc.absolute_path) or "inputs"
                        raise AgentBridgeError(f"OpenMontage MCP arguments are invalid at {location}") from exc
                    if name == "openmontage_get_context":
                        payload = await asyncio.to_thread(bridge.get_context)
                    elif name == "openmontage_execute_tool":
                        payload = await asyncio.to_thread(
                            bridge._execute_tool,
                            str(arguments.get("tool_name") or ""),
                            arguments.get("inputs") or {},
                        )
                    elif name == "openmontage_submit_stage":
                        payload = await asyncio.to_thread(
                            bridge.submit_stage,
                            arguments.get("artifacts") or {},
                            status=str(arguments.get("status") or "completed"),
                            error=arguments.get("error"),
                        )
                    safe = redact_mapping(payload)
                    encoded = json.dumps(safe, ensure_ascii=False, default=str)
                    return CallToolResult(
                        content=[TextContent(type="text", text=encoded)],
                        structuredContent=safe,
                    )
                except Exception as exc:
                    message = redact_text(exc)
                    return CallToolResult(
                        content=[TextContent(type="text", text=message)],
                        isError=True,
                    )

        self.server = _Server(
            name=LOCAL_MCP_SERVER_NAME,
            title="OpenMontage local director tools",
            instructions=(
                "Use these tools to direct one locally claimed OpenMontage run. "
                "Read the current context first, call only its stage tools, and "
                "submit manifest artifacts without bypassing human or provider approvals."
            ),
        )

    def run(self) -> None:
        self.server.run(transport="stdio")


def main() -> int:
    try:
        # Verify the scoped run capability before loading production provider
        # secrets into this tool-server process.
        bridge = LocalDirectorBridge.from_environment()
        # Production credentials stay in this private tool-server process.
        # The surrounding local director CLI is launched with credential-like
        # environment variables removed and never receives this .env content.
        from lib.env_loader import load_env

        load_env()
        # Force registry discovery after the MCP server loads the app runtime's
        # production credentials for provider tools.
        bridge._get_registry()
        keeper = _LeaseKeeper(bridge)
    except Exception as exc:
        sys.stderr.write(f"OpenMontage local tool bridge could not start: {redact_text(exc)}\n")
        return 2
    server = OpenMontageMCPServer(bridge)
    keeper.start()
    try:
        server.run()
    finally:
        keeper.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AgentBridgeError",
    "LocalDirectorBridge",
    "OpenMontageMCPServer",
    "main",
    "public_input_schema",
]
