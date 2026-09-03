"""Durable, manifest-derived execution work orders.

The work order is the boundary between Backlot request handling and the
agent-controlled production run.  It contains no creative output and never
chooses a stage on its own: the selected manifest supplies the ordered stage
list, while agents and deterministic services update the persisted state in
later roadmap tasks.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

import jsonschema

from lib.pipeline_loader import get_stage_order


REPO_ROOT = Path(__file__).resolve().parent.parent
WORK_ORDER_SCHEMA_PATH = REPO_ROOT / "schemas" / "work_orders" / "work_order.schema.json"
WORK_ORDER_FILENAME = "work_order.json"
WORK_ORDER_VERSION = "1.0"

try:
    from filelock import FileLock, Timeout as FileLockTimeout
except ImportError:  # pragma: no cover - the bundled environment includes filelock
    FileLock = None

    class FileLockTimeout(TimeoutError):
        pass


_WORK_ORDER_THREAD_LOCK = threading.RLock()
# Human decisions are a separate transaction from ordinary stage submission.
# Serialising the complete decision (read -> bind digest -> write checkpoint ->
# update order) prevents two concurrent approval clicks from creating two
# valid records for the same awaiting snapshot.
_APPROVAL_TRANSITION_THREAD_LOCK = threading.RLock()
DEFAULT_LEASE_SECONDS = 300
# A one-second lower bound keeps the lease contract testable while the normal
# API default remains five minutes.  Production callers should use the default
# unless they have an explicit heartbeat policy.
MIN_LEASE_SECONDS = 1
MAX_LEASE_SECONDS = 86_400


class WorkOrderValidationError(ValueError):
    """Raised when a durable work order violates its schema or invariants."""


class WorkOrderConflictError(WorkOrderValidationError):
    """Raised when another live agent owns the work-order lease."""


class WorkOrderStateError(WorkOrderValidationError):
    """Raised when a requested claim/advance is illegal for current state."""


@lru_cache(maxsize=1)
def _load_work_order_schema() -> dict[str, Any]:
    return json.loads(WORK_ORDER_SCHEMA_PATH.read_text(encoding="utf-8"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _lease_seconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkOrderStateError("lease_seconds must be an integer")
    if not MIN_LEASE_SECONDS <= value <= MAX_LEASE_SECONDS:
        raise WorkOrderStateError(
            f"lease_seconds must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS}"
        )
    return value


def _stage_order_from_order(order: Mapping[str, Any]) -> list[str]:
    records = order.get("stages")
    if not isinstance(records, list):
        raise WorkOrderValidationError("work order stages must be a list")
    return [str(record.get("name")) for record in records if isinstance(record, Mapping)]


def next_stage_from_work_order(order: Mapping[str, Any]) -> str | None:
    """Derive the resumable stage from manifest-ordered stage records.

    This never trusts a caller-supplied pointer.  Failed, running, ready, and
    awaiting-approval stages all resume at themselves; only a contiguous
    completed prefix is skipped.
    """
    for record in order.get("stages", []):
        if not isinstance(record, Mapping):
            continue
        if record.get("status") != "completed":
            return str(record.get("name"))
    return None


def _manifest_for_order(order: Mapping[str, Any]) -> tuple[dict[str, Any], Path]:
    from lib.pipeline_loader import load_pipeline_readonly

    pipeline_type = str(order.get("pipeline_type") or "")
    if not pipeline_type:
        raise WorkOrderValidationError("work order pipeline_type is required for execution")
    manifest_path = REPO_ROOT / "pipeline_defs" / f"{pipeline_type}.yaml"
    try:
        manifest = load_pipeline_readonly(pipeline_type, defs_dir=REPO_ROOT / "pipeline_defs")
    except Exception as exc:
        raise WorkOrderValidationError(
            f"manifest for work order pipeline {pipeline_type!r} cannot be loaded: {exc}"
        ) from exc
    return manifest, manifest_path


def _read_execution_order(project_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    path = work_order_path(project_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkOrderValidationError(f"work order cannot be read: {path}") from exc
    if not isinstance(payload, dict):
        raise WorkOrderValidationError("work order root must be an object")
    manifest, manifest_path = _manifest_for_order(payload)
    validate_work_order(payload, manifest=manifest, manifest_path=manifest_path)
    return payload, manifest, manifest_path


@contextmanager
def _work_order_lock(path: Path):
    """Serialize claim/advance mutations across threads and processes."""
    with _WORK_ORDER_THREAD_LOCK:
        if FileLock is None:
            yield
            return
        try:
            with FileLock(str(path) + ".lock", timeout=10):
                yield
        except FileLockTimeout as exc:
            raise WorkOrderConflictError("timed out waiting for the work-order lock") from exc


@contextmanager
def _approval_transition_lock(project_path: Path):
    """Serialize human-gate decisions across threads and processes."""
    lock_path = project_path / "approval_transition.lock"
    with _APPROVAL_TRANSITION_THREAD_LOCK:
        if FileLock is None:
            yield
            return
        try:
            with FileLock(str(lock_path), timeout=10):
                yield
        except FileLockTimeout as exc:
            raise WorkOrderConflictError(
                "timed out waiting for the human-approval transition lock"
            ) from exc


def _atomic_write_order(project_path: Path, order: Mapping[str, Any]) -> Path:
    destination = work_order_path(project_path)
    temporary = project_path / f".{WORK_ORDER_FILENAME}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(order), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


def _require_agent_id(agent_id: Any) -> str:
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise WorkOrderStateError("agent_id is required")
    normalized = agent_id.strip()
    if len(normalized) > 200:
        raise WorkOrderStateError("agent_id must be 200 characters or fewer")
    return normalized


def _claim_is_expired(claim: Mapping[str, Any], now: datetime) -> bool:
    expires = _parse_timestamp(claim.get("lease_expires_at"))
    return expires is None or expires <= now


def _stage_gate_required(manifest: Mapping[str, Any], stage: str) -> bool:
    for record in manifest.get("stages", []):
        if isinstance(record, Mapping) and record.get("name") == stage:
            return bool(record.get("human_approval_default", False))
    raise WorkOrderStateError(f"stage {stage!r} is not declared by the manifest")


def manifest_digest(manifest_path: Path | str) -> str:
    """Return the SHA-256 digest of the exact manifest bytes used for a run."""
    path = Path(manifest_path)
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise WorkOrderValidationError(f"manifest cannot be hashed: {path}") from exc


def _normalise_stage_order(manifest: Mapping[str, Any]) -> list[str]:
    try:
        stages = [str(stage) for stage in get_stage_order(dict(manifest))]
    except Exception as exc:
        raise WorkOrderValidationError(f"manifest stage order cannot be resolved: {exc}") from exc
    if not stages:
        raise WorkOrderValidationError("manifest must declare at least one stage")
    if len(set(stages)) != len(stages):
        raise WorkOrderValidationError("manifest stage order contains duplicate stage names")
    return stages


def _stage_records(stage_order: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "order": index,
            "status": "ready" if index == 0 else "pending",
            "checkpoint": None,
            "completed_at": None,
            "error": None,
        }
        for index, name in enumerate(stage_order)
    ]


def build_work_order(
    *,
    project_id: str,
    title: str,
    topic_prompt: str,
    target_duration_seconds: int,
    pipeline_type: str,
    manifest: Mapping[str, Any],
    manifest_path: Path | str,
    selections: Mapping[str, Any],
    run_id: str | None = None,
    attempt: int = 1,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build and validate the initial queued work order for a project.

    This function is pure apart from reading the manifest hash.  It does not
    create directories, call providers, or start a process.
    """
    stage_order = _normalise_stage_order(manifest)
    resolved_run_id = run_id or str(uuid.uuid4())
    timestamp = created_at or _utc_now()
    first_stage = stage_order[0]
    first_gate = next(
        (
            stage
            for stage in manifest.get("stages", [])
            if isinstance(stage, Mapping) and stage.get("human_approval_default") is True
        ),
        None,
    )
    first_gate_name = str(first_gate.get("name")) if isinstance(first_gate, Mapping) else "pipeline"
    first_gate_artifact = None
    if isinstance(first_gate, Mapping):
        produces = first_gate.get("produces") or []
        if produces:
            first_gate_artifact = str(produces[0])
    selection_payload = {
        "playbook": str(selections.get("playbook") or ""),
        "voice": str(selections.get("voice") or ""),
        "voice_provider": str(selections.get("voice_provider") or ""),
        "render_runtime": str(selections.get("render_runtime") or ""),
        "output_profile": str(selections.get("output_profile") or ""),
        "aspect_ratio": str(selections.get("aspect_ratio") or ""),
        "source_mode": selections.get("source_mode"),
    }
    order: dict[str, Any] = {
        "version": WORK_ORDER_VERSION,
        "project_id": str(project_id),
        "title": str(title),
        "topic_prompt": str(topic_prompt),
        "target_duration_seconds": target_duration_seconds,
        "pipeline_type": str(pipeline_type),
        "manifest_version": str(manifest.get("version") or ""),
        "manifest_hash": manifest_digest(manifest_path),
        "run_id": resolved_run_id,
        "attempt": attempt,
        "run_record_ref": f"runs/{resolved_run_id}/run.json",
        "status": "queued",
        "created_at": timestamp,
        "updated_at": timestamp,
        "stages": _stage_records(stage_order),
        "current_stage": first_stage,
        "next_stage": first_stage,
        "selections": selection_payload,
        "approvals": [
            {
                "gate": first_gate_name,
                "status": "pending",
                "required": bool(first_gate),
                "artifact_ref": f"artifacts/{first_gate_artifact}.json" if first_gate_artifact else None,
                "approved_by": None,
                "approved_at": None,
                "notes": (
                    f"Human approval is required before stage {first_gate_name} advances."
                    if first_gate
                    else "No manifest stage declares a human approval gate."
                ),
            }
        ],
        "claim": {
            "claimed_by": None,
            "lease_expires_at": None,
            "last_heartbeat_at": None,
            "lease_version": 0,
        },
        "resume": {
            "last_successful_stage": None,
            "last_successful_checkpoint": None,
            "next_action": f"start_stage:{first_stage}",
            "resume_from_stage": first_stage,
        },
        "blocker": {
            "code": None,
            "message": None,
            "details": {},
        },
        "metadata": {
            "manifest_name": str(manifest.get("name") or pipeline_type),
            "manifest_category": manifest.get("category"),
            "release_status": "not_certified",
            "production_ready": False,
            "production_gate": "PR-11G",
            "attempt_started_at": None,
        },
    }
    validate_work_order(order, manifest=manifest, manifest_path=manifest_path)
    return order


def validate_work_order(
    order: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: Path | str | None = None,
) -> None:
    """Validate schema plus manifest/order identity and no-skip invariants."""
    candidate = dict(order)
    try:
        jsonschema.validate(
            instance=candidate,
            schema=_load_work_order_schema(),
            format_checker=jsonschema.FormatChecker(),
        )
    except jsonschema.ValidationError as exc:
        raise WorkOrderValidationError(f"work order failed schema validation: {exc.message}") from exc

    stage_records = candidate["stages"]
    names = [record["name"] for record in stage_records]
    orders = [record["order"] for record in stage_records]
    if names != list(dict.fromkeys(names)):
        raise WorkOrderValidationError("work order stage list contains duplicate names")
    if orders != list(range(len(stage_records))):
        raise WorkOrderValidationError("work order stage order must be contiguous and manifest-derived")

    if manifest is not None:
        expected = _normalise_stage_order(manifest)
        if names != expected:
            raise WorkOrderValidationError(
                f"work order stages {names!r} do not match manifest order {expected!r}"
            )
        manifest_version = str(manifest.get("version") or "")
        if candidate["manifest_version"] != manifest_version:
            raise WorkOrderValidationError("work order manifest_version does not match the selected manifest")
        if candidate["pipeline_type"] != str(manifest.get("name") or candidate["pipeline_type"]):
            raise WorkOrderValidationError("work order pipeline identity does not match the selected manifest")
    if manifest_path is not None:
        expected_hash = manifest_digest(manifest_path)
        if candidate["manifest_hash"] != expected_hash:
            raise WorkOrderValidationError("work order manifest_hash does not match the selected manifest bytes")

    valid_stage_names = set(names)
    for field in ("current_stage", "next_stage"):
        value = candidate[field]
        if value is not None and value not in valid_stage_names:
            raise WorkOrderValidationError(f"{field} {value!r} is not declared by the manifest")
    resume = candidate["resume"]
    for field in ("last_successful_stage", "resume_from_stage"):
        value = resume[field]
        if value is not None and value not in valid_stage_names:
            raise WorkOrderValidationError(f"resume.{field} {value!r} is not declared by the manifest")

    # A later stage cannot be completed while an earlier stage is pending.
    # This catches accidental stage skipping even when the JSON shape is valid.
    completed_orders = [
        record["order"] for record in stage_records if record["status"] == "completed"
    ]
    if completed_orders:
        highest = max(completed_orders)
        if any(stage_records[index]["status"] != "completed" for index in range(highest)):
            raise WorkOrderValidationError("completed stages contain a skipped earlier stage")

    # The resumable pointer is derived state, but it is persisted so an agent
    # can restart without reconstructing progress from a directory scan.  It
    # must nevertheless agree with the manifest-ordered records on every read.
    derived_next = next_stage_from_work_order(candidate)
    if candidate["next_stage"] != derived_next:
        raise WorkOrderValidationError(
            f"next_stage {candidate['next_stage']!r} does not match the first "
            f"non-completed manifest stage {derived_next!r}"
        )
    current_stage = candidate["current_stage"]
    if derived_next is None:
        if current_stage is not None:
            raise WorkOrderValidationError("current_stage must be null after all manifest stages complete")
    elif current_stage not in (None, derived_next):
        raise WorkOrderValidationError(
            f"current_stage {current_stage!r} does not match resumable stage {derived_next!r}"
        )

    # Terminal state must agree with the manifest-derived pointer.  Without
    # this check a hand-edited order could claim ``completed`` while a later
    # stage is still pending, causing a resume caller to skip work entirely.
    if derived_next is None:
        if candidate["status"] not in {"completed", "cancelled"}:
            raise WorkOrderValidationError(
                "work order with all manifest stages completed must be terminal"
            )
    elif candidate["status"] == "completed":
        raise WorkOrderValidationError(
            "work order cannot be completed while a manifest stage remains"
        )

    claim = candidate["claim"]
    claimed_by = claim.get("claimed_by")
    has_expiry = claim.get("lease_expires_at") is not None
    has_heartbeat = claim.get("last_heartbeat_at") is not None
    if claimed_by is None and (has_expiry or has_heartbeat):
        raise WorkOrderValidationError(
            "claim lease timestamps cannot be set when claimed_by is null"
        )
    if claimed_by is not None and not has_expiry:
        raise WorkOrderValidationError(
            "claim.lease_expires_at is required when a work order is claimed"
        )


def work_order_path(project_dir: Path | str) -> Path:
    return Path(project_dir) / WORK_ORDER_FILENAME


def write_work_order(project_dir: Path | str, order: Mapping[str, Any]) -> Path:
    """Validate and atomically persist a work order under an existing project."""
    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    validate_work_order(order)
    destination = work_order_path(project_path)
    with _work_order_lock(destination):
        result = _atomic_write_order(project_path, order)
        # The run record is the provenance ledger paired with the work order.
        # Keep creation inside this durable write boundary so every newly
        # accepted work order has an inspectable run identity before an agent
        # can claim it.  Import lazily to avoid a module-import cycle.
        from lib.run_record import sync_run_record

        sync_run_record(project_path, order)
        return result


def read_work_order(
    project_dir: Path | str,
    *,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    """Read and validate a persisted work order."""
    path = work_order_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkOrderValidationError(f"work order cannot be read: {path}") from exc
    if not isinstance(data, dict):
        raise WorkOrderValidationError("work order root must be an object")
    validate_work_order(data, manifest=manifest, manifest_path=manifest_path)
    return data


def _normalise_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise WorkOrderStateError("now must be a datetime")
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _claim_owner(order: Mapping[str, Any], agent_id: str, now: datetime) -> None:
    claim = order.get("claim") or {}
    owner = claim.get("claimed_by")
    if owner != agent_id:
        if owner and not _claim_is_expired(claim, now):
            raise WorkOrderConflictError(
                f"work order is leased by another live agent: {owner!r}"
            )
        raise WorkOrderConflictError("agent does not hold the active work-order lease")
    if _claim_is_expired(claim, now):
        raise WorkOrderConflictError("work-order lease has expired; reclaim it before advancing")


def claim_work_order(
    project_dir: Path | str,
    agent_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically claim one queued/resumable work order for an agent.

    A live lease owned by another agent is a conflict.  An expired lease may
    be reclaimed, incrementing ``claim.lease_version`` so observers can detect
    ownership changes.  Repeating a claim by the same live agent is idempotent
    and simply renews the lease.
    """
    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    agent = _require_agent_id(agent_id)
    seconds = _lease_seconds(lease_seconds)
    current_time = _normalise_now(now)
    destination = work_order_path(project_path)
    with _work_order_lock(destination):
        order, manifest, manifest_path = _read_execution_order(project_path)
        if order["status"] in {"completed", "cancelled"}:
            raise WorkOrderStateError(
                f"cannot claim a terminal work order with status {order['status']!r}"
            )
        claim = order["claim"]
        owner = claim.get("claimed_by")
        expired = _claim_is_expired(claim, current_time) if owner else True
        if owner and owner != agent and not expired:
            raise WorkOrderConflictError(
                f"work order is leased by another live agent: {owner!r}"
            )

        # A repeated claim by the same live owner is an idempotent lease
        # renewal.  The work order still needs its lease timestamp persisted,
        # but the paired run record has no lease fields and therefore does not
        # change for this path.  Avoiding that second fsync keeps duplicate
        # ``/run`` requests inside the queue-start latency budget while the
        # ordinary claim/recovery paths continue to synchronize both ledgers.
        renewing_live_claim = owner == agent and not expired

        recovered_owner = owner if owner and expired and owner != agent else None
        if owner != agent or expired:
            claim["lease_version"] = int(claim.get("lease_version") or 0) + 1
            metadata = order.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["attempt_started_at"] = current_time.isoformat()
                if recovered_owner:
                    metadata.update({
                        "last_recovered_at": current_time.isoformat(),
                        "last_recovered_from_agent": str(recovered_owner),
                        "last_recovery_reason": "worker_lease_expired",
                    })
        claim["claimed_by"] = agent
        claim["last_heartbeat_at"] = current_time.isoformat()
        claim["lease_expires_at"] = (current_time + timedelta(seconds=seconds)).isoformat()

        next_stage = next_stage_from_work_order(order)
        if next_stage is None:
            raise WorkOrderStateError("work order has no resumable stage")
        next_record = next(
            item for item in order["stages"] if item.get("name") == next_stage
        )
        # Claiming is the point at which an agent starts work.  Keep a failed
        # stage resumable, but expose the active execution state explicitly so
        # observers do not mistake a live lease for an untouched ``ready``
        # record.  Awaiting-approval remains gated and is intentionally not
        # rewritten to running.
        if next_record.get("status") in {"pending", "ready", "failed"}:
            next_record["status"] = "running"
        order["current_stage"] = next_stage
        order["next_stage"] = next_stage
        if order["status"] in {"queued", "preflighting", "failed", "revising"}:
            order["status"] = "running"
            if order.get("blocker", {}).get("code") in {"stage_failed", "human_revision_requested"}:
                order["blocker"] = {"code": None, "message": None, "details": {}}
        order["resume"]["resume_from_stage"] = next_stage
        order["resume"]["next_action"] = f"start_stage:{next_stage}"
        order["updated_at"] = current_time.isoformat()
        validate_work_order(order, manifest=manifest, manifest_path=manifest_path)
        _atomic_write_order(project_path, order)
        if not renewing_live_claim:
            from lib.run_record import sync_run_record

            sync_run_record(project_path, order, now=current_time)
        return order


def resume_work_order(
    project_dir: Path | str,
    agent_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Claim a run and return the manifest-derived stage to resume."""
    return claim_work_order(
        project_dir,
        agent_id,
        lease_seconds=lease_seconds,
        now=now,
    )


def heartbeat_work_order(
    project_dir: Path | str,
    agent_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Renew an active lease without changing stage state."""
    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    agent = _require_agent_id(agent_id)
    seconds = _lease_seconds(lease_seconds)
    current_time = _normalise_now(now)
    destination = work_order_path(project_path)
    with _work_order_lock(destination):
        order, manifest, manifest_path = _read_execution_order(project_path)
        _claim_owner(order, agent, current_time)
        claim = order["claim"]
        claim["last_heartbeat_at"] = current_time.isoformat()
        claim["lease_expires_at"] = (current_time + timedelta(seconds=seconds)).isoformat()
        order["updated_at"] = current_time.isoformat()
        validate_work_order(order, manifest=manifest, manifest_path=manifest_path)
        _atomic_write_order(project_path, order)
        from lib.run_record import sync_run_record
        sync_run_record(project_path, order, now=current_time)
        return order


def release_work_order(
    project_dir: Path | str,
    agent_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Release an agent lease while retaining the resumable stage pointer."""
    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    agent = _require_agent_id(agent_id)
    current_time = _normalise_now(now)
    destination = work_order_path(project_path)
    with _work_order_lock(destination):
        order, manifest, manifest_path = _read_execution_order(project_path)
        claim = order["claim"]
        owner = claim.get("claimed_by")
        if owner != agent and owner and not _claim_is_expired(claim, current_time):
            raise WorkOrderConflictError(f"work order is leased by another live agent: {owner!r}")
        if owner != agent:
            raise WorkOrderConflictError("agent does not hold the work-order lease")
        for field in ("claimed_by", "lease_expires_at", "last_heartbeat_at"):
            claim[field] = None
        next_stage = next_stage_from_work_order(order)
        order["current_stage"] = next_stage
        order["next_stage"] = next_stage
        if order["status"] not in {"awaiting_approval", "completed", "cancelled"}:
            order["status"] = "queued"
        order["resume"]["resume_from_stage"] = next_stage
        order["resume"]["next_action"] = (
            f"start_stage:{next_stage}" if next_stage else "complete"
        )
        order["updated_at"] = current_time.isoformat()
        validate_work_order(order, manifest=manifest, manifest_path=manifest_path)
        _atomic_write_order(project_path, order)
        from lib.run_record import sync_run_record
        sync_run_record(project_path, order, now=current_time)
        return order


def _clear_claim(order: dict[str, Any]) -> None:
    claim = order.setdefault("claim", {})
    for field in ("claimed_by", "lease_expires_at", "last_heartbeat_at"):
        claim[field] = None


def _assert_lifecycle_owner(
    order: Mapping[str, Any], agent_id: str, now: datetime
) -> tuple[str | None, bool]:
    """Validate cancellation/restart ownership and return owner/expiry state."""
    agent = _require_agent_id(agent_id)
    claim = order.get("claim") or {}
    owner = claim.get("claimed_by")
    expired = _claim_is_expired(claim, now) if owner else True
    if owner and owner != agent and not expired:
        raise WorkOrderConflictError(
            f"work order is leased by another live agent: {owner!r}"
        )
    return (str(owner) if owner else None), expired


def cancel_work_order(
    project_dir: Path | str,
    agent_id: str,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Cancel a queued or active run without deleting checkpoints/artifacts.

    Cancellation is a durable terminal state for the current attempt.  The
    manifest-derived resume pointer remains on the active stage so an explicit
    restart can continue from the last completed checkpoint rather than
    silently skipping or deleting work.
    """
    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    current_time = _normalise_now(now)
    agent = _require_agent_id(agent_id)
    destination = work_order_path(project_path)
    with _work_order_lock(destination):
        order, manifest, manifest_path = _read_execution_order(project_path)
        if order["status"] in {"completed", "cancelled"}:
            raise WorkOrderStateError(
                f"cannot cancel a terminal work order with status {order['status']!r}"
            )
        owner, _expired = _assert_lifecycle_owner(order, agent, current_time)
        active_stage = next_stage_from_work_order(order)
        if active_stage is None:
            raise WorkOrderStateError("work order has no cancellable stage")
        stage_record = next(
            item for item in order["stages"] if item.get("name") == active_stage
        )
        stage_record["status"] = "cancelled"
        stage_record["completed_at"] = None
        stage_record["error"] = (reason or "cancelled by operator").strip()[:2000]
        order["status"] = "cancelled"
        order["current_stage"] = active_stage
        order["next_stage"] = active_stage
        order["resume"].update({
            "resume_from_stage": active_stage,
            "next_action": "restart_required",
        })
        order["blocker"] = {
            "code": "cancelled",
            "message": f"work order cancelled at stage {active_stage!r}",
            "details": {
                "stage": active_stage,
                "cancelled_by": agent,
                "reason": reason or "cancelled by operator",
            },
        }
        metadata = order.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.update({
                "cancelled_at": current_time.isoformat(),
                "cancelled_by": agent,
                "cancel_reason": reason or "cancelled by operator",
                "cancelled_owner": owner,
            })
        _clear_claim(order)
        order["updated_at"] = current_time.isoformat()
        validate_work_order(order, manifest=manifest, manifest_path=manifest_path)
        _atomic_write_order(project_path, order)
        from lib.run_record import sync_run_record

        sync_run_record(project_path, order, now=current_time)

    try:
        from lib.events import emit_event

        emit_event(project_path, {
            "tool": "work_order",
            "event": "work_order_cancelled",
            "stage": active_stage,
            "agent_id": agent,
            "reason": reason or "cancelled by operator",
        })
    except Exception:
        pass
    return order


def restart_work_order(
    project_dir: Path | str,
    agent_id: str,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reset a cancelled/failed attempt to a resumable queued state.

    Completed manifest stages and their checkpoints remain untouched.  Only
    the first non-completed stage is made ready, preserving the exact
    manifest-derived resume boundary.  A live lease must be released or
    expire before restart, preventing duplicate active work.
    """
    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    current_time = _normalise_now(now)
    agent = _require_agent_id(agent_id)
    destination = work_order_path(project_path)
    with _work_order_lock(destination):
        order, manifest, manifest_path = _read_execution_order(project_path)
        if order["status"] == "completed":
            raise WorkOrderStateError("cannot restart a completed work order")
        if order["status"] == "awaiting_approval":
            raise WorkOrderStateError(
                "cannot restart an awaiting-approval work order; resolve the human gate first"
            )
        owner, expired = _assert_lifecycle_owner(order, agent, current_time)
        if owner and owner == agent and not expired:
            raise WorkOrderConflictError(
                "release or cancel the live work-order lease before restarting"
            )
        active_stage = next_stage_from_work_order(order)
        if active_stage is None:
            raise WorkOrderStateError("work order has no resumable stage")
        stage_record = next(
            item for item in order["stages"] if item.get("name") == active_stage
        )
        if stage_record.get("status") == "awaiting_approval":
            raise WorkOrderStateError(
                f"stage {active_stage!r} is awaiting human approval and cannot be restarted"
            )
        stage_record["status"] = "ready"
        stage_record["completed_at"] = None
        stage_record["error"] = None
        order["status"] = "queued"
        order["current_stage"] = active_stage
        order["next_stage"] = active_stage
        order["resume"].update({
            "resume_from_stage": active_stage,
            "next_action": f"start_stage:{active_stage}",
        })
        order["blocker"] = {"code": None, "message": None, "details": {}}
        metadata = order.setdefault("metadata", {})
        if isinstance(metadata, dict):
            try:
                restart_count = int(metadata.get("restart_count") or 0) + 1
            except (TypeError, ValueError):
                restart_count = 1
            metadata.update({
                "restart_count": restart_count,
                "last_restart_at": current_time.isoformat(),
                "last_restart_by": agent,
                "last_restart_reason": reason or "explicit restart",
                "last_restart_owner": owner,
            })
        _clear_claim(order)
        order["updated_at"] = current_time.isoformat()
        validate_work_order(order, manifest=manifest, manifest_path=manifest_path)
        _atomic_write_order(project_path, order)
        from lib.run_record import sync_run_record

        sync_run_record(project_path, order, now=current_time)

    try:
        from lib.events import emit_event

        emit_event(project_path, {
            "tool": "work_order",
            "event": "work_order_restarted",
            "stage": active_stage,
            "agent_id": agent,
            "reason": reason or "explicit restart",
        })
    except Exception:
        pass
    return order


def _checkpoint_path_for_ref(project_path: Path, stage: str, checkpoint_ref: str | None) -> Path:
    candidate = project_path / f"checkpoint_{stage}.json" if not checkpoint_ref else Path(checkpoint_ref)
    if not candidate.is_absolute():
        candidate = project_path / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to(project_path.resolve())
    except (ValueError, OSError) as exc:
        raise WorkOrderStateError("checkpoint_ref must resolve inside the project directory") from exc
    return candidate


def _load_stage_checkpoint(
    project_path: Path,
    order: Mapping[str, Any],
    stage: str,
    checkpoint_ref: str | None,
) -> tuple[dict[str, Any], str]:
    from lib.checkpoint import validate_checkpoint

    path = _checkpoint_path_for_ref(project_path, stage, checkpoint_ref)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkOrderStateError(f"checkpoint cannot be read: {path}") from exc
    if not isinstance(payload, dict):
        raise WorkOrderStateError("checkpoint root must be an object")
    try:
        validate_checkpoint(payload)
    except Exception as exc:
        raise WorkOrderStateError(f"checkpoint failed validation: {exc}") from exc
    if payload.get("stage") != stage:
        raise WorkOrderStateError(
            f"checkpoint stage {payload.get('stage')!r} does not match requested stage {stage!r}"
        )
    for field in ("project_id", "pipeline_type", "run_id"):
        if payload.get(field) != order.get(field):
            raise WorkOrderStateError(
                f"checkpoint {field} does not match work-order identity"
            )
    return payload, path.relative_to(project_path.resolve()).as_posix()


def _set_approval_record(
    order: dict[str, Any],
    *,
    stage: str,
    required: bool,
    status: str,
    checkpoint_ref: str,
    now: datetime,
    approval_record: Mapping[str, Any] | None = None,
) -> None:
    record = next((item for item in order["approvals"] if item.get("gate") == stage), None)
    if record is None:
        record = {
            "gate": stage,
            "status": status,
            "required": required,
            "artifact_ref": checkpoint_ref,
            "approved_by": None,
            "approved_at": None,
            "notes": None,
        }
        order["approvals"].append(record)
    else:
        record["status"] = status
        record["required"] = required
        record["artifact_ref"] = checkpoint_ref
    if status == "approved":
        record["approved_at"] = (
            (approval_record or {}).get("recorded_at")
            or record.get("approved_at")
            or now.isoformat()
        )
    if isinstance(approval_record, Mapping):
        record.update({
            "approval_id": approval_record.get("approval_id"),
            "artifact_digest": approval_record.get("artifact_digest"),
            "artifact_version": approval_record.get("artifact_version"),
            "decision": approval_record.get("decision"),
            "approved_by": approval_record.get("approver_id"),
            "record_ref": "approval_records.json",
            "notes": approval_record.get("notes"),
        })


def advance_work_order(
    project_dir: Path | str,
    agent_id: str,
    stage: str,
    checkpoint_ref: str | None = None,
    *,
    checkpoint_path: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Advance exactly one manifest stage from a validated checkpoint.

    The checkpoint is the stage's output evidence.  A gated stage can only
    become completed when the checkpoint carries explicit human approval;
    otherwise it remains ``awaiting_approval`` and the next stage cannot move.
    Failed checkpoints leave the stage resumable at itself.  No caller can
    select a later stage or bypass the manifest order.
    """
    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    agent = _require_agent_id(agent_id)
    if not isinstance(stage, str) or not stage.strip():
        raise WorkOrderStateError("stage is required")
    stage = stage.strip()
    if checkpoint_ref and checkpoint_path and checkpoint_ref != checkpoint_path:
        raise WorkOrderStateError("checkpoint_ref and checkpoint_path must match when both are supplied")
    resolved_checkpoint_ref = checkpoint_ref or checkpoint_path
    current_time = _normalise_now(now)
    destination = work_order_path(project_path)
    with _work_order_lock(destination):
        order, manifest, manifest_path = _read_execution_order(project_path)
        _claim_owner(order, agent, current_time)
        expected_stage = next_stage_from_work_order(order)
        if stage != expected_stage:
            raise WorkOrderStateError(
                f"cannot advance stage {stage!r}; manifest-derived next stage is {expected_stage!r}"
            )
        record = next((item for item in order["stages"] if item.get("name") == stage), None)
        if record is None:
            raise WorkOrderStateError(f"stage {stage!r} is not declared by the work order")
        checkpoint, relative_ref = _load_stage_checkpoint(
            project_path, order, stage, resolved_checkpoint_ref
        )
        checkpoint_status = str(checkpoint.get("status") or "")
        if checkpoint_status == "in_progress":
            raise WorkOrderStateError("an in_progress checkpoint cannot advance a stage")

        gate_required = _stage_gate_required(manifest, stage)
        approval_record = checkpoint.get("approval_record")
        approved = False
        explicit_gate = bool(checkpoint.get("human_approval_required"))
        if checkpoint_status == "completed":
            final_review = (checkpoint.get("artifacts") or {}).get("final_review")
            if stage == "compose":
                if not isinstance(final_review, Mapping):
                    raise WorkOrderStateError(
                        "compose cannot advance without a persisted final_review artifact"
                    )
                review_status = str(final_review.get("status") or "").strip().lower()
                if review_status != "pass":
                    raise WorkOrderStateError(
                        f"compose cannot advance with final_review.status={review_status!r}"
                    )
            if stage == "publish":
                review_path = project_path / "artifacts" / "final_review.json"
                try:
                    review_payload = json.loads(review_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise WorkOrderStateError(
                        "publish cannot advance without a readable final_review artifact"
                    ) from exc
                if str(review_payload.get("status") or "").strip().lower() != "pass":
                    raise WorkOrderStateError(
                        f"publish cannot advance with final_review.status={review_payload.get('status')!r}"
                    )
            if gate_required or explicit_gate:
                if not isinstance(approval_record, Mapping):
                    raise WorkOrderStateError(
                        f"stage {stage!r} requires an immutable approval record before completion"
                    )
                try:
                    from lib.approval_contracts import validate_checkpoint_approval

                    validate_checkpoint_approval(checkpoint, approval_record)
                except Exception as exc:
                    raise WorkOrderStateError(
                        f"stage {stage!r} approval is not bound to this checkpoint artifact: {exc}"
                    ) from exc
                approved = True
            record.update({
                "status": "completed",
                "checkpoint": relative_ref,
                "completed_at": checkpoint.get("timestamp") or current_time.isoformat(),
                "error": None,
            })
            _set_approval_record(
                order,
                stage=stage,
                required=gate_required or explicit_gate,
                status="approved" if approved else "not_required",
                checkpoint_ref=relative_ref,
                now=current_time,
                approval_record=approval_record if isinstance(approval_record, Mapping) else None,
            )
            next_stage = next_stage_from_work_order(order)
            if next_stage is None:
                order["current_stage"] = None
                order["next_stage"] = None
                order["status"] = "completed"
                order["resume"].update({
                    "last_successful_stage": stage,
                    "last_successful_checkpoint": relative_ref,
                    "next_action": "complete",
                    "resume_from_stage": None,
                })
                for field in ("claimed_by", "lease_expires_at", "last_heartbeat_at"):
                    order["claim"][field] = None
            else:
                next_record = next(item for item in order["stages"] if item.get("name") == next_stage)
                if next_record.get("status") == "pending":
                    next_record["status"] = "ready"
                order["current_stage"] = next_stage
                order["next_stage"] = next_stage
                order["status"] = "running"
                order["resume"].update({
                    "last_successful_stage": stage,
                    "last_successful_checkpoint": relative_ref,
                    "next_action": f"start_stage:{next_stage}",
                    "resume_from_stage": next_stage,
                })
            order["blocker"] = {"code": None, "message": None, "details": {}}
        elif checkpoint_status == "awaiting_human":
            record.update({
                "status": "awaiting_approval",
                "checkpoint": relative_ref,
                "completed_at": None,
                "error": None,
            })
            order["current_stage"] = stage
            order["next_stage"] = stage
            order["status"] = "awaiting_approval"
            order["resume"].update({
                "next_action": f"await_approval:{stage}",
                "resume_from_stage": stage,
            })
            order["blocker"] = {
                "code": "human_approval_required",
                "message": f"stage {stage!r} is awaiting human approval",
                "details": {"checkpoint": relative_ref},
            }
            _set_approval_record(
                order,
                stage=stage,
                required=True,
                status="pending",
                checkpoint_ref=relative_ref,
                now=current_time,
            )
        elif checkpoint_status == "failed":
            record.update({
                "status": "failed",
                "checkpoint": relative_ref,
                "completed_at": None,
                "error": checkpoint.get("error") or "stage checkpoint reported failure",
            })
            order["current_stage"] = stage
            order["next_stage"] = stage
            order["status"] = "failed"
            order["resume"].update({
                "next_action": f"retry_stage:{stage}",
                "resume_from_stage": stage,
            })
            order["blocker"] = {
                "code": "stage_failed",
                "message": f"stage {stage!r} failed and must be retried",
                "details": {"checkpoint": relative_ref, "error": record["error"]},
            }
        else:
            raise WorkOrderStateError(
                f"checkpoint status {checkpoint_status!r} cannot advance a work-order stage"
            )

        order["updated_at"] = current_time.isoformat()
        validate_work_order(order, manifest=manifest, manifest_path=manifest_path)
        _atomic_write_order(project_path, order)
        from lib.run_record import sync_run_record
        sync_run_record(project_path, order, now=current_time)
        return order


def _invalidate_downstream_outputs(
    project_path: Path,
    order: dict[str, Any],
    manifest: Mapping[str, Any],
    *,
    stage: str,
    now: datetime,
) -> list[str]:
    """Quarantine downstream state after a human revision/rejection.

    A later artifact must never remain discoverable as the current input after
    an earlier stage is sent back. The files are moved into a project-local
    history directory (never deleted), while the work-order records are reset
    to the manifest-derived pending prefix.
    """
    import shutil

    stage_records = [item for item in order.get("stages", []) if isinstance(item, Mapping)]
    stage_index = next(
        (index for index, item in enumerate(stage_records) if item.get("name") == stage),
        None,
    )
    if stage_index is None:
        raise WorkOrderStateError(f"stage {stage!r} is not declared by the work order")

    definitions = {
        str(item.get("name")): item
        for item in manifest.get("stages", []) or []
        if isinstance(item, Mapping) and item.get("name")
    }
    producer_index: dict[str, int] = {}
    for index, item in enumerate(stage_records):
        definition = definitions.get(str(item.get("name"))) or {}
        for artifact_name in definition.get("produces", []) or []:
            producer_index[str(artifact_name)] = index

    stamp = "".join(ch for ch in now.isoformat() if ch.isalnum()) or str(now.timestamp())
    invalidated_dir = project_path / "artifacts" / "history" / f"invalidated_{stamp}_{uuid.uuid4().hex[:8]}"
    invalidated: list[str] = []

    for index, record in enumerate(stage_records):
        if index <= stage_index:
            continue
        name = str(record.get("name"))
        # Preserve a downstream checkpoint in the ordinary replay history and
        # remove the current pointer so Backlot cannot display it as live.
        checkpoint_path = project_path / f"checkpoint_{name}.json"
        if checkpoint_path.is_file():
            history_dir = project_path / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            target = history_dir / f"checkpoint_{name}_{stamp}_{uuid.uuid4().hex[:8]}_invalidated.json"
            shutil.copy2(checkpoint_path, target)
            try:
                checkpoint_path.unlink()
            except OSError as exc:
                raise WorkOrderStateError(
                    f"could not quarantine downstream checkpoint {checkpoint_path}: {exc}"
                ) from exc
            invalidated.append(target.relative_to(project_path).as_posix())

        definition = definitions.get(name) or {}
        for artifact_name in definition.get("produces", []) or []:
            artifact_name = str(artifact_name)
            # Do not remove an artifact whose only producer is at or before the
            # revised stage (decision logs are append-only and intentionally
            # remain visible). A later producer is safe to quarantine.
            if producer_index.get(artifact_name, -1) <= stage_index:
                continue
            artifact_path = project_path / "artifacts" / f"{artifact_name}.json"
            if not artifact_path.is_file():
                continue
            invalidated_dir.mkdir(parents=True, exist_ok=True)
            target = invalidated_dir / artifact_path.name
            if target.exists():
                target = invalidated_dir / f"{artifact_path.stem}_{uuid.uuid4().hex[:8]}{artifact_path.suffix}"
            shutil.copy2(artifact_path, target)
            try:
                artifact_path.unlink()
            except OSError as exc:
                raise WorkOrderStateError(
                    f"could not quarantine downstream artifact {artifact_path}: {exc}"
                ) from exc
            invalidated.append(target.relative_to(project_path).as_posix())

        record["status"] = "pending"
        record["checkpoint"] = None
        record["completed_at"] = None
        record["error"] = f"invalidated by human {stage} decision"

    if invalidated:
        metadata = order.setdefault("metadata", {})
        if isinstance(metadata, dict):
            entries = metadata.setdefault("invalidated_artifacts", [])
            if not isinstance(entries, list):
                entries = []
                metadata["invalidated_artifacts"] = entries
            entries.append({
                "stage": stage,
                "recorded_at": now.isoformat(),
                "paths": invalidated,
            })
    return invalidated


def _decide_human_gate_unlocked(
    project_dir: Path | str,
    stage: str,
    *,
    approver_id: str,
    decision: str = "approve",
    notes: str | None = None,
    agent_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record and apply one human decision for the active gated stage.

    ``approve`` creates a completed checkpoint only after the decision is
    bound to the current checkpoint's artifact digest/version, then advances
    the manifest-derived work order at most once.  ``revise`` reopens the
    same stage (downstream stages remain untouched but cannot run), while
    ``reject`` terminally stops the current attempt.  Repeating an approval
    after the checkpoint has already advanced is an idempotent no-op.
    """
    from lib.approval_contracts import (
        ApprovalValidationError,
        append_approval_record,
        artifact_digest,
        build_checkpoint_approval,
        latest_approval,
        validate_checkpoint_approval,
    )
    from lib.checkpoint import write_checkpoint

    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    normalized_stage = str(stage or "").strip()
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in {"approve", "revise", "reject"}:
        raise WorkOrderStateError("decision must be approve, revise, or reject")
    if not str(approver_id or "").strip():
        raise WorkOrderStateError("approver_id is required")
    current_time = _normalise_now(now)

    # Read the current stage/checkpoint before generating a record.  A later
    # call will re-read this state and therefore cannot approve an old version.
    order = read_work_order(project_path)
    expected_stage = next_stage_from_work_order(order)
    if agent_id:
        claim = order.get("claim") or {}
        owner = claim.get("claimed_by")
        expires = _parse_timestamp(claim.get("lease_expires_at"))
        if owner and expires and expires > current_time and str(owner) != str(agent_id).strip():
            raise WorkOrderConflictError(
                f"approval transition was requested for agent {agent_id!r}, but the live lease belongs to {owner!r}"
            )
    if normalized_stage != expected_stage:
        # Once an approved stage has advanced, repeated UI clicks are safe and
        # should return the durable current state rather than advance again.
        prior = latest_approval(
            project_path,
            stage=normalized_stage,
            run_id=str(order.get("run_id")),
        )
        if prior and prior.get("decision") == "approve":
            return {
                "ok": True,
                "idempotent": True,
                "decision": prior,
                "transition": "already_advanced",
                "work_order": order,
            }
        raise WorkOrderStateError(
            f"cannot decide stage {normalized_stage!r}; manifest-derived next stage is {expected_stage!r}"
        )

    checkpoint, checkpoint_ref = _load_stage_checkpoint(
        project_path, order, normalized_stage, None
    )
    manifest, _manifest_path = _manifest_for_order(order)
    manifest_gate = _stage_gate_required(manifest, normalized_stage)
    if not manifest_gate and checkpoint.get("human_approval_required") is not True:
        raise WorkOrderStateError(
            f"stage {normalized_stage!r} is not declared as human-gated"
        )
    if checkpoint.get("status") == "completed":
        existing = checkpoint.get("approval_record")
        if not isinstance(existing, Mapping):
            raise WorkOrderStateError(
                f"completed stage {normalized_stage!r} has no immutable approval record"
            )
        try:
            validate_checkpoint_approval(checkpoint, existing)
        except ApprovalValidationError as exc:
            raise WorkOrderStateError(str(exc)) from exc
        return {
            "ok": True,
            "idempotent": True,
            "decision": dict(existing),
            "transition": "already_completed",
            "work_order": order,
        }
    if checkpoint.get("status") != "awaiting_human":
        raise WorkOrderStateError(
            f"stage {normalized_stage!r} is not awaiting human approval (status={checkpoint.get('status')!r})"
        )

    current_digest = artifact_digest(checkpoint.get("artifacts") or {})
    prior = latest_approval(
        project_path,
        stage=normalized_stage,
        artifact_digest_value=current_digest,
        artifact_version=str(checkpoint.get("timestamp") or ""),
        run_id=str(order.get("run_id")),
    )
    supersedes = prior.get("approval_id") if prior else None
    record = build_checkpoint_approval(
        checkpoint,
        approver_id=str(approver_id).strip(),
        decision=normalized_decision,
        artifact_ref=checkpoint_ref,
        notes=notes,
        supersedes=supersedes,
        recorded_at=current_time,
    )

    if normalized_decision == "approve":
        # write_checkpoint archives the awaiting snapshot, validates the
        # digest-bound record, and appends it to the immutable log.
        write_checkpoint(
            project_path.parent,
            project_path.name,
            normalized_stage,
            "completed",
            dict(checkpoint.get("artifacts") or {}),
            pipeline_type=checkpoint.get("pipeline_type"),
            run_id=checkpoint.get("run_id"),
            attempt=checkpoint.get("attempt"),
            producer_stage=checkpoint.get("producer_stage"),
            producer_tool="human-approval",
            style_playbook=checkpoint.get("style_playbook"),
            checkpoint_policy=str(checkpoint.get("checkpoint_policy") or "guided"),
            human_approval_required=True,
            approval_record=record,
            timestamp=record["artifact_version"],
            review=checkpoint.get("review"),
            cost_snapshot=checkpoint.get("cost_snapshot"),
            metadata=checkpoint.get("metadata"),
        )
        owner = (order.get("claim") or {}).get("claimed_by")
        owner_expires = _parse_timestamp((order.get("claim") or {}).get("lease_expires_at"))
        if owner and owner_expires and owner_expires > current_time:
            try:
                advanced = advance_work_order(
                    project_path, str(owner), normalized_stage, now=current_time
                )
                return {
                    "ok": True,
                    "idempotent": False,
                    "decision": record,
                    "transition": "advanced",
                    "work_order": advanced,
                }
            except WorkOrderConflictError:
                # The approval remains durable; the caller must reclaim and
                # advance after the previous lease is gone.
                pass

        # No live worker owns the run.  The approval is still allowed to
        # complete the reviewed stage, but no worker is started implicitly.
        # Queue the exact manifest-derived next stage so a later claim resumes
        # there instead of leaving the order stuck in awaiting_approval.
        refreshed = read_work_order(project_path)
        with _work_order_lock(work_order_path(project_path)):
            refreshed, manifest, manifest_path = _read_execution_order(project_path)
            stage_record = next(
                item for item in refreshed["stages"] if item.get("name") == normalized_stage
            )
            stage_record.update({
                "status": "completed",
                "checkpoint": checkpoint_ref,
                "completed_at": record["artifact_version"],
                "error": None,
            })
            summary = next(
                (item for item in refreshed.get("approvals", []) if item.get("gate") == normalized_stage),
                None,
            )
            if summary is None:
                summary = {"gate": normalized_stage, "required": True}
                refreshed.setdefault("approvals", []).append(summary)
            summary.update({
                "status": "approved",
                "artifact_ref": checkpoint_ref,
                "approval_id": record["approval_id"],
                "artifact_digest": record["artifact_digest"],
                "artifact_version": record["artifact_version"],
                "decision": "approve",
                "approved_by": record["approver_id"],
                "approved_at": record["recorded_at"],
                "record_ref": "approval_records.json",
                "notes": record.get("notes"),
            })
            next_stage = next_stage_from_work_order(refreshed)
            refreshed["current_stage"] = next_stage
            refreshed["next_stage"] = next_stage
            if next_stage is None:
                refreshed["status"] = "completed"
                refreshed["resume"].update({
                    "last_successful_stage": normalized_stage,
                    "last_successful_checkpoint": checkpoint_ref,
                    "next_action": "complete",
                    "resume_from_stage": None,
                })
            else:
                next_record = next(
                    item for item in refreshed["stages"] if item.get("name") == next_stage
                )
                if next_record.get("status") == "pending":
                    next_record["status"] = "ready"
                refreshed["status"] = "queued"
                refreshed["resume"].update({
                    "last_successful_stage": normalized_stage,
                    "last_successful_checkpoint": checkpoint_ref,
                    "next_action": f"start_stage:{next_stage}",
                    "resume_from_stage": next_stage,
                })
            refreshed["blocker"] = {"code": None, "message": None, "details": {}}
            _clear_claim(refreshed)
            refreshed["updated_at"] = current_time.isoformat()
            validate_work_order(refreshed, manifest=manifest, manifest_path=manifest_path)
            _atomic_write_order(project_path, refreshed)
            from lib.run_record import sync_run_record

            sync_run_record(project_path, refreshed, now=current_time)
        return {
            "ok": True,
            "idempotent": False,
            "decision": record,
            "transition": "approved_queued" if refreshed.get("next_stage") else "completed",
            "work_order": refreshed,
        }

    # Revision/rejection decisions are persisted without mutating or deleting
    # the reviewed checkpoint.  A subsequent agent submission creates the next
    # checkpoint version, so the previous artifact and decision remain in
    # history for audit/replay.
    append_approval_record(project_path, record)
    with _work_order_lock(work_order_path(project_path)):
        updated, manifest, manifest_path = _read_execution_order(project_path)
        invalidated_paths = _invalidate_downstream_outputs(
            project_path,
            updated,
            manifest,
            stage=normalized_stage,
            now=current_time,
        )
        stage_record = next(
            item for item in updated["stages"] if item.get("name") == normalized_stage
        )
        summary = next(
            (item for item in updated.get("approvals", []) if item.get("gate") == normalized_stage),
            None,
        )
        if summary is None:
            summary = {"gate": normalized_stage, "required": True}
            updated.setdefault("approvals", []).append(summary)
        summary.update({
            "status": "rejected" if normalized_decision == "reject" else "pending",
            "artifact_ref": checkpoint_ref,
            "approval_id": record["approval_id"],
            "artifact_digest": record["artifact_digest"],
            "artifact_version": record["artifact_version"],
            "decision": normalized_decision,
            "approved_by": record["approver_id"],
            "approved_at": record["recorded_at"],
            "record_ref": "approval_records.json",
            "notes": record.get("notes"),
        })
        if normalized_decision == "reject":
            stage_record.update({
                "status": "cancelled",
                "completed_at": None,
                "error": (notes or "rejected by human reviewer").strip()[:2000],
            })
            updated["status"] = "cancelled"
            updated["blocker"] = {
                "code": "human_rejected",
                "message": f"stage {normalized_stage!r} was rejected by a human reviewer",
                "details": {
                    "approval_id": record["approval_id"],
                    "checkpoint": checkpoint_ref,
                    "invalidated_paths": invalidated_paths,
                },
            }
            _clear_claim(updated)
            updated["resume"]["next_action"] = "restart_required"
        else:
            stage_record.update({
                "status": "ready",
                "completed_at": None,
                "error": (notes or "revision requested by human reviewer").strip()[:2000],
            })
            updated["status"] = "revising"
            updated["blocker"] = {
                "code": "human_revision_requested",
                "message": f"stage {normalized_stage!r} requires revision before approval",
                "details": {
                    "approval_id": record["approval_id"],
                    "checkpoint": checkpoint_ref,
                    "invalidated_paths": invalidated_paths,
                },
            }
            updated["resume"]["next_action"] = f"revise_stage:{normalized_stage}"
        updated["current_stage"] = normalized_stage
        updated["next_stage"] = normalized_stage
        updated["resume"]["resume_from_stage"] = normalized_stage
        updated["updated_at"] = current_time.isoformat()
        validate_work_order(updated, manifest=manifest, manifest_path=manifest_path)
        _atomic_write_order(project_path, updated)
        from lib.run_record import sync_run_record

        sync_run_record(project_path, updated, now=current_time)
    return {
        "ok": True,
        "idempotent": False,
        "decision": record,
        "transition": "revising" if normalized_decision == "revise" else "rejected",
        "work_order": updated,
    }


def decide_human_gate(
    project_dir: Path | str,
    stage: str,
    *,
    approver_id: str,
    decision: str = "approve",
    notes: str | None = None,
    agent_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one digest-bound human decision as an atomic transition.

    The implementation is deliberately wrapped in a project-scoped lock. A
    browser double-click, two Backlot tabs, or a process retry must observe the
    same awaiting checkpoint and produce at most one approval record for it.
    """
    project_path = Path(project_dir)
    if not project_path.is_dir():
        raise WorkOrderValidationError(f"project directory does not exist: {project_path}")
    with _approval_transition_lock(project_path):
        return _decide_human_gate_unlocked(
            project_path,
            stage,
            approver_id=approver_id,
            decision=decision,
            notes=notes,
            agent_id=agent_id,
            now=now,
        )


__all__ = [
    "WORK_ORDER_FILENAME",
    "WORK_ORDER_SCHEMA_PATH",
    "WORK_ORDER_VERSION",
    "DEFAULT_LEASE_SECONDS",
    "MAX_LEASE_SECONDS",
    "MIN_LEASE_SECONDS",
    "WorkOrderConflictError",
    "WorkOrderStateError",
    "WorkOrderValidationError",
    "advance_work_order",
    "build_work_order",
    "cancel_work_order",
    "claim_work_order",
    "heartbeat_work_order",
    "decide_human_gate",
    "manifest_digest",
    "next_stage_from_work_order",
    "read_work_order",
    "release_work_order",
    "restart_work_order",
    "resume_work_order",
    "validate_work_order",
    "work_order_path",
    "write_work_order",
]
