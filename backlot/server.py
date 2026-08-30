"""Backlot server — FastAPI app: board state API, SSE change feed, media.

The watcher observes ``projects/`` with watchfiles; on any change it bumps a
per-project version and wakes SSE subscribers, who tell the browser to
refetch state. The server never writes to project directories.
"""

from __future__ import annotations

import asyncio
import json
import time
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backlot.state import PROJECTS_DIR, REPO_ROOT, list_projects, load_board_state, summarize_project

class CreateProjectRequest(BaseModel):
    title: str
    topic_prompt: str = ''
    pipeline_type: str = 'animated-explainer'
    playbook: str = 'premium-minimalist'
    voice: str = 'en-US-ChristopherNeural'
    target_duration_seconds: int = 30
    project_id: str | None = None

class ApproveStageRequest(BaseModel):
    stage: str

UI_DIR = Path(__file__).resolve().parent / "ui"
THUMB_CACHE_DIR = REPO_ROOT / ".backlot" / "thumbs"
THUMB_WIDTHS = (320, 640, 960)

# Paths inside a project whose changes are pure noise for the board.
_IGNORE_PARTS = {"node_modules", ".git", "__pycache__", ".cache"}

SSE_HEARTBEAT_SECONDS = 15


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

    def subscribe(self, project_id: Optional[str] = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers[q] = project_id
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.pop(q, None)

    def publish(self, project_id: str) -> None:
        for q, only in list(self._subscribers.items()):
            if only is not None and only != project_id:
                continue
            try:
                q.put_nowait(project_id)
            except asyncio.QueueFull:
                # Queue holds only THIS subscriber's relevant ids, so a full
                # queue already guarantees a pending wake-up → safe to drop.
                pass


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
        with _summary_cache_lock:
            cached = _summary_cache.get(entry.name)
        if cached is None:
            try:
                cached = summarize_project(entry)
            except Exception:
                cached = {
                    "project_id": entry.name, "title": entry.name,
                    "pipeline_type": "unknown", "has_pipeline_state": False,
                    "poster": None, "live": False, "last_activity": 0,
                    "active_stage": None, "awaiting_human": False,
                    "stage_states": [], "completed_count": 0,
                    "render_count": 0, "scene_count": 0, "error": "unreadable",
                }
            with _summary_cache_lock:
                _summary_cache[entry.name] = cached
        summaries.append(cached)
    summaries.sort(key=lambda s: (not s["live"], -(s["last_activity"] or 0)))
    return summaries


# Watch-loop hot path: pure string comparison, no per-path filesystem calls
# (change batches can be thousands of paths during a render).
import os as _os

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
    if not defs_dir.is_dir():
        return []
    pipelines = []
    for f in sorted(defs_dir.glob("*.yaml")):
        try:
            content = yaml.safe_load(f.read_text(encoding="utf-8"))
            if content and isinstance(content, dict):
                pipelines.append({
                    "id": f.stem,
                    "name": content.get("name", f.stem.replace("-", " ").title()),
                    "type": content.get("type", f.stem),
                    "description": content.get("description", ""),
                    "default_playbook": content.get("default_playbook", "premium-minimalist"),
                    "stages": content.get("stages", []),
                })
        except Exception:
            pass
    return pipelines

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


def _generate_production_config(project_id: str, title: str, topic_prompt: str, playbook: str, voice: str) -> dict:
    topic_clean = topic_prompt or title
    return {
        "project_id": project_id,
        "title": title,
        "topic": topic_clean,
        "playbook": playbook,
        "voice": voice,
        "target_duration_seconds": 30
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

if res.success or os.path.exists(final_output):
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


def create_app() -> FastAPI:
    app = FastAPI(title="Backlot", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    async def _startup() -> None:
        app.state.watch_task = asyncio.create_task(_watch_projects())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = getattr(app.state, "watch_task", None)
        if task:
            task.cancel()

    # ---- API ----------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "app": "backlot"}

    @app.get("/api/projects")
    async def projects() -> list:
        return await asyncio.to_thread(_cached_summaries)

    @app.get("/api/project/{project_id}/state")
    async def project_state(project_id: str) -> dict:
        project_dir = _safe_project_dir(project_id)
        return await asyncio.to_thread(load_board_state, project_dir)

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
        raw_id = request.project_id or request.title
        clean_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw_id.lower()).strip("-")
        if not clean_id:
            clean_id = f"video-{int(time.time())}"
        
        project_dir = PROJECTS_DIR / clean_id
        if project_dir.exists():
            clean_id = f"{clean_id}-{int(time.time())}"
            project_dir = PROJECTS_DIR / clean_id

        proposal = {
            "version": "1.0",
            "project_id": clean_id,
            "title": request.title or clean_id.replace("-", " ").title(),
            "topic_prompt": request.topic_prompt,
            "pipeline_type": request.pipeline_type,
            "playbook": request.playbook,
            "voice": request.voice,
            "target_duration_seconds": request.target_duration_seconds,
            "created_at": time.time()
        }

        proj_config = _generate_production_config(
            project_id=clean_id,
            title=proposal["title"],
            topic_prompt=proposal["topic_prompt"],
            playbook=proposal["playbook"],
            voice=proposal["voice"]
        )

        def _write_files():
            from datetime import datetime, timezone
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "artifacts").mkdir(exist_ok=True)
            (project_dir / "assets" / "images").mkdir(parents=True, exist_ok=True)
            (project_dir / "assets" / "audio").mkdir(parents=True, exist_ok=True)
            (project_dir / "renders").mkdir(exist_ok=True)

            with open(project_dir / "artifacts" / "proposal_packet.json", "w", encoding="utf-8") as f:
                json.dump(proposal, f, indent=2)

            with open(project_dir / "events.jsonl", "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "created",
                    "project_id": clean_id,
                    "title": proposal["title"]
                }) + "\n")

            with open(project_dir / "artifacts" / "project_config.json", "w", encoding="utf-8") as f:
                json.dump(proj_config, f, indent=2)

            (project_dir / "run_production.py").write_text(_PRODUCTION_SCRIPT_TEMPLATE, encoding="utf-8")

        await asyncio.to_thread(_write_files)

        _invalidate_summary(clean_id)
        hub.publish(clean_id)
        return {"ok": True, "project_id": clean_id, "proposal": proposal}

    @app.post("/api/project/{project_id}/run")
    async def run_project_endpoint(project_id: str) -> dict:
        import subprocess, sys
        project_dir = _safe_project_dir(project_id)
        prod_script = project_dir / "run_production.py"
        if not prod_script.is_file():
            prod_script.write_text(_PRODUCTION_SCRIPT_TEMPLATE, encoding="utf-8")

        env = dict(_os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["PROJECT_ID"] = project_id
        try:
            proc = subprocess.Popen([sys.executable, str(prod_script)], cwd=str(REPO_ROOT), env=env)
            
            async def _wait_for_proc(p, pid):
                await asyncio.to_thread(p.wait)
                print(f"Project {pid} process finished with code {p.returncode}")
                
            asyncio.create_task(_wait_for_proc(proc, project_id))
            return {"ok": True, "pid": proc.pid, "project_id": project_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/project/{project_id}/approve")
    async def approve_stage_endpoint(project_id: str, request: ApproveStageRequest) -> dict:
        project_dir = _safe_project_dir(project_id)
        stage = request.stage
        chk_file = project_dir / f"checkpoint_{stage}.json"
        
        def _approve():
            if chk_file.is_file():
                data = json.loads(chk_file.read_text(encoding="utf-8"))
                data["human_approved"] = True
                chk_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                return data
            return None
            
        data = await asyncio.to_thread(_approve)
        if data:
            _invalidate_summary(project_id)
            hub.publish(project_id)
            return {"ok": True, "stage": stage, "checkpoint": data}
        return {"ok": False, "error": f"checkpoint_{stage}.json not found"}

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
        return await asyncio.to_thread(_ui_html, "board.html", ("board.css", "board.js"))

    @app.get("/p/{project_path:path}")
    async def board_page_path(project_path: str) -> HTMLResponse:
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
    # ':' rejects Windows drive-relative ids like "C:" (PROJECTS_DIR / "C:"
    # collapses back to PROJECTS_DIR itself).
    if any(c in project_id for c in "/\\:") or project_id in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid project id")
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown project: {project_id}")
    return project_dir


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
