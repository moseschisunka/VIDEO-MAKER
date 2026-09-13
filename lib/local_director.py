"""Run supported local coding agents as OpenMontage directors.

The Backlot launcher owns the handoff and starts this module as a small
adapter.  Model API keys belong to OpenMontage's production tools, so they are
removed from the director CLI's environment.  Those tools can still load their
own production credentials from the ignored project ``.env`` when invoked.
Claude Code is listed for discovery, but subscription-backed interactive use
is deliberately not dispatched through this adapter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Sequence


DIRECTOR_EXECUTABLES = {
    "codex": "codex",
    "claude": "claude",
    "antigravity": "agy",
}

DIRECTOR_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "antigravity": "Antigravity",
}

CLAUDE_INTERACTIVE_ONLY_NOTE = (
    "Use Claude Code in its native interactive CLI. Backlot can prepare and copy "
    "a handoff, but you must paste and submit it yourself; it never sends a "
    "request through Claude subscription credentials. Claude's headless mode "
    "uses a separate Agent SDK allowance."
)
CLAUDE_INTERACTIVE_AGENT_ID = "openmontage-claude-interactive"

_CREDENTIAL_ENV_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
)


class LocalDirectorError(ValueError):
    """Raised when a local director is unknown or unavailable."""


def normalize_director(name: str) -> str:
    """Return a supported director name or raise a useful configuration error."""
    director = str(name or "").strip().lower()
    if director not in DIRECTOR_EXECUTABLES:
        choices = ", ".join(sorted(DIRECTOR_EXECUTABLES))
        raise LocalDirectorError(
            f"Unknown local director {director!r}; choose one of: {choices}."
        )
    return director


def find_director_executable(name: str) -> str:
    """Resolve the installed CLI without starting a model session."""
    director = normalize_director(name)
    cli = DIRECTOR_EXECUTABLES[director]
    # On Windows prefer a native executable when one exists. Python can use a
    # PowerShell shim through an explicit -File invocation, but CreateProcess
    # cannot execute .bat/.cmd shims directly and those require shell parsing.
    if os.name == "nt":
        executable = shutil.which(f"{cli}.exe") or shutil.which(cli)
        if executable and Path(executable).suffix.lower() in {".bat", ".cmd"}:
            # PATHEXT commonly returns the npm batch shim before the PowerShell
            # shim. Prefer the latter rather than falling back to shell parsing.
            executable = shutil.which(f"{cli}.ps1")
    else:
        executable = shutil.which(cli)
    if executable and Path(executable).suffix.lower() in {".bat", ".cmd"}:
        executable = None
    if executable and Path(executable).suffix.lower() == ".ps1":
        if not (shutil.which("pwsh.exe") or shutil.which("powershell.exe")):
            executable = None
    if not executable:
        raise LocalDirectorError(
            f"Local director {director!r} needs a directly executable '{cli}' "
            "on PATH (or a PowerShell shim). Install and sign in to that local "
            "CLI before using Run Pipeline."
        )
    return executable


def director_launcher_command(name: str) -> tuple[str, ...]:
    """Return the trusted adapter command for one installed local CLI."""
    director = normalize_director(name)
    if director == "claude":
        raise LocalDirectorError(CLAUDE_INTERACTIVE_ONLY_NOTE)
    executable = find_director_executable(director)
    if director == "codex":
        auth_status = _codex_auth_status(executable)
        if auth_status == "not_signed_in":
            raise LocalDirectorError(
                "Codex is not signed in. Run 'codex login' and choose ChatGPT sign-in; "
                "the production API key is not for local director use."
            )
        if auth_status == "api_key":
            raise LocalDirectorError(
                "Codex is signed in with an API key. Use 'codex logout', then 'codex login' "
                "and choose ChatGPT sign-in to keep director usage on your ChatGPT account."
            )
        if auth_status != "chatgpt":
            raise LocalDirectorError(
                "Could not confirm that Codex is using ChatGPT sign-in. Run "
                "'codex login status'; local director runs require confirmed ChatGPT "
                "sign-in so they do not fall back to API-key billing."
            )
    return (sys.executable, "-m", "lib.local_director", director)


def local_director_catalog() -> list[dict[str, str | bool]]:
    """Report local CLI readiness without returning account details or secrets."""
    catalog: list[dict[str, str | bool]] = []
    for director, label in DIRECTOR_LABELS.items():
        if director == "claude":
            try:
                find_director_executable(director)
            except LocalDirectorError as exc:
                catalog.append({
                    "id": director,
                    "label": label,
                    "installed": False,
                    "ready": False,
                    "mode": "interactive_only",
                    "auth_status": "manual",
                    "handoff_agent_id": CLAUDE_INTERACTIVE_AGENT_ID,
                    "status_note": f"{exc} {CLAUDE_INTERACTIVE_ONLY_NOTE}",
                })
            else:
                catalog.append({
                    "id": director,
                    "label": label,
                    "installed": True,
                    "ready": False,
                    "mode": "interactive_only",
                    "auth_status": "manual",
                    "handoff_agent_id": CLAUDE_INTERACTIVE_AGENT_ID,
                    "status_note": CLAUDE_INTERACTIVE_ONLY_NOTE,
                })
            continue
        try:
            executable = find_director_executable(director)
        except LocalDirectorError as exc:
            catalog.append({
                "id": director,
                "label": label,
                "installed": False,
                "ready": False,
                "auth_status": "unavailable",
                "status_note": str(exc),
            })
        else:
            auth_status = _codex_auth_status(executable) if director == "codex" else "unknown"
            ready = auth_status == "chatgpt" if director == "codex" else True
            if auth_status == "not_signed_in":
                status_note = "Not signed in. Run 'codex login' and choose ChatGPT sign-in."
            elif auth_status == "api_key":
                status_note = "Signed in with API-key billing. Use ChatGPT sign-in for local director usage."
            elif auth_status == "chatgpt":
                status_note = "Signed in with ChatGPT."
            elif director == "codex":
                status_note = (
                    "Could not confirm ChatGPT sign-in. Run 'codex login status', then "
                    "sign in with ChatGPT before running locally."
                )
            elif director == "claude":
                status_note = (
                    "Billing mode cannot be verified here. Sign in with a Claude "
                    "subscription; Claude Console sign-in uses API billing."
                )
            elif director == "antigravity":
                status_note = (
                    "Installed. Sign in through an interactive 'agy' session first; "
                    "Backlot cannot verify this account's plan."
                )
            else:
                status_note = "Installed. Sign in with this CLI's own account before running it."
            catalog.append({
                "id": director,
                "label": label,
                "installed": True,
                "ready": ready,
                "auth_status": auth_status,
                "status_note": status_note,
            })
    return catalog


def _executable_argv(executable: str) -> list[str]:
    """Return an argv prefix that starts a native executable or PS1 shim."""
    if os.name == "nt" and Path(executable).suffix.lower() == ".ps1":
        shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not shell:
            raise LocalDirectorError(
                "A PowerShell executable is required to start this local director."
            )
        return [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            executable,
        ]
    return [executable]


def _codex_auth_status(executable: str) -> str:
    """Classify Codex's saved sign-in without returning account details."""
    try:
        result = subprocess.run(
            [*_executable_argv(executable), "login", "status"],
            env=director_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=6,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    output = str(result.stdout or "").lower()
    if "not logged in" in output or "not authenticated" in output:
        return "not_signed_in"
    if any(marker in output for marker in ("api key", "api-key", "api_key", "apikey")):
        return "api_key"
    if result.returncode == 0 and "chatgpt" in output:
        return "chatgpt"
    return "unknown"


def director_from_command(argv: Sequence[str]) -> str | None:
    """Recognize the documented ``python -m lib.local_director <name>`` form."""
    if len(argv) == 4 and tuple(argv[1:3]) == ("-m", "lib.local_director"):
        try:
            return normalize_director(argv[3])
        except LocalDirectorError:
            return str(argv[3]).strip().lower()
    return None


def director_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy the environment without credential-like values.

    The exact set of production providers changes over time. Matching common
    credential markers keeps new provider keys out of local directors without
    requiring every tool integration to be added to this adapter.
    """
    source_env = os.environ if source is None else source
    return {
        name: value
        for name, value in source_env.items()
        if not any(marker in str(name).upper() for marker in _CREDENTIAL_ENV_MARKERS)
    }


def command_for_director(
    name: str,
    executable: str,
    prompt: str,
    *,
    working_directory: Path | str,
) -> tuple[list[str], str | None]:
    """Build a shell-free command and optional stdin prompt for one CLI."""
    director = normalize_director(name)
    if director == "claude":
        raise LocalDirectorError(CLAUDE_INTERACTIVE_ONLY_NOTE)
    executable_argv = _executable_argv(executable)
    if director == "codex":
        return (
            [
                *executable_argv,
                "exec",
                "--sandbox",
                "workspace-write",
                "--cd",
                str(working_directory),
                "-",
            ],
            prompt,
        )
    return [*executable_argv, "-p", prompt], None


def run_local_director(name: str, *, env: Mapping[str, str] | None = None) -> int:
    """Forward the Backlot handoff to a signed-in local CLI and return its code."""
    try:
        director = normalize_director(name)
    except LocalDirectorError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    source_env = dict(os.environ if env is None else env)
    prompt = str(source_env.get("OPENMONTAGE_AGENT_PROMPT") or "").strip()
    if not prompt:
        print("OPENMONTAGE_AGENT_PROMPT is missing; no director was started.", file=sys.stderr)
        return 2

    try:
        executable = find_director_executable(director)
    except LocalDirectorError as exc:
        print(str(exc), file=sys.stderr)
        return 127

    project_directory = str(source_env.get("OPENMONTAGE_PROJECT_DIR") or "").strip()
    if not project_directory:
        print(
            "OPENMONTAGE_PROJECT_DIR is missing; no director was started.",
            file=sys.stderr,
        )
        return 2
    working_directory = Path(project_directory).expanduser().resolve()
    if not working_directory.is_dir():
        print(
            "OPENMONTAGE_PROJECT_DIR must point to an existing project workspace; "
            "no director was started.",
            file=sys.stderr,
        )
        return 2
    try:
        argv, stdin_prompt = command_for_director(
            director,
            executable,
            prompt,
            working_directory=working_directory,
        )
    except LocalDirectorError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        result = subprocess.run(
            argv,
            cwd=str(working_directory),
            env=director_environment(source_env),
            input=stdin_prompt,
            text=True,
            shell=False,
            check=False,
        )
    except OSError as exc:
        print(f"Could not start local director {director!r}: {exc}", file=sys.stderr)
        return 127
    return int(result.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--help"], ["-h"]):
        print("Usage: python -m lib.local_director <codex|claude|antigravity>")
        return 0
    if len(args) != 1:
        print(
            "Usage: python -m lib.local_director <codex|claude|antigravity>",
            file=sys.stderr,
        )
        return 2
    return run_local_director(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
