"""Launch contract for an external OpenMontage production agent.

OpenMontage owns the durable work order and the creative tools, but it does
not embed an LLM or pretend that a Python demo runner is a production agent.
This module is the small process boundary between Backlot and the configured
agent application (Codex, Claude, or a project-specific worker).

The command is deliberately configured by the operator.  It is parsed into an
argument vector and launched with ``shell=False``; project and run identity are
provided through ``OPENMONTAGE_*`` environment variables so the adapter does
not have to invent a command-line protocol for every agent implementation.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.paths import REPO_ROOT, runtime_root
from lib.secrets import redact_text

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


def agent_command_status() -> dict[str, Any]:
    """Return a safe configuration summary for diagnostics and API errors."""
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
    return {
        "configured": True,
        "valid": True,
        "agent_id": configured_agent_id(),
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


def launch_agent(
    project_dir: Path | str,
    order: Mapping[str, Any],
    *,
    agent_id: str,
    backlot_url: str = "",
) -> AgentLaunch:
    """Start the configured agent for one claimed work order.

    The caller must claim the work order first.  If process creation or log
    setup fails, ``AgentLaunchError`` is raised and no success metadata is
    written, allowing the API to release the lease safely.
    """
    argv = configured_agent_command()
    if argv is None:
        raise AgentConfigurationError(
            f"{AGENT_COMMAND_ENV} is not configured; set it to the trusted agent command"
        )
    clean_agent_id = str(agent_id or "").strip()
    if not clean_agent_id:
        raise AgentConfigurationError("agent_id is required for an automatic launch")
    run_id = str(order.get("run_id") or "").strip()
    if not run_id:
        raise AgentLaunchError("work order has no run_id")
    project_path = Path(project_dir).expanduser().resolve()
    if not project_path.is_dir():
        raise AgentLaunchError(f"project directory does not exist: {project_path}")

    log_path = project_path / PROCESS_LOG_NAME
    env = dict(os.environ)
    env.update(
        {
            "OPENMONTAGE_PROJECT_ID": str(order.get("project_id") or project_path.name),
            "OPENMONTAGE_PROJECT_DIR": str(project_path),
            "OPENMONTAGE_RUN_ID": run_id,
            "OPENMONTAGE_AGENT_ID": clean_agent_id,
            "OPENMONTAGE_STAGE": str(order.get("next_stage") or ""),
            "OPENMONTAGE_BACKLOT_URL": str(backlot_url or ""),
        }
    )
    env["OPENMONTAGE_AGENT_PROMPT"] = (
        "Drive the OpenMontage production in "
        f"{env['OPENMONTAGE_PROJECT_DIR']}. Read AGENT_GUIDE.md, follow the "
        f"manifest, and begin at stage {env['OPENMONTAGE_STAGE']!r} for run "
        f"{run_id!r}. Use agent id {clean_agent_id!r} for Backlot heartbeats "
        "and checkpoints, and pause for required human approvals."
    )
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
