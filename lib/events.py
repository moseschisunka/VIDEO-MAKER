"""Backlot event stream — append-only tool-event log per project.

Written by the BaseTool instrumentation layer (tools/base_tool.py) whenever a
tool executes against a project directory; consumed by the Backlot board's
watcher to power live activity and per-scene generating states.

Design rules:
- Observability must never break production: every public function swallows
  its own errors. A failed event write is silently dropped.
- Zero agent burden: project attribution is inferred from the tool's inputs
  (explicit ``project_dir`` or any path argument under ``projects/``).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lib.paths import PROJECTS_DIR, REPO_ROOT  # single source of truth
from lib.observability import (
    correlation_fields,
    event_id,
    metrics,
    sanitize_observation,
    structured_log,
)
from lib.secrets import redact_mapping

EVENTS_FILENAME = "events.jsonl"

# Thread-level serialization only. Cross-PROCESS appends are unsynchronized
# by design: single-line O_APPEND writes rarely tear, and read_events skips
# malformed lines, so a torn line degrades to one missing activity entry.
_write_lock = threading.Lock()
_logger = logging.getLogger("openmontage.events")
try:
    from filelock import FileLock
except ImportError:
    FileLock = None

# Input keys checked (in order) when inferring the project a tool call
# belongs to. Explicit project keys win over path inference.
_EXPLICIT_PROJECT_KEYS = ("project_dir", "project_path")
_PATH_HINT_KEYS = (
    "output_path",
    "output_dir",
    "output_file",
    "input_path",
    "video_path",
    "audio_path",
    "image_path",
    "file_path",
)


def _project_identity_defaults(project_dir: Path) -> dict[str, Any]:
    """Read persisted identity used to enrich events that omit it.

    Tool instrumentation predates the durable work order and many callers do
    not know the pipeline/run fields.  Enrichment is best-effort and never
    overrides an explicit caller value; the cross-artifact validator remains
    responsible for detecting an explicitly wrong value.
    """
    defaults: dict[str, Any] = {}
    for filename in ("work_order.json", "project.json"):
        try:
            payload = json.loads((project_dir / filename).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for field in ("project_id", "pipeline_type", "run_id", "attempt"):
            if defaults.get(field) in (None, "") and payload.get(field) not in (None, ""):
                defaults[field] = payload[field]
        # A tool call often does not carry the stage explicitly.  The active
        # work-order stage is the only safe default; never infer a stage from
        # a filename or from a caller's free-form description.
        if defaults.get("stage") in (None, ""):
            candidate_stage = payload.get("current_stage") or payload.get("next_stage")
            if candidate_stage not in (None, ""):
                defaults["stage"] = candidate_stage
    return defaults


def infer_project_dir(inputs: Any) -> Optional[Path]:
    """Best-effort: which project directory does this tool call belong to?

    Returns None when the call can't be attributed — the event is then
    simply not emitted (principle: never guess loudly, never fail).
    """
    if not isinstance(inputs, dict):
        return None
    try:
        # Only paths under the canonical projects root are attributable —
        # an explicit project_dir pointing elsewhere (HyperFrames workspace,
        # arbitrary user dir) must not receive an events.jsonl. Explicit
        # values are normalized to the project ROOT the same way hints are,
        # so project_dir="projects/x/renders/build" attributes to projects/x.
        projects_root = PROJECTS_DIR.resolve()
        for key in _EXPLICIT_PROJECT_KEYS + _PATH_HINT_KEYS:
            value = inputs.get(key)
            if not isinstance(value, (str, Path)) or not str(value):
                continue
            try:
                resolved = Path(value).resolve()
                rel = resolved.relative_to(projects_root)
            except (ValueError, OSError):
                continue
            if rel.parts:
                return PROJECTS_DIR / rel.parts[0]
    except Exception:
        return None
    return None


def emit_event(project_dir: Path | str, payload: dict[str, Any]) -> None:
    """Append one event to the project's events.jsonl. Never raises.

    Writes only into an EXISTING project directory — a typo'd path must not
    spawn a ghost project on the board.
    """
    try:
        project_dir = Path(project_dir)
        if not project_dir.is_dir():
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat()}
        # Do not mutate the caller's dictionary: the same payload is commonly
        # reused for a finish event and for result provenance.
        safe_payload = sanitize_observation(redact_mapping(dict(payload)))
        # System-owned fields cannot be spoofed by provider/user payloads.
        for key in ("ts", "schema_version", "event_id", "span_id"):
            safe_payload.pop(key, None)
        defaults = _project_identity_defaults(project_dir)
        for field, value in defaults.items():
            if safe_payload.get(field) in (None, ""):
                safe_payload[field] = value
        safe_payload.setdefault("project_id", project_dir.name)
        safe_payload["schema_version"] = "1.0"
        safe_payload["event_id"] = event_id()
        safe_payload["span_id"] = event_id()[:16]
        current_trace = correlation_fields(
            project_id=safe_payload.get("project_id"),
            run_id=safe_payload.get("run_id"),
            pipeline_type=safe_payload.get("pipeline_type"),
            stage=safe_payload.get("stage"),
            attempt=safe_payload.get("attempt"),
            agent_id=safe_payload.get("agent_id"),
            tool=safe_payload.get("tool"),
            provider=safe_payload.get("provider"),
        ).get("trace_id")
        if current_trace:
            safe_payload["trace_id"] = current_trace
        entry.update({k: v for k, v in safe_payload.items() if v is not None})
        event_name = str(entry.get("event") or "unknown")
        metrics.increment("openmontage_events_total", labels={"event": event_name})
        structured_log(
            _logger,
            logging.DEBUG,
            f"backlot event: {event_name}",
            context={
                key: entry.get(key)
                for key in (
                    "project_id", "run_id", "pipeline_type", "stage", "attempt",
                    "agent_id", "tool", "provider",
                )
                if entry.get(key) not in (None, "")
            },
            event=event_name,
            success=entry.get("success"),
            duration_s=entry.get("duration_s"),
            cost_usd=entry.get("cost_usd"),
        )
        path = project_dir / EVENTS_FILENAME
        line = json.dumps(entry, default=str)
        with _write_lock:
            if FileLock:
                with FileLock(str(path) + '.lock'):
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
            else:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
    except Exception:
        pass


def read_events(project_dir: Path | str, limit: Optional[int] = None) -> list[dict[str, Any]]:
    """Read events for a project (oldest first). Tolerates malformed lines."""
    path = Path(project_dir) / EVENTS_FILENAME
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    if limit is not None:
        return events[-limit:]
    return events
