"""Read-only cross-artifact project identity validation.

Pipeline selection is a production invariant, not a display hint.  This
module reads the durable project marker/work order and compares identity fields
on every artifact that can drive, describe, or audit a run.  It deliberately
does not mutate project state, call providers, or execute a renderer.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping


IDENTITY_FIELDS = ("project_id", "pipeline_type", "run_id")
PROJECT_MARKER = "project.json"
WORK_ORDER = "work_order.json"


class ProjectIdentityValidationError(ValueError):
    """Raised when project artifacts do not share one immutable identity."""


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"cannot read JSON: {exc}"
    if not isinstance(value, dict):
        return None, "JSON root must be an object"
    return value, None


def _source_path_map(project_dir: Path) -> list[tuple[str, Path]]:
    """Return deterministic identity-bearing project sources.

    Optional stage artifacts are included only when present.  A queued project
    therefore remains valid before render/checkpoint artifacts exist, while a
    present artifact can never silently omit its identity.
    """
    paths: list[tuple[str, Path]] = [
        ("marker", project_dir / PROJECT_MARKER),
        ("work_order", project_dir / WORK_ORDER),
        ("project_config", project_dir / "artifacts" / "project_config.json"),
        ("proposal", project_dir / "artifacts" / "proposal_packet.json"),
        ("decision_log", project_dir / "artifacts" / "decision_log.json"),
        ("decision_log_root", project_dir / "decision_log.json"),
        ("approval_log", project_dir / "approval_records.json"),
        ("render_report", project_dir / "artifacts" / "render_report.json"),
    ]

    current_checkpoints = sorted(project_dir.glob("checkpoint_*.json"), key=lambda p: p.name)
    paths.extend((f"checkpoint:{path.name}", path) for path in current_checkpoints)
    history_dir = project_dir / "history"
    history_checkpoints = sorted(history_dir.glob("checkpoint_*.json"), key=lambda p: p.name)
    paths.extend((f"checkpoint_history:{path.name}", path) for path in history_checkpoints)
    return [(label, path) for label, path in paths if path.is_file()]


def _event_records(path: Path) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    records: list[tuple[str, dict[str, Any]]] = []
    warnings: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return records, [{"source": "events", "code": "read_error", "message": str(exc)}]
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            # Event logging is best-effort and intentionally tolerant of a
            # torn append.  A malformed line is a warning, not an identity
            # mismatch; valid neighboring events are still checked.
            warnings.append({
                "source": f"events:{line_number}",
                "code": "malformed_event",
                "message": "ignored malformed JSON event line",
            })
            continue
        if isinstance(value, dict):
            records.append((f"events:{line_number}", value))
        else:
            warnings.append({
                "source": f"events:{line_number}",
                "code": "event_not_object",
                "message": "ignored non-object event line",
            })
    return records, warnings


def _extract_identity(source: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Extract root/metadata identity plus proposal/checkpoint conventions."""
    metadata = value.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    result = {
        field: value.get(field) if value.get(field) is not None else metadata.get(field)
        for field in IDENTITY_FIELDS
    }
    if source == "approval_log":
        records = value.get("records")
        first = records[0] if isinstance(records, list) and records and isinstance(records[0], Mapping) else {}
        result = {
            field: value.get(field) if value.get(field) not in (None, "") else first.get(field)
            for field in IDENTITY_FIELDS
        }
    if source == "proposal":
        plan = value.get("production_plan")
        if isinstance(plan, Mapping) and not result["pipeline_type"]:
            # Older proposal writers stored the manifest name only inside the
            # production plan.  New writers emit the root field as well.
            result["pipeline_type"] = plan.get("pipeline")
    return result


def _issue(
    issues: list[dict[str, Any]],
    *,
    source: str,
    code: str,
    field: str | None = None,
    expected: Any = None,
    actual: Any = None,
    message: str,
) -> None:
    item: dict[str, Any] = {
        "source": source,
        "code": code,
        "message": message,
    }
    if field is not None:
        item["field"] = field
    if expected is not None:
        item["expected"] = expected
    if actual is not None:
        item["actual"] = actual
    issues.append(item)


def _first_value(records: Iterable[tuple[str, Mapping[str, Any]]], field: str) -> Any:
    for source, value in records:
        identity = _extract_identity(source, value)
        candidate = identity.get(field)
        if candidate not in (None, ""):
            return candidate
    return None


def validate_project_identity(
    project_dir: Path | str,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Return a structured identity report without changing project state.

    ``strict=False`` is suitable for an in-progress project: optional stage
    artifacts may not exist yet.  Any artifact that *does* exist must carry all
    three identity fields and match the canonical values.  ``strict=True``
    additionally requires the marker and durable work order.
    """
    root = Path(project_dir)
    report: dict[str, Any] = {
        "valid": False,
        "project_dir": str(root),
        "expected": {field: None for field in IDENTITY_FIELDS},
        "sources": [],
        "issues": [],
        "warnings": [],
    }
    if not root.is_dir():
        _issue(
            report["issues"],
            source="project",
            code="project_missing",
            message=f"project directory does not exist: {root}",
        )
        return report

    loaded: list[tuple[str, dict[str, Any]]] = []
    for source, path in _source_path_map(root):
        value, error = _read_json(path)
        if error:
            _issue(
                report["issues"],
                source=source,
                code="unreadable_artifact",
                message=f"{path.name}: {error}",
            )
            report["sources"].append({"source": source, "path": str(path), "valid": False})
            continue
        assert value is not None
        loaded.append((source, value))
        report["sources"].append({"source": source, "path": str(path), "valid": True})

    events_path = root / "events.jsonl"
    if events_path.is_file():
        event_records, event_warnings = _event_records(events_path)
        report["warnings"].extend(event_warnings)
        loaded.extend(event_records)
        report["sources"].extend(
            {"source": source, "path": str(events_path), "valid": True}
            for source, _ in event_records
        )

    # The directory name is immutable project identity.  The work order is the
    # canonical execution identity when present; marker/config/proposal provide
    # compatible fallbacks for legacy or internal-demo fixtures.
    expected_project_id = root.name
    expected_pipeline = _first_value(
        (record for record in loaded if record[0] == "work_order"), "pipeline_type"
    ) or _first_value(loaded, "pipeline_type")
    expected_run_id = _first_value(
        (record for record in loaded if record[0] == "work_order"), "run_id"
    ) or _first_value(loaded, "run_id")
    report["expected"].update({
        "project_id": expected_project_id,
        "pipeline_type": expected_pipeline,
        "run_id": expected_run_id,
    })

    if strict:
        present_names = {source for source, _ in loaded}
        for required_source in ("marker", "work_order"):
            if required_source not in present_names:
                _issue(
                    report["issues"],
                    source=required_source,
                    code="required_source_missing",
                    message=f"strict identity validation requires {required_source}",
                )
    if not expected_pipeline:
        _issue(
            report["issues"],
            source="project",
            code="pipeline_identity_missing",
            field="pipeline_type",
            message="no pipeline_type could be established from the project identity sources",
        )
    if not expected_run_id:
        _issue(
            report["issues"],
            source="project",
            code="run_identity_missing",
            field="run_id",
            message="no run_id could be established from the project identity sources",
        )
    else:
        try:
            parsed_run_id = uuid.UUID(str(expected_run_id))
        except (ValueError, AttributeError, TypeError):
            parsed_run_id = None
        if parsed_run_id is None or parsed_run_id.version != 4:
            _issue(
                report["issues"],
                source="project",
                code="run_identity_invalid",
                field="run_id",
                actual=expected_run_id,
                message="run_id must be a UUIDv4 execution identity",
            )

    for source, value in loaded:
        identity = _extract_identity(source, value)
        for field, expected in report["expected"].items():
            actual = identity.get(field)
            if actual in (None, ""):
                _issue(
                    report["issues"],
                    source=source,
                    code="identity_missing",
                    field=field,
                    expected=expected,
                    message=f"{source} does not declare {field}",
                )
            elif expected not in (None, "") and actual != expected:
                _issue(
                    report["issues"],
                    source=source,
                    code="identity_mismatch",
                    field=field,
                    expected=expected,
                    actual=actual,
                    message=f"{source} {field} does not match the canonical project identity",
                )

        # A proposal's production-plan pipeline and an artifact metadata
        # identity are independently consumed by downstream renderers.  Check
        # both representations so a root field cannot hide a stale nested
        # value (the historical hardcoded animated-explainer defect).
        if source == "proposal":
            plan = value.get("production_plan")
            if isinstance(plan, Mapping) and plan.get("pipeline") not in (None, ""):
                plan_pipeline = plan.get("pipeline")
                if report["expected"]["pipeline_type"] not in (None, "") and plan_pipeline != report["expected"]["pipeline_type"]:
                    _issue(
                        report["issues"],
                        source=source,
                        code="identity_mismatch",
                        field="production_plan.pipeline",
                        expected=report["expected"]["pipeline_type"],
                        actual=plan_pipeline,
                        message="proposal production_plan.pipeline does not match the canonical project identity",
                    )
        metadata = value.get("metadata")
        if isinstance(metadata, Mapping):
            for field, expected in report["expected"].items():
                if field in metadata and metadata.get(field) not in (None, "") and expected not in (None, "") and metadata.get(field) != expected:
                    _issue(
                        report["issues"],
                        source=source,
                        code="identity_mismatch",
                        field=f"metadata.{field}",
                        expected=expected,
                        actual=metadata.get(field),
                        message=f"{source} metadata {field} does not match the canonical project identity",
                    )

        # Checkpoint files carry inline copies of canonical artifacts.  A
        # stale inline copy must not be able to disagree with the artifact on
        # disk merely because the checkpoint envelope itself is correct.
        if source.startswith("checkpoint:") or source.startswith("checkpoint_history:"):
            embedded = value.get("artifacts")
            if isinstance(embedded, Mapping):
                for artifact_name, artifact_value in embedded.items():
                    if artifact_name not in {"proposal_packet", "decision_log", "render_report", "edit_decisions"}:
                        continue
                    if not isinstance(artifact_value, Mapping):
                        continue
                    embedded_source = f"{source}:artifacts.{artifact_name}"
                    embedded_identity = _extract_identity(str(artifact_name), artifact_value)
                    for field, expected in report["expected"].items():
                        actual = embedded_identity.get(field)
                        if actual in (None, ""):
                            _issue(
                                report["issues"],
                                source=embedded_source,
                                code="identity_missing",
                                field=field,
                                expected=expected,
                                message=f"{embedded_source} does not declare {field}",
                            )
                        elif expected not in (None, "") and actual != expected:
                            _issue(
                                report["issues"],
                                source=embedded_source,
                                code="identity_mismatch",
                                field=field,
                                expected=expected,
                                actual=actual,
                                message=f"{embedded_source} {field} does not match the canonical project identity",
                            )

    report["valid"] = not report["issues"]
    return report


def assert_project_identity(
    project_dir: Path | str,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Raise a concise error when cross-artifact identity is not valid."""
    report = validate_project_identity(project_dir, strict=strict)
    if not report["valid"]:
        details = "; ".join(
            f"{item['source']}: {item['message']}"
            for item in report["issues"][:8]
        )
        if len(report["issues"]) > 8:
            details += f"; +{len(report['issues']) - 8} more"
        raise ProjectIdentityValidationError(details or "project identity validation failed")
    return report


__all__ = [
    "IDENTITY_FIELDS",
    "ProjectIdentityValidationError",
    "assert_project_identity",
    "validate_project_identity",
]
