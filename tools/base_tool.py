"""Base tool class implementing the expanded ToolContract.

Every tool in OpenMontage inherits from BaseTool. This enforces a uniform
interface for discovery, execution, cost estimation, and health reporting.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import platform
import signal
import subprocess
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from lib.secrets import redact_text
from lib.observability import metrics


def _load_dotenv() -> None:
    """Load .env into os.environ once at import time.

    This ensures API keys are available before any tool is instantiated,
    even when tools are imported directly without going through the registry.
    Only sets variables that are not already in the environment.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    import re
    with open(env_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Quoted value: take the content inside the quotes verbatim.
            if value[:1] in ("'", '"'):
                quote = value[0]
                end = value.find(quote, 1)
                value = value[1:end] if end != -1 else value[1:]
            else:
                # Strip an inline comment ('#' at line start or after
                # whitespace) so "VAR=   # note" yields "" not "# note".
                match = re.search(r"(^|\s)#", value)
                if match:
                    value = value[: match.start()]
                value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


class ToolTier(str, Enum):
    CORE = "core"
    VOICE = "voice"
    ENHANCE = "enhance"
    GENERATE = "generate"
    SOURCE = "source"
    ANALYZE = "analyze"
    PUBLISH = "publish"


class ToolStability(str, Enum):
    EXPERIMENTAL = "experimental"
    BETA = "beta"
    PRODUCTION = "production"


class ToolStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class ToolRuntime(str, Enum):
    """Where and how a tool executes."""
    LOCAL = "local"            # Runs entirely on-device, free, no network
    LOCAL_GPU = "local_gpu"    # Runs on-device but needs GPU (VRAM)
    API = "api"                # Calls an external API, requires API key, costs money
    HYBRID = "hybrid"          # Can run locally OR via API (e.g., image_selector)


class ExecutionMode(str, Enum):
    SYNC = "sync"
    ASYNC = "async"


class Determinism(str, Enum):
    DETERMINISTIC = "deterministic"
    SEEDED = "seeded"
    STOCHASTIC = "stochastic"


class ResumeSupport(str, Enum):
    NONE = "none"
    FROM_START = "from_start"
    FROM_CHECKPOINT = "from_checkpoint"


@dataclass
class ResourceProfile:
    """Hardware resource envelope for a tool."""
    cpu_cores: int = 1
    ram_mb: int = 512
    vram_mb: int = 0
    disk_mb: int = 100
    network_required: bool = False


@dataclass
class RetryPolicy:
    """Safe retry behavior for a tool."""
    max_retries: int = 0
    backoff_seconds: float = 1.0
    retryable_errors: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    """Standard result returned by tool execution."""
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    error: Optional[str] = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    seed: Optional[int] = None
    model: Optional[str] = None

    def __post_init__(self) -> None:
        # Provider exceptions often contain request URLs or echoed headers.
        # Redact at the common result boundary before errors can reach an event
        # log, run record, CLI, or Backlot response.
        if self.error is not None:
            self.error = redact_text(self.error)


import threading as _threading

# Shared nesting counter for instrumented execute() calls (thread-local so
# parallel tool threads don't see each other's depth).
_EXECUTE_DEPTH = _threading.local()


def _instrument_execute(fn: Callable) -> Callable:
    """Wrap a tool's execute() with Backlot event emission.

    Appends start/finish/error entries to the owning project's events.jsonl
    when the call can be attributed to a project (explicit project_dir input
    or any path input under projects/). Powers the board's live activity
    ticker and per-scene generating states with zero agent involvement.

    Instrumentation is strictly non-fatal: any failure inside the event layer
    is swallowed and the tool call proceeds untouched.
    """
    if getattr(fn, "_backlot_instrumented", False):
        return fn

    depth_state = _EXECUTE_DEPTH  # shared across all tools (selector → provider)

    @functools.wraps(fn)
    def wrapper(self, inputs: Any, *args: Any, **kwargs: Any):
        # Enforce declared top-level boolean types before instrumentation,
        # provider discovery, filesystem mutation, or network work.  A schema
        # saying ``type: boolean`` must not be defeated by Python truthiness
        # (for example, ``"false"`` or ``1``).  Tool-specific contracts still
        # validate nested objects and richer cross-field rules.
        if isinstance(inputs, dict) and getattr(self, "schema_boolean_validation", True):
            schema = getattr(self, "input_schema", {})
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            if isinstance(properties, dict):
                for field_name, field_schema in properties.items():
                    if (
                        isinstance(field_schema, dict)
                        and field_schema.get("type") == "boolean"
                        and field_name in inputs
                        and not isinstance(inputs[field_name], bool)
                    ):
                        return ToolResult(
                            success=False,
                            error=f"{field_name} must be boolean",
                        )
        # Event layer is fully optional: if it can't import, run untouched.
        try:
            from lib.events import emit_event, infer_project_dir
        except Exception:
            return fn(self, inputs, *args, **kwargs)

        tool_name = getattr(self, "name", "") or self.__class__.__name__
        scene_id = inputs.get("scene_id") if isinstance(inputs, dict) else None
        output_path = inputs.get("output_path") if isinstance(inputs, dict) else None
        stage = inputs.get("stage") if isinstance(inputs, dict) else None
        agent_id = inputs.get("agent_id") if isinstance(inputs, dict) else None
        # Nesting depth: selector tools delegate to provider tools' execute().
        # Both emit (the ticker wants the provider name too), but depth lets
        # consumers dedupe — e.g. sum cost_usd only at depth 0.
        depth = getattr(depth_state, "value", 0)
        depth_state.value = depth + 1
        project_dir = infer_project_dir(inputs)

        base = {
            "tool": tool_name,
            "scene_id": scene_id,
            "stage": stage,
            "agent_id": agent_id,
            "depth": depth if depth else None,
        }
        if project_dir is not None:
            emit_event(project_dir, {
                **base, "event": "start",
                "output_path": str(output_path) if output_path else None,
            })

        started = time.monotonic()
        try:
            metrics.increment("openmontage_tool_calls_total", labels={"tool": tool_name, "status": "started"})
        except Exception:
            pass
        try:
            # Identity-bearing provider calls are automatically routed through
            # the common kernel.  Selectors call the bridge explicitly, while
            # a direct provider invocation in a production project/run cannot
            # silently bypass timeout, idempotency, cost, and artifact policy.
            # The bridge adds ``_provider_executor_bypass`` only for its
            # implementation callback, so provider code itself is not
            # recursively wrapped.
            kernel_lane = (
                isinstance(inputs, dict)
                and getattr(self, "provider", "") not in {"", "openmontage", "selector"}
                and not inputs.get("_provider_executor_bypass")
                and (
                    inputs.get("provider_kernel") is True
                    or inputs.get("project_dir")
                    or inputs.get("run_id")
                )
            )
            if kernel_lane:
                from lib.providers.bridge import execute_with_provider_executor

                result = execute_with_provider_executor(
                    self,
                    inputs,
                    implementation=lambda provider_inputs: fn(self, provider_inputs, *args, **kwargs),
                )
            else:
                result = fn(self, inputs, *args, **kwargs)
        except Exception as exc:
            elapsed = max(0.0, time.monotonic() - started)
            try:
                metrics.increment("openmontage_tool_calls_total", labels={"tool": tool_name, "status": "failed"})
                metrics.observe("openmontage_tool_duration_seconds", elapsed, labels={"tool": tool_name})
            except Exception:
                pass
            if project_dir is not None:
                emit_event(project_dir, {
                    **base, "event": "error",
                    "error": redact_text(str(exc)[:300]),
                    "duration_s": round(time.monotonic() - started, 2),
                })
            raise
        finally:
            depth_state.value = depth

        if project_dir is None:
            # The tool may have created its own project dir during execute
            # (first call of a run) — attribute the finish if possible.
            project_dir = infer_project_dir(inputs)
        if project_dir is not None:
            # Attach durable run provenance to the result and register any
            # local artifacts.  This is intentionally best-effort: telemetry
            # must never turn a successful creative tool call into a failure.
            try:
                from lib.run_record import build_result_provenance, record_tool_result

                provenance = build_result_provenance(
                    project_dir,
                    tool=tool_name,
                    stage=stage,
                    agent_id=agent_id,
                )
                record_tool_result(
                    project_dir,
                    result,
                    tool=tool_name,
                    inputs=inputs if isinstance(inputs, dict) else None,
                    stage=stage,
                )
                if provenance is not None and hasattr(result, "data"):
                    if not isinstance(result.data, dict):
                        result.data = {}
                    result.data["provenance"] = provenance
            except Exception:
                # The underlying ToolResult remains authoritative. A later
                # run-record reconciliation can recover missing telemetry.
                pass
            cost = getattr(result, "cost_usd", None)
            try:
                status = "succeeded" if getattr(result, "success", False) else "failed"
                metrics.increment("openmontage_tool_calls_total", labels={"tool": tool_name, "status": status})
                metrics.observe(
                    "openmontage_tool_duration_seconds",
                    max(0.0, time.monotonic() - started),
                    labels={"tool": tool_name},
                )
                if isinstance(cost, (int, float)):
                    metrics.increment(
                        "openmontage_tool_cost_usd_total",
                        float(cost),
                        labels={"tool": tool_name},
                    )
            except Exception:
                pass
            emit_event(project_dir, {
                **base, "event": "finish",
                "output_path": str(output_path) if output_path else None,
                "success": getattr(result, "success", None),
                # NOTE: 0.0 is meaningful (ran for free) — only None is dropped.
                "cost_usd": cost if isinstance(cost, (int, float)) else None,
                "duration_s": round(time.monotonic() - started, 2),
            })
        return result

    wrapper._backlot_instrumented = True  # type: ignore[attr-defined]
    return wrapper


class BaseTool(ABC):
    """Abstract base class for all OpenMontage tools."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Auto-instrument every concrete execute() with Backlot events."""
        super().__init_subclass__(**kwargs)
        impl = cls.__dict__.get("execute")
        if impl is not None and not getattr(impl, "__isabstractmethod__", False):
            cls.execute = _instrument_execute(impl)

    # --- Identity (override in subclasses) ---
    name: str = ""
    version: str = "0.1.0"
    tier: ToolTier = ToolTier.CORE
    stability: ToolStability = ToolStability.EXPERIMENTAL
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    determinism: Determinism = Determinism.DETERMINISTIC
    runtime: ToolRuntime = ToolRuntime.LOCAL

    # --- Dependencies ---
    # For API tools, add "env:ENVVAR_NAME" to signal required API keys
    dependencies: list[str] = []
    install_instructions: str = ""

    # --- Capabilities ---
    capability: str = "generic"
    provider: str = "openmontage"
    capabilities: list[str] = []
    input_schema: dict = {}
    # Most tools use the shared pre-execution schema guard.  A small number of
    # report-producing QA tools may opt out when their own strict parser must
    # preserve a structured ``revise`` report instead of returning a transport
    # failure; such classes must document and enforce that parser themselves.
    schema_boolean_validation: bool = True
    output_schema: dict = {}
    artifact_schema: dict = {}
    progress_schema: Optional[dict] = None
    supports: dict[str, Any] = {}
    best_for: list[str] = []
    not_good_for: list[str] = []
    provider_matrix: dict[str, Any] = {}

    # --- Resource & retry ---
    resource_profile: ResourceProfile = ResourceProfile()
    retry_policy: RetryPolicy = RetryPolicy()

    # --- Resume & idempotency ---
    resume_support: ResumeSupport = ResumeSupport.NONE
    idempotency_key_fields: list[str] = []

    # --- Side effects & fallback ---
    side_effects: list[str] = []
    fallback: Optional[str] = None
    fallback_tools: list[str] = []

    # --- Agent skills (Layer 3 references) ---
    # Names of installed agent skills in .agents/skills/ that teach the
    # underlying technology. The orchestrator uses these to load relevant
    # API knowledge when planning tool usage.
    agent_skills: list[str] = []

    # --- Verification ---
    user_visible_verification: list[str] = []

    # --- Optional telemetry / quality hints for the scoring engine ---
    # If set (0.0-1.0), lib/scoring.py uses these directly instead of falling
    # back to stability-based heuristics. Leave unset unless the tool has a
    # real measured or well-calibrated value.
    quality_score: Optional[float] = None
    historical_success_rate: Optional[float] = None
    latency_p50_seconds: Optional[float] = None

    # ---- Status reporting ----

    def get_status(self) -> ToolStatus:
        """Check if this tool's dependencies are satisfied."""
        try:
            self.check_dependencies()
            return ToolStatus.AVAILABLE
        except DependencyError:
            return ToolStatus.UNAVAILABLE

    def check_dependencies(self) -> None:
        """Verify all dependencies are installed. Raises DependencyError if not."""
        for dep in self.dependencies:
            if dep.startswith(("cmd:", "binary:")):
                prefix = "cmd:" if dep.startswith("cmd:") else "binary:"
                cmd_name = dep[len(prefix):]
                if shutil.which(cmd_name) is None:
                    raise DependencyError(
                        f"Command {cmd_name!r} not found. {self.install_instructions}"
                    )
            elif dep.startswith("env:"):
                env_name = dep[4:]
                if not os.environ.get(env_name):
                    raise DependencyError(
                        f"Environment variable {env_name!r} not set. {self.install_instructions}"
                    )
            elif dep.startswith("python:"):
                module_name = dep[7:]
                try:
                    __import__(module_name)
                except ImportError:
                    raise DependencyError(
                        f"Python module {module_name!r} not installed. {self.install_instructions}"
                    )

    def get_info(self, *, include_status: bool = True) -> dict[str, Any]:
        """Return full tool contract info for registry/discovery.

        ``include_status=False`` is used by fast catalog/preflight paths to
        avoid invoking provider health checks.  The default remains the live
        status behavior used by existing diagnostics and support reports.
        """
        usage_location = inspect.getfile(self.__class__)
        return {
            "name": self.name,
            "version": self.version,
            "tier": self.tier.value,
            "capability": self.capability,
            "provider": self.provider,
            "stability": self.stability.value,
            "status": self.get_status().value if include_status else ToolStatus.UNAVAILABLE.value,
            "execution_mode": self.execution_mode.value,
            "determinism": self.determinism.value,
            "runtime": self.runtime.value,
            "module_path": self.__class__.__module__,
            "usage_location": usage_location,
            "dependencies": self.dependencies,
            "install_instructions": self.install_instructions,
            "capabilities": self.capabilities,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "artifact_schema": self.artifact_schema,
            "supports": self.supports,
            "best_for": self.best_for,
            "not_good_for": self.not_good_for,
            "provider_matrix": self.provider_matrix,
            "resource_profile": {
                "cpu_cores": self.resource_profile.cpu_cores,
                "ram_mb": self.resource_profile.ram_mb,
                "vram_mb": self.resource_profile.vram_mb,
                "disk_mb": self.resource_profile.disk_mb,
                "network_required": self.resource_profile.network_required,
            },
            "resume_support": self.resume_support.value,
            "side_effects": self.side_effects,
            "fallback": self.fallback,
            "fallback_tools": self.fallback_tools or ([self.fallback] if self.fallback else []),
            "agent_skills": self.agent_skills,
            "related_skills": self.agent_skills,
            "user_visible_verification": self.user_visible_verification,
            "quality_score": self.quality_score,
            "historical_success_rate": self.historical_success_rate,
            "latency_p50_seconds": self.latency_p50_seconds,
        }

    # ---- Cost estimation ----

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """Estimate cost in USD for the given inputs. Override for paid tools."""
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        """Estimate runtime in seconds. Override for long-running tools."""
        return 0.0

    # ---- Idempotency ----

    def idempotency_key(self, inputs: dict[str, Any]) -> str:
        """Compute a cache key from idempotency fields."""
        key_data = {k: inputs.get(k) for k in self.idempotency_key_fields}
        raw = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ---- Execution ----

    @abstractmethod
    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Run the tool. Subclasses must implement this."""
        ...

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Preflight check without side effects. Override for paid/publishing tools."""
        return {
            "tool": self.name,
            "estimated_cost_usd": self.estimate_cost(inputs),
            "estimated_runtime_seconds": self.estimate_runtime(inputs),
            "status": self.get_status().value,
            "would_execute": True,
        }

    # ---- CLI helper ----

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        """Terminate a CLI process and any wrapper children it spawned.

        Windows ``.CMD`` shims such as npm/npx create a child Node process.
        Killing only the shim can leave that child holding inherited output
        handles, which makes timeout cleanup block forever.  ``taskkill /T``
        handles the Windows tree; POSIX commands run in their own session so
        the complete process group can be terminated there as well.
        """
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            try:
                if process.poll() is None:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        try:
            if process.poll() is None:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            pass

    @classmethod
    def _run_bounded_process(
        cls,
        cmd: list[str],
        *,
        timeout: Optional[float],
        cwd: Optional[Path] = None,
    ) -> subprocess.CompletedProcess:
        """Run a CLI without allowing wrapper descendants to defeat timeout.

        Output is captured in temporary files rather than OS pipes. A child
        retaining a pipe after a Windows ``.CMD`` wrapper exits can otherwise
        make ``communicate()`` wait indefinitely after the deadline.
        """
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt"
            else 0
        )
        stdout_file = tempfile.TemporaryFile(mode="w+b")
        stderr_file = tempfile.TemporaryFile(mode="w+b")

        def read_capture(stream: Any) -> str:
            try:
                stream.flush()
                stream.seek(0)
                return stream.read().decode("utf-8", errors="replace")
            except (OSError, ValueError, AttributeError):
                return ""

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=str(cwd) if cwd else None,
                creationflags=creationflags,
                start_new_session=(os.name != "nt"),
            )
        except BaseException:
            stdout_file.close()
            stderr_file.close()
            raise
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            cls._terminate_process_tree(process)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except (OSError, subprocess.SubprocessError):
                    pass
                try:
                    process.wait(timeout=1)
                except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                    pass
            result = subprocess.CompletedProcess(
                args=cmd,
                returncode=124,
                stdout=read_capture(stdout_file),
                stderr=f"{read_capture(stderr_file)}\n[timeout after {timeout}s]",
            )
            setattr(result, "_openmontage_timed_out", True)
            return result
        else:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=process.returncode,
                stdout=read_capture(stdout_file),
                stderr=read_capture(stderr_file),
            )
        finally:
            try:
                stdout_file.close()
            except (OSError, ValueError):
                pass
            try:
                stderr_file.close()
            except (OSError, ValueError):
                pass

    def run_command(
        self,
        cmd: list[str],
        *,
        timeout: Optional[int] = None,
        cwd: Optional[Path] = None,
    ) -> subprocess.CompletedProcess:
        """Run a subprocess command with standard error handling.

        On Windows, resolves .cmd/.bat wrappers (e.g. npx, npm) via
        shutil.which() so the bounded process helper can resolve them without
        shell=True.
        """
        resolved_cmd = list(cmd)
        if platform.system() == "Windows" and resolved_cmd:
            exe = shutil.which(resolved_cmd[0])
            if exe:
                resolved_cmd[0] = exe
        completed = self._run_bounded_process(
            resolved_cmd,
            timeout=timeout,
            cwd=cwd,
        )
        if getattr(completed, "_openmontage_timed_out", False):
            raise subprocess.TimeoutExpired(
                resolved_cmd,
                timeout,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            detail = stderr or stdout or f"command exited with status {completed.returncode}"
            raise ToolCommandError(
                completed.returncode,
                resolved_cmd,
                output=completed.stdout,
                stderr=completed.stderr,
                detail=detail,
            )
        return completed


class ToolCommandError(subprocess.CalledProcessError):
    """CalledProcessError with stderr/stdout surfaced in str(error)."""

    def __init__(
        self,
        returncode: int,
        cmd: list[str],
        *,
        output: Optional[str] = None,
        stderr: Optional[str] = None,
        detail: str = "",
    ) -> None:
        super().__init__(returncode, cmd, output=output, stderr=stderr)
        self.detail = detail

    def __str__(self) -> str:
        base = super().__str__()
        if self.detail:
            return f"{base}\n{self.detail}"
        return base


class DependencyError(Exception):
    """Raised when a tool's dependency is not satisfied."""
    pass
