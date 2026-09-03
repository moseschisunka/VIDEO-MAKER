"""Canonical repository paths — single source of truth.

The projects root is the most load-bearing path in the system: checkpoints
are written under it, tool events are attributed against it, and the Backlot
board watches it. Define it once.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Overridable for staging/screenshots/tests. Everything — checkpoint writes,
# event attribution, the Backlot board — follows the same root.
PROJECTS_DIR = Path(os.environ.get("OPENMONTAGE_PROJECTS_DIR") or (REPO_ROOT / "projects"))


class RunPathError(ValueError):
    """Raised when a run-scoped path cannot be safely resolved."""


@dataclass(frozen=True)
class RunPaths:
    """Isolated filesystem envelope for one project run.

    The directory is deliberately below the project root and is keyed by the
    immutable UUIDv4 from the work order.  All renderer scratch material must
    live in one of these subdirectories; cleanup code can therefore never
    sweep another project or another run accidentally.
    """

    root: Path
    work: Path
    inputs: Path
    props: Path
    logs: Path
    candidates: Path
    reports: Path
    run_id: str


def run_paths(
    project_dir: Path | str,
    run_id: str,
    *,
    create: bool = True,
) -> RunPaths:
    """Return the validated run-scoped directories for ``run_id``.

    A run ID is an execution identity, not a user-controlled folder name.
    Restricting it to UUIDv4 prevents traversal, collisions with legacy
    folders, and accidental reuse of a path from another execution.
    """
    project = Path(project_dir).expanduser().resolve()
    if not project.is_dir():
        raise RunPathError(f"project directory does not exist: {project}")
    try:
        parsed = uuid.UUID(str(run_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RunPathError("run_id must be a UUIDv4") from exc
    if parsed.version != 4:
        raise RunPathError("run_id must be a UUIDv4")
    normalized = str(parsed)

    root = project / "runs" / normalized
    # Resolve before creating so a pre-existing symlink/junction cannot point
    # cleanup or writes outside the project envelope.
    try:
        root.resolve().relative_to(project)
    except (OSError, ValueError) as exc:
        raise RunPathError("run directory escapes the project directory") from exc
    if root.is_symlink():
        raise RunPathError("run directory must not be a symlink")

    paths = RunPaths(
        root=root,
        work=root / "work",
        inputs=root / "inputs",
        props=root / "props",
        logs=root / "logs",
        candidates=root / "candidates",
        reports=root / "reports",
        run_id=normalized,
    )
    if create:
        for directory in (
            paths.root,
            paths.work,
            paths.inputs,
            paths.props,
            paths.logs,
            paths.candidates,
            paths.reports,
        ):
            directory.mkdir(parents=True, exist_ok=True)
    return paths


__all__ = ["PROJECTS_DIR", "REPO_ROOT", "RunPathError", "RunPaths", "run_paths"]
