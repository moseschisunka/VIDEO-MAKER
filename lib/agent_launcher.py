"""Launch contract for an external OpenMontage production agent.

OpenMontage owns the durable work order and the creative tools, but it does
not embed an LLM or pretend that a Python demo runner is a production agent.
This module is the small process boundary between Backlot and the configured
agent application (Codex CLI, Antigravity, or a project-specific worker).

The command is deliberately configured by the operator. It is parsed into an
argument vector and launched with ``shell=False``; project and run identity are
provided through ``OPENMONTAGE_*`` environment variables so the adapter does
not have to invent a command-line protocol for every agent implementation.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.paths import REPO_ROOT, resource_path, runtime_root
from lib.secrets import redact_text
from lib.local_director_capabilities import (
    issue_local_director_capability,
    revoke_local_director_capability,
)
from lib.local_director import (
    LOCAL_MCP_SERVER_NAME,
    LocalDirectorError,
    director_launcher_command,
    director_environment,
    director_from_command,
    normalize_director,
)

AGENT_COMMAND_ENV = "OPENMONTAGE_AGENT_COMMAND"
AGENT_ID_ENV = "OPENMONTAGE_AGENT_ID"
DEFAULT_AGENT_ID = "openmontage-agent"
PROCESS_RECORD_NAME = "agent_process.json"
PROCESS_LOG_NAME = "agent.log"


class AgentConfigurationError(ValueError):
    """Raised when the configured external agent command is unusable."""


class AgentLaunchError(RuntimeError):
    """Raised when a configured external agent cannot be started."""


@dataclass(frozen=True)
class AgentLaunch:
    """Durable metadata returned after a process has been started."""

    pid: int
    agent_id: str
    run_id: str
    started_at: str
    log_path: str
    command: tuple[str, ...]
    cwd: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "started",
            "pid": self.pid,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "log_path": self.log_path,
            "command": list(self.command),
            "cwd": self.cwd,
        }


def configured_agent_id() -> str:
    """Return the operator-selected agent id used for automatic launches."""
    value = os.environ.get(AGENT_ID_ENV, DEFAULT_AGENT_ID).strip()
    return value or DEFAULT_AGENT_ID


def configured_agent_command() -> tuple[str, ...] | None:
    """Parse the trusted operator command, or return ``None`` when absent.

    Commands use shell-like quoting even on Windows.  They are never handed
    to a shell, which prevents metacharacters from becoming a second command.
    """
    raw = os.environ.get(AGENT_COMMAND_ENV, "").strip()
    if not raw:
        return None
    try:
        argv = tuple(shlex.split(raw, posix=True))
    except ValueError as exc:
        raise AgentConfigurationError(
            f"{AGENT_COMMAND_ENV} has invalid quoting: {exc}"
        ) from exc
    if not argv:
        raise AgentConfigurationError(f"{AGENT_COMMAND_ENV} must contain an executable")
    return argv


def agent_command_status(director: str | None = None) -> dict[str, Any]:
    """Return a safe summary for the configured or explicitly selected agent."""
    if director is not None:
        try:
            normalized = normalize_director(director)
            argv = director_launcher_command(normalized)
        except LocalDirectorError as exc:
            return {
                "configured": True,
                "valid": False,
                "director": str(director).strip().lower(),
                "error": str(exc),
            }
        return {
            "configured": True,
            "valid": True,
            "agent_id": f"openmontage-{normalized}",
            "director": normalized,
            "command": [redact_text(part) for part in argv],
        }

    try:
        argv = configured_agent_command()
    except AgentConfigurationError as exc:
        return {
            "configured": False,
            "valid": False,
            "error": str(exc),
        }
    if argv is None:
        return {
            "configured": False,
            "valid": True,
            "agent_id": configured_agent_id(),
        }
    director = director_from_command(argv)
    if director:
        try:
            director_launcher_command(director)
        except LocalDirectorError as exc:
            return {
                "configured": True,
                "valid": False,
                "agent_id": configured_agent_id(),
                "director": director,
                "error": str(exc),
            }
    return {
        "configured": True,
        "valid": True,
        "agent_id": configured_agent_id(),
        **({"director": director} if director else {}),
        # Keep diagnostics useful without persisting environment secrets.
        "command": [redact_text(part) for part in argv],
    }


def process_record_path(project_dir: Path | str) -> Path:
    return Path(project_dir) / PROCESS_RECORD_NAME


def read_agent_process(project_dir: Path | str) -> dict[str, Any] | None:
    """Read launch metadata, returning ``None`` for a missing/corrupt record."""
    path = process_record_path(project_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_process_record(project_dir: Path, payload: Mapping[str, Any]) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    destination = process_record_path(project_dir)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=project_dir,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(dict(payload), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _atomic_write_text(destination: Path, content: str) -> None:
    """Replace a handoff file atomically without following an old file link."""
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _issue_local_capability(
    project_path: Path,
    project_id: str,
    run_id: str,
    agent_id: str,
) -> dict[str, str]:
    try:
        return issue_local_director_capability(
            project_path,
            project_id,
            run_id,
            agent_id,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise AgentLaunchError("could not issue the local director run capability") from exc


def _ignore_local_director_config(project_path: Path, destination: Path) -> None:
    """Keep the per-run MCP bearer out of any project Git history."""
    ignore_file = project_path / ".gitignore"
    if ignore_file.is_symlink():
        raise AgentLaunchError("project .gitignore must not be a symlink")
    relative_config = "/" + destination.relative_to(project_path).as_posix()
    try:
        existing = ignore_file.read_text(encoding="utf-8") if ignore_file.exists() else ""
    except (OSError, UnicodeError) as exc:
        raise AgentLaunchError(f"could not safely update project .gitignore: {exc}") from exc
    if relative_config in {line.strip() for line in existing.splitlines()}:
        return
    if not existing:
        separator = ""
    elif existing.endswith(("\n", "\r")):
        separator = "\n"
    else:
        separator = "\n\n"
    addition = f"{separator}# OpenMontage per-run director capability\n{relative_config}\n"
    _atomic_write_text(ignore_file, existing + addition)


def _local_mcp_server_config(
    project_path: Path,
    order: Mapping[str, Any],
    *,
    agent_id: str,
    backlot_url: str,
    capability_token: str,
) -> dict[str, Any]:
    """Build an MCP config with only this run's scoped capability."""
    return {
        "command": sys.executable,
        "args": ["-m", "lib.agent_mcp"],
        "cwd": str(runtime_root()),
        "env": {
            "OPENMONTAGE_PROJECT_DIR": str(project_path),
            "OPENMONTAGE_PROJECT_ID": str(order.get("project_id") or project_path.name),
            "OPENMONTAGE_RUN_ID": str(order.get("run_id") or ""),
            "OPENMONTAGE_AGENT_ID": str(agent_id),
            "OPENMONTAGE_BACKLOT_URL": str(backlot_url).rstrip("/"),
            "OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN": str(capability_token),
            "OPENMONTAGE_RUNTIME_ROOT": str(runtime_root()),
            "OPENMONTAGE_PROJECTS_DIR": str(project_path.parent),
        },
    }


def _write_workspace_mcp_config(
    project_path: Path,
    order: Mapping[str, Any],
    *,
    agent_id: str,
    director: str,
    backlot_url: str,
    capability_token: str,
) -> str:
    """Merge the current run's stdio MCP server into a director workspace file."""
    normalized = normalize_director(director)
    if normalized == "antigravity":
        parent = project_path / ".agents"
        destination = parent / "mcp_config.json"
    elif normalized == "claude":
        parent = project_path
        destination = parent / ".mcp.json"
    else:
        raise AgentLaunchError(f"workspace MCP config is not used for {normalized!r}")

    if parent.is_symlink():
        raise AgentLaunchError(f"{parent.name} MCP config directory must not be a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise AgentLaunchError(f"{destination.name} MCP config must not be a symlink")
    if destination.exists():
        try:
            payload = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AgentLaunchError(f"could not safely merge {destination.name}: {exc}") from exc
        if not isinstance(payload, dict):
            raise AgentLaunchError(f"{destination.name} must contain a JSON object")
    else:
        payload = {}
    servers = payload.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise AgentLaunchError(f"{destination.name} mcpServers must be a JSON object")
    servers[LOCAL_MCP_SERVER_NAME] = _local_mcp_server_config(
        project_path,
        order,
        agent_id=agent_id,
        backlot_url=backlot_url,
        capability_token=capability_token,
    )
    _ignore_local_director_config(project_path, destination)
    _atomic_write_text(
        destination,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    return str(destination)


def _read_instruction_resource(relative_path: str) -> str:
    """Read a tracked instruction file using a safe repository-relative path."""
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise AgentLaunchError(f"invalid instruction resource path: {relative_path!r}")
    source = resource_path(relative)
    if not source.is_file():
        raise AgentLaunchError(f"required instruction resource is missing: {relative_path}")
    try:
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentLaunchError(f"could not read instruction resource {relative_path}: {exc}") from exc


def _read_stage_director_skill(skill_id: str) -> str:
    """Resolve the manifest's short skill id to its packaged Markdown file."""
    relative = Path(skill_id)
    if relative.parts and relative.parts[0] == "pipelines":
        relative = Path("skills") / relative
    if not relative.suffix:
        relative = relative.with_suffix(".md")
    return _read_instruction_resource(str(relative))


def _write_agent_handoff(
    project_path: Path,
    order: Mapping[str, Any],
    execution_context: Any | None,
) -> str:
    """Copy the manifest-derived instructions into the isolated project workspace."""
    bundle_dir = project_path / ".openmontage"
    if bundle_dir.is_symlink():
        raise AgentLaunchError("project .openmontage handoff directory must not be a symlink")
    bundle_dir.mkdir(parents=True, exist_ok=True)
    if not bundle_dir.resolve().is_relative_to(project_path.resolve()):
        raise AgentLaunchError("project handoff directory escapes the project workspace")

    _atomic_write_text(
        bundle_dir / "AGENT_GUIDE.md",
        _read_instruction_resource("AGENT_GUIDE.md"),
    )
    _atomic_write_text(
        bundle_dir / "PROJECT_CONTEXT.md",
        _read_instruction_resource("PROJECT_CONTEXT.md"),
    )

    execution: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    if isinstance(execution_context, Mapping):
        execution = dict(execution_context.get("execution") or {})
        manifest_value = execution_context.get("manifest")
        if isinstance(manifest_value, Mapping):
            manifest = dict(manifest_value)
    elif execution_context is not None:
        as_dict = getattr(execution_context, "as_dict", None)
        if callable(as_dict):
            execution = dict(as_dict())
        manifest_value = getattr(execution_context, "manifest", None)
        if isinstance(manifest_value, Mapping):
            manifest = dict(manifest_value)

    skill_path = str(execution.get("director_skill") or "").strip()
    stage_skill = (
        _read_stage_director_skill(skill_path)
        if skill_path
        else "No stage-specific skill is required for this handoff.\n"
    )
    _atomic_write_text(bundle_dir / "stage_director.md", stage_skill)
    _atomic_write_text(
        bundle_dir / "pipeline_manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    handoff = {
        "project_id": order.get("project_id") or project_path.name,
        "run_id": order.get("run_id"),
        "stage": execution.get("next_stage") or order.get("next_stage"),
        "execution": execution,
        "manifest": manifest,
        "instructions": {
            "agent_guide": ".openmontage/AGENT_GUIDE.md",
            "project_context": ".openmontage/PROJECT_CONTEXT.md",
            "pipeline_manifest": ".openmontage/pipeline_manifest.json",
            "stage_director": ".openmontage/stage_director.md",
        },
    }
    _atomic_write_text(
        bundle_dir / "agent_handoff.json",
        json.dumps(handoff, indent=2, ensure_ascii=False, default=str) + "\n",
    )
    return ".openmontage/agent_handoff.json"


def prepare_manual_agent_handoff(
    project_dir: Path | str,
    order: Mapping[str, Any],
    *,
    agent_id: str,
    backlot_url: str,
    execution_context: Any | None = None,
) -> dict[str, str]:
    """Prepare a copyable handoff without starting or contacting a model CLI."""
    project_path = Path(project_dir).expanduser().resolve()
    if not project_path.is_dir():
        raise AgentLaunchError(f"project directory does not exist: {project_path}")
    clean_agent_id = str(agent_id or "").strip()
    run_id = str(order.get("run_id") or "").strip()
    if not clean_agent_id or not run_id:
        raise AgentLaunchError("manual handoff needs an agent_id and run_id")
    project_id = str(order.get("project_id") or project_path.name)
    capability = _issue_local_capability(
        project_path,
        project_id,
        run_id,
        clean_agent_id,
    )

    handoff_relative_path = _write_agent_handoff(
        project_path,
        order,
        execution_context,
    )
    mcp_config_path = _write_workspace_mcp_config(
        project_path,
        order,
        agent_id=clean_agent_id,
        director="claude",
        backlot_url=backlot_url,
        capability_token=capability["token"],
    )
    execution: Mapping[str, Any] = {}
    if isinstance(execution_context, Mapping):
        execution_value = execution_context.get("execution")
        if isinstance(execution_value, Mapping):
            execution = execution_value
    elif execution_context is not None:
        as_dict = getattr(execution_context, "as_dict", None)
        if callable(as_dict):
            execution_value = as_dict()
            if isinstance(execution_value, Mapping):
                execution = execution_value
    stage = str(execution.get("next_stage") or order.get("next_stage") or "")
    prompt = "\n".join(
        [
            "Work as the OpenMontage local pipeline director in this native, interactive Claude Code session.",
            "The user will submit this prompt manually. Backlot did not send it to Claude and did not call a model API.",
            f"Project workspace: {project_path}",
            f"Project id: {project_id}",
            f"Run id: {run_id}",
            f"Agent id: {clean_agent_id}",
            f"Current stage: {stage}",
            f"Local Backlot API: {backlot_url.rstrip('/')}",
            f"Handoff file: {project_path / handoff_relative_path}",
            f"OpenMontage MCP config: {mcp_config_path}",
            "Read the bundled AGENT_GUIDE.md, PROJECT_CONTEXT.md, pipeline_manifest.json, and stage_director.md before acting.",
            "This work order is already claimed by the listed agent id. Do not claim it again. Use openmontage_get_context, openmontage_execute_tool, and openmontage_submit_stage for the current manifest stage. The local bridge renews the lease while connected. Follow all human approval gates.",
            "Claude Code remains a native interactive session. Approve the project-scoped OpenMontage MCP server if Claude asks. Do not read or print .env or credentials, and do not use the OpenAI production API key for director inference.",
        ]
    )
    prompt_path = project_path / ".openmontage" / "claude_interactive_prompt.txt"
    _atomic_write_text(prompt_path, prompt + "\n")
    return {
        "mode": "interactive_manual_handoff",
        "agent_id": clean_agent_id,
        "project_id": project_id,
        "project_dir": str(project_path),
        "run_id": run_id,
        "stage": stage,
        "backlot_url": backlot_url.rstrip("/"),
        "handoff_path": str(project_path / handoff_relative_path),
        "prompt_path": str(prompt_path),
        "mcp_config_path": mcp_config_path,
        "prompt": prompt,
    }


def launch_agent(
    project_dir: Path | str,
    order: Mapping[str, Any],
    *,
    agent_id: str,
    backlot_url: str = "",
    director: str | None = None,
    execution_context: Any | None = None,
) -> AgentLaunch:
    """Start the configured agent for one claimed work order.

    The caller must claim the work order first.  If process creation or log
    setup fails, ``AgentLaunchError`` is raised and no success metadata is
    written, allowing the API to release the lease safely.
    """
    try:
        argv = (
            director_launcher_command(director)
            if director is not None
            else configured_agent_command()
        )
    except LocalDirectorError as exc:
        raise AgentConfigurationError(str(exc)) from exc
    if argv is None:
        raise AgentConfigurationError(
            f"{AGENT_COMMAND_ENV} is not configured; set it to the trusted agent command"
        )
    selected_director = normalize_director(director) if director is not None else director_from_command(argv)
    clean_agent_id = str(agent_id or "").strip()
    if not clean_agent_id:
        raise AgentConfigurationError("agent_id is required for an automatic launch")
    run_id = str(order.get("run_id") or "").strip()
    if not run_id:
        raise AgentLaunchError("work order has no run_id")
    project_path = Path(project_dir).expanduser().resolve()
    if not project_path.is_dir():
        raise AgentLaunchError(f"project directory does not exist: {project_path}")

    handoff_relative_path = _write_agent_handoff(
        project_path,
        order,
        execution_context,
    )
    project_id = str(order.get("project_id") or project_path.name)
    capability: dict[str, str] | None = None
    if selected_director in {"codex", "antigravity"}:
        capability = _issue_local_capability(
            project_path,
            project_id,
            run_id,
            clean_agent_id,
        )
    try:
        if selected_director == "antigravity":
            _write_workspace_mcp_config(
                project_path,
                order,
                agent_id=clean_agent_id,
                director=selected_director,
                backlot_url=backlot_url,
                capability_token=capability["token"],
            )
    except Exception:
        if capability is not None:
            revoke_local_director_capability(
                project_path,
                project_id,
                run_id,
                clean_agent_id,
            )
        raise
    log_path = project_path / PROCESS_LOG_NAME
    # External directors are untrusted with respect to production provider
    # credentials. Keep their run context, but never inherit credential-like
    # environment values from Backlot or the operator shell.
    env = director_environment(os.environ)
    env.update(
        {
            "OPENMONTAGE_PROJECT_ID": str(order.get("project_id") or project_path.name),
            "OPENMONTAGE_PROJECT_DIR": str(project_path),
            "OPENMONTAGE_RUN_ID": run_id,
            "OPENMONTAGE_AGENT_ID": clean_agent_id,
            "OPENMONTAGE_STAGE": str(order.get("next_stage") or ""),
            "OPENMONTAGE_BACKLOT_URL": str(backlot_url or ""),
            "OPENMONTAGE_AGENT_HANDOFF": str(project_path / handoff_relative_path),
            "OPENMONTAGE_RUNTIME_ROOT": str(runtime_root()),
            "OPENMONTAGE_PROJECTS_DIR": str(project_path.parent),
        }
    )
    if capability is not None:
        env["OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN"] = capability["token"]
    prompt_lines = [
        "Direct the OpenMontage production in "
        f"{env['OPENMONTAGE_PROJECT_DIR']}. Read "
        ".openmontage/AGENT_GUIDE.md, .openmontage/PROJECT_CONTEXT.md, "
        ".openmontage/pipeline_manifest.json, and "
        ".openmontage/stage_director.md from the bundled handoff; begin at "
        f"stage {env['OPENMONTAGE_STAGE']!r} for run {run_id!r}.",
    ]
    if selected_director in {"codex", "antigravity"}:
        prompt_lines.append(
            "Use the OpenMontage MCP tools: start with openmontage_get_context, "
            "use openmontage_execute_tool only for tools listed for the current "
            "stage, and submit artifacts with openmontage_submit_stage. The "
            "bridge renews this agent's lease. Stop at human gates and never "
            "supply provider approval controls."
        )
    else:
        prompt_lines.append(
            f"Use agent id {clean_agent_id!r} for the Backlot heartbeat and "
            "checkpoint calls, and pause for required human approvals."
        )
    env["OPENMONTAGE_AGENT_PROMPT"] = " ".join(prompt_lines)
    # Make source-checkout helpers importable for commands that run the local
    # package with ``python -m ...``. Installed environments already expose the
    # package through site-packages, so preserve any existing PYTHONPATH.
    package_root = str(REPO_ROOT)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        package_root
        if not existing_pythonpath
        else package_root + os.pathsep + existing_pythonpath
    )

    try:
        with log_path.open("a", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                list(argv),
                cwd=str(runtime_root()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                shell=False,
            )
    except (OSError, ValueError) as exc:
        if capability is not None:
            revoke_local_director_capability(
                project_path,
                project_id,
                run_id,
                clean_agent_id,
            )
        raise AgentLaunchError(
            f"could not start configured agent executable {argv[0]!r}: {exc}"
        ) from exc

    started_at = datetime.now(timezone.utc).isoformat()
    safe_command = tuple(redact_text(part) for part in argv)
    launch = AgentLaunch(
        pid=int(process.pid),
        agent_id=clean_agent_id,
        run_id=run_id,
        started_at=started_at,
        log_path=PROCESS_LOG_NAME,
        command=safe_command,
        cwd=str(runtime_root()),
    )
    try:
        _write_process_record(
            project_path,
            {
                **launch.as_dict(),
                "status": "started",
            },
        )
    except OSError as exc:
        # The child is real, but without a durable record duplicate protection
        # becomes ambiguous.  Terminate this just-started process and surface
        # the failure so the API can release the lease.
        try:
            process.terminate()
        except OSError:
            pass
        raise AgentLaunchError(f"could not persist agent launch record: {exc}") from exc
    return launch


__all__ = [
    "AGENT_COMMAND_ENV",
    "AGENT_ID_ENV",
    "AgentConfigurationError",
    "AgentLaunch",
    "AgentLaunchError",
    "agent_command_status",
    "configured_agent_command",
    "configured_agent_id",
    "launch_agent",
    "process_record_path",
    "read_agent_process",
]
