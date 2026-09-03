"""Explicit, fail-closed migrations for durable project state.

Only control-plane state is migrated here: the project marker, work order,
checkpoints, and run records. Creative artifacts are validated by their own
schemas and are never rewritten opportunistically. A migration must identify a
known legacy version, preserve a byte-for-byte copy in ``history/migrations/``,
and validate every transformed file before it is promoted.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CURRENT_STATE_VERSION = "1.0"
LEGACY_STATE_VERSIONS = frozenset({"", "legacy", "0.8", "0.9"})
MIGRATION_HISTORY_DIRNAME = "history/migrations"


class StateMigrationError(ValueError):
    """Raised when state cannot be safely migrated or validated."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateMigrationError(f"state file cannot be read: {path}") from exc
    if not isinstance(value, dict):
        raise StateMigrationError(f"state file root must be an object: {path}")
    return value


def _state_files(project_dir: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    marker = project_dir / "project.json"
    work_order = project_dir / "work_order.json"
    if marker.is_file():
        files.append(("project_marker", marker))
    if work_order.is_file():
        files.append(("work_order", work_order))
    files.extend(("checkpoint", path) for path in sorted(project_dir.glob("checkpoint_*.json")))
    runs = project_dir / "runs"
    if runs.is_dir():
        files.extend(
            ("run_record", path)
            for path in sorted(runs.glob("*/run.json"))
            if path.is_file()
        )
    return files


def _source_version(payload: Mapping[str, Any]) -> str:
    raw = payload.get("version")
    return str(raw).strip() if raw not in (None, "") else "legacy"


def _migrate_payload(payload: Mapping[str, Any], kind: str) -> tuple[dict[str, Any], str, bool]:
    source = _source_version(payload)
    if source == CURRENT_STATE_VERSION:
        return dict(payload), source, False
    if source not in LEGACY_STATE_VERSIONS:
        raise StateMigrationError(
            f"unsupported {kind} state version {source!r}; expected {CURRENT_STATE_VERSION!r} or a known legacy version"
        )

    migrated = dict(payload)
    migrated["version"] = CURRENT_STATE_VERSION
    if kind == "project_marker":
        if not str(migrated.get("project_id") or "").strip() or not str(migrated.get("pipeline_type") or "").strip():
            raise StateMigrationError("legacy project marker is missing project_id or pipeline_type")
    elif kind == "work_order":
        required = ("project_id", "pipeline_type", "run_id", "stages", "selections", "claim", "resume", "blocker")
        missing = [field for field in required if field not in migrated]
        if missing:
            raise StateMigrationError(f"legacy work order is missing required fields: {', '.join(missing)}")
        migrated.setdefault("run_record_ref", f"runs/{migrated['run_id']}/run.json")
        metadata = migrated.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.setdefault("production_gate", "PR-11G")
            metadata.setdefault("production_ready", False)
    elif kind == "run_record":
        required = ("project_id", "pipeline_type", "run_id", "attempt", "status", "created_at", "updated_at", "started_at", "finished_at", "work_order_ref", "current_stage", "next_stage", "artifacts")
        missing = [field for field in required if field not in migrated]
        if missing:
            raise StateMigrationError(f"legacy run record is missing required fields: {', '.join(missing)}")
        metadata = migrated.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.setdefault("production_gate", "PR-11G")
            metadata.setdefault("production_ready", False)
    elif kind == "checkpoint":
        required = ("project_id", "pipeline_type", "stage", "status", "timestamp", "artifacts")
        missing = [field for field in required if field not in migrated]
        if missing:
            raise StateMigrationError(f"legacy checkpoint is missing required fields: {', '.join(missing)}")
        migrated.setdefault("producer_stage", migrated.get("stage"))
        migrated.setdefault("producer_tool", "legacy-migration")
        migrated.setdefault("checkpoint_policy", "guided")
        migrated.setdefault("human_approval_required", False)
        migrated.setdefault("human_approved", False)
    else:
        raise StateMigrationError(f"unknown state file kind: {kind!r}")
    return migrated, source, True


def _validate_payload(payload: Mapping[str, Any], kind: str) -> None:
    if kind == "project_marker":
        if str(payload.get("version") or "") != CURRENT_STATE_VERSION:
            raise StateMigrationError("project marker did not reach the current state version")
        return
    if kind == "work_order":
        from lib.work_order import validate_work_order

        validate_work_order(payload)
    elif kind == "run_record":
        from lib.run_record import validate_run_record

        validate_run_record(payload)
    elif kind == "checkpoint":
        from lib.checkpoint import validate_checkpoint

        validate_checkpoint(dict(payload))
    else:
        raise StateMigrationError(f"unknown state file kind: {kind!r}")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_project_state(project_dir: Path | str, *, dry_run: bool = False) -> dict[str, Any]:
    """Migrate known legacy control files and return an auditable report.

    The operation is idempotent. Current-version files are validated but not
    rewritten. When writing is requested, originals are copied to a unique
    history directory before any replacement; a write failure rolls changed
    files back from those copies.
    """

    root = Path(project_dir).expanduser()
    if not root.is_dir() or root.is_symlink():
        raise StateMigrationError("project directory must be an existing non-symlink directory")
    root = root.resolve()
    files = _state_files(root)
    if not files:
        raise StateMigrationError("project has no migratable control-state files")

    transformed: list[tuple[str, Path, dict[str, Any], str, bool]] = []
    source_versions: dict[str, str] = {}
    for kind, path in files:
        payload = _read_object(path)
        migrated, source, changed = _migrate_payload(payload, kind)
        _validate_payload(migrated, kind)
        transformed.append((kind, path, migrated, source, changed))
        source_versions[path.relative_to(root).as_posix()] = source

    migration_id = str(uuid.uuid4())
    changed_files = [item for item in transformed if item[4]]
    report: dict[str, Any] = {
        "version": CURRENT_STATE_VERSION,
        "migration_id": migration_id,
        "project_id": root.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_versions": source_versions,
        "target_version": CURRENT_STATE_VERSION,
        "migrated_files": [path.relative_to(root).as_posix() for _, path, _, _, changed in transformed if changed],
        "validated_files": [path.relative_to(root).as_posix() for _, path, _, _, _ in transformed],
        "dry_run": bool(dry_run),
        "status": "DRY_RUN" if dry_run else ("NOOP" if not changed_files else "MIGRATED"),
    }
    if dry_run or not changed_files:
        return report

    history_root = root / MIGRATION_HISTORY_DIRNAME / migration_id
    written: list[Path] = []
    try:
        for _kind, path, _payload, _source, _changed in changed_files:
            relative = path.relative_to(root)
            archive = history_root / relative
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, archive)
        for _kind, path, payload, _source, _changed in changed_files:
            _atomic_write_json(path, payload)
            written.append(path)
    except Exception as exc:
        for path in written:
            archive = history_root / path.relative_to(root)
            try:
                shutil.copy2(archive, path)
            except OSError:
                pass
        raise StateMigrationError(f"state migration rolled back after write failure: {exc}") from exc

    report["history_ref"] = (history_root.relative_to(root)).as_posix()
    return report


__all__ = [
    "CURRENT_STATE_VERSION",
    "LEGACY_STATE_VERSIONS",
    "MIGRATION_HISTORY_DIRNAME",
    "StateMigrationError",
    "migrate_project_state",
]
