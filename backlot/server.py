"""Backlot server — FastAPI app: board state API, SSE change feed, media.

The watcher observes ``projects/`` with watchfiles; on any change it bumps a
per-project version and wakes SSE subscribers, who tell the browser to
refetch state. The server also owns explicit project lifecycle actions such
as creating a visual-only voiceover-reusing variant.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os as _os
import re
import shutil
import time
import threading
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from lib.approval_contracts import ApprovalValidationError
from lib.checkpoint import CheckpointValidationError, init_project, read_checkpoint, write_checkpoint
from lib.demo_runner import RUNNER_KIND, is_internal_demo_project
from lib.pipeline_release import pipeline_release_metadata, studio_release_status
from lib.project_identity import validate_project_identity
from lib.manifest_executor import (
    ManifestExecutionError,
    is_certified_executor_order,
    load_manifest_stage_context,
    submit_manifest_stage,
)
from lib.observability import metrics, structured_log
from lib.work_order import (
    DEFAULT_LEASE_SECONDS,
    WorkOrderConflictError,
    WorkOrderStateError,
    WorkOrderValidationError,
    advance_work_order,
    build_work_order,
    cancel_work_order,
    claim_work_order,
    decide_human_gate,
    heartbeat_work_order,
    read_work_order,
    release_work_order,
    restart_work_order,
    resume_work_order,
    write_work_order,
)
from backlot.state import PROJECTS_DIR, REPO_ROOT, list_projects, load_board_state, summarize_project


_logger = logging.getLogger("openmontage.backlot")

class CreateProjectRequest(BaseModel):
    title: str
    topic_prompt: str = ''
    # The default must stay inside the approved Phase 0 launch boundary.  A
    # generated explainer remains discoverable as experimental, but is not the
    # implicit workflow until its provider/runtime gates pass.
    pipeline_type: str = 'screen-demo'
    playbook: str = 'premium-minimalist'
    voice: str = 'en-US-ChristopherNeural'
    voice_provider: str = 'edge_tts'
    tts_provider: str | None = None
    render_runtime: str = 'remotion'
    output_profile: str = 'youtube_landscape'
    profile: str | None = None
    aspect_ratio: str | None = None
    source_mode: str | None = None
    target_duration_seconds: int = 30
    project_id: str | None = None


class CreateVariantRequest(BaseModel):
    """Visual-only fork settings; narration is always copied, never regenerated."""

    visual_variant: str = "balanced-grid"
    variant_name: str | None = None

class ApproveStageRequest(BaseModel):
    stage: str
    # Optional for backwards compatibility with the board's one-click action.
    # When omitted, the active work-order lease owner is used to complete the
    # transition; a released/expired run remains approved but resumable.
    agent_id: str | None = None
    approver_id: str = "backlot-user"
    notes: str | None = None
    decision: str = "approve"


class ClaimWorkOrderRequest(BaseModel):
    agent_id: str
    lease_seconds: int = DEFAULT_LEASE_SECONDS


class HeartbeatWorkOrderRequest(BaseModel):
    agent_id: str
    lease_seconds: int = DEFAULT_LEASE_SECONDS


class AdvanceWorkOrderRequest(BaseModel):
    agent_id: str
    stage: str
    checkpoint_ref: str | None = None
    # ``checkpoint_path`` and ``checkpoint`` are accepted as migration aliases
    # for thin agents that already use one of those names.
    checkpoint_path: str | None = None
    checkpoint: str | None = None


class ReleaseWorkOrderRequest(BaseModel):
    agent_id: str


class CancelWorkOrderRequest(BaseModel):
    agent_id: str
    reason: str | None = None


class RestartWorkOrderRequest(BaseModel):
    agent_id: str
    reason: str | None = None


class SubmitManifestStageRequest(BaseModel):
    """Agent-produced artifact handoff; Python only validates/persists it."""

    agent_id: str
    stage: str
    artifacts: dict[str, Any] = Field(default_factory=dict)
    status: str = "completed"
    # Do not let Pydantic coerce JSON strings/integers at the approval
    # boundary. The manifest executor repeats this check for direct callers.
    human_approved: StrictBool = False
    checkpoint_policy: str = "guided"
    review: dict[str, Any] | None = None
    cost_snapshot: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None
    producing_tool: str = "stage-director"


VISUAL_VARIANTS = {
    "balanced-grid": "Balanced teaching grid",
    "diagram-focus": "Diagram focus",
    "minimal-lecture": "Minimal lecture",
}

UI_DIR = Path(__file__).resolve().parent / "ui"
THUMB_CACHE_DIR = REPO_ROOT / ".backlot" / "thumbs"
THUMB_WIDTHS = (320, 640, 960)

# Paths inside a project whose changes are pure noise for the board.
_IGNORE_PARTS = {"node_modules", ".git", "__pycache__", ".cache"}

SSE_HEARTBEAT_SECONDS = 15

# Backlot is intentionally usable without credentials when it is bound only
# to loopback for local development. Any non-loopback binding always fails
# closed unless a bearer token is configured. ``BACKLOT_AUTH_REQUIRED`` may
# force authentication on loopback, but it must never disable authentication
# for a remotely reachable binding.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_PROJECT_SCOPE_WILDCARD = "*"


def _auth_required() -> bool:
    host = _os.getenv("BACKLOT_HOST", "127.0.0.1").strip().lower()
    if host not in _LOOPBACK_HOSTS:
        return True
    explicit = _os.getenv("BACKLOT_AUTH_REQUIRED")
    return explicit is not None and explicit.strip().lower() in _TRUE_VALUES


def _configured_auth_token() -> str:
    return _os.getenv("BACKLOT_AUTH_TOKEN", "").strip()


def _record_auth_failure(reason: str) -> None:
    """Record an auth failure without retaining credentials or headers."""

    safe_reason = reason if reason in {"missing_config", "invalid_bearer"} else "unknown"
    try:
        metrics.increment("openmontage_auth_failures_total", labels={"reason": safe_reason})
        structured_log(
            _logger,
            logging.WARNING,
            "auth_failure",
            event="auth_failure",
            reason=safe_reason,
        )
    except Exception:
        # Authentication must still fail closed if telemetry is unavailable.
        pass


def _project_scope_rules() -> tuple[str, ...] | None:
    """Return the optional project allow-list.

    Rules are comma-separated project ids.  A trailing ``*`` grants a
    prefix scope (for example ``school-a-*``); an empty value means all
    projects.  Prefix rules make tenant-scoped variant ids possible without
    weakening the default deny-by-configuration behavior.
    """
    raw = _os.getenv("BACKLOT_PROJECT_SCOPE", "").strip()
    if not raw:
        return None
    return tuple(rule.strip() for rule in raw.split(",") if rule.strip())


def _project_is_allowed(project_id: str) -> bool:
    rules = _project_scope_rules()
    if rules is None:
        return True
    for rule in rules:
        if rule == _PROJECT_SCOPE_WILDCARD:
            return True
        if rule.endswith("*") and project_id.startswith(rule[:-1]):
            return True
        if hmac.compare_digest(project_id, rule):
            return True
    return False


def _ui_html(name: str, assets: tuple[str, ...]) -> HTMLResponse:
    html = (UI_DIR / name).read_text(encoding="utf-8")
    for asset in assets:
        path = UI_DIR / asset
        if path.is_file():
            version = str(int(path.stat().st_mtime))
            html = html.replace(f"/ui/{asset}", f"/ui/{asset}?v={version}")
    return HTMLResponse(html)


class ChangeHub:
    """Fan-out of project-change notifications to SSE subscribers.

    Subscriptions are filtered: a board subscribed to one project only ever
    receives that project's ids, so unrelated-project bursts can't flood its
    queue and starve out the one notification it actually needs.
    """

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue, Optional[str]] = {}
        metrics.set_gauge("openmontage_sse_subscribers", 0)

    def subscriber_count(self) -> int:
        """Return the current number of live SSE subscriptions.

        Keeping this observation on the hub gives the load/soak gate and
        operators a stable, bounded signal without exposing the subscriber
        dictionary to callers.
        """
        return len(self._subscribers)

    def subscribe(self, project_id: Optional[str] = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers[q] = project_id
        metrics.set_gauge("openmontage_sse_subscribers", self.subscriber_count())
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.pop(q, None)
        metrics.set_gauge("openmontage_sse_subscribers", self.subscriber_count())

    def publish(self, project_id: str) -> None:
        for q, only in list(self._subscribers.items()):
            if only is not None and only != project_id:
                continue
            try:
                q.put_nowait(project_id)
            except asyncio.QueueFull:
                # Queue holds only THIS subscriber's relevant ids, so a full
                # queue already guarantees a pending wake-up → safe to drop.
                metrics.increment(
                    "openmontage_sse_events_dropped_total",
                    labels={"reason": "subscriber_queue_full"},
                )


hub = ChangeHub()

# Library summaries are expensive to derive (full state parse per project);
# cache per project and invalidate from the watcher.
_summary_cache: dict[str, dict] = {}
_summary_cache_lock = threading.Lock()


def _invalidate_summary(project_id: str) -> None:
    with _summary_cache_lock:
        _summary_cache.pop(project_id, None)


def _cached_summaries() -> list[dict]:
    if not PROJECTS_DIR.is_dir():
        return []
    summaries = []
    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        # Never enumerate a tenant outside the configured scope, and never
        # follow a project-root symlink from the library listing.  The latter
        # prevents an attacker from making an external directory look like a
        # project even though the media endpoints perform their own checks.
        if not _project_is_allowed(entry.name) or entry.is_symlink():
            continue
        try:
            project_dir = entry.resolve(strict=True)
            project_dir.relative_to(Path(PROJECTS_DIR).resolve())
        except (OSError, ValueError, FileNotFoundError):
            continue
        with _summary_cache_lock:
            cached = _summary_cache.get(entry.name)
        if cached is None:
            try:
                cached = summarize_project(project_dir)
            except Exception:
                cached = {
                    "project_id": entry.name, "title": entry.name,
                    "pipeline_type": "unknown", "has_pipeline_state": False,
                    "poster": None, "live": False, "last_activity": 0,
                    "active_stage": None, "awaiting_human": False,
                    "stage_states": [], "completed_count": 0,
                    "render_count": 0, "scene_count": 0,
                    "work_order_status": None, "work_order_run_id": None,
                    "work_order_next_stage": None, "error": "unreadable",
                }
            with _summary_cache_lock:
                _summary_cache[entry.name] = cached
        summaries.append(cached)
    summaries.sort(key=lambda s: (not s["live"], -(s["last_activity"] or 0)))
    return summaries


# Watch-loop hot path: pure string comparison, no per-path filesystem calls
# (change batches can be thousands of paths during a render).
_PROJECTS_ROOT_STR = _os.path.normcase(str(PROJECTS_DIR.resolve()))


def _project_of_change(path_str: str) -> Optional[str]:
    """Map a changed filesystem path to a project id (None = irrelevant)."""
    norm = _os.path.normcase(_os.path.normpath(path_str))
    if not norm.startswith(_PROJECTS_ROOT_STR):
        return None
    rel = norm[len(_PROJECTS_ROOT_STR):].lstrip("\\/")
    if not rel:
        return None
    parts = rel.replace("\\", "/").split("/")
    if _IGNORE_PARTS.intersection(parts):
        return None
    return parts[0]


async def _watch_projects() -> None:
    """Background task: watch projects/ and publish debounced changes."""
    try:
        from watchfiles import awatch
    except ImportError:
        return  # watcher unavailable → board still works via manual refresh
    if not PROJECTS_DIR.is_dir():
        return
    async for changes in awatch(PROJECTS_DIR, recursive=True, step=400):
        touched: set[str] = set()
        for _change, path_str in changes:
            pid = _project_of_change(path_str)
            if pid:
                touched.add(pid)
        for pid in touched:
            _invalidate_summary(pid)
            hub.publish(pid)


import yaml

def _load_playbooks_data() -> list[dict]:
    styles_dir = REPO_ROOT / "styles"
    if not styles_dir.is_dir():
        return []
    playbooks = []
    for f in sorted(styles_dir.glob("*.yaml")):
        try:
            content = yaml.safe_load(f.read_text(encoding="utf-8"))
            if content and isinstance(content, dict):
                playbooks.append({
                    "id": f.stem,
                    "name": content.get("identity", {}).get("name", f.stem.replace("-", " ").title()),
                    "category": content.get("identity", {}).get("category", "General"),
                    "mood": content.get("identity", {}).get("mood", ""),
                    "best_for": content.get("identity", {}).get("best_for", ""),
                    "color_palette": content.get("visual_language", {}).get("color_palette", {}),
                    "typography": content.get("typography", {}),
                    "motion": content.get("motion", {}),
                })
        except Exception:
            pass
    return playbooks

def _load_pipelines_data() -> list[dict]:
    defs_dir = REPO_ROOT / "pipeline_defs"
    from lib.pipeline_loader import list_pipeline_catalog
    return list_pipeline_catalog(defs_dir=defs_dir)


def _normalise_create_request(request: CreateProjectRequest) -> dict:
    """Normalize non-creative request identifiers before validation."""
    title = str(request.title or "").strip()
    topic = str(request.topic_prompt or "").strip()
    pipeline_type = str(request.pipeline_type or "").strip().lower()
    playbook = str(request.playbook or "").strip().lower()
    voice = str(request.voice or "").strip()
    requested_provider = str(request.voice_provider or "").strip().lower()
    alias_provider = str(request.tts_provider or "").strip().lower()
    # ``tts_provider`` was the name used by the original Backlot client while
    # ``voice_provider`` is the public request name.  Accept either, but do
    # not silently choose one when a caller sends conflicting selections.
    voice_provider = alias_provider or requested_provider
    render_runtime = str(request.render_runtime or "").strip().lower()
    requested_profile = str(request.output_profile or "").strip().lower()
    alias_profile = str(request.profile or "").strip().lower()
    output_profile = alias_profile or requested_profile
    aspect_ratio = str(request.aspect_ratio or "").strip()
    source_mode = str(request.source_mode or "").strip().lower() or None
    return {
        "title": title,
        "topic_prompt": topic,
        "pipeline_type": pipeline_type,
        "playbook": playbook,
        "voice": voice,
        "voice_provider": voice_provider,
        "requested_voice_provider": requested_provider,
        "alias_voice_provider": alias_provider,
        "render_runtime": render_runtime,
        "output_profile": output_profile,
        "requested_output_profile": requested_profile,
        "alias_output_profile": alias_profile,
        "aspect_ratio": aspect_ratio or None,
        "source_mode": source_mode,
        "target_duration_seconds": request.target_duration_seconds,
        "project_id": str(request.project_id or "").strip(),
    }


def _validate_create_request(request: CreateProjectRequest) -> tuple[dict, dict, dict]:
    """Validate selections before any project directory mutation.

    Returns ``(normalized_request, catalog_record, manifest)``.  This helper
    performs no provider calls and intentionally delegates creative decisions
    to the manifest/agent layers.
    """
    normalized = _normalise_create_request(request)
    errors: list[str] = []
    if not normalized["title"]:
        errors.append("title is required")
    if len(normalized["title"]) > 240:
        errors.append("title must be 240 characters or fewer")
    if len(normalized["topic_prompt"]) > 20_000:
        errors.append("topic_prompt must be 20,000 characters or fewer")
    if normalized["project_id"] and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", normalized["project_id"]
    ):
        errors.append("project_id must contain only letters, numbers, dot, underscore, or hyphen")
    if (
        normalized["requested_voice_provider"]
        and normalized["alias_voice_provider"]
        and normalized["requested_voice_provider"] != normalized["alias_voice_provider"]
    ):
        errors.append("voice_provider and tts_provider must match when both are supplied")
    if (
        normalized["requested_output_profile"]
        and normalized["alias_output_profile"]
        and normalized["requested_output_profile"] != normalized["alias_output_profile"]
    ):
        errors.append("output_profile and profile must match when both are supplied")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,127}", normalized["voice"] or ""):
        errors.append("voice must be a provider voice identifier")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", normalized["voice_provider"] or ""):
        errors.append("voice_provider must be a provider identifier")
    if normalized["render_runtime"] not in {"remotion", "hyperframes", "ffmpeg"}:
        errors.append("render_runtime must be one of remotion, hyperframes, or ffmpeg")
    if not isinstance(normalized["target_duration_seconds"], int) or isinstance(normalized["target_duration_seconds"], bool):
        errors.append("target_duration_seconds must be an integer")
    elif not 1 <= normalized["target_duration_seconds"] <= 86_400:
        errors.append("target_duration_seconds must be between 1 and 86400")

    from lib.pipeline_loader import list_pipeline_catalog, load_pipeline_readonly
    catalog = list_pipeline_catalog(defs_dir=REPO_ROOT / "pipeline_defs", include_hidden=True)
    record = next((item for item in catalog if item["id"] == normalized["pipeline_type"]), None)
    manifest = None
    if record is None:
        errors.append(f"unknown pipeline_type {normalized['pipeline_type']!r}")
    else:
        if not record["ui_visible"]:
            errors.append(f"pipeline {normalized['pipeline_type']!r} is hidden from user-facing discovery")
        if not record["creation_enabled"]:
            errors.append(f"pipeline {normalized['pipeline_type']!r} is not enabled for the current release scope")
        if not record["schema_valid"]:
            errors.append(f"pipeline {normalized['pipeline_type']!r} has an invalid manifest")
        if not record.get("agent_contract_valid", False):
            errors.append(f"pipeline {normalized['pipeline_type']!r} has an incomplete agent contract")
        try:
            manifest = load_pipeline_readonly(
                normalized["pipeline_type"],
                defs_dir=REPO_ROOT / "pipeline_defs",
            )
        except Exception as exc:
            errors.append(f"pipeline manifest cannot be loaded: {exc}")

    if manifest is not None:
        compatible = manifest.get("compatible_playbooks")
        allowed: set[str] = set()
        custom_allowed = False
        if isinstance(compatible, list):
            allowed.update(str(item).strip().lower() for item in compatible)
        elif isinstance(compatible, dict):
            for key in ("recommended", "also_works"):
                values = compatible.get(key) or []
                if isinstance(values, list):
                    allowed.update(str(item).strip().lower() for item in values)
            custom_allowed = bool(compatible.get("custom_allowed", False))
        try:
            from styles.playbook_loader import load_playbook
            load_playbook(normalized["playbook"], styles_dir=REPO_ROOT / "styles")
        except Exception as exc:
            errors.append(f"invalid playbook {normalized['playbook']!r}: {exc}")
        else:
            custom_path = REPO_ROOT / "styles" / "custom" / f"{normalized['playbook']}.yaml"
            if allowed and normalized["playbook"] not in allowed and not (custom_allowed and custom_path.is_file()):
                errors.append(
                    f"playbook {normalized['playbook']!r} is not compatible with "
                    f"pipeline {normalized['pipeline_type']!r}"
                )

    if record is not None and normalized["render_runtime"] not in set(record.get("supported_runtimes") or []):
        errors.append(
            f"render_runtime {normalized['render_runtime']!r} is not supported by "
            f"pipeline {normalized['pipeline_type']!r}"
        )

    profile = None
    try:
        from lib.media_profiles import get_profile, validate_duration
        profile = get_profile(normalized["output_profile"])
        validate_duration(normalized["output_profile"], normalized["target_duration_seconds"])
    except Exception as exc:
        errors.append(f"invalid output_profile: {exc}")
    if record is not None and normalized["output_profile"] not in set(record.get("supported_profiles") or []):
        errors.append(
            f"output_profile {normalized['output_profile']!r} is not supported by "
            f"pipeline {normalized['pipeline_type']!r}"
        )
    if normalized["aspect_ratio"]:
        valid_aspects = {"16:9", "9:16", "1:1", "21:9", "4:3"}
        if normalized["aspect_ratio"] not in valid_aspects:
            errors.append("aspect_ratio must be one of 16:9, 9:16, 1:1, 21:9, or 4:3")
        elif profile is not None and normalized["aspect_ratio"] != profile.aspect_ratio.value:
            errors.append(
                f"aspect_ratio {normalized['aspect_ratio']!r} does not match "
                f"output_profile {normalized['output_profile']!r}"
            )
    elif profile is not None:
        normalized["aspect_ratio"] = profile.aspect_ratio.value

    if manifest is not None and normalized["source_mode"]:
        declared_modes = {str(mode.get("name")) for mode in (manifest.get("production_modes") or []) if isinstance(mode, dict)}
        if declared_modes and normalized["source_mode"] not in declared_modes:
            errors.append(
                f"source_mode {normalized['source_mode']!r} is not declared by "
                f"pipeline {normalized['pipeline_type']!r}"
            )
    if normalized["pipeline_type"] == "talking-head":
        normalized["source_mode"] = normalized["source_mode"] or "source_footage"
        if normalized["source_mode"] != "source_footage":
            errors.append("talking-head launch scope supports source_footage only")

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return normalized, record, manifest

def _load_voices_data() -> list[dict]:
    return [
        {"id": "en-US-ChristopherNeural", "name": "Christopher (US Male - Authoritative & Warm)", "gender": "Male", "locale": "en-US", "tone": "Expert Explainer"},
        {"id": "en-US-AriaNeural", "name": "Aria (US Female - Clear & Dynamic)", "gender": "Female", "locale": "en-US", "tone": "Narrative & Commercial"},
        {"id": "en-US-GuyNeural", "name": "Guy (US Male - Casual & Conversational)", "gender": "Male", "locale": "en-US", "tone": "Podcast & Casual"},
        {"id": "en-US-JennyNeural", "name": "Jenny (US Female - Natural & Friendly)", "gender": "Female", "locale": "en-US", "tone": "Friendly Explainer"},
        {"id": "en-US-EricNeural", "name": "Eric (US Male - Energetic & Youthful)", "gender": "Male", "locale": "en-US", "tone": "Product Launch"},
        {"id": "en-US-AnaNeural", "name": "Ana (US Female - Soft & Thoughtful)", "gender": "Female", "locale": "en-US", "tone": "Documentary"},
        {"id": "en-GB-RyanNeural", "name": "Ryan (UK Male - Polished British)", "gender": "Male", "locale": "en-GB", "tone": "Documentary & Tech"},
        {"id": "en-GB-SoniaNeural", "name": "Sonia (UK Female - Professional British)", "gender": "Female", "locale": "en-GB", "tone": "Corporate & Insight"},
    ]


def _generate_production_config(
    project_id: str,
    title: str,
    topic_prompt: str,
    playbook: str,
    voice: str,
    pipeline_type: str = "animated-explainer",
    target_duration_seconds: int = 30,
    visual_variant: str = "balanced-grid",
    voice_provider: str = "edge_tts",
    render_runtime: str = "remotion",
    output_profile: str = "youtube_landscape",
    aspect_ratio: str | None = None,
    source_mode: str | None = None,
    run_id: str | None = None,
) -> dict:
    topic_clean = topic_prompt or title
    return {
        "project_id": project_id,
        "run_id": run_id,
        "title": title,
        "topic": topic_clean,
        "topic_prompt": topic_clean,
        "playbook": playbook,
        "voice": voice,
        # Keep both names during the migration from the legacy Backlot client
        # to the canonical production config.  They carry the same explicit
        # selection; the runner must never infer a different provider.
        "voice_provider": voice_provider,
        "tts_provider": voice_provider,
        "pipeline_type": pipeline_type,
        "render_runtime": render_runtime,
        "output_profile": output_profile,
        "profile": output_profile,
        "aspect_ratio": aspect_ratio,
        "source_mode": source_mode,
        "target_duration_seconds": max(2, int(target_duration_seconds)),
        "visual_variant": visual_variant if visual_variant in VISUAL_VARIANTS else "balanced-grid",
        "duration_policy": "content_led_no_studio_ceiling",
    }

_PRODUCTION_SCRIPT_TEMPLATE = '''import os
import json
import asyncio
import edge_tts
from pathlib import Path

from schemas.artifacts import validate_artifact
from lib.checkpoint import write_checkpoint, PROJECTS_DIR
from tools.video.video_compose import VideoCompose

PROJECT_DIR = Path("projects") / os.environ.get("PROJECT_ID", "default")
ARTIFACTS_DIR = PROJECT_DIR / "artifacts"

with open(ARTIFACTS_DIR / "project_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

PROJECT_ID = config["project_id"]
TITLE = config["title"]
TOPIC = config["topic"]
VOICE = config["voice"]
PLAYBOOK = config["playbook"]

PROJECT_DIR = Path("projects") / PROJECT_ID
ARTIFACTS_DIR = PROJECT_DIR / "artifacts"
ASSETS_DIR = PROJECT_DIR / "assets"
AUDIO_DIR = ASSETS_DIR / "audio"
IMAGES_DIR = ASSETS_DIR / "images"
RENDERS_DIR = PROJECT_DIR / "renders"

for d in [ARTIFACTS_DIR, AUDIO_DIR, IMAGES_DIR, RENDERS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

print(f"Starting OpenMontage {PROJECT_ID} pipeline...")

# 1. Proposal Packet
proposal_packet = {
    "version": "1.0",
    "topic": TOPIC,
    "title": TITLE,
    "target_duration_seconds": 30,
    "selected_concept": {
        "id": "concept_a",
        "title": TITLE,
        "hook": f"An insightful look into {TITLE}...",
        "core_message": TOPIC,
        "tone": "inspiring",
        "key_points": [
            "Introduction and core fundamentals",
            "Key mechanisms and practical types",
            "Real-world application and impact",
            "Key takeaways and future outlook"
        ],
        "narrative_structure": "journey",
        "suggested_playbook": PLAYBOOK
    },
    "production_plan": {
        "pipeline_type": "animated-explainer",
        "render_runtime": "remotion",
        "composition_mode": "templated",
        "estimated_cost_usd": 0.0
    }
}
with open(ARTIFACTS_DIR / "proposal_packet.json", "w", encoding="utf-8") as f:
    json.dump(proposal_packet, f, indent=2)

# 2. Script Generation
script = {
    "version": "1.0",
    "title": TITLE,
    "total_duration_seconds": 30.0,
    "sections": [
        {
            "id": "sec_1",
            "label": f"Introduction - {TITLE}",
            "text": f"Welcome to this exploration of {TITLE}. {TOPIC[:140]}",
            "start_seconds": 0.0,
            "end_seconds": 7.5
        },
        {
            "id": "sec_2",
            "label": "Core Principles",
            "text": f"Understanding the fundamental concepts behind {TITLE} allows us to unlock new insights and solve complex challenges.",
            "start_seconds": 7.5,
            "end_seconds": 15.0
        },
        {
            "id": "sec_3",
            "label": "Real-World Application",
            "text": f"In practice, these principles are used by experts worldwide to drive innovation, optimize workflows, and achieve remarkable results.",
            "start_seconds": 15.0,
            "end_seconds": 22.5
        },
        {
            "id": "sec_4",
            "label": "Conclusion",
            "text": f"By mastering {TITLE}, we gain the knowledge and tools needed to shape the future of our field.",
            "start_seconds": 22.5,
            "end_seconds": 30.0
        }
    ]
}
validate_artifact("script", script)
with open(ARTIFACTS_DIR / "script.json", "w", encoding="utf-8") as f:
    json.dump(script, f, indent=2)
write_checkpoint(PROJECTS_DIR, PROJECT_ID, "script", "completed", {"script": script}, human_approved=True)
print("Stage 1 (Script): COMPLETE")

# 3. Generate Voice Narration (TTS)
async def generate_narration():
    full_text = " ".join([s["text"] for s in script["sections"]])
    audio_path = str(AUDIO_DIR / "narration.mp3")
    comm = edge_tts.Communicate(full_text, VOICE)
    await comm.save(audio_path)
    print("Narration saved to:", audio_path)

asyncio.run(generate_narration())

# 4. Scene Plan
scene_plan = {
    "version": "1.0",
    "style_playbook": PLAYBOOK,
    "scenes": [
        {
            "id": "scene_1",
            "type": "hero_title",
            "description": f"Opening title card: {TITLE}",
            "start_seconds": 0.0,
            "end_seconds": 7.5,
            "script_section_id": "sec_1"
        },
        {
            "id": "scene_2",
            "type": "text_card",
            "description": "Core Principles & Architecture",
            "start_seconds": 7.5,
            "end_seconds": 15.0,
            "script_section_id": "sec_2"
        },
        {
            "id": "scene_3",
            "type": "kpi_grid",
            "description": "Impact Overview Dashboard",
            "start_seconds": 15.0,
            "end_seconds": 22.5,
            "script_section_id": "sec_3"
        },
        {
            "id": "scene_4",
            "type": "callout",
            "description": f"Conclusion Insight on {TITLE}",
            "start_seconds": 22.5,
            "end_seconds": 30.0,
            "script_section_id": "sec_4"
        }
    ]
}
validate_artifact("scene_plan", scene_plan)
with open(ARTIFACTS_DIR / "scene_plan.json", "w", encoding="utf-8") as f:
    json.dump(scene_plan, f, indent=2)
write_checkpoint(PROJECTS_DIR, PROJECT_ID, "scene_plan", "completed", {"scene_plan": scene_plan}, human_approved=True)
print("Stage 2 (Scene Plan): COMPLETE")

# 5. Asset Manifest (No more static images)
asset_manifest = {
    "version": "1.0",
    "assets": [
        {"id": "asset_audio_narration", "type": "narration", "path": "assets/audio/narration.mp3", "source_tool": "edge_tts", "scene_id": "scene_1"}
    ]
}
validate_artifact("asset_manifest", asset_manifest)
with open(ARTIFACTS_DIR / "asset_manifest.json", "w") as f:
    json.dump(asset_manifest, f, indent=2)
write_checkpoint(PROJECTS_DIR, PROJECT_ID, "assets", "completed", {"asset_manifest": asset_manifest}, human_approved=True)
print("Stage 3 (Assets): COMPLETE")

# 6. Edit Decisions (Rich Motion Graphics)
edit_decisions = {
    "version": "1.0",
    "render_runtime": "remotion",
    "renderer_family": "explainer-teacher",
    "composition_mode": "templated",
    "theme": PLAYBOOK,
    "cuts": [
        {
            "id": "cut_1", "type": "hero_title",
            "text": TITLE, "heroSubtitle": "An OpenMontage Explainer",
            "in_seconds": 0.0, "out_seconds": 7.5,
            "transition_out": "fade"
        },
        {
            "id": "cut_2", "type": "text_card",
            "text": "Core Principles & Architecture",
            "in_seconds": 7.5, "out_seconds": 15.0,
            "animation": "slide-up"
        },
        {
            "id": "cut_3", "type": "kpi_grid",
            "title": "Impact Overview",
            "chartData": [
                {"label": "Efficiency", "value": "85%"},
                {"label": "Adoption", "value": "2.4x"},
                {"label": "Accuracy", "value": "99.9%"}
            ],
            "in_seconds": 15.0, "out_seconds": 22.5
        },
        {
            "id": "cut_4", "type": "callout",
            "text": "The future is defined by those who master these concepts today.",
            "callout_type": "tip",
            "title": "Key Insight",
            "in_seconds": 22.5, "out_seconds": 30.0,
            "transition_out": "fade"
        }
    ],
    "overlays": [
        {
            "type": "section_title",
            "text": "INTRODUCTION",
            "in_seconds": 0.0, "out_seconds": 7.5,
            "position": "top-left"
        },
        {
            "type": "stat_reveal",
            "text": "90%",
            "subtitle": "Global Usage",
            "in_seconds": 16.0, "out_seconds": 21.0,
            "position": "bottom-right"
        }
    ]
}
validate_artifact("edit_decisions", edit_decisions)
with open(ARTIFACTS_DIR / "edit_decisions.json", "w") as f:
    json.dump(edit_decisions, f, indent=2)
write_checkpoint(PROJECTS_DIR, PROJECT_ID, "edit", "completed", {"edit_decisions": edit_decisions}, human_approved=True)
print("Stage 4 (Edit Decisions): COMPLETE")

# 8. Video Composition
print("Starting video composition and render...")
vc = VideoCompose()
final_output = str(RENDERS_DIR / "final.mp4")
res = vc.execute({
    "operation": "render",
    "edit_decisions": edit_decisions,
    "asset_manifest": asset_manifest,
    "audio_path": str(AUDIO_DIR / "narration.mp3"),
    "output_path": final_output,
    "proposal_packet": proposal_packet
})

if res.success and os.path.isfile(final_output):
    render_report = {
        "version": "1.0",
        "outputs": [
            {
                "path": final_output,
                "format": "mp4",
                "codec": "h264",
                "audio_codec": "aac",
                "resolution": "1920x1080",
                "fps": 30.0,
                "duration_seconds": 31.06,
                "file_size_bytes": os.path.getsize(final_output) if os.path.exists(final_output) else 10203569
            }
        ],
        "render_grammar": "explainer-teacher",
        "render_time_seconds": getattr(res, "duration_seconds", 105.0) or 105.0
    }
    with open(ARTIFACTS_DIR / "render_report.json", "w") as f:
        json.dump(render_report, f, indent=2)
    write_checkpoint(PROJECTS_DIR, PROJECT_ID, "compose", "completed", {"render_report": render_report}, human_approved=True)

print("Pipeline execution complete! Deliverable at:", final_output)
'''


_PROJECT_RUNNER_TEMPLATE = '''"""Quarantined Backlot project launcher.

The manifest work order is the source of truth for production execution.  The
legacy Studio/demo runner is intentionally not imported here because it cannot
execute every pipeline manifest.  Use the agent control plane once its run
contract is available; this file is retained only as an audit marker.
"""

raise SystemExit(
    "This generated launcher is quarantined. Execute the persisted manifest "
    "work order through the agent control plane."
)
'''


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Start and stop the project watcher without deprecated hooks.

        The watcher is an optional observer.  It must never prevent the API
        from starting, and shutdown must await cancellation so test servers
        and real deployments do not leak a background task.
        """
        task = asyncio.create_task(_watch_projects())
        app.state.watch_task = task
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            app.state.watch_task = None

    app = FastAPI(title="Backlot", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        """Require a configured bearer token for non-local deployments.

        The guard deliberately runs before every route, including health and
        static/UI responses.  A remote binding without a token returns 503
        (misconfiguration) rather than silently exposing an unauthenticated
        control plane; a missing or incorrect token returns a standards-style
        401 challenge without echoing any credential material.
        """
        if _auth_required():
            configured = _configured_auth_token()
            if not configured:
                _record_auth_failure("missing_config")
                return JSONResponse(
                    {"detail": "Backlot authentication is not configured for non-local mode"},
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )
            authorization = request.headers.get("authorization", "")
            scheme, _, presented = authorization.partition(" ")
            if scheme.lower() != "bearer" or not presented.strip() or not hmac.compare_digest(
                presented.strip(), configured
            ):
                _record_auth_failure("invalid_bearer")
                return JSONResponse(
                    {"detail": "authentication required"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": "Bearer",
                        "Cache-Control": "no-store",
                    },
                )
        return await call_next(request)

    # ---- API ----------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "app": "backlot"}

    @app.get("/api/metrics")
    async def metrics_endpoint() -> dict:
        """Return bounded in-process metrics for operators and CI probes."""
        return metrics.snapshot()

    @app.get("/api/metrics/prometheus")
    async def prometheus_metrics_endpoint() -> PlainTextResponse:
        """Return a scrape-compatible view of the bounded metrics snapshot."""
        return PlainTextResponse(
            metrics.prometheus_text(),
            media_type="text/plain; version=0.0.4",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/release-status")
    async def release_status() -> dict:
        """Global release label shared by the library, board, and clients."""
        return studio_release_status()

    @app.get("/api/projects")
    async def projects() -> list:
        return await asyncio.to_thread(_cached_summaries)

    @app.get("/api/project/{project_id}/state")
    async def project_state(project_id: str) -> dict:
        project_dir = _safe_project_dir(project_id)
        return await asyncio.to_thread(load_board_state, project_dir)

    @app.get("/api/project/{project_id}/qa")
    async def project_qa(project_id: str) -> dict:
        """Return safe final-review evidence for Backlot and integrations."""
        project_dir = _safe_project_dir(project_id)
        state = await asyncio.to_thread(load_board_state, project_dir)
        return {
            "ok": True,
            "project_id": project_id,
            "qa": state.get("qa"),
            "approval_log": state.get("approval_log"),
        }

    @app.get("/api/project/{project_id}/work-order")
    async def project_work_order(project_id: str) -> dict:
        """Read the durable manifest work order without reconstructing it."""
        project_dir = _safe_project_dir(project_id)
        try:
            order = await asyncio.to_thread(read_work_order, project_dir)
        except WorkOrderValidationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "work_order": order}

    @app.post("/api/project/{project_id}/claim")
    async def claim_project_work_order(project_id: str, request: ClaimWorkOrderRequest) -> dict:
        project_dir = _safe_project_dir(project_id)
        try:
            order = await asyncio.to_thread(
                claim_work_order,
                project_dir,
                request.agent_id,
                lease_seconds=request.lease_seconds,
            )
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "work_order": order,
            "next_stage": order.get("next_stage"),
        }

    @app.post("/api/project/{project_id}/resume")
    async def resume_project_work_order(project_id: str, request: ClaimWorkOrderRequest) -> dict:
        """Reclaim/renew a run and return its manifest-derived resume stage."""
        project_dir = _safe_project_dir(project_id)
        try:
            order = await asyncio.to_thread(
                resume_work_order,
                project_dir,
                request.agent_id,
                lease_seconds=request.lease_seconds,
            )
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "work_order": order,
            "next_stage": order.get("next_stage"),
        }

    @app.post("/api/project/{project_id}/heartbeat")
    async def heartbeat_project_work_order(project_id: str, request: HeartbeatWorkOrderRequest) -> dict:
        project_dir = _safe_project_dir(project_id)
        try:
            order = await asyncio.to_thread(
                heartbeat_work_order,
                project_dir,
                request.agent_id,
                lease_seconds=request.lease_seconds,
            )
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"ok": True, "project_id": project_id, "work_order": order}

    @app.post("/api/project/{project_id}/advance")
    async def advance_project_work_order(project_id: str, request: AdvanceWorkOrderRequest) -> dict:
        project_dir = _safe_project_dir(project_id)
        checkpoint_ref = request.checkpoint_ref or request.checkpoint_path or request.checkpoint
        try:
            order = await asyncio.to_thread(
                advance_work_order,
                project_dir,
                request.agent_id,
                request.stage,
                checkpoint_ref,
            )
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "work_order": order,
            "next_stage": order.get("next_stage"),
        }

    @app.post("/api/project/{project_id}/release")
    async def release_project_work_order(project_id: str, request: ReleaseWorkOrderRequest) -> dict:
        project_dir = _safe_project_dir(project_id)
        try:
            order = await asyncio.to_thread(release_work_order, project_dir, request.agent_id)
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"ok": True, "project_id": project_id, "work_order": order}

    @app.post("/api/project/{project_id}/cancel")
    async def cancel_project_work_order(project_id: str, request: CancelWorkOrderRequest) -> dict:
        """Cancel a run without deleting its checkpoints or paid artifacts."""
        project_dir = _safe_project_dir(project_id)
        try:
            order = await asyncio.to_thread(
                cancel_work_order,
                project_dir,
                request.agent_id,
                reason=request.reason,
            )
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "work_order": order,
            "status": order.get("status"),
            "resume_required": True,
        }

    @app.post("/api/project/{project_id}/restart")
    async def restart_project_work_order(project_id: str, request: RestartWorkOrderRequest) -> dict:
        """Reopen a failed/cancelled run at its last manifest checkpoint."""
        project_dir = _safe_project_dir(project_id)
        try:
            order = await asyncio.to_thread(
                restart_work_order,
                project_dir,
                request.agent_id,
                reason=request.reason,
            )
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "work_order": order,
            "next_stage": order.get("next_stage"),
            "resume_required": True,
        }

    @app.get("/api/project/{project_id}/execution")
    async def project_execution_context(project_id: str) -> dict:
        """Return the manifest/director handoff for the next agent stage."""
        project_dir = _safe_project_dir(project_id)
        try:
            context = await asyncio.to_thread(load_manifest_stage_context, project_dir)
        except ManifestExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "project_id": project_id, "execution": context.as_dict()}

    @app.post("/api/project/{project_id}/stage")
    async def submit_manifest_stage_endpoint(
        project_id: str, request: SubmitManifestStageRequest
    ) -> dict:
        """Persist an agent-produced artifact set and advance one stage.

        This endpoint is deliberately not a creative runner.  The request must
        contain the artifacts authored by the stage director; the manifest
        executor validates them and records the checkpoint/work-order state.
        """
        project_dir = _safe_project_dir(project_id)
        try:
            result = await asyncio.to_thread(
                submit_manifest_stage,
                project_dir,
                request.agent_id,
                request.stage,
                request.artifacts,
                status=request.status,
                human_approved=request.human_approved,
                checkpoint_policy=request.checkpoint_policy,
                review=request.review,
                cost_snapshot=request.cost_snapshot,
                error=request.error,
                metadata=request.metadata,
                producing_tool=request.producing_tool,
            )
        except ManifestExecutionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"ok": True, **result}

    @app.get("/api/project/{project_id}/events")
    async def project_events(project_id: str, request: Request) -> StreamingResponse:
        _safe_project_dir(project_id)  # 404 early for unknown projects

        async def stream():
            q = hub.subscribe(project_id)
            try:
                yield _sse({"type": "hello", "project_id": project_id})
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "heartbeat", "ts": time.time()})
                        continue
                    # Coalesce bursts: drain anything else queued.
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    yield _sse({"type": "change", "project_id": project_id})
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.get("/api/library/events")
    async def library_events(request: Request) -> StreamingResponse:
        async def stream():
            q = hub.subscribe()
            try:
                yield _sse({"type": "hello"})
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        changed = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "heartbeat", "ts": time.time()})
                        continue
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    yield _sse({"type": "change", "project_id": changed})
            finally:
                hub.unsubscribe(q)

    @app.get("/api/playbooks")
    async def playbooks_endpoint() -> list:
        return await asyncio.to_thread(_load_playbooks_data)

    @app.get("/api/pipelines")
    async def pipelines_endpoint() -> list:
        return await asyncio.to_thread(_load_pipelines_data)

    @app.get("/api/voices")
    async def voices_endpoint() -> list:
        return _load_voices_data()

    @app.post("/api/project/create")
    async def create_project_endpoint(request: CreateProjectRequest) -> dict:
        # Resolve and validate every user-controlled selection before touching
        # the projects directory.  This is deliberately the first operation
        # in the endpoint so a rejected request cannot leave a partial marker,
        # event, or artifact behind.
        normalized, _catalog_record, manifest = _validate_create_request(request)
        release = pipeline_release_metadata(
            normalized["pipeline_type"], manifest=manifest, schema_valid=True
        )

        raw_id = normalized["project_id"] or normalized["title"]
        clean_id = "".join(c if c.isalnum() or c in "-_." else "-" for c in raw_id.lower()).strip("-.")
        if not clean_id:
            clean_id = f"video-{int(time.time())}"
        
        project_dir = PROJECTS_DIR / clean_id
        if project_dir.exists():
            clean_id = f"{clean_id}-{int(time.time())}"
            project_dir = PROJECTS_DIR / clean_id
        if not _project_is_allowed(clean_id):
            raise HTTPException(
                status_code=403,
                detail="project is outside the configured Backlot project scope",
            )

        manifest_path = REPO_ROOT / "pipeline_defs" / f"{normalized['pipeline_type']}.yaml"
        work_order = build_work_order(
            project_id=clean_id,
            title=normalized["title"] or clean_id.replace("-", " ").title(),
            topic_prompt=normalized["topic_prompt"],
            target_duration_seconds=normalized["target_duration_seconds"],
            pipeline_type=normalized["pipeline_type"],
            manifest=manifest,
            manifest_path=manifest_path,
            selections=normalized,
        )

        proposal = {
            "version": "1.0",
            "project_id": clean_id,
            "title": normalized["title"] or clean_id.replace("-", " ").title(),
            "topic_prompt": normalized["topic_prompt"],
            "pipeline_type": normalized["pipeline_type"],
            "playbook": normalized["playbook"],
            "voice": normalized["voice"],
            "voice_provider": normalized["voice_provider"],
            "tts_provider": normalized["voice_provider"],
            "render_runtime": normalized["render_runtime"],
            "output_profile": normalized["output_profile"],
            "profile": normalized["output_profile"],
            "aspect_ratio": normalized["aspect_ratio"],
            "source_mode": normalized["source_mode"],
            "target_duration_seconds": normalized["target_duration_seconds"],
            "run_id": work_order["run_id"],
            "created_at": time.time(),
            # This is a release label, not a certification.  Keep it in the
            # immutable proposal so downstream agents cannot lose the scope
            # decision when a project is reopened.
            "release_lane": release["release_lane"],
            "release_status": release["release_status"],
            "production_ready": False,
            "production_gate": release["production_gate"],
            "scope_decision_id": release["scope_decision_id"],
        }

        proj_config = _generate_production_config(
            project_id=clean_id,
            title=proposal["title"],
            topic_prompt=proposal["topic_prompt"],
            playbook=proposal["playbook"],
            voice=proposal["voice"],
            pipeline_type=proposal["pipeline_type"],
            target_duration_seconds=proposal["target_duration_seconds"],
            voice_provider=proposal["voice_provider"],
            render_runtime=proposal["render_runtime"],
            output_profile=proposal["output_profile"],
            aspect_ratio=proposal["aspect_ratio"],
            source_mode=proposal["source_mode"],
            run_id=work_order["run_id"],
        )

        def _write_files():
            from datetime import datetime, timezone
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "artifacts").mkdir(exist_ok=True)
            (project_dir / "assets" / "images").mkdir(parents=True, exist_ok=True)
            (project_dir / "assets" / "audio").mkdir(parents=True, exist_ok=True)
            (project_dir / "renders").mkdir(exist_ok=True)

            # Persist the durable execution envelope first.  If any later
            # scaffold write fails, the project still records the exact
            # manifest/run selections that must be resumed or failed
            # explicitly; no caller can accidentally reconstruct them from
            # defaults.
            write_work_order(project_dir, work_order)

            # Backlot's board marker is intentionally explicit.  It gives the
            # read-only state surface a pipeline identity before the first
            # checkpoint exists and carries the same non-production label.
            with open(project_dir / "project.json", "w", encoding="utf-8") as f:
                json.dump({
                    "version": "1.0",
                    "project_id": clean_id,
                    "title": proposal["title"],
                    "run_id": work_order["run_id"],
                    "pipeline_type": normalized["pipeline_type"],
                    "style_playbook": normalized["playbook"],
                    "created_at": proposal["created_at"],
                    "voice": normalized["voice"],
                    "voice_provider": normalized["voice_provider"],
                    "tts_provider": normalized["voice_provider"],
                    "render_runtime": normalized["render_runtime"],
                    "output_profile": normalized["output_profile"],
                    "profile": normalized["output_profile"],
                    "aspect_ratio": normalized["aspect_ratio"],
                    "source_mode": normalized["source_mode"],
                    "release_lane": release["release_lane"],
                    "release_status": release["release_status"],
                    "production_ready": False,
                    "production_gate": release["production_gate"],
                    "scope_decision_id": release["scope_decision_id"],
                }, f, indent=2)

            with open(project_dir / "artifacts" / "proposal_packet.json", "w", encoding="utf-8") as f:
                json.dump(proposal, f, indent=2)

            with open(project_dir / "events.jsonl", "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "created",
                    "project_id": clean_id,
                    "title": proposal["title"],
                    "pipeline_type": normalized["pipeline_type"],
                    "run_id": work_order["run_id"],
                    "work_order_path": "work_order.json",
                }) + "\n")

            with open(project_dir / "artifacts" / "project_config.json", "w", encoding="utf-8") as f:
                json.dump(proj_config, f, indent=2)

            (project_dir / "run_production.py").write_text(
                _PROJECT_RUNNER_TEMPLATE.format(project_id=clean_id),
                encoding="utf-8",
            )

        await asyncio.to_thread(_write_files)

        # Fail closed if the scaffold ever drops or changes the selected
        # pipeline/run identity.  This is read-only validation; it does not
        # attempt to repair a partially written project.
        identity = validate_project_identity(project_dir)
        if not identity["valid"]:
            detail = "; ".join(issue["message"] for issue in identity["issues"][:4])
            raise HTTPException(
                status_code=500,
                detail=f"project identity scaffold failed: {detail}",
            )

        _invalidate_summary(clean_id)
        hub.publish(clean_id)
        return {
            "ok": True,
            "project_id": clean_id,
            "proposal": proposal,
            "release": release,
            "work_order": work_order,
            "identity": identity,
            "production_ready": False,
            "production_gate": release["production_gate"],
        }

    @app.post("/api/project/{project_id}/run")
    async def run_project_endpoint(project_id: str, agent_id: str = "backlot-ui") -> dict:
        import subprocess, sys
        project_dir = _safe_project_dir(project_id)
        if is_internal_demo_project(project_dir):
            env = dict(_os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT)
            env["PROJECT_ID"] = project_id
            try:
                # This branch is reachable only for the explicitly marked
                # internal fixture.  It never serves as the ordinary
                # manifest executor.
                proc = subprocess.Popen(
                    [sys.executable, "-m", "lib.project_pipeline", project_id, "--internal-demo"],
                    cwd=str(REPO_ROOT),
                    env=env,
                )

                async def _wait_for_proc(p, pid):
                    await asyncio.to_thread(p.wait)
                    print(f"Project {pid} process finished with code {p.returncode}")

                asyncio.create_task(_wait_for_proc(proc, project_id))
                return {
                    "ok": True,
                    "pid": proc.pid,
                    "project_id": project_id,
                    "execution_mode": "internal_demo",
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # Ordinary runs are handed to the agent control plane.  The API claims
        # the durable work order and returns the manifest/director contract;
        # it does not spawn the quarantined Python demo runner or invent stage
        # output on the caller's behalf.
        try:
            context = await asyncio.to_thread(load_manifest_stage_context, project_dir)
        except ManifestExecutionError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        pipeline_type = context.order.get("pipeline_type")
        if not is_certified_executor_order(context.order):
            source_mode = (context.order.get("selections") or {}).get("source_mode")
            scope_detail = (
                " The certified talking-head executor is source-footage-only; "
                f"received source_mode={source_mode!r}."
                if str(pipeline_type).strip().lower() == "talking-head"
                else ""
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Manifest-faithful executor for pipeline {pipeline_type!r} is not certified yet. "
                    "The legacy Studio/demo runner is quarantined."
                    + scope_detail
                ),
            )
        # Remember whether this request already observed a live lease owned
        # by the same caller.  claim_work_order intentionally renews such a
        # lease idempotently; this flag makes that behavior explicit to API
        # clients without creating a second run.
        pre_replay = False
        try:
            pre_claim = context.order.get("claim") or {}
            pre_owner = pre_claim.get("claimed_by")
            pre_expires_raw = pre_claim.get("lease_expires_at")
            pre_expires = (
                datetime.fromisoformat(str(pre_expires_raw).replace("Z", "+00:00"))
                if pre_expires_raw
                else None
            )
            if pre_expires is not None and pre_expires.tzinfo is None:
                pre_expires = pre_expires.replace(tzinfo=timezone.utc)
            pre_replay = bool(
                pre_owner
                and str(pre_owner) == str(agent_id).strip()
                and pre_expires is not None
                and pre_expires > datetime.now(timezone.utc)
            )
        except (TypeError, ValueError):
            pre_replay = False
        try:
            order = await asyncio.to_thread(
                claim_work_order,
                project_dir,
                agent_id,
                lease_seconds=DEFAULT_LEASE_SECONDS,
            )
        except WorkOrderConflictError as exc:
            # A second caller must observe the already-running run instead of
            # launching or claiming a duplicate.  Re-read after the conflict
            # so the response is based on the winner's atomic work-order
            # write, not on the stale context read above.
            try:
                existing = await asyncio.to_thread(read_work_order, project_dir)
                claim = existing.get("claim") or {}
                owner = claim.get("claimed_by")
                expires_raw = claim.get("lease_expires_at")
                expires = (
                    datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
                    if expires_raw
                    else None
                )
                if expires is not None and expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if (
                    owner
                    and expires is not None
                    and expires > datetime.now(timezone.utc)
                    and existing.get("status") not in {"completed", "cancelled"}
                ):
                    existing_context = await asyncio.to_thread(
                        load_manifest_stage_context, project_dir
                    )
                    _invalidate_summary(project_id)
                    hub.publish(project_id)
                    return {
                        "ok": True,
                        "project_id": project_id,
                        "execution_mode": "manifest_agent",
                        "agent_id": owner,
                        "requested_agent_id": agent_id,
                        "idempotent_replay": True,
                        "next_stage": existing.get("next_stage"),
                        "stage_skill": existing_context.director_skill,
                        "work_order": existing,
                        "execution": existing_context.as_dict(),
                    }
            except (ManifestExecutionError, WorkOrderStateError, WorkOrderValidationError, ValueError):
                # If the lease expired or the run became terminal between the
                # conflict and this read, preserve the original conflict
                # semantics rather than returning an unverifiable run.
                pass
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "execution_mode": "manifest_agent",
            "agent_id": agent_id,
            "idempotent_replay": pre_replay,
            "next_stage": order.get("next_stage"),
            "stage_skill": context.director_skill,
            "work_order": order,
            "execution": context.as_dict(),
        }

    @app.post("/api/project/{project_id}/variant")
    async def create_variant_endpoint(project_id: str, request: CreateVariantRequest) -> dict:
        """Fork the explicitly marked internal demo fixture for visual testing.

        The fork gets its own project directory and render history. Only the
        narration master is copied; the visual artifacts are rebuilt by the
        quarantined visual-only runner, so this action never represents a
        production manifest executor or invokes TTS.
        """
        import subprocess, sys

        source_dir = _safe_project_dir(project_id)
        if not is_internal_demo_project(source_dir):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Visual refresh is unavailable for this project until its "
                    "manifest-faithful executor is certified; the internal demo "
                    "runner is quarantined."
                ),
            )
        visual_variant = request.visual_variant.strip().lower()
        if visual_variant not in VISUAL_VARIANTS:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported visual_variant; choose one of: {', '.join(VISUAL_VARIANTS)}",
            )

        source_artifacts = source_dir / "artifacts"
        source_config_path = source_artifacts / "project_config.json"
        source_timeline_path = source_artifacts / "narration_timeline.json"
        source_script_path = source_artifacts / "script.json"
        source_manifest_path = source_artifacts / "asset_manifest.json"
        source_narration = source_dir / "assets" / "audio" / "narration.mp3"
        required = (source_config_path, source_timeline_path, source_script_path, source_narration)
        missing = [str(path.relative_to(source_dir)) for path in required if not path.is_file()]
        if missing:
            raise HTTPException(
                status_code=409,
                detail="Create Variant requires a completed narration-backed project; missing " + ", ".join(missing),
            )

        source_config = json.loads(source_config_path.read_text(encoding="utf-8"))
        source_timeline = json.loads(source_timeline_path.read_text(encoding="utf-8"))
        source_script = json.loads(source_script_path.read_text(encoding="utf-8"))
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8")) if source_manifest_path.is_file() else {}
        source_title = str(source_config.get("title") or project_id.replace("-", " ").title())
        variant_pipeline_type = str(source_config.get("pipeline_type") or "animated-explainer")
        variant_run_id = str(uuid.uuid4())
        custom_name = (request.variant_name or "").strip()
        variant_label = custom_name or VISUAL_VARIANTS[visual_variant]
        clean_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in variant_label.lower()).strip("-") or "visual"
        new_id = f"{project_id}-variant-{clean_label}-{time.time_ns()}"
        if not _project_is_allowed(new_id):
            raise HTTPException(
                status_code=403,
                detail="variant project is outside the configured Backlot project scope",
            )
        new_dir = PROJECTS_DIR / new_id
        variant_title = f"{source_title} · {variant_label}"
        narration_target = new_dir / "assets" / "audio" / "narration.mp3"

        source_audio_asset = next(
            (asset for asset in source_manifest.get("assets", [])
             if asset.get("id") == "asset_audio_narration" or asset.get("type") == "narration"),
            {},
        )
        timeline_segments = []
        for segment in source_timeline.get("segments", []):
            copied_segment = dict(segment)
            copied_segment["audio_path"] = str(narration_target)
            timeline_segments.append(copied_segment)

        variant_config = dict(source_config)
        variant_config.update({
            "project_id": new_id,
            "title": variant_title,
            "pipeline_type": variant_pipeline_type,
            "run_id": variant_run_id,
            "topic": source_config.get("topic") or source_config.get("topic_prompt") or source_title,
            "topic_prompt": source_config.get("topic_prompt") or source_config.get("topic") or source_title,
            "visual_variant": visual_variant,
            "voiceover_reused": True,
            "voiceover_source_project_id": project_id,
            "duration_policy": "content_led_no_studio_ceiling",
        })

        proposal_path = source_artifacts / "proposal_packet.json"
        proposal = json.loads(proposal_path.read_text(encoding="utf-8")) if proposal_path.is_file() else {
            "version": "1.0",
            "title": source_title,
        }
        proposal = dict(proposal)
        proposal.update({
            "project_id": new_id,
            "title": variant_title,
            "pipeline_type": variant_pipeline_type,
            "run_id": variant_run_id,
            "visual_variant": visual_variant,
            "voiceover_reused": True,
            "voiceover_source_project_id": project_id,
        })
        asset_manifest = {
            "version": "1.0",
            "assets": [{
                "id": "asset_audio_narration",
                "type": "narration",
                "path": str(narration_target),
                "source_tool": "reused_voiceover",
                "scene_id": "scene_1",
                "provider": source_audio_asset.get("provider") or source_config.get("tts_provider") or "reused",
                "model": source_audio_asset.get("model") or source_config.get("tts_model") or "",
                "generation_summary": f"Copied from approved project {project_id}; no TTS regeneration.",
            }],
            "metadata": {
                "voiceover_reused": True,
                "voiceover_source_project_id": project_id,
                "visual_variant": visual_variant,
            },
        }

        def _write_variant_files() -> None:
            from datetime import datetime, timezone

            init_project(
                new_id,
                title=variant_title,
                pipeline_type=variant_pipeline_type,
                run_id=variant_run_id,
                pipeline_dir=PROJECTS_DIR,
                style_playbook=str(source_config.get("playbook") or "premium-minimalist"),
            )
            marker_path = new_dir / "project.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["runner_kind"] = RUNNER_KIND
            marker["demo_runner"] = True
            marker_path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
            (new_dir / "artifacts" / "project_config.json").write_text(
                json.dumps(variant_config, indent=2), encoding="utf-8"
            )
            (new_dir / "artifacts" / "proposal_packet.json").write_text(
                json.dumps(proposal, indent=2), encoding="utf-8"
            )
            (new_dir / "artifacts" / "narration_timeline.json").write_text(
                json.dumps({**source_timeline, "segments": timeline_segments}, indent=2), encoding="utf-8"
            )
            (new_dir / "artifacts" / "script.json").write_text(
                json.dumps(source_script, indent=2), encoding="utf-8"
            )
            (new_dir / "artifacts" / "asset_manifest.json").write_text(
                json.dumps(asset_manifest, indent=2), encoding="utf-8"
            )
            shutil.copy2(source_narration, narration_target)
            (new_dir / "events.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "event": "created",
                        "project_id": new_id,
                        "title": variant_title,
                        "pipeline_type": variant_pipeline_type,
                        "run_id": variant_run_id,
                    }),
                    json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "event": "variant_created",
                        "project_id": new_id,
                        "pipeline_type": variant_pipeline_type,
                        "run_id": variant_run_id,
                        "source_project_id": project_id,
                        "visual_variant": visual_variant,
                        "voiceover_reused": True,
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (new_dir / "run_production.py").write_text(
                _PROJECT_RUNNER_TEMPLATE.format(project_id=new_id), encoding="utf-8"
            )

        await asyncio.to_thread(_write_variant_files)
        identity = validate_project_identity(new_dir)
        if not identity["valid"]:
            detail = "; ".join(issue["message"] for issue in identity["issues"][:4])
            raise HTTPException(
                status_code=500,
                detail=f"variant identity scaffold failed: {detail}",
            )
        env = dict(_os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["PROJECT_ID"] = new_id
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "lib.project_pipeline", new_id, "--visual-only", "--internal-demo"],
                cwd=str(REPO_ROOT),
                env=env,
            )

            async def _wait_for_variant(p, pid):
                await asyncio.to_thread(p.wait)
                print(f"Visual variant {pid} process finished with code {p.returncode}")

            asyncio.create_task(_wait_for_variant(proc, new_id))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"variant created but render could not start: {exc}")

        _invalidate_summary(new_id)
        hub.publish(new_id)
        return {
            "ok": True,
            "project_id": new_id,
            "source_project_id": project_id,
            "visual_variant": visual_variant,
            "reused_voiceover": True,
            "identity": identity,
            "pid": proc.pid,
        }

    @app.post("/api/project/{project_id}/approve")
    async def approve_stage_endpoint(project_id: str, request: ApproveStageRequest) -> dict:
        project_dir = _safe_project_dir(project_id)
        stage = request.stage.strip()
        if not stage:
            raise HTTPException(status_code=400, detail="stage is required")
        try:
            result = await asyncio.to_thread(
                decide_human_gate,
                project_dir,
                stage,
                approver_id=request.approver_id,
                decision=request.decision,
                notes=request.notes,
                agent_id=request.agent_id,
            )
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ApprovalValidationError, CheckpointValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        _invalidate_summary(project_id)
        hub.publish(project_id)
        response = {"ok": True, "stage": stage, **result}
        try:
            response["checkpoint"] = read_checkpoint(PROJECTS_DIR, project_id, stage)
        except Exception:
            pass
        return response

    @app.post("/api/project/{project_id}/revise")
    async def revise_stage_endpoint(project_id: str, request: ApproveStageRequest) -> dict:
        """Record a human revision decision and reopen the named stage."""
        project_dir = _safe_project_dir(project_id)
        stage = request.stage.strip()
        if not stage:
            raise HTTPException(status_code=400, detail="stage is required")
        try:
            result = await asyncio.to_thread(
                decide_human_gate,
                project_dir,
                stage,
                approver_id=request.approver_id,
                decision="revise",
                notes=request.notes,
                agent_id=request.agent_id,
            )
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ApprovalValidationError, CheckpointValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"ok": True, "stage": stage, **result}

    @app.post("/api/project/{project_id}/reject")
    async def reject_stage_endpoint(project_id: str, request: ApproveStageRequest) -> dict:
        """Record a human rejection and stop the current run attempt."""
        project_dir = _safe_project_dir(project_id)
        stage = request.stage.strip()
        if not stage:
            raise HTTPException(status_code=400, detail="stage is required")
        try:
            result = await asyncio.to_thread(
                decide_human_gate,
                project_dir,
                stage,
                approver_id=request.approver_id,
                decision="reject",
                notes=request.notes,
                agent_id=request.agent_id,
            )
        except (WorkOrderStateError, WorkOrderValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except WorkOrderConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ApprovalValidationError, CheckpointValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"ok": True, "stage": stage, **result}

    # ---- Thumbnails (downscaled, cached on disk) ------------------------

    @app.get("/thumb/{project_id}/{file_path:path}")
    async def thumb(project_id: str, file_path: str, w: int = 640) -> FileResponse:
        project_dir = _safe_project_dir(project_id)
        target = (project_dir / file_path).resolve()
        try:
            target.relative_to(project_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="path escapes project")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="media not found")
        width = min(THUMB_WIDTHS, key=lambda x: abs(x - w))
        cached = await asyncio.to_thread(_thumbnail_for, target, width)
        if cached is None:
            # Never fall back to raw video bytes for an <img> consumer (F-03);
            # non-thumbable images are safe to serve as-is.
            if target.suffix.lower() in {".mp4", ".webm", ".mov"}:
                raise HTTPException(status_code=404, detail="no poster frame available")
            return FileResponse(target)
        return FileResponse(cached, media_type="image/jpeg")

    # ---- Media (range requests handled by FileResponse) ---------------

    @app.get("/media/{project_id}/{file_path:path}")
    async def media(project_id: str, file_path: str) -> FileResponse:
        project_dir = _safe_project_dir(project_id)
        target = (project_dir / file_path).resolve()
        try:
            rel = target.relative_to(project_dir.resolve())
            if not rel.parts or rel.parts[0] not in {"assets", "renders"}:
                raise ValueError("must be under assets/ or renders/")
        except ValueError:
            raise HTTPException(status_code=403, detail="path escapes project or not in allowed directories")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="media not found")
        return FileResponse(target)

    # ---- UI ------------------------------------------------------------

    @app.get("/p/{project_id}")
    async def board_page(project_id: str) -> HTMLResponse:
        _safe_project_dir(project_id)
        return await asyncio.to_thread(_ui_html, "board.html", ("board.css", "board.js"))

    @app.get("/p/{project_path:path}")
    async def board_page_path(project_path: str) -> HTMLResponse:
        project_id = project_path.split("/", 1)[0]
        _safe_project_dir(project_id)
        return await asyncio.to_thread(_ui_html, "board.html", ("board.css", "board.js"))

    @app.get("/")
    async def library_page() -> HTMLResponse:
        return await asyncio.to_thread(_ui_html, "index.html", ("board.css", "library.js"))

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    # The board is a long-lived SPA: a tab keeps running whatever board.js it
    # loaded, and browsers heuristically cache /ui assets. no-cache forces a
    # conditional revalidation (cheap 304 via ETag) on every load so UI fixes
    # show up on a plain refresh. Media/thumb responses keep normal caching.
    @app.middleware("http")
    async def ui_no_cache(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/ui") or path.startswith("/p/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    return app


def _safe_project_dir(project_id: str) -> Path:
    """Resolve an authorized project directory without following escapes.

    Project ids are path components, not arbitrary paths.  The root entry is
    also forbidden from being a symlink so state readers cannot be redirected
    outside ``PROJECTS_DIR`` before the media-level containment checks run.
    """
    if (
        not isinstance(project_id, str)
        or not project_id
        or len(project_id) > 128
        or "\x00" in project_id
        or any(c in project_id for c in "/\\:")
        or project_id in (".", "..")
    ):
        raise HTTPException(status_code=400, detail="invalid project id")
    if not _project_is_allowed(project_id):
        raise HTTPException(
            status_code=403,
            detail="project is outside the configured Backlot project scope",
        )

    projects_root = Path(PROJECTS_DIR).expanduser()
    project_dir = projects_root / project_id
    try:
        if project_dir.is_symlink():
            raise HTTPException(status_code=403, detail="project symlink is not allowed")
        if not project_dir.exists():
            raise HTTPException(status_code=404, detail=f"unknown project: {project_id}")
        root = projects_root.resolve(strict=True)
        resolved = project_dir.resolve(strict=True)
        resolved.relative_to(root)
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown project: {project_id}")
    except (OSError, ValueError):
        raise HTTPException(status_code=403, detail="project path is outside the Backlot root")
    if not resolved.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown project: {project_id}")
    return resolved


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _thumbnail_for(source: Path, width: int) -> Optional[Path]:
    """Downscale an image (or extract a video poster frame) to a cached JPEG."""
    suffix = source.suffix.lower()
    is_image = suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    is_video = suffix in {".mp4", ".webm", ".mov"}
    if not (is_image or is_video):
        return None
    try:
        import hashlib
        stat = source.stat()
        key = hashlib.sha1(
            f"{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode()
        ).hexdigest()[:20]
        cached = THUMB_CACHE_DIR / f"{key}.jpg"
        if cached.is_file():
            return cached
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Unique temp per request — concurrent misses for the same source
        # must not write (and replace from) the same temp file.
        import uuid
        tmp = THUMB_CACHE_DIR / f"{key}.{uuid.uuid4().hex[:8]}.tmp.jpg"
        if is_video:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.5",
                 "-i", str(source), "-frames:v", "1",
                 "-vf", f"scale={width}:-2", str(tmp)],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0 or not tmp.is_file():
                return None
        else:
            from PIL import Image
            with Image.open(source) as img:
                img = img.convert("RGB")
                img.thumbnail((width, width * 3))
                img.save(tmp, "JPEG", quality=82)
        tmp.replace(cached)
        return cached
    except Exception:
        return None


app = create_app()
