"""Short-lived, request-bound approvals for production provider calls."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from filelock import FileLock, Timeout as FileLockTimeout

from lib.secrets import redact_text
from lib.providers.contracts import FallbackClass


DEFAULT_TTL_SECONDS = 5 * 60
POLL_INTERVAL_SECONDS = 1.0
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_STATUSES = {"pending", "approved", "rejected", "consumed", "expired"}
_MEDIA_PATH_FIELDS = {
    "audio_path", "audio_paths", "end_image_path", "face_image_path",
    "image_path", "image_paths", "input_audio_path", "input_image_path",
    "input_reference_path", "input_video_path", "reference_audio_path",
    "reference_audio_paths", "reference_image_path", "reference_image_paths",
    "reference_tail_image_path", "reference_video_path", "reference_video_paths",
    "video_path", "video_paths",
}


class ProviderApprovalError(ValueError):
    """Raised when provider approval state is invalid or unavailable."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ProviderApprovalError("provider approval expiry is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderApprovalError("provider approval expiry is invalid") from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def provider_approval_store_dir(
    project_dir: Path | str,
    *,
    store_dir: Path | str | None = None,
) -> Path:
    """Return the control-plane directory shared by Backlot and local directors."""
    if store_dir is not None:
        return Path(store_dir).expanduser().resolve()
    project = Path(project_dir).expanduser().resolve()
    configured_projects = os.environ.get("OPENMONTAGE_PROJECTS_DIR")
    if configured_projects:
        projects_root = Path(configured_projects).expanduser().resolve()
        if project.parent == projects_root:
            return projects_root.parent / ".backlot" / "provider-approvals"
    try:
        from lib.paths import PROJECTS_DIR

        projects_root = Path(PROJECTS_DIR).expanduser().resolve()
        if project.parent == projects_root:
            return projects_root.parent / ".backlot" / "provider-approvals"
    except Exception:
        pass
    # Tests and custom integrations can pass a single project directory.
    return project.parent / ".backlot" / "provider-approvals"


def _log_path(project_dir: Path | str, *, store_dir: Path | str | None = None) -> Path:
    project = Path(project_dir).expanduser().resolve()
    if not _PROJECT_ID_RE.fullmatch(project.name):
        raise ProviderApprovalError("project id is not safe for provider approval storage")
    return provider_approval_store_dir(project, store_dir=store_dir) / f"{project.name}.json"


def _request_digest(request: Any, *, agent_id: str) -> str:
    """Bind approval to the full provider operation and current run identity."""
    value = {
        "capability": request.capability,
        "operation": request.operation,
        "provider": request.provider,
        "model": request.model,
        "payload": dict(request.payload),
        "idempotency_key": request.idempotency_key,
        "project_id": request.project_id,
        "pipeline_type": request.pipeline_type,
        "run_id": request.run_id,
        "attempt": request.attempt,
        "stage": request.stage,
        "agent_id": str(agent_id),
        "timeout_seconds": float(request.timeout_seconds),
        "max_retries": int(request.max_retries),
        "estimated_cost_usd": float(request.estimated_cost_usd),
        "fallback_class": request.fallback_class,
        "runtime": str(request.metadata.get("runtime") or ""),
        "tool": str(request.metadata.get("tool") or ""),
        "requires_external_media_approval": bool(
            request.metadata.get("requires_external_media_approval")
        ),
    }
    try:
        canonical = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProviderApprovalError("provider request is not JSON serializable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read(path: Path, project_id: str) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "project_id": project_id, "requests": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderApprovalError("provider approval state cannot be read") from exc
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("project_id") != project_id
        or not isinstance(value.get("requests"), list)
    ):
        raise ProviderApprovalError("provider approval state has an invalid project identity")
    for item in value["requests"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("request_id"), str)
            or not isinstance(item.get("request_digest"), str)
            or item.get("status") not in _STATUSES
        ):
            raise ProviderApprovalError("provider approval state contains an invalid request")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name: str | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except (OSError, TypeError, ValueError) as exc:
        if name:
            try:
                os.unlink(name)
            except OSError:
                pass
        raise ProviderApprovalError("provider approval state could not be saved") from exc


def _locked_store(project_dir: Path | str, *, store_dir: Path | str | None = None):
    project = Path(project_dir).expanduser().resolve()
    if not project.is_dir():
        raise ProviderApprovalError("active project directory is missing")
    path = _log_path(project, store_dir=store_dir)
    return project, path, FileLock(str(path) + ".lock", timeout=10)


def _prompt_preview(payload: Mapping[str, Any]) -> tuple[str | None, bool]:
    for key in ("prompt", "text", "script", "content", "query"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = redact_text(value.strip())
            return cleaned[:1200] + ("…" if len(cleaned) > 1200 else ""), len(cleaned) > 1200
    return None, False


def _media_preview(payload: Mapping[str, Any], project_dir: Path) -> list[str]:
    """Expose project-relative media names so transfer consent is informed."""
    project = project_dir.resolve()
    found: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in _MEDIA_PATH_FIELDS:
                    values = item if isinstance(item, (list, tuple)) else [item]
                    for raw in values:
                        if not isinstance(raw, (str, Path)):
                            continue
                        text = str(raw).strip()
                        if not text or text.lower().startswith(("http://", "https://", "data:")):
                            continue
                        path = Path(text).expanduser()
                        resolved = (path if path.is_absolute() else project / path).resolve()
                        try:
                            label = resolved.relative_to(project).as_posix()
                        except (OSError, ValueError):
                            label = f"[outside project] {resolved.name}"
                        if label not in found:
                            found.append(label)
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(payload)
    return found[:8]


def _display_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Keep control-only data and full provider payloads out of the board API."""
    keys = (
        "request_id", "request_digest", "run_id", "attempt", "pipeline_type",
        "stage", "agent_id", "provider", "model", "capability", "operation",
        "estimated_cost_usd", "runtime", "external_media_transfer", "prompt_preview",
        "prompt_preview_truncated", "media_preview", "created_at", "expires_at",
        "status", "approver_id",
    )
    return {key: record.get(key) for key in keys}


def _clear_review_content(record: dict[str, Any]) -> None:
    record["prompt_preview"] = None
    record["prompt_preview_truncated"] = False
    record["media_preview"] = []


def create_provider_approval_request(
    project_dir: Path | str,
    request: Any,
    *,
    agent_id: str,
    store_dir: Path | str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Create one short-lived request, reusing an identical active decision."""
    project, path, lock = _locked_store(project_dir, store_dir=store_dir)
    if not str(agent_id or "").strip():
        raise ProviderApprovalError("provider approval requires an agent id")
    if str(request.project_id or "") != project.name:
        raise ProviderApprovalError("provider request project identity does not match its directory")
    if any(getattr(request, field, None) is None for field in (
        "pipeline_type", "run_id", "attempt", "stage"
    )):
        raise ProviderApprovalError("provider approval requires a complete active run identity")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not 1 <= ttl_seconds <= 900
    ):
        raise ProviderApprovalError("provider approval expiry is outside the supported range")

    digest = _request_digest(request, agent_id=agent_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock:
            store = _read(path, project.name)
            now = _now()
            cleanup_changed = False
            for existing in store["requests"]:
                if (
                    existing.get("status") in {"pending", "approved"}
                    and _parse_timestamp(existing.get("expires_at")) <= now
                ):
                    existing["status"] = "expired"
                    _clear_review_content(existing)
                    cleanup_changed = True
            for existing in reversed(store["requests"]):
                if existing.get("request_digest") != digest:
                    continue
                if existing.get("status") in {"pending", "approved", "rejected"}:
                    if _parse_timestamp(existing.get("expires_at")) > now:
                        if cleanup_changed:
                            _write(path, store)
                        return _display_record(existing)
                    existing["status"] = "expired"
                    _clear_review_content(existing)
            expires = now + timedelta(seconds=ttl_seconds)
            prompt_preview, prompt_preview_truncated = _prompt_preview(dict(request.payload))
            record = {
                "request_id": str(uuid.uuid4()),
                "request_digest": digest,
                "run_id": str(request.run_id),
                "attempt": int(request.attempt),
                "pipeline_type": str(request.pipeline_type),
                "stage": str(request.stage),
                "agent_id": str(agent_id),
                "provider": str(request.provider),
                "model": str(request.model or ""),
                "capability": str(request.capability),
                "operation": str(request.operation),
                "estimated_cost_usd": float(request.estimated_cost_usd),
                "runtime": str(request.metadata.get("runtime") or ""),
                "external_media_transfer": bool(
                    request.metadata.get("requires_external_media_approval") is True
                    or request.fallback_class == FallbackClass.MATERIAL_MEDIA_CHANGE.value
                ),
                "prompt_preview": prompt_preview,
                "prompt_preview_truncated": prompt_preview_truncated,
                "media_preview": _media_preview(dict(request.payload), project),
                "created_at": _timestamp(now),
                "expires_at": _timestamp(expires),
                "status": "pending",
            }
            store["requests"].append(record)
            # Keep bounded terminal history without ever dropping a live ticket.
            active = [item for item in store["requests"] if item.get("status") in {"pending", "approved"}]
            history = [item for item in store["requests"] if item.get("status") not in {"pending", "approved"}]
            history_limit = max(0, 100 - len(active))
            store["requests"] = (history[-history_limit:] if history_limit else []) + active
            _write(path, store)
            return _display_record(record)
    except FileLockTimeout as exc:
        raise ProviderApprovalError("timed out creating provider approval request") from exc
    except OSError as exc:
        raise ProviderApprovalError("provider approval state could not be saved") from exc


def list_provider_approval_requests(
    project_dir: Path | str,
    *,
    store_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Return active, presentation-safe requests for the Backlot board."""
    try:
        project, path, lock = _locked_store(project_dir, store_dir=store_dir)
        if not path.is_file():
            return {"requests": [], "error": None}
        with lock:
            store = _read(path, project.name)
            now = _now()
            changed = False
            for item in store["requests"]:
                if (
                    item.get("status") in {"pending", "approved"}
                    and _parse_timestamp(item.get("expires_at")) <= now
                ):
                    item["status"] = "expired"
                    _clear_review_content(item)
                    changed = True
            if changed:
                _write(path, store)
        now = _now()
        active = [
            _display_record(item)
            for item in store["requests"]
            if item.get("status") in {"pending", "approved"}
            and _parse_timestamp(item.get("expires_at")) > now
        ]
        return {"requests": active[-10:], "error": None}
    except (ProviderApprovalError, OSError, ValueError) as exc:
        return {"requests": [], "error": redact_text(exc)}


def get_provider_approval_request(
    project_dir: Path | str,
    request_id: str,
    *,
    store_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    project, path, lock = _locked_store(project_dir, store_dir=store_dir)
    if not path.is_file():
        return None
    try:
        with lock:
            store = _read(path, project.name)
            record = next(
                (item for item in store["requests"] if item.get("request_id") == str(request_id)),
                None,
            )
            return _display_record(record) if record else None
    except FileLockTimeout as exc:
        raise ProviderApprovalError("timed out reading provider approval request") from exc


def decide_provider_approval_request(
    project_dir: Path | str,
    request_id: str,
    request_digest: str,
    *,
    decision: str,
    approver_id: str = "backlot-user",
    store_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Record a one-time decision tied to the exact current request digest."""
    if decision not in {"approve", "reject"}:
        raise ProviderApprovalError("decision must be approve or reject")
    if not isinstance(request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", request_digest):
        raise ProviderApprovalError("request_digest must be a SHA-256 digest")
    actor = str(approver_id or "").strip()
    if not actor or len(actor) > 120:
        raise ProviderApprovalError("approver_id must contain 1 to 120 characters")
    project, path, lock = _locked_store(project_dir, store_dir=store_dir)
    try:
        with lock:
            store = _read(path, project.name)
            record = next(
                (item for item in store["requests"] if item.get("request_id") == str(request_id)),
                None,
            )
            if record is None:
                raise ProviderApprovalError("provider approval request was not found")
            if record.get("request_digest") != request_digest:
                raise ProviderApprovalError("provider approval request changed; reload the board")
            if record.get("status") == "expired":
                raise ProviderApprovalError("provider approval request expired; start a new run")
            if record.get("status") != "pending":
                raise ProviderApprovalError("provider approval request is no longer pending")
            if _parse_timestamp(record.get("expires_at")) <= _now():
                record["status"] = "expired"
                _clear_review_content(record)
                _write(path, store)
                raise ProviderApprovalError("provider approval request expired; start a new run")
            record["status"] = "approved" if decision == "approve" else "rejected"
            record["approver_id"] = actor
            record["decided_at"] = _timestamp()
            if decision == "reject":
                _clear_review_content(record)
            _write(path, store)
            return _display_record(record)
    except FileLockTimeout as exc:
        raise ProviderApprovalError("timed out recording provider approval decision") from exc


def consume_provider_approval_request(
    project_dir: Path | str,
    request_id: str,
    request_digest: str,
    *,
    store_dir: Path | str | None = None,
) -> bool:
    """Consume an approved request once so a decision cannot be replayed."""
    project, path, lock = _locked_store(project_dir, store_dir=store_dir)
    try:
        with lock:
            store = _read(path, project.name)
            record = next(
                (item for item in store["requests"] if item.get("request_id") == str(request_id)),
                None,
            )
            if (
                record is None
                or record.get("request_digest") != request_digest
                or record.get("status") != "approved"
            ):
                return False
            if _parse_timestamp(record.get("expires_at")) <= _now():
                record["status"] = "expired"
                _clear_review_content(record)
                _write(path, store)
                return False
            record["status"] = "consumed"
            record["consumed_at"] = _timestamp()
            _clear_review_content(record)
            _write(path, store)
            return True
    except FileLockTimeout as exc:
        raise ProviderApprovalError("timed out consuming provider approval") from exc


def expire_provider_approval_request(
    project_dir: Path | str,
    request_id: str,
    request_digest: str,
    *,
    store_dir: Path | str | None = None,
) -> None:
    project, path, lock = _locked_store(project_dir, store_dir=store_dir)
    try:
        with lock:
            store = _read(path, project.name)
            record = next(
                (item for item in store["requests"] if item.get("request_id") == str(request_id)),
                None,
            )
            if (
                record is not None
                and record.get("request_digest") == request_digest
                and record.get("status") in {"pending", "approved"}
            ):
                record["status"] = "expired"
                _clear_review_content(record)
                _write(path, store)
    except FileLockTimeout as exc:
        raise ProviderApprovalError("timed out expiring provider approval") from exc


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "ProviderApprovalError",
    "consume_provider_approval_request",
    "create_provider_approval_request",
    "decide_provider_approval_request",
    "expire_provider_approval_request",
    "get_provider_approval_request",
    "list_provider_approval_requests",
    "provider_approval_store_dir",
]
