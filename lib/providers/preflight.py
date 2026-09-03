"""Fast, cacheable provider preflight and explicitly live diagnostics.

The registry's historic ``get_status`` methods are useful diagnostics, but a
number of API-backed tools perform network calls from that method.  Calling
those probes while a user is only trying to plan a video made the preflight
path slow and nondeterministic.  This module therefore keeps two intentionally
different operations:

``fast_preflight``
    Local-only dependency inspection.  It never calls ``get_status`` and
    never opens a socket.  Results are safe to cache for a short TTL.

``deep_preflight``
    Opt-in diagnostics.  It may call each tool's live status method, but every
    call is bounded by a timeout and the result records that a live probe ran.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.base_tool import ToolRuntime, ToolStatus


class PreflightStatus(str, Enum):
    CONFIGURED = "configured"
    AVAILABLE_LOCAL = "available_local"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNTESTED = "untested"
    REQUIRES_LIVE_PROBE = "requires_live_probe"


@dataclass(frozen=True)
class DependencyCheck:
    dependency: str
    satisfied: bool | None
    kind: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependency": self.dependency,
            "satisfied": self.satisfied,
            "kind": self.kind,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PreflightRecord:
    tool: str
    provider: str
    capability: str
    runtime: str
    status: PreflightStatus
    reasons: tuple[str, ...] = ()
    dependencies: tuple[DependencyCheck, ...] = ()
    live_probe: bool = False
    checked_at: str = ""
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "provider": self.provider,
            "capability": self.capability,
            "runtime": self.runtime,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "live_probe": self.live_probe,
            "checked_at": self.checked_at,
            "cached": self.cached,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_iter(registry_or_tools: Any) -> list[Any]:
    if hasattr(registry_or_tools, "ensure_discovered"):
        registry_or_tools.ensure_discovered()
    if hasattr(registry_or_tools, "list_all") and hasattr(registry_or_tools, "get"):
        return [registry_or_tools.get(name) for name in registry_or_tools.list_all() if registry_or_tools.get(name) is not None]
    return list(registry_or_tools or [])


def _dependency_check(dependency: Any) -> DependencyCheck:
    raw = str(dependency or "").strip()
    if raw.startswith("env:"):
        name = raw[4:]
        present = bool(os.environ.get(name))
        return DependencyCheck(raw, present, "env", f"{name} is {'set' if present else 'not set'}")
    if raw.startswith(("cmd:", "binary:")):
        prefix = "cmd:" if raw.startswith("cmd:") else "binary:"
        name = raw[len(prefix):]
        present = shutil.which(name) is not None
        return DependencyCheck(raw, present, prefix[:-1], f"{name} is {'on PATH' if present else 'not on PATH'}")
    if raw.startswith("python:"):
        name = raw[7:]
        present = importlib.util.find_spec(name) is not None
        return DependencyCheck(raw, present, "python", f"{name} is {'importable' if present else 'not importable'}")
    if not raw:
        return DependencyCheck(raw, None, "unknown", "empty dependency declaration")
    return DependencyCheck(raw, None, "unknown", "dependency kind is not locally testable")


def _record_for_tool(tool: Any, *, live_probe: bool = False, timeout_seconds: float = 3.0) -> PreflightRecord:
    name = str(getattr(tool, "name", "") or tool.__class__.__name__)
    provider = str(getattr(tool, "provider", "") or "")
    capability = str(getattr(tool, "capability", "generic") or "generic")
    runtime_obj = getattr(tool, "runtime", ToolRuntime.LOCAL)
    runtime = getattr(runtime_obj, "value", str(runtime_obj))
    dependencies = tuple(_dependency_check(dep) for dep in (getattr(tool, "dependencies", []) or []))
    known = [item.satisfied for item in dependencies if item.satisfied is not None]
    unknown = any(item.satisfied is None for item in dependencies)
    missing = any(item.satisfied is False for item in dependencies)
    reasons: list[str] = []

    if live_probe:
        status, reason = _live_status(tool, timeout_seconds)
        if status == ToolStatus.AVAILABLE:
            mapped = PreflightStatus.AVAILABLE_LOCAL if runtime in {ToolRuntime.LOCAL.value, ToolRuntime.LOCAL_GPU.value} else PreflightStatus.CONFIGURED
        elif status == ToolStatus.DEGRADED:
            mapped = PreflightStatus.DEGRADED
        else:
            mapped = PreflightStatus.UNAVAILABLE
        reasons.append(reason)
        return PreflightRecord(name, provider, capability, runtime, mapped, tuple(reasons), dependencies, True, _now_iso())

    # Selectors aggregate other tools and do not have a direct dependency
    # contract.  Marking them untested avoids falsely claiming a provider path
    # is executable before candidates are ranked.
    if provider == "selector":
        return PreflightRecord(name, provider, capability, runtime, PreflightStatus.UNTESTED,
                               ("selector status is derived from child providers",), dependencies, False, _now_iso())

    # Local GPU tools backed by a service client (ComfyUI is the current
    # example) have no static dependency that can prove the server/model is
    # ready.  Do not report them as locally available without an explicit live
    # health/model probe.
    if hasattr(tool, "_client"):
        return PreflightRecord(
            name,
            provider,
            capability,
            runtime,
            PreflightStatus.UNTESTED,
            ("service/client readiness requires an explicit live probe",),
            dependencies,
            False,
            _now_iso(),
        )

    if runtime in {ToolRuntime.LOCAL.value, ToolRuntime.LOCAL_GPU.value}:
        if missing:
            mapped = PreflightStatus.UNAVAILABLE
            reasons.extend(item.detail for item in dependencies if item.satisfied is False)
        elif unknown:
            mapped = PreflightStatus.UNTESTED
            reasons.append("one or more dependencies require a tool-specific check")
        else:
            mapped = PreflightStatus.AVAILABLE_LOCAL
            reasons.append("local dependencies satisfied; no network probe performed")
    elif runtime == ToolRuntime.API.value:
        if missing:
            mapped = PreflightStatus.UNAVAILABLE
            reasons.extend(item.detail for item in dependencies if item.satisfied is False)
        elif dependencies and not unknown:
            mapped = PreflightStatus.REQUIRES_LIVE_PROBE
            reasons.append("credentials/dependencies are present; reachability was not tested")
        else:
            mapped = PreflightStatus.UNTESTED
            reasons.append("API tool has dynamic or undeclared configuration; live probe required")
    elif runtime == ToolRuntime.HYBRID.value:
        if missing and any(item.kind == "env" for item in dependencies):
            mapped = PreflightStatus.DEGRADED
            reasons.extend(item.detail for item in dependencies if item.satisfied is False)
            reasons.append("a local or alternate provider may still be available")
        elif missing:
            mapped = PreflightStatus.UNAVAILABLE
            reasons.extend(item.detail for item in dependencies if item.satisfied is False)
        elif unknown:
            mapped = PreflightStatus.UNTESTED
            reasons.append("hybrid provider configuration needs diagnostics")
        elif dependencies:
            mapped = PreflightStatus.REQUIRES_LIVE_PROBE
            reasons.append("hybrid dependencies are configured; reachability was not tested")
        else:
            mapped = PreflightStatus.UNTESTED
            reasons.append("hybrid tool declares no static dependencies")
    else:
        mapped = PreflightStatus.UNTESTED
        reasons.append(f"runtime {runtime!r} is not locally classified")

    return PreflightRecord(name, provider, capability, runtime, mapped, tuple(reasons), dependencies, False, _now_iso())


def _live_status(tool: Any, timeout_seconds: float) -> tuple[ToolStatus, str]:
    pool: ThreadPoolExecutor | None = None
    try:
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preflight")
        future = pool.submit(tool.get_status)
        try:
            status = future.result(timeout=max(0.01, float(timeout_seconds)))
        except FutureTimeoutError:
            future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            return ToolStatus.UNAVAILABLE, f"live probe timed out after {timeout_seconds:g}s"
        pool.shutdown(wait=True)
        if isinstance(status, ToolStatus):
            return status, f"live probe returned {status.value}"
        return ToolStatus.UNAVAILABLE, f"live probe returned unknown status {status!r}"
    except Exception as exc:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        return ToolStatus.UNAVAILABLE, f"live probe failed: {exc.__class__.__name__}: {str(exc)[:160]}"


def _cache_key(records: Iterable[PreflightRecord]) -> str:
    payload = [
        {
            "tool": item.tool,
            "provider": item.provider,
            "capability": item.capability,
            "runtime": item.runtime,
            "dependencies": [dep.to_dict() for dep in item.dependencies],
        }
        for item in records
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _summary(records: list[PreflightRecord]) -> dict[str, Any]:
    counts: dict[str, int] = {status.value: 0 for status in PreflightStatus}
    capabilities: dict[str, dict[str, int]] = {}
    for item in records:
        counts[item.status.value] += 1
        bucket = capabilities.setdefault(item.capability, {status.value: 0 for status in PreflightStatus})
        bucket[item.status.value] += 1
    return {"counts": counts, "capabilities": dict(sorted(capabilities.items()))}


def fast_preflight(
    registry_or_tools: Any,
    *,
    cache_path: Path | str | None = None,
    ttl_seconds: float = 30.0,
    now: float | None = None,
) -> dict[str, Any]:
    """Return a local-only, cacheable preflight report.

    The function intentionally never calls ``tool.get_status`` or any network
    API.  A cache hit is accepted only when the dependency fingerprint and TTL
    still match, so changing an API key or installing FFmpeg invalidates it.
    """
    started = time.monotonic()
    current = time.time() if now is None else float(now)
    tools = _tool_iter(registry_or_tools)
    provisional = [_record_for_tool(tool) for tool in tools]
    key = _cache_key(provisional)
    path = Path(cache_path).expanduser() if cache_path else None
    if path and path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            age = current - float(payload.get("created_epoch", 0))
            if payload.get("cache_key") == key and age >= 0 and age <= max(0.0, float(ttl_seconds)):
                records = []
                for raw in payload.get("records", []):
                    item = dict(raw)
                    item["cached"] = True
                    records.append(item)
                return {
                    "version": "1.0",
                    "mode": "fast",
                    "cached": True,
                    "cache_key": key,
                    "created_at": payload.get("created_at", _now_iso()),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "records": records,
                    "summary": payload.get("summary", {}),
                }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    record_objects = [_record_for_tool(tool) for tool in tools]
    records = [item.to_dict() for item in record_objects]
    report = {
        "version": "1.0",
        "mode": "fast",
        "cached": False,
        "cache_key": key,
        "created_at": _now_iso(),
        "created_epoch": current,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "records": records,
        "summary": _summary(record_objects),
    }
    if path:
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass
    report.pop("created_epoch", None)
    return report


def deep_preflight(
    registry_or_tools: Any,
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    """Run bounded live diagnostics; never used by the fast planning path."""
    started = time.monotonic()
    records = [_record_for_tool(tool, live_probe=True, timeout_seconds=timeout_seconds) for tool in _tool_iter(registry_or_tools)]
    return {
        "version": "1.0",
        "mode": "deep",
        "cached": False,
        "created_at": _now_iso(),
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "records": [item.to_dict() for item in records],
        "summary": _summary(records),
    }


__all__ = [
    "DependencyCheck",
    "PreflightRecord",
    "PreflightStatus",
    "deep_preflight",
    "fast_preflight",
]
