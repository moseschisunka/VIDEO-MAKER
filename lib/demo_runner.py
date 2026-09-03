"""Explicit quarantine boundary for the legacy Studio/demo runner.

The teaching-slide implementation in :mod:`lib.project_pipeline` is useful
for a controlled fixture, but it is not a generic manifest executor.  Keeping
the marker and allow-list in this small dependency-free module lets Backlot
enforce that boundary without importing provider-heavy runner code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


RUNNER_KIND = "internal_demo"
DEMO_PIPELINE_TYPES = frozenset({"animated-explainer"})


class DemoRunnerQuarantinedError(RuntimeError):
    """Raised when the internal demo runner is used as a production runner."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def is_internal_demo_project(project_dir: Path | str) -> bool:
    """Return whether a project carries an explicit, compatible demo marker."""
    root = Path(project_dir)
    marker = _read_json(root / "project.json")
    config = _read_json(root / "artifacts" / "project_config.json")
    pipeline_type = str(marker.get("pipeline_type") or config.get("pipeline_type") or "")
    return bool(
        marker.get("runner_kind") == RUNNER_KIND
        and marker.get("demo_runner") is True
        and pipeline_type in DEMO_PIPELINE_TYPES
    )


def assert_internal_demo_project(project_dir: Path | str) -> None:
    """Fail closed unless the project is explicitly marked as the fixture."""
    if not is_internal_demo_project(project_dir):
        raise DemoRunnerQuarantinedError(
            "lib.project_pipeline is an internal demo runner and cannot execute "
            "an unmarked or unrelated pipeline"
        )


__all__ = [
    "DEMO_PIPELINE_TYPES",
    "DemoRunnerQuarantinedError",
    "RUNNER_KIND",
    "assert_internal_demo_project",
    "is_internal_demo_project",
]
