"""Short-lived, run-scoped capabilities for Backlot's local director bridge.

The bearer value and its binding live only in Backlot's process memory; the
index uses a digest, and the value is never persisted by this registry. Backlot
validates it against the claimed project/run/agent and live work-order lease.
It is deliberately narrower than the global Backlot auth token and grants no
API access beyond validation of this local MCP bridge identity.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CAPABILITY_TTL_SECONDS = 8 * 60 * 60


class LocalDirectorCapabilityError(ValueError):
    """Raised when a local director capability is missing, expired, or misbound."""


@dataclass
class _CapabilityRecord:
    token_digest: str
    token: str
    project_dir: str
    project_id: str
    run_id: str
    agent_id: str
    expires_at: datetime


_LOCK = threading.RLock()
_BY_DIGEST: dict[str, _CapabilityRecord] = {}
_BY_BINDING: dict[tuple[str, str, str, str], str] = {}


def _path_key(project_dir: Path | str) -> str:
    return str(Path(project_dir).expanduser().resolve()).casefold()


def _binding_key(
    project_dir: Path | str,
    project_id: str,
    run_id: str,
    agent_id: str,
) -> tuple[str, str, str, str]:
    return (_path_key(project_dir), project_id, run_id, agent_id)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _remove_locked(digest: str) -> None:
    record = _BY_DIGEST.pop(digest, None)
    if record is not None:
        binding = _binding_key(
            record.project_dir,
            record.project_id,
            record.run_id,
            record.agent_id,
        )
        if _BY_BINDING.get(binding) == digest:
            _BY_BINDING.pop(binding, None)


def _purge_expired_locked(now: datetime) -> None:
    for digest, record in tuple(_BY_DIGEST.items()):
        if record.expires_at <= now:
            _remove_locked(digest)


def issue_local_director_capability(
    project_dir: Path | str,
    project_id: str,
    run_id: str,
    agent_id: str,
    *,
    now: datetime | None = None,
    ttl_seconds: int = CAPABILITY_TTL_SECONDS,
) -> dict[str, str]:
    """Issue or reuse a random bearer bound to one project, run, and agent."""
    clean_project_id = str(project_id or "").strip()
    clean_run_id = str(run_id or "").strip()
    clean_agent_id = str(agent_id or "").strip()
    clean_project_dir = _path_key(project_dir)
    if not clean_project_id or not clean_run_id or not clean_agent_id or not clean_project_dir:
        raise LocalDirectorCapabilityError("project, run, and agent identity are required")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise LocalDirectorCapabilityError("capability lifetime must be a positive integer")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    binding = (clean_project_dir, clean_project_id, clean_run_id, clean_agent_id)
    with _LOCK:
        _purge_expired_locked(current_time)
        current_digest = _BY_BINDING.get(binding)
        current = _BY_DIGEST.get(current_digest or "")
        if current is not None and current.expires_at > current_time:
            return {"token": current.token, "expires_at": current.expires_at.isoformat()}

        token = secrets.token_urlsafe(32)
        digest = _token_digest(token)
        expires_at = current_time + timedelta(seconds=ttl_seconds)
        record = _CapabilityRecord(
            token_digest=digest,
            token=token,
            project_dir=clean_project_dir,
            project_id=clean_project_id,
            run_id=clean_run_id,
            agent_id=clean_agent_id,
            expires_at=expires_at,
        )
        _BY_DIGEST[digest] = record
        _BY_BINDING[binding] = digest
    return {"token": token, "expires_at": expires_at.isoformat()}


def validate_local_director_capability(
    token: str,
    project_dir: Path | str,
    project_id: str,
    run_id: str,
    agent_id: str,
    *,
    now: datetime | None = None,
    slide_expiry: bool = True,
) -> dict[str, Any]:
    """Validate a bearer against its complete identity and slide its expiry."""
    if not isinstance(token, str) or not token or len(token) > 256:
        raise LocalDirectorCapabilityError("local director capability is invalid or expired")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    digest = _token_digest(token)
    expected = _binding_key(project_dir, project_id, run_id, agent_id)
    with _LOCK:
        _purge_expired_locked(current_time)
        record = _BY_DIGEST.get(digest)
        if record is None:
            raise LocalDirectorCapabilityError("local director capability is invalid or expired")
        actual = _binding_key(
            record.project_dir,
            record.project_id,
            record.run_id,
            record.agent_id,
        )
        if actual != expected:
            raise LocalDirectorCapabilityError("local director capability is not valid for this run")
        if slide_expiry:
            record.expires_at = current_time + timedelta(seconds=CAPABILITY_TTL_SECONDS)
        expires_at = record.expires_at
    return {
        "project_id": record.project_id,
        "run_id": record.run_id,
        "agent_id": record.agent_id,
        "expires_at": expires_at.isoformat(),
    }


def revoke_local_director_capability(
    project_dir: Path | str,
    project_id: str,
    run_id: str,
    agent_id: str,
) -> None:
    """Revoke the capability for one director handoff, if it exists."""
    binding = _binding_key(project_dir, project_id, run_id, agent_id)
    with _LOCK:
        digest = _BY_BINDING.get(binding)
        if digest is not None:
            _remove_locked(digest)


__all__ = [
    "CAPABILITY_TTL_SECONDS",
    "LocalDirectorCapabilityError",
    "issue_local_director_capability",
    "revoke_local_director_capability",
    "validate_local_director_capability",
]
