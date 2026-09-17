"""Contracts for short-lived local director run capabilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lib.local_director_capabilities import (
    LocalDirectorCapabilityError,
    issue_local_director_capability,
    revoke_local_director_capability,
    validate_local_director_capability,
)


def test_capability_is_random_reusable_and_bound_to_one_full_run(tmp_path) -> None:
    project = tmp_path / "project-a"
    project.mkdir()
    now = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
    issued = issue_local_director_capability(
        project,
        "project-a",
        "run-a",
        "codex-a",
        now=now,
        ttl_seconds=60,
    )
    repeated = issue_local_director_capability(
        project,
        "project-a",
        "run-a",
        "codex-a",
        now=now + timedelta(seconds=10),
        ttl_seconds=60,
    )

    assert len(issued["token"]) >= 40
    assert issued["token"] == repeated["token"]
    assert validate_local_director_capability(
        issued["token"], project, "project-a", "run-a", "codex-a", now=now
    )["run_id"] == "run-a"

    for wrong in (
        (project, "project-b", "run-a", "codex-a"),
        (project, "project-a", "run-b", "codex-a"),
        (project, "project-a", "run-a", "other-agent"),
        (tmp_path / "project-copy", "project-a", "run-a", "codex-a"),
    ):
        with pytest.raises(LocalDirectorCapabilityError):
            validate_local_director_capability(issued["token"], *wrong, now=now)

    revoke_local_director_capability(project, "project-a", "run-a", "codex-a")
    with pytest.raises(LocalDirectorCapabilityError, match="invalid or expired"):
        validate_local_director_capability(
            issued["token"], project, "project-a", "run-a", "codex-a", now=now
        )


def test_expired_capability_fails_closed(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    now = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
    issued = issue_local_director_capability(
        project,
        "project",
        "run",
        "agent",
        now=now,
        ttl_seconds=5,
    )

    with pytest.raises(LocalDirectorCapabilityError, match="invalid or expired"):
        validate_local_director_capability(
            issued["token"],
            project,
            "project",
            "run",
            "agent",
            now=now + timedelta(seconds=6),
        )

