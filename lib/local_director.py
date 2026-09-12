"""Run a locally authenticated coding agent as an OpenMontage director.

The Backlot launcher owns the handoff and starts this module as a small
adapter.  Model API keys belong to OpenMontage's production tools, so they are
removed from the director CLI's environment.  Those tools can still load their
own production credentials from the ignored project ``.env`` when invoked.
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

# These credentials select paid model API access.  Local CLIs should instead
# use their existing account sign-in; media tools load production keys from
# OpenMontage's .env when they run.
MODEL_API_CREDENTIALS = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }
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
    # On Windows prefer a native executable over npm's .cmd shims.  The
    # launcher deliberately uses shell=False, and CreateProcess cannot execute
    # a batch shim directly.
    executable = (
        shutil.which(f"{cli}.exe") or shutil.which(cli)
        if os.name == "nt"
        else shutil.which(cli)
    )
    if executable and Path(executable).suffix.lower() in {".bat", ".cmd"}:
        executable = None
    if not executable:
        raise LocalDirectorError(
            f"Local director {director!r} needs a directly executable '{cli}' "
            "on PATH. Install and sign in to that local CLI before using Run Pipeline."
        )
    return executable


def director_from_command(argv: Sequence[str]) -> str | None:
    """Recognize the documented ``python -m lib.local_director <name>`` form."""
    if len(argv) == 4 and tuple(argv[1:3]) == ("-m", "lib.local_director"):
        try:
            return normalize_director(argv[3])
        except LocalDirectorError:
            return str(argv[3]).strip().lower()
    return None


def director_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy the environment without provider API credentials used for inference."""
    result = dict(os.environ if source is None else source)
    for name in MODEL_API_CREDENTIALS:
        result.pop(name, None)
    return result


def command_for_director(
    name: str,
    executable: str,
    prompt: str,
    *,
    working_directory: Path | str,
) -> tuple[list[str], str | None]:
    """Build a shell-free command and optional stdin prompt for one CLI."""
    director = normalize_director(name)
    if director == "codex":
        return (
            [
                executable,
                "exec",
                "--sandbox",
                "workspace-write",
                "--cd",
                str(working_directory),
                "-",
            ],
            prompt,
        )
    return [executable, "-p", prompt], None


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

    working_directory = Path.cwd().resolve()
    argv, stdin_prompt = command_for_director(
        director,
        executable,
        prompt,
        working_directory=working_directory,
    )
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
