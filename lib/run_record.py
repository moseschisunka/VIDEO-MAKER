"""Durable run records and output provenance.

The work order is the control-plane state machine; this module is the
run-scoped evidence ledger.  It gives every local tool result and canonical
artifact a stable project/pipeline/run/attempt/stage/tool association without
moving creative orchestration into Python.
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

from lib.work_order import read_work_order


REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_RECORD_SCHEMA_PATH = REPO_ROOT / "schemas" / "runs" / "run_record.schema.json"
RUN_RECORD_FILENAME = "run.json"
RUN_RECORD_VERSION = "1.0"

try:
    from filelock import FileLock, Timeout as FileLockTimeout
except ImportError:  # pragma: no cover - bundled environments include filelock
    FileLock = None

    class FileLockTimeout(TimeoutError):
        pass


class RunRecordValidationError(ValueError):
    """Raised when a run record or provenance update is invalid."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _validate_run_id(run_id: Any) -> str:
    try:
        parsed = uuid.UUID(str(run_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RunRecordValidationError("run_id must be a UUIDv4") from exc
    if parsed.version != 4:
        raise RunRecordValidationError("run_id must be a UUIDv4")
    return str(parsed)


def _load_schema() -> dict[str, Any]:
    try:
        return json.loads(RUN_RECORD_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunRecordValidationError(
            f"run record schema cannot be read: {RUN_RECORD_SCHEMA_PATH}"
        ) from exc


def validate_run_record(record: Mapping[str, Any]) -> None:
    if not isinstance(record, Mapping):
        raise RunRecordValidationError("run record must be an object")
    try:
        jsonschema.validate(
            instance=dict(record),
            schema=_load_schema(),
            format_checker=jsonschema.FormatChecker(),
        )
    except jsonschema.ValidationError as exc:
        raise RunRecordValidationError(
            f"run record failed schema validation: {exc.message}"
        ) from exc


def run_record_path(project_dir: Path | str, run_id: str) -> Path:
    """Return the run record path after validating the UUID path component."""
    normalized = _validate_run_id(run_id)
    return Path(project_dir) / "runs" / normalized / RUN_RECORD_FILENAME


def _identity_from_order(order: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "project_id": order.get("project_id"),
        "pipeline_type": order.get("pipeline_type"),
        "run_id": order.get("run_id"),
        "attempt": order.get("attempt"),
    }
    if not all(fields.get(key) not in (None, "") for key in fields):
        raise RunRecordValidationError(
            "work order must provide project_id, pipeline_type, run_id, and attempt"
        )
    fields["run_id"] = _validate_run_id(fields["run_id"])
    if isinstance(fields["attempt"], bool) or not isinstance(fields["attempt"], int):
        raise RunRecordValidationError("attempt must be an integer")
    if fields["attempt"] < 1:
        raise RunRecordValidationError("attempt must be at least 1")
    return fields


def build_run_record(
    order: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Build a new empty run record from an already validated work order."""
    identity = _identity_from_order(order)
    timestamp = _timestamp(now)
    status = str(order.get("status") or "queued")
    started_at = timestamp if status in {"preflighting", "running"} else None
    finished_at = timestamp if status in {"completed", "cancelled"} else None
    record: dict[str, Any] = {
        "version": RUN_RECORD_VERSION,
        **identity,
        "status": status,
        "created_at": str(order.get("created_at") or timestamp),
        "updated_at": str(order.get("updated_at") or timestamp),
        "started_at": started_at,
        "finished_at": finished_at,
        "work_order_ref": "work_order.json",
        "current_stage": order.get("current_stage"),
        "next_stage": order.get("next_stage"),
        "last_successful_stage": (order.get("resume") or {}).get("last_successful_stage"),
        "last_successful_checkpoint": (order.get("resume") or {}).get("last_successful_checkpoint"),
        "artifacts": {},
        "outputs": [],
        "last_error": (order.get("blocker") or {}).get("message"),
        "metadata": {
            "production_gate": "PR-11G",
            "production_ready": False,
        },
    }
    validate_run_record(record)
    return record


def _record_lock(path: Path):
    @contextmanager
    def manager():
        if FileLock is None:
            yield
            return
        try:
            with FileLock(str(path) + ".lock", timeout=10):
                yield
        except FileLockTimeout as exc:
            raise RunRecordValidationError("timed out waiting for the run-record lock") from exc

    return manager()


def _atomic_write(path: Path, record: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(raw_path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(record), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return path


def write_run_record(project_dir: Path | str, record: Mapping[str, Any]) -> Path:
    root = Path(project_dir)
    if not root.is_dir():
        raise RunRecordValidationError(f"project directory does not exist: {root}")
    validate_run_record(record)
    path = run_record_path(root, str(record["run_id"]))
    with _record_lock(path):
        return _atomic_write(path, record)


def read_run_record(project_dir: Path | str, run_id: str) -> dict[str, Any]:
    path = run_record_path(project_dir, run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunRecordValidationError(f"run record cannot be read: {path}") from exc
    if not isinstance(payload, dict):
        raise RunRecordValidationError("run record root must be an object")
    validate_run_record(payload)
    return payload


def _assert_identity(record: Mapping[str, Any], order: Mapping[str, Any]) -> None:
    identity = _identity_from_order(order)
    for field in ("project_id", "pipeline_type", "run_id", "attempt"):
        if record.get(field) != identity[field]:
            raise RunRecordValidationError(
                f"run record {field} {record.get(field)!r} does not match work order {identity[field]!r}"
            )


def sync_run_record(
    project_dir: Path | str,
    order: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create or synchronize the control fields of a run record.

    Artifact/output entries are preserved.  This function is intentionally
    deterministic and does not infer a new run or attempt.
    """
    root = Path(project_dir)
    if not root.is_dir():
        raise RunRecordValidationError(f"project directory does not exist: {root}")
    identity = _identity_from_order(order)
    path = run_record_path(root, identity["run_id"])
    current_time = _timestamp(now)
    with _record_lock(path):
        if path.is_file():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RunRecordValidationError(f"run record cannot be read: {path}") from exc
            if not isinstance(record, dict):
                raise RunRecordValidationError("run record root must be an object")
            validate_run_record(record)
            _assert_identity(record, order)
        else:
            record = build_run_record(order, now=now)

        # Work-order lifecycle metadata (attempt/recovery/restart/cancel
        # timestamps) is part of the run evidence.  Merge it without
        # discarding the run-record's own production-gate metadata.
        order_metadata = order.get("metadata")
        if isinstance(order_metadata, Mapping):
            record_metadata = record.setdefault("metadata", {})
            if isinstance(record_metadata, dict):
                record_metadata.update(dict(order_metadata))

        record.update({
            **identity,
            "status": str(order.get("status") or record.get("status") or "queued"),
            "updated_at": current_time,
            "current_stage": order.get("current_stage"),
            "next_stage": order.get("next_stage"),
            "last_successful_stage": (order.get("resume") or {}).get("last_successful_stage"),
            "last_successful_checkpoint": (order.get("resume") or {}).get("last_successful_checkpoint"),
            "last_error": (order.get("blocker") or {}).get("message"),
        })
        if record.get("status") in {"preflighting", "running"} and not record.get("started_at"):
            record["started_at"] = current_time
        if record.get("status") in {"completed", "cancelled"} and not record.get("finished_at"):
            record["finished_at"] = current_time
        if record.get("status") not in {"completed", "cancelled"}:
            # An explicit restart reopens a previously cancelled attempt.  A
            # stale finished_at would make the run look terminal to readers
            # even though the work order is queued/resumable again.
            record["finished_at"] = None
        validate_run_record(record)
        _atomic_write(path, record)
        return record


def build_result_provenance(
    project_dir: Path | str,
    *,
    tool: str,
    stage: str | None = None,
    agent_id: str | None = None,
    order: Mapping[str, Any] | None = None,
    recorded_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Build the stable provenance attached to one tool result.

    If no durable work order exists, returning ``None`` is safer than inventing
    an attempt or run identity.  Tool telemetry remains non-fatal in that case.
    """
    root = Path(project_dir)
    if order is None:
        try:
            order = read_work_order(root)
        except Exception:
            return None
    identity = _identity_from_order(order)
    resolved_stage = str(stage or order.get("current_stage") or "").strip()
    if not resolved_stage:
        return None
    normalized_tool = str(tool or "").strip()
    if not normalized_tool:
        return None
    run_ref = str(order.get("run_record_ref") or run_record_path(root, identity["run_id"]).relative_to(root).as_posix())
    return {
        "project_id": identity["project_id"],
        "pipeline_type": identity["pipeline_type"],
        "run_id": identity["run_id"],
        "attempt": identity["attempt"],
        "stage": resolved_stage,
        "tool": normalized_tool,
        "agent_id": str(agent_id).strip() if agent_id else None,
        "recorded_at": _timestamp(recorded_at),
        "run_record_ref": run_ref,
    }


def _local_artifact_path(root: Path, raw_path: str | Path) -> tuple[str, Path] | None:
    if not raw_path:
        return None
    value = str(raw_path)
    if value.startswith(("http://", "https://", "s3://", "gs://", "file://")):
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return relative, resolved


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def record_artifacts(
    project_dir: Path | str,
    order: Mapping[str, Any],
    *,
    stage: str,
    tool: str,
    artifacts: Mapping[str, str | Path],
    agent_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Add current-run provenance entries for canonical local artifacts."""
    root = Path(project_dir)
    identity = _identity_from_order(order)
    path = run_record_path(root, identity["run_id"])
    current_time = _timestamp(now)
    with _record_lock(path):
        if path.is_file():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RunRecordValidationError(f"run record cannot be read: {path}") from exc
            if not isinstance(record, dict):
                raise RunRecordValidationError("run record root must be an object")
            validate_run_record(record)
            _assert_identity(record, order)
        else:
            record = build_run_record(order, now=now)

        for artifact_name, raw_path in artifacts.items():
            local = _local_artifact_path(root, raw_path)
            if local is None:
                raise RunRecordValidationError(
                    f"artifact {artifact_name!r} must reference a local path inside the project"
                )
            relative, resolved = local
            try:
                size = resolved.stat().st_size if resolved.is_file() else None
                created_at = datetime.fromtimestamp(
                    resolved.stat().st_mtime, tz=timezone.utc
                ).isoformat() if resolved.is_file() else None
            except OSError:
                size = None
                created_at = None
            entry = {
                "path": relative,
                "artifact_name": str(artifact_name),
                "project_id": identity["project_id"],
                "pipeline_type": identity["pipeline_type"],
                "run_id": identity["run_id"],
                "attempt": identity["attempt"],
                "stage": str(stage),
                "tool": str(tool),
                "agent_id": str(agent_id).strip() if agent_id else None,
                "recorded_at": current_time,
                "sha256": _sha256(resolved),
                "size_bytes": size,
                "created_at": created_at,
            }
            record["artifacts"][str(artifact_name)] = entry
            if str(artifact_name).startswith("output:"):
                record["outputs"] = [
                    item for item in record.get("outputs", [])
                    if item.get("path") != relative
                ] + [entry]

        record.update({
            "status": str(order.get("status") or record.get("status") or "queued"),
            "updated_at": current_time,
            "current_stage": order.get("current_stage"),
            "next_stage": order.get("next_stage"),
            "last_successful_stage": (order.get("resume") or {}).get("last_successful_stage"),
            "last_successful_checkpoint": (order.get("resume") or {}).get("last_successful_checkpoint"),
            "last_error": (order.get("blocker") or {}).get("message"),
        })
        if record.get("status") in {"preflighting", "running"} and not record.get("started_at"):
            record["started_at"] = current_time
        if record.get("status") in {"completed", "cancelled"} and not record.get("finished_at"):
            record["finished_at"] = current_time
        validate_run_record(record)
        _atomic_write(path, record)
        return record


def record_tool_result(
    project_dir: Path | str,
    result: Any,
    *,
    tool: str,
    inputs: Mapping[str, Any] | None = None,
    stage: str | None = None,
) -> dict[str, Any] | None:
    """Record a BaseTool result without allowing telemetry to break execution."""
    try:
        root = Path(project_dir)
        order = read_work_order(root)
        result_paths: dict[str, str | Path] = {}
        for index, artifact in enumerate(getattr(result, "artifacts", []) or []):
            result_paths[f"result:{index}"] = artifact
        output_path = (inputs or {}).get("output_path")
        if output_path:
            result_paths.setdefault("output:primary", output_path)
        if not result_paths:
            return sync_run_record(root, order)
        return record_artifacts(
            root,
            order,
            stage=stage or (inputs or {}).get("stage") or order.get("current_stage") or "tool",
            tool=tool,
            artifacts=result_paths,
            agent_id=(inputs or {}).get("agent_id"),
        )
    except Exception:
        # Observability is explicitly best-effort; the underlying tool result
        # must remain available for the caller and for a later retry.
        return None


__all__ = [
    "RUN_RECORD_FILENAME",
    "RUN_RECORD_SCHEMA_PATH",
    "RUN_RECORD_VERSION",
    "RunRecordValidationError",
    "build_result_provenance",
    "build_run_record",
    "read_run_record",
    "record_artifacts",
    "record_tool_result",
    "run_record_path",
    "sync_run_record",
    "validate_run_record",
    "write_run_record",
]
