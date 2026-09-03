"""Append-only human approval records bound to immutable artifact versions.

The old ``human_approved`` checkpoint bit is deliberately not an authority.
This module gives the control plane one small, deterministic contract for
recording and validating a human decision.  A record contains the project/run
identity, the exact artifact digest and version being reviewed, the actor,
decision, timestamp, notes, and a self-checking record digest.  Records are
appended to ``approval_records.json``; they are never edited in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import jsonschema


REPO_ROOT = Path(__file__).resolve().parent.parent
APPROVAL_SCHEMA_PATH = REPO_ROOT / "schemas" / "approvals" / "approval_record.schema.json"
APPROVAL_LOG_SCHEMA_PATH = REPO_ROOT / "schemas" / "approvals" / "approval_log.schema.json"
APPROVAL_LOG_FILENAME = "approval_records.json"
APPROVAL_VERSION = "1.0"

try:
    from filelock import FileLock, Timeout as FileLockTimeout
except ImportError:  # pragma: no cover - normal installs include filelock
    FileLock = None

    class FileLockTimeout(TimeoutError):
        pass


class ApprovalValidationError(ValueError):
    """Raised when an approval record or log violates the contract."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ApprovalValidationError(f"approval payload is not canonical JSON: {exc}") from exc


def canonical_digest(value: Any) -> str:
    """Return the SHA-256 digest of a canonical JSON value."""
    return hashlib.sha256(_canonical(value)).hexdigest()


def artifact_digest(artifacts: Mapping[str, Any]) -> str:
    """Digest the exact artifact map embedded in a checkpoint."""
    if not isinstance(artifacts, Mapping):
        raise ApprovalValidationError("checkpoint artifacts must be an object")
    return canonical_digest(dict(artifacts))


def _uuid4(value: Any, field: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ApprovalValidationError(f"{field} must be a UUIDv4") from exc
    if parsed.version != 4:
        raise ApprovalValidationError(f"{field} must be a UUIDv4")
    return str(parsed)


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ApprovalValidationError("recorded_at must be an ISO-8601 timestamp") from exc
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
    raise ApprovalValidationError("recorded_at must be a datetime or ISO-8601 string")


def _record_body(record: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in record.items() if key != "record_digest"}


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApprovalValidationError(f"approval schema cannot be read: {path}") from exc
    if not isinstance(value, dict):
        raise ApprovalValidationError(f"approval schema root must be an object: {path}")
    return value


def build_approval_record(
    *,
    project_id: str,
    pipeline_type: str,
    run_id: str,
    attempt: int,
    stage: str,
    artifact_ref: str,
    artifact_digest_value: str,
    artifact_version: str,
    approver_id: str,
    decision: str,
    notes: str | None = None,
    supersedes: str | None = None,
    approval_id: str | None = None,
    recorded_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Build a self-digesting approval record.

    There is intentionally no ``approved``/``human_approved`` argument.  The
    only authority is the explicit decision plus its bound artifact digest.
    """
    normalized_project = str(project_id or "").strip()
    normalized_pipeline = str(pipeline_type or "").strip()
    normalized_stage = str(stage or "").strip()
    normalized_ref = str(artifact_ref or "").strip()
    normalized_actor = str(approver_id or "").strip()
    normalized_decision = str(decision or "").strip().lower()
    if not normalized_project or not normalized_pipeline or not normalized_stage:
        raise ApprovalValidationError("project_id, pipeline_type, and stage are required")
    if not normalized_ref:
        raise ApprovalValidationError("artifact_ref is required")
    if not normalized_actor:
        raise ApprovalValidationError("approver_id is required")
    if normalized_decision not in {"approve", "revise", "reject"}:
        raise ApprovalValidationError("decision must be approve, revise, or reject")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ApprovalValidationError("attempt must be an integer >= 1")
    digest = str(artifact_digest_value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ApprovalValidationError("artifact_digest must be a lowercase SHA-256 digest")

    record: dict[str, Any] = {
        "version": APPROVAL_VERSION,
        "approval_id": _uuid4(approval_id or uuid.uuid4(), "approval_id"),
        "project_id": normalized_project,
        "pipeline_type": normalized_pipeline,
        "run_id": _uuid4(run_id, "run_id"),
        "attempt": attempt,
        "stage": normalized_stage,
        "artifact_ref": normalized_ref,
        "artifact_digest": digest,
        "artifact_version": str(artifact_version or "").strip(),
        "approver_id": normalized_actor,
        "decision": normalized_decision,
        "recorded_at": _timestamp(recorded_at),
        "notes": str(notes) if notes is not None else None,
        "supersedes": _uuid4(supersedes, "supersedes") if supersedes else None,
    }
    if not record["artifact_version"]:
        raise ApprovalValidationError("artifact_version is required")
    record["record_digest"] = canonical_digest(_record_body(record))
    validate_approval_record(record)
    return record


def validate_approval_record(record: Mapping[str, Any]) -> None:
    """Validate schema, UUID identity, and the self-digest of one record."""
    if not isinstance(record, Mapping):
        raise ApprovalValidationError("approval record must be an object")
    candidate = dict(record)
    try:
        jsonschema.validate(
            instance=candidate,
            schema=_load_schema(APPROVAL_SCHEMA_PATH),
            format_checker=jsonschema.FormatChecker(),
        )
    except jsonschema.ValidationError as exc:
        raise ApprovalValidationError(f"approval record failed schema validation: {exc.message}") from exc
    expected = canonical_digest(_record_body(candidate))
    if candidate.get("record_digest") != expected:
        raise ApprovalValidationError("approval record digest does not match its immutable fields")
    _uuid4(candidate.get("run_id"), "run_id")
    if candidate.get("supersedes"):
        _uuid4(candidate.get("supersedes"), "supersedes")


def approval_log_path(project_dir: Path | str) -> Path:
    return Path(project_dir) / APPROVAL_LOG_FILENAME


def _validate_log(log: Mapping[str, Any], *, project_id: str | None = None) -> None:
    schema = _load_schema(APPROVAL_LOG_SCHEMA_PATH)
    # Embed the local item schema so validation is fully offline and does not
    # interpret the log schema's logical $id as a filesystem base URI.
    if isinstance(schema.get("properties"), dict):
        records_schema = schema["properties"].get("records")
        if isinstance(records_schema, dict):
            records_schema["items"] = _load_schema(APPROVAL_SCHEMA_PATH)
    try:
        jsonschema.validate(
            instance=dict(log),
            schema=schema,
            format_checker=jsonschema.FormatChecker(),
        )
    except jsonschema.ValidationError as exc:
        raise ApprovalValidationError(f"approval log failed schema validation: {exc.message}") from exc
    if project_id is not None and log.get("project_id") != str(project_id):
        raise ApprovalValidationError("approval log project_id does not match project")
    seen: set[str] = set()
    for item in log.get("records", []) or []:
        validate_approval_record(item)
        approval_id = str(item["approval_id"])
        if approval_id in seen:
            raise ApprovalValidationError(f"duplicate approval_id in log: {approval_id}")
        seen.add(approval_id)


def read_approval_log(project_dir: Path | str) -> dict[str, Any]:
    root = Path(project_dir)
    path = approval_log_path(root)
    if not path.exists():
        return {"version": APPROVAL_VERSION, "project_id": root.name, "records": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApprovalValidationError(f"approval log cannot be read: {path}") from exc
    if not isinstance(payload, dict):
        raise ApprovalValidationError("approval log root must be an object")
    _validate_log(payload, project_id=root.name)
    return payload


@contextmanager
def _log_lock(path: Path):
    if FileLock is None:
        yield
        return
    try:
        with FileLock(str(path) + ".lock", timeout=10):
            yield
    except FileLockTimeout as exc:
        raise ApprovalValidationError("timed out waiting for approval log lock") from exc


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass


def append_approval_record(project_dir: Path | str, record: Mapping[str, Any]) -> dict[str, Any]:
    """Append a record exactly once; an identical ID is idempotent."""
    root = Path(project_dir)
    if not root.is_dir():
        raise ApprovalValidationError(f"project directory does not exist: {root}")
    validate_approval_record(record)
    if record.get("project_id") != root.name:
        raise ApprovalValidationError("approval record project_id does not match project directory")
    path = approval_log_path(root)
    with _log_lock(path):
        log = read_approval_log(root)
        for existing in log["records"]:
            if existing["approval_id"] == record["approval_id"]:
                if existing != dict(record):
                    raise ApprovalValidationError("approval_id already exists with different immutable content")
                return dict(existing)
        if log["records"]:
            previous = log["records"][-1]
            if str(record.get("recorded_at")) < str(previous.get("recorded_at")):
                raise ApprovalValidationError("approval records must be appended in timestamp order")
        log["records"].append(dict(record))
        _validate_log(log, project_id=root.name)
        _atomic_write(path, log)
        return dict(record)


def artifact_approval_digest(artifacts: Mapping[str, Any]) -> str:
    """Named alias used by checkpoint/work-order code."""
    return artifact_digest(artifacts)


def build_checkpoint_approval(
    checkpoint: Mapping[str, Any],
    *,
    approver_id: str,
    decision: str,
    artifact_ref: str | None = None,
    notes: str | None = None,
    supersedes: str | None = None,
    recorded_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Create a decision bound to the exact artifact map in a checkpoint."""
    if not isinstance(checkpoint, Mapping):
        raise ApprovalValidationError("checkpoint must be an object")
    return build_approval_record(
        project_id=str(checkpoint.get("project_id") or ""),
        pipeline_type=str(checkpoint.get("pipeline_type") or ""),
        run_id=str(checkpoint.get("run_id") or ""),
        attempt=int(checkpoint.get("attempt") or 1),
        stage=str(checkpoint.get("stage") or ""),
        artifact_ref=artifact_ref or f"checkpoint_{checkpoint.get('stage')}.json",
        artifact_digest_value=artifact_digest(checkpoint.get("artifacts") or {}),
        artifact_version=str(checkpoint.get("timestamp") or ""),
        approver_id=approver_id,
        decision=decision,
        notes=notes,
        supersedes=supersedes,
        recorded_at=recorded_at,
    )


def validate_checkpoint_approval(
    checkpoint: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    expected_decision: str = "approve",
) -> None:
    """Ensure a decision is for this exact checkpoint artifact version."""
    validate_approval_record(record)
    if str(record.get("project_id")) != str(checkpoint.get("project_id")):
        raise ApprovalValidationError("approval project_id does not match checkpoint")
    for field in ("pipeline_type", "run_id", "stage"):
        if str(record.get(field)) != str(checkpoint.get(field)):
            raise ApprovalValidationError(f"approval {field} does not match checkpoint")
    if str(record.get("artifact_version")) != str(checkpoint.get("timestamp")):
        raise ApprovalValidationError("approval artifact_version does not match checkpoint timestamp")
    expected_digest = artifact_digest(checkpoint.get("artifacts") or {})
    if record.get("artifact_digest") != expected_digest:
        raise ApprovalValidationError("approval artifact_digest does not match checkpoint artifacts")
    expected_ref = f"checkpoint_{checkpoint.get('stage')}.json"
    if str(record.get("artifact_ref")) != expected_ref:
        raise ApprovalValidationError("approval artifact_ref does not match checkpoint")
    if expected_decision and record.get("decision") != expected_decision:
        raise ApprovalValidationError(
            f"approval decision {record.get('decision')!r} is not {expected_decision!r}"
        )


def latest_approval(
    project_dir: Path | str,
    *,
    stage: str,
    artifact_digest_value: str | None = None,
    artifact_version: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the newest matching record without mutating state."""
    records = read_approval_log(project_dir).get("records", [])
    for record in reversed(records):
        if record.get("stage") != stage:
            continue
        if run_id is not None and record.get("run_id") != str(run_id):
            continue
        if artifact_digest_value is not None and record.get("artifact_digest") != artifact_digest_value:
            continue
        if artifact_version is not None and record.get("artifact_version") != str(artifact_version):
            continue
        return dict(record)
    return None


__all__ = [
    "APPROVAL_LOG_FILENAME",
    "APPROVAL_SCHEMA_PATH",
    "APPROVAL_VERSION",
    "ApprovalValidationError",
    "append_approval_record",
    "approval_log_path",
    "artifact_approval_digest",
    "artifact_digest",
    "build_approval_record",
    "build_checkpoint_approval",
    "canonical_digest",
    "latest_approval",
    "read_approval_log",
    "validate_approval_record",
    "validate_checkpoint_approval",
]
