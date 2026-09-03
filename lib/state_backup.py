"""Integrity-checked, local backup and restore for project state.

Backups are ordinary ZIP archives with a signed-by-hash manifest, not an
encryption boundary. They may contain private user media and must be stored in
an approved encrypted location by the operator. Restore never extracts an
untrusted member directly into the destination: every path, size, digest, and
symlink bit is checked in a disposable staging directory first.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

import jsonschema

BACKUP_SCHEMA_VERSION = "1.0"
BACKUP_MANIFEST_FILENAME = "backup_manifest.json"
BACKUP_MANIFEST_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "backups"
    / "backup_manifest.schema.json"
)
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_FILENAMES = {".env", ".env.local", ".env.production", "credentials.json", "service-account.json"}
_EXCLUDED_SUFFIXES = {".lock", ".tmp", ".part"}
_EXCLUDED_DIRS = {"__pycache__", ".git"}


class StateBackupError(ValueError):
    """Raised when a backup cannot be created, inspected, or restored safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise StateBackupError(f"cannot hash backup file: {path}") from exc
    return digest.hexdigest()


def _safe_relative(path: str) -> str:
    raw = str(path).replace("\\", "/")
    parsed = PurePosixPath(raw)
    if not raw or parsed.is_absolute() or ".." in parsed.parts or ":" in raw:
        raise StateBackupError(f"unsafe archive path: {path!r}")
    normalized = parsed.as_posix()
    if normalized in {"", "."} or normalized.startswith("../"):
        raise StateBackupError(f"unsafe archive path: {path!r}")
    return normalized


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise StateBackupError("backup manifest must be an object")
    try:
        schema = json.loads(BACKUP_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=dict(manifest), schema=schema)
    except (OSError, UnicodeError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
        raise StateBackupError(f"backup manifest failed validation: {exc}") from exc
    if str(manifest.get("version") or "") != BACKUP_SCHEMA_VERSION:
        raise StateBackupError("unsupported backup manifest version")
    project_id = str(manifest.get("project_id") or "")
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise StateBackupError("backup manifest contains an invalid project_id")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise StateBackupError("backup manifest must list at least one file")
    seen: set[str] = set()
    for record in files:
        if not isinstance(record, Mapping):
            raise StateBackupError("backup file record must be an object")
        relative = _safe_relative(str(record.get("path") or ""))
        if relative in seen or relative == BACKUP_MANIFEST_FILENAME:
            raise StateBackupError(f"duplicate or reserved backup member: {relative}")
        seen.add(relative)
        size = record.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise StateBackupError(f"invalid size for backup member: {relative}")
        digest = str(record.get("sha256") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise StateBackupError(f"invalid sha256 for backup member: {relative}")


def _eligible_files(root: Path, *, include_media: bool, archive_path: Path | None) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    excluded: list[str] = []
    archive_resolved = archive_path.resolve() if archive_path is not None else None
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise StateBackupError(f"symlinks are not allowed in a project backup: {relative}")
        if not path.is_file():
            continue
        if archive_resolved is not None and path.resolve() == archive_resolved:
            continue
        if path.name in _SECRET_FILENAMES or path.name.startswith(".env."):
            excluded.append(f"{relative}:secret_or_environment_file")
            continue
        if path.suffix.lower() in _EXCLUDED_SUFFIXES:
            excluded.append(f"{relative}:temporary_or_lock_file")
            continue
        if not include_media and (relative.startswith("assets/") or relative.startswith("renders/")):
            excluded.append(f"{relative}:media_excluded_by_request")
            continue
        _safe_relative(relative)
        files.append(path)
    return files, excluded


def create_backup(
    project_dir: Path | str,
    archive_path: Path | str,
    *,
    include_media: bool = True,
) -> dict[str, Any]:
    """Create an atomic ZIP backup and return its validated manifest."""

    root = Path(project_dir).expanduser()
    if not root.is_dir() or root.is_symlink():
        raise StateBackupError("project directory must be an existing non-symlink directory")
    root = root.resolve()
    archive = Path(archive_path).expanduser()
    if archive.exists() and archive.is_symlink():
        raise StateBackupError("backup destination must not be a symlink")
    archive = archive.resolve()
    try:
        archive.relative_to(root)
    except ValueError:
        pass
    else:
        raise StateBackupError("backup archive must be outside the project directory")
    archive.parent.mkdir(parents=True, exist_ok=True)

    files, excluded = _eligible_files(root, include_media=include_media, archive_path=archive)
    if not files:
        raise StateBackupError("project contains no backup-eligible files")
    records: list[dict[str, Any]] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise StateBackupError(f"cannot stat backup file: {path}") from exc
        records.append({"path": relative, "size_bytes": int(size), "sha256": _sha256(path)})
    manifest: dict[str, Any] = {
        "version": BACKUP_SCHEMA_VERSION,
        "backup_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": root.name,
        "include_media": bool(include_media),
        "source_layout": "openmontage-project-root",
        "files": records,
        "excluded": excluded,
    }
    _validate_manifest(manifest)

    temporary = archive.with_name(f".{archive.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path, record in zip(files, records):
                bundle.write(path, arcname=record["path"])
            bundle.writestr(
                BACKUP_MANIFEST_FILENAME,
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            )
        os.replace(temporary, archive)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, StateBackupError):
            raise
        raise StateBackupError(f"backup archive could not be written: {archive}") from exc
    return {**manifest, "archive_path": str(archive)}


def read_backup_manifest(archive_path: Path | str) -> dict[str, Any]:
    """Read and validate a backup manifest without extracting its contents."""

    archive = Path(archive_path).expanduser()
    if not archive.is_file() or archive.is_symlink():
        raise StateBackupError("backup archive must be an existing non-symlink file")
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            if bundle.namelist().count(BACKUP_MANIFEST_FILENAME) != 1:
                raise StateBackupError("backup archive must contain exactly one backup_manifest.json")
            raw = json.loads(bundle.read(BACKUP_MANIFEST_FILENAME).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as exc:
        raise StateBackupError(f"backup archive cannot be inspected: {archive}") from exc
    _validate_manifest(raw)
    return dict(raw)


def _validate_zip_members(bundle: zipfile.ZipFile, manifest: Mapping[str, Any]) -> dict[str, zipfile.ZipInfo]:
    expected = {_safe_relative(str(item["path"])) for item in manifest["files"]}
    members: dict[str, zipfile.ZipInfo] = {}
    for info in bundle.infolist():
        name = info.filename
        if name == BACKUP_MANIFEST_FILENAME:
            continue
        relative = _safe_relative(name)
        if relative in members:
            raise StateBackupError(f"duplicate archive member: {relative}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise StateBackupError(f"symlink archive member is not allowed: {relative}")
        if info.is_dir() or relative not in expected:
            raise StateBackupError(f"unexpected archive member: {relative}")
        members[relative] = info
    if set(members) != expected:
        missing = sorted(expected - set(members))
        raise StateBackupError(f"backup archive is missing members: {missing}")
    return members


def _validate_restored_state(project_dir: Path, *, expected_project_id: str | None = None) -> None:
    """Validate control state after extraction and before promotion."""

    from lib.checkpoint import validate_checkpoint
    from lib.project_identity import validate_project_identity
    from lib.run_record import validate_run_record
    from lib.work_order import validate_work_order

    marker = project_dir / "project.json"
    if not marker.is_file():
        raise StateBackupError("restored project is missing project.json")
    work_order = project_dir / "work_order.json"
    if work_order.is_file():
        try:
            validate_work_order(json.loads(work_order.read_text(encoding="utf-8")))
        except Exception as exc:
            raise StateBackupError("restored work_order.json failed validation") from exc
    for path in sorted(project_dir.glob("checkpoint_*.json")):
        try:
            validate_checkpoint(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            raise StateBackupError(f"restored checkpoint failed validation: {path.name}") from exc
    runs = project_dir / "runs"
    for path in sorted(runs.glob("*/run.json")) if runs.is_dir() else []:
        try:
            validate_run_record(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            raise StateBackupError(f"restored run record failed validation: {path}") from exc
    if work_order.is_file():
        if expected_project_id is None or project_dir.name == expected_project_id:
            identity = validate_project_identity(project_dir, strict=True)
            if not identity["valid"]:
                raise StateBackupError("restored project identity failed validation")
        else:
            # The pre-promotion staging directory intentionally has a nonce in
            # its name. Validate the identity fields directly and defer the
            # directory-name check to the final target path.
            try:
                marker_payload = json.loads(marker.read_text(encoding="utf-8"))
                order_payload = json.loads(work_order.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise StateBackupError("restored project identity could not be read") from exc
            for field in ("project_id", "pipeline_type", "run_id"):
                marker_value = marker_payload.get(field)
                order_value = order_payload.get(field)
                if field == "project_id":
                    mismatch = marker_value != expected_project_id or order_value != expected_project_id
                else:
                    mismatch = marker_value != order_value
                if mismatch:
                    raise StateBackupError(f"restored project identity mismatch for {field}")


def restore_backup(
    archive_path: Path | str,
    target_root: Path | str,
    *,
    project_id: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Verify and restore a backup into an atomically promoted project root."""

    archive = Path(archive_path).expanduser()
    manifest = read_backup_manifest(archive)
    requested_id = str(project_id or manifest["project_id"])
    if not PROJECT_ID_PATTERN.fullmatch(requested_id):
        raise StateBackupError("project_id must be a safe single path component")
    if requested_id != manifest["project_id"]:
        raise StateBackupError("requested project_id does not match the backup manifest")

    destination_root = Path(target_root).expanduser()
    if destination_root.exists() and destination_root.is_symlink():
        raise StateBackupError("restore target root must not be a symlink")
    destination_root.mkdir(parents=True, exist_ok=True)
    destination_root = destination_root.resolve()
    target = destination_root / requested_id
    if target.exists() and not overwrite:
        raise StateBackupError(f"restore target already exists: {target}")
    if target.is_symlink():
        raise StateBackupError("restore target project must not be a symlink")

    staging: Path | None = Path(
        tempfile.mkdtemp(prefix=f".restore-{requested_id}-", dir=str(destination_root))
    )
    previous: Path | None = None
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            members = _validate_zip_members(bundle, manifest)
            expected = {str(item["path"]): item for item in manifest["files"]}
            for relative, info in members.items():
                output = staging / relative
                try:
                    output.resolve().relative_to(staging.resolve())
                except (OSError, ValueError) as exc:
                    raise StateBackupError(f"archive member escapes restore staging: {relative}") from exc
                output.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with bundle.open(info, "r") as source, output.open("wb") as sink:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        sink.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                record = expected[relative]
                if size != int(record["size_bytes"]) or digest.hexdigest() != str(record["sha256"]):
                    raise StateBackupError(f"integrity check failed for restored member: {relative}")
        _validate_restored_state(staging, expected_project_id=requested_id)
        if target.exists():
            previous = destination_root / f".pre-restore-{requested_id}-{uuid.uuid4().hex}"
            target.rename(previous)
        try:
            staging.rename(target)
        except Exception:
            if previous is not None and not target.exists() and previous.exists():
                previous.rename(target)
            raise
        staging = None  # ownership transferred to target
    except Exception as exc:
        if isinstance(exc, StateBackupError):
            raise
        raise StateBackupError(f"backup restore failed: {archive}") from exc
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return {
        "version": BACKUP_SCHEMA_VERSION,
        "backup_id": manifest["backup_id"],
        "project_id": requested_id,
        "restored_path": str(target),
        "files_restored": len(manifest["files"]),
        "previous_path": str(previous) if previous is not None else None,
        "status": "RESTORED",
    }


__all__ = [
    "BACKUP_MANIFEST_FILENAME",
    "BACKUP_SCHEMA_VERSION",
    "StateBackupError",
    "create_backup",
    "read_backup_manifest",
    "restore_backup",
]
