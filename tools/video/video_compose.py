"""Video composition tool — FFmpeg + Remotion + HyperFrames (runtime-aware).

Pipeline-facing orchestration surface for composition. Takes `edit_decisions`,
`asset_manifest`, and audio, and delegates to the technical runtime chosen
at proposal stage.

Routing is driven by `edit_decisions.render_runtime` (locked at proposal):

- `remotion`   → React-based frame-accurate render via `npx remotion render`.
                 Handles the existing scene-component stack, word-level captions,
                 TalkingHead/CinematicRenderer. Current default.
- `hyperframes` → HTML/CSS/GSAP render via `hyperframes_compose`.
                 Handles kinetic typography, product promos, website-to-video,
                 registry blocks. Added in the parallel-runtime initiative.
- `ffmpeg`     → FFmpeg concat/trim. Used only for simple video cuts without
                 composition, or when the approved path explicitly names FFmpeg.

Authoring mode is orthogonal to runtime. Setting
`edit_decisions.composition_mode = "atelier"` (or `renderer_family="bespoke"`)
means the composition is hand-authored rather than assembled from stock scene
components. Runtime still wins first: HyperFrames atelier routes through
`hyperframes_compose`, FFmpeg stays FFmpeg-only, and only Remotion atelier uses
`_render_via_atelier` for a project-local Remotion entry that bypasses the
cut-schema and stock scene-type registry.

Silent runtime swaps are forbidden by governance. If the chosen runtime is
unavailable or fails, this tool surfaces a structured blocker and waits for
the agent to re-ask the user rather than substituting a different engine.
"""

from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from lib.media_contracts import MediaContractError, strict_bool
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ResumeSupport,
    ToolResult,
    ToolStability,
    ToolTier,
)
from lib.video_timeline import (
    validate_narration_timeline,
    validate_visual_timeline,
)


class VideoCompose(BaseTool):
    name = "video_compose"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe"]
    install_instructions = "Install FFmpeg (including ffprobe): https://ffmpeg.org/download.html"
    agent_skills = ["remotion-best-practices", "remotion", "ffmpeg"]

    capabilities = [
        "compose_cuts",
        "burn_subtitles",
        "overlay_assets",
        "encode_profile",
        "remotion_render",
    ]

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["compose", "render", "remotion_render", "burn_subtitles", "overlay", "encode"],
                "description": (
                    "compose: low-level concat cuts + audio + subtitles. "
                    "render: high-level — resolves asset IDs, auto-routes to Remotion "
                    "for images/animations or FFmpeg for video-only. Preferred for compose-director. "
                    "remotion_render: render via Remotion (Node.js). "
                    "burn_subtitles: burn subtitle file into existing video. "
                    "overlay: composite overlays onto base video. "
                    "encode: re-encode to a target profile/codec."
                ),
            },
            "input_path": {"type": "string"},
            "output_path": {"type": "string"},
            "project_dir": {
                "type": "string",
                "description": (
                    "Project workspace containing project.json/work_order.json. "
                    "Manifest asset, audio, and subtitle paths are resolved "
                    "relative to this directory."
                ),
            },
            "edit_decisions": {
                "type": "object",
                "description": "Full edit_decisions artifact (required for compose/render)",
            },
            "asset_manifest": {
                "type": "object",
                "description": (
                    "Full asset_manifest artifact (required for render). "
                    "Used to resolve asset IDs in cuts[].source to file paths."
                ),
            },
            "proposal_packet": {
                "type": "object",
                "description": (
                    "Full proposal_packet artifact. Optional but STRONGLY "
                    "recommended — when present, final_review compares "
                    "proposal_packet.production_plan.render_runtime against "
                    "edit_decisions.render_runtime and flags runtime_swap_detected. "
                    "Without it, runtime-swap detection falls back to checking "
                    "edit_decisions.metadata.proposal_render_runtime."
                ),
            },
            "narration_transcript_path": {
                "type": "string",
                "description": (
                    "Path to a word-level transcript JSON (from `transcriber` "
                    "tool output). Optional but STRONGLY recommended: when "
                    "combined with script_path/script_text, final_review "
                    "runs transcript_comparison and catches TTS failures "
                    "like 'Chirp3-HD reads ... as the word dot'. Without "
                    "it, content-level audio bugs ship silently."
                ),
            },
            "script_path": {
                "type": "string",
                "description": (
                    "Path to the source narration script (plain text). "
                    "Used by transcript_comparison to diff against the "
                    "transcribed audio. Provide this OR script_text."
                ),
            },
            "script_text": {
                "type": "string",
                "description": (
                    "Inline source narration script. Used by "
                    "transcript_comparison when a file path is unavailable."
                ),
            },
            "subtitle_path": {"type": "string"},
            "captions": {
                "type": "array",
                "description": "Neutral caption cues with start/end/text for runtime certification.",
            },
            "transcript": {
                "type": "object",
                "description": "Canonical verified narration transcript used to derive captions.",
            },
            "require_verified_transcript": {
                "type": "boolean",
                "default": False,
                "description": "Fail closed unless transcript approval/verification is explicit.",
            },
            "expected_language": {"type": "string"},
            "expected_text": {"type": "string"},
            "caption_mode": {
                "type": "string",
                "enum": ["burn_in", "sidecar"],
                "default": "burn_in",
                "description": "Burn captions into the video or retain a sidecar for packaging.",
            },
            "safe_area": {"type": "object"},
            "max_lines": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "max_chars_per_line": {"type": "integer", "default": 42, "minimum": 1, "maximum": 200},
            "subtitle_style": {
                "type": "object",
                "description": "ASS subtitle styling. Also extracted from edit_decisions.subtitles if not provided.",
                "properties": {
                    "font": {"type": "string", "default": "Arial"},
                    "font_size": {"type": "integer", "default": 24},
                    "primary_color": {"type": "string", "default": "&HFFFFFF"},
                    "outline_color": {"type": "string", "default": "&H000000"},
                    "outline_width": {"type": "number", "default": 2},
                    "margin_v": {"type": "integer", "default": 40},
                    "alignment": {"type": "integer", "default": 2},
                },
            },
            "overlays": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "asset_path": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                        "start_seconds": {"type": "number"},
                        "end_seconds": {"type": "number"},
                        "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
            "audio_path": {"type": "string", "description": "Mixed audio to mux into output"},
            "profile": {
                "type": "string",
                "description": (
                    "Media profile name from media_profiles.py "
                    "(e.g. youtube_landscape, tiktok, instagram_reels). "
                    "Applied in render and encode operations."
                ),
            },
            "options": {
                "type": "object",
                "description": "Render options (used by the render operation)",
                "properties": {
                    "subtitle_burn": {"type": "boolean", "default": True},
                    "two_pass_encode": {"type": "boolean", "default": False},
                },
            },
            "codec": {"type": "string", "default": "libx264"},
            "crf": {"type": "integer", "default": 23},
            # ``fast`` keeps CRF quality while meeting the interactive local
            # render SLO on the supported reference workload. Callers may
            # explicitly request ``medium``/``slow`` for a quality-size trade.
            "preset": {"type": "string", "default": "fast"},
            "remotion_timeout_ms": {
                "type": "integer",
                "description": (
                    "Remotion render timeout in milliseconds, passed through as "
                    "`--timeout` (governs headless-browser setup and delayRender). "
                    "Raise this when the browser is slow to start (e.g. restricted "
                    "networks). The subprocess timeout is widened to match."
                ),
            },
            "render_timeout_seconds": {
                "type": "integer",
                "description": (
                    "Overall Remotion subprocess timeout in seconds. This is a "
                    "render-process safety timeout, not a video-duration limit."
                ),
            },
            "render_concurrency": {
                "type": "integer",
                "description": (
                    "Optional Remotion worker request. Production policy caps or "
                    "reduces it from the machine/resource profile; it is never "
                    "passed through as an unbounded worker count."
                ),
            },
            "workers": {
                "type": "integer",
                "description": "Compatibility alias for render_concurrency.",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=4, ram_mb=2048, vram_mb=0, disk_mb=5000, network_required=False
    )

    # Remotion scene types that trigger React-based rendering
    _REMOTION_COMPONENTS = [
        "text_card", "stat_card", "callout", "comparison",
        "progress", "chart", "bar_chart", "line_chart", "pie_chart", "kpi_grid",
    ]

    best_for = [
        "Final render for explainer and animation pipelines",
        "Image-to-video with spring animations (Remotion)",
        "Animated text cards, stat cards, charts (Remotion)",
        "Complex transitions between scenes (Remotion)",
        "Pure video concat and trim (FFmpeg)",
    ]
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["Conversion failed"])
    resume_support = ResumeSupport.FROM_START
    idempotency_key_fields = ["operation", "input_path", "edit_decisions"]
    side_effects = ["writes video file to output_path"]
    user_visible_verification = [
        "Play the composed output and verify cuts, subtitles, and overlays",
    ]

    def _remotion_available(self) -> bool:
        """Check if Remotion rendering is available (requires npx + composer project + node_modules)."""
        import shutil as _shutil

        if not _shutil.which("npx"):
            return False
        composer_dir = Path(__file__).resolve().parent.parent.parent / "remotion-composer"
        if not composer_dir.exists() or not (composer_dir / "package.json").exists():
            return False
        # Check that node_modules are actually installed — without this,
        # npx remotion render will fail even though the project exists.
        if not (composer_dir / "node_modules").exists():
            return False
        return True

    def _ffmpeg_available(self) -> bool:
        """Check if the ffmpeg binary is actually resolvable on PATH."""
        import shutil as _shutil

        return bool(_shutil.which("ffmpeg"))

    def _hyperframes_available(self) -> bool:
        """Check if HyperFrames rendering is available.

        Delegates to the dedicated tool so the availability check stays in
        one place (node 22 floor, ffmpeg + npx on PATH).
        """
        try:
            from tools.video.hyperframes_compose import HyperFramesCompose
            return bool(HyperFramesCompose()._runtime_check()["runtime_available"])
        except Exception:
            return False

    def get_info(self, *, include_status: bool = True) -> dict[str, Any]:
        """Extend base get_info to surface all available render runtimes.

        Preflight reports each runtime's availability separately so the agent
        can choose an appropriate `render_runtime` at proposal stage. Silent
        fallback between runtimes is forbidden.
        """
        info = super().get_info(include_status=include_status)
        ffmpeg_ok = self._ffmpeg_available()
        remotion_ok = self._remotion_available()
        # The full HyperFrames check includes an npm registry lookup.  Fast
        # catalog/preflight calls report the local prerequisite only and defer
        # package reachability to explicit deep diagnostics.
        hyperframes_ok = self._hyperframes_available() if include_status else False
        info["render_engines"] = {
            "ffmpeg": ffmpeg_ok,
            "remotion": remotion_ok,
            "hyperframes": hyperframes_ok,
        }
        # Backwards-compat alias — some proposal skills inspect this name.
        info["render_runtimes"] = info["render_engines"]

        if remotion_ok:
            info["remotion_components"] = self._REMOTION_COMPONENTS
            info["remotion_note"] = (
                "Remotion is available for React-based rendering. Use it for "
                "image-to-video with spring animations, animated text/stat cards, "
                "charts, callouts, comparisons, and word-level caption burn. "
                "Prefer Remotion over Ken Burns pan-and-zoom for explainer "
                "and motion-graphics pipelines that already use the scene-component stack."
            )
        else:
            composer_dir = Path(__file__).resolve().parent.parent.parent / "remotion-composer"
            if composer_dir.exists() and (composer_dir / "package.json").exists() and not (composer_dir / "node_modules").exists():
                info["remotion_note"] = (
                    "Remotion project exists but node_modules are NOT installed. "
                    "Run 'cd remotion-composer && npm install' to enable Remotion rendering."
                )
            else:
                info["remotion_note"] = (
                    "Remotion is NOT available (needs Node.js/npx + remotion-composer + node_modules)."
                )

        if hyperframes_ok:
            info["hyperframes_note"] = (
                "HyperFrames is available for HTML/CSS/GSAP composition. Use it "
                "for kinetic typography, product promos, launch reels, "
                "website-to-video, and registry-block-driven scenes. Consumed via "
                "'npx hyperframes' (npm package: 'hyperframes'). "
                "Before locking render_runtime='hyperframes' at the proposal stage, "
                "verify the runtime with `hyperframes_compose` operation='doctor' "
                "or `make hyperframes-doctor`. An 'available' flag from the runtime "
                "check means node + ffmpeg + the npm package all resolve; it does "
                "not guarantee a render will succeed on the first specific "
                "composition."
            )
        else:
            info["hyperframes_note"] = (
                "HyperFrames is NOT available. Requires Node.js >= 22, FFmpeg, "
                "npx on PATH, and the 'hyperframes' npm package to be resolvable. "
                "Run `make hyperframes-doctor` to see the specific missing piece, "
                "or call `hyperframes_compose` operation='doctor' directly."
            )

        # Governance note — agents and reviewers consume this.
        info["runtime_governance"] = (
            "render_runtime is locked at proposal stage and carried unchanged "
            "through edit_decisions. Silent swaps are forbidden. If the "
            "chosen runtime fails, surface a structured blocker and wait for "
            "user approval before switching."
        )
        return info

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs["operation"]
        start = time.time()

        try:
            if operation == "compose":
                result = self._compose(inputs)
            elif operation == "render":
                result = self._render(inputs)
            elif operation == "remotion_render":
                result = self._remotion_render(inputs)
            elif operation == "burn_subtitles":
                result = self._burn_subtitles(inputs)
            elif operation == "overlay":
                result = self._overlay(inputs)
            elif operation == "encode":
                result = self._encode(inputs)
            else:
                return ToolResult(success=False, error=f"Unknown operation: {operation}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        result.duration_seconds = round(time.time() - start, 2)
        return result

    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".svg"}

    @staticmethod
    def _is_image(path: Path) -> bool:
        """Check if a file is a still image (routes to Remotion, not FFmpeg)."""
        return path.suffix.lower() in VideoCompose._IMAGE_EXTENSIONS

    @staticmethod
    def _has_audio_stream(path: Path) -> bool:
        """Return True iff ffprobe reports at least one audio stream.

        Many stock video clips (especially from Pexels) ship with no audio
        stream at all. If we blindly tell ffmpeg to transcode the 0:a stream
        on such a file it errors out. This helper lets the segment builder
        branch on stream presence so it can synthesize a silent track when
        needed, keeping the concat segment layout consistent.
        """
        try:
            out = subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "a",
                    "-show_entries", "stream=codec_type",
                    "-of", "default=nw=1:nk=1",
                    str(path),
                ],
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ffprobe is required to determine whether a source has an audio stream"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ffprobe timed out while inspecting source: {path}") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.output or "").strip()
            suffix = f": {detail[-300:]}" if detail else ""
            raise RuntimeError(f"ffprobe failed while inspecting source {path}{suffix}") from exc
        return any(line.strip() == "audio" for line in out.splitlines())

    @staticmethod
    def _project_root_for_inputs(
        inputs: dict[str, Any], output_path: Path | None = None
    ) -> Path | None:
        """Find the canonical project root for project-relative media paths.

        Artifact schemas deliberately store paths such as
        ``assets/video/source.mp4`` relative to a project directory.  Render
        tools are often invoked from the repository root, a worker directory,
        or a temporary test directory, so resolving those strings with
        ``Path.cwd()`` is not deterministic.  Prefer an explicit project
        directory and otherwise walk the output path ancestors looking for
        the project marker/work order.
        """
        for key in ("project_dir", "project_path"):
            raw = inputs.get(key)
            if raw:
                try:
                    candidate = Path(raw).expanduser().resolve()
                    if candidate.is_dir():
                        return candidate
                except (OSError, TypeError, ValueError):
                    pass

        if output_path is not None:
            try:
                resolved_output = Path(output_path).expanduser().resolve()
                # Include the output parent and all ancestors.  The output
                # file may not exist yet, which is why this checks marker
                # files rather than the output itself.
                for candidate in (resolved_output.parent, *resolved_output.parents):
                    if any(
                        (candidate / marker).is_file()
                        for marker in ("project.json", "work_order.json")
                    ):
                        return candidate
            except (OSError, TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _resolve_project_path(
        value: Any, *, project_root: Path | None
    ) -> Any:
        """Resolve one local project-relative path without changing URLs.

        When a project root is known, a relative path is resolved *only*
        against that root (with a compatibility candidate for the historical
        ``projects/<id>/...`` spelling).  Falling back to the current working
        directory in that case could select a stale file from another project
        and silently render the wrong visuals.  If no project root is known,
        legacy cwd-relative calls remain supported.
        """
        if not isinstance(value, str) or not value:
            return value
        if value.startswith(("http://", "https://", "file://")):
            return value

        raw = Path(value).expanduser()
        if raw.is_absolute():
            return str(raw)

        if project_root is not None:
            candidates = [project_root / raw]
            parts = raw.parts
            if len(parts) > 1 and parts[0].lower() == "projects":
                # Compatibility with repo-relative manifests.  Only the
                # project-root candidate above is used for the normal schema.
                candidates.append(project_root.parent / Path(*parts[1:]))
            for candidate in candidates:
                try:
                    if candidate.is_file():
                        return str(candidate.resolve())
                except OSError:
                    continue
            # Keep the error deterministic and anchored to the project rather
            # than accidentally probing a same-named cwd file.
            return str((project_root / raw).resolve())

        candidate = Path.cwd() / raw
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:
            pass
        return value

    @staticmethod
    def _resolve_output_path(value: Any, *, project_root: Path | None) -> Path:
        """Resolve an output path against the current project when known."""
        raw = Path(value or "renders/output.mp4").expanduser()
        if project_root is not None and not raw.is_absolute():
            raw = project_root / raw
        return raw.resolve() if project_root is not None else raw

    @staticmethod
    def _run_paths_for_inputs(inputs: dict[str, Any], project_root: Path | None):
        """Create the isolated run envelope when a durable project run exists."""
        if project_root is None:
            return None

        requested = inputs.get("run_id")
        persisted = None
        for identity_name in ("work_order.json", "project.json"):
            try:
                payload = json.loads(
                    (project_root / identity_name).read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("run_id"):
                persisted = str(payload["run_id"])
                break
        if persisted and requested and str(requested).lower() != persisted.lower():
            raise ValueError(
                "run_id does not match the durable project work-order identity"
            )
        resolved = str(requested or persisted or "").strip()
        if not resolved:
            return None
        from lib.paths import run_paths

        return run_paths(project_root, resolved)

    @staticmethod
    def _candidate_output_for_run(run_envelope: Any, final_path: Path) -> Path | None:
        if run_envelope is None:
            return None
        from lib.output_promotion import candidate_path

        return candidate_path(run_envelope.candidates, final_path)

    @staticmethod
    def _expected_duration(
        edit_decisions: Mapping[str, Any] | None,
        inputs: Mapping[str, Any] | None = None,
    ) -> float | None:
        edit_decisions = edit_decisions or {}
        inputs = inputs or {}
        declared = (
            inputs.get("expected_duration_seconds")
            or edit_decisions.get("total_duration_seconds")
            or (edit_decisions.get("metadata") or {}).get("target_duration_seconds")
        )
        try:
            return float(declared) if declared is not None else None
        except (TypeError, ValueError):
            return None

    def _promote_run_output(
        self,
        *,
        candidate: Path,
        final_path: Path,
        run_envelope: Any,
        inputs: Mapping[str, Any],
        profile: str | None,
        expected_duration_seconds: float | None,
        stage: str,
        tool: str,
    ) -> dict[str, Any]:
        """Probe and atomically adopt a run candidate into the final path."""
        from lib.output_promotion import promote_candidate

        attempt = inputs.get("attempt")
        if attempt is None:
            try:
                order_payload = json.loads(
                    (run_envelope.root.parent.parent / "work_order.json").read_text(
                        encoding="utf-8"
                    )
                )
                attempt = order_payload.get("attempt")
            except (OSError, UnicodeError, json.JSONDecodeError):
                attempt = None
        provenance = {
            "project_id": run_envelope.root.parent.parent.name,
            "run_id": run_envelope.run_id,
            "attempt": attempt,
            "stage": stage,
            "tool": tool,
            "run_record_ref": f"runs/{run_envelope.run_id}/run.json",
        }
        # Freshness is anchored to the durable run record, not to the final
        # path.  The attempt timestamp is preferred when present so a retry
        # cannot claim an artifact that predates the active attempt; the
        # original started_at remains a safe compatibility fallback for older
        # records.
        run_started_at = None
        try:
            from lib.run_record import read_run_record

            record = read_run_record(run_envelope.root.parent.parent, run_envelope.run_id)
            metadata = record.get("metadata") if isinstance(record, dict) else {}
            run_started_at = (
                (metadata or {}).get("attempt_started_at")
                or record.get("started_at")
            )
        except Exception:
            # A durable render without a readable run record must not silently
            # turn a stale final into success.  Leave the timestamp unset only
            # for legacy test doubles that provide a run envelope but no record;
            # production manifest runs always persist the record before render.
            run_started_at = None
        return promote_candidate(
            candidate,
            final_path,
            profile=profile,
            expected_duration_seconds=expected_duration_seconds,
            provenance=provenance,
            run_started_at=run_started_at,
        )

    def _compose(self, inputs: dict[str, Any]) -> ToolResult:
        """FFmpeg composition: concat video cuts, add audio, burn subtitles.

        Handles video sources only. Still images and animated scene types
        are routed to Remotion via the render operation — call compose
        directly only for pure video pipelines (e.g. talking-head).
        """
        edit_decisions = inputs.get("edit_decisions")
        if not edit_decisions:
            return ToolResult(success=False, error="edit_decisions required for compose")

        raw_output_path = inputs.get("output_path", "composed_output.mp4")
        project_root = self._project_root_for_inputs(inputs, Path(raw_output_path))
        output_path = self._resolve_output_path(raw_output_path, project_root=project_root)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        run_envelope = self._run_paths_for_inputs(inputs, project_root)
        final_output_path = output_path
        candidate_output_path = self._candidate_output_for_run(run_envelope, final_output_path)
        if candidate_output_path is not None:
            output_path = candidate_output_path
        audio_path = self._resolve_project_path(
            inputs.get("audio_path"), project_root=project_root
        )
        subtitle_path = self._resolve_project_path(
            inputs.get("subtitle_path"), project_root=project_root
        )
        if audio_path is not None and not Path(audio_path).is_file():
            return ToolResult(success=False, error=f"Audio source not found: {audio_path}")
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 23)
        # Interactive composition defaults to a bounded fast preset. The
        # quality control remains CRF-based and an explicit preset is honored.
        preset = inputs.get("preset", "fast")
        profile_name = inputs.get("profile")

        # Resolve target resolution + fit mode. Priority: explicit `profile`
        # arg > edit_decisions.metadata.compose_target > default (landscape HD).
        # compose_target = {"width": W, "height": H, "fit": "pad"|"cover"} lets a
        # caller request vertical (9:16) or any aspect without a named profile.
        # fit="pad" letterboxes (no content loss, the historical default);
        # fit="cover" scales-to-fill and centre-crops (better for vertical social).
        resolution = "1920x1080"
        fit_mode = "pad"
        compose_target = (edit_decisions.get("metadata") or {}).get("compose_target")
        if isinstance(compose_target, dict):
            try:
                resolution = f"{int(compose_target['width'])}x{int(compose_target['height'])}"
            except (KeyError, ValueError, TypeError):
                pass
            if compose_target.get("fit") in ("pad", "cover"):
                fit_mode = compose_target["fit"]
        if profile_name:
            try:
                from lib.media_profiles import get_profile
                p = get_profile(profile_name)
                resolution = f"{p.width}x{p.height}"
            except (ImportError, ValueError):
                pass
        try:
            target_w, target_h = (int(v) for v in resolution.split("x"))
        except ValueError:
            target_w, target_h = 1920, 1080

        raw_cuts = edit_decisions.get("cuts", [])
        if not isinstance(raw_cuts, list) or not raw_cuts:
            return ToolResult(success=False, error="No cuts in edit_decisions")
        try:
            cuts = [dict(cut) for cut in raw_cuts]
        except (TypeError, ValueError) as exc:
            return ToolResult(success=False, error=f"Invalid cut object in edit_decisions: {exc}")
        timeline_error = self._validate_compose_cuts(cuts)
        if timeline_error:
            return ToolResult(success=False, error=timeline_error)

        # Artifact contracts store media paths relative to the project root.
        # Resolve them here as well as in the high-level render path so direct
        # ``operation='compose'`` calls cannot accidentally depend on the
        # process working directory.
        for cut in cuts:
            cut["source"] = self._resolve_project_path(
                cut.get("source"), project_root=project_root
            )

        # Resolve subtitle style using the layered priority resolver
        # (explicit > edit_decisions > playbook > defaults)
        playbook_data = inputs.get("playbook")
        resolved_sub_style = self._resolve_subtitle_style(
            inputs.get("subtitle_style"),
            edit_decisions,
            playbook_data,
        )
        inputs = dict(inputs)
        inputs["subtitle_style"] = resolved_sub_style

        ed_subs = edit_decisions.get("subtitles", {})
        if ed_subs.get("source") and not subtitle_path:
            subtitle_path = self._resolve_project_path(ed_subs["source"], project_root=project_root)

        caption_contract = None
        transcript_verification = None
        caption_mode = str(inputs.get("caption_mode", "burn_in")).strip().lower()
        if subtitle_path or inputs.get("captions") is not None or inputs.get("transcript") is not None or ed_subs.get("enabled"):
            try:
                caption_contract, transcript_verification = self._caption_contract_for_inputs(
                    inputs,
                    subtitle_path=subtitle_path,
                    width=target_w,
                    height=target_h,
                    duration_seconds=max(float(c.get("out_seconds", 0) or 0) for c in cuts),
                    runtime="ffmpeg",
                    mode=caption_mode,
                    style=resolved_sub_style,
                )
            except Exception as exc:
                return ToolResult(success=False, error=f"Caption render contract rejected: {exc}")
            if ed_subs.get("enabled") and caption_contract is None:
                return ToolResult(
                    success=False,
                    error="subtitles are enabled but no certified caption source was provided",
                )
            if caption_contract is not None:
                resolved_sub_style["margin_v"] = max(
                    int(resolved_sub_style.get("margin_v", 0) or 0),
                    int(caption_contract["safe_area"]["pixels"]["bottom"]),
                )
                inputs["subtitle_style"] = resolved_sub_style

        caption_source_path = subtitle_path

        temp_dir = (
            run_envelope.work / f"ffmpeg-{uuid.uuid4().hex}"
            if run_envelope is not None
            else output_path.parent / ".compose_tmp"
        )
        temp_dir.mkdir(parents=True, exist_ok=True)
        if caption_contract is not None and caption_mode == "burn_in" and caption_source_path is None:
            caption_source_path = self._write_caption_srt(caption_contract, temp_dir / "captions.srt")
        temp_segments: list[Path] = []
        concat_path: Path | None = None
        concat_out: Path | None = None

        try:
            segment_commands = []
            for i, cut in enumerate(cuts):
                source = Path(cut["source"])
                if not source.exists():
                    return ToolResult(success=False, error=f"Cut source not found: {source}")

                seg_path = temp_dir / f"seg_{i:04d}.mp4"
                in_s = cut["in_seconds"]
                out_s = cut["out_seconds"]
                duration = out_s - in_s
                speed = cut.get("speed", 1.0)

                if self._is_image(source):
                    return ToolResult(
                        success=False,
                        error=(
                            f"Still image '{source.name}' in cuts. "
                            "Use operation='render' (auto-routes to Remotion) "
                            "or operation='remotion_render' for compositions "
                            "with images, animations, or component scenes."
                        ),
                    )
                else:
                    # Video source: trim to segment.
                    #
                    # Semantics:
                    #   -ss BEFORE -i   → fast input-level seek to in_s
                    #   -t  AFTER  -i   → "play for `duration` seconds"
                    #                     (unambiguous regardless of seek mode)
                    #
                    # We MUST re-encode here — `-c copy` cannot do frame-accurate
                    # cuts because it snaps to keyframes. With sparse GOPs (common
                    # in Pexels / AI-generated clips), stream-copy can produce
                    # segments significantly longer than `duration`, breaking the
                    # target timeline. Re-encoding with libx264/AAC is slower but
                    # gives exact cut boundaries. Same resolution in → same
                    # resolution out, so same-res inputs concat cleanly.
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(in_s),
                        "-async", "1",
                        "-t", str(duration),
                        "-i", str(source),
                    ]

                    # Normalize every segment to a consistent container so the
                    # concat-copy step is always safe. The concat demuxer with
                    # `-c copy` requires identical codec / resolution / fps /
                    # pix_fmt / sar across ALL segments — otherwise it throws
                    # "Non-monotonous DTS" or silently produces corrupt output.
                    #
                    # Target is target_w x target_h @ 30fps, yuv420p, sar=1
                    # (default 1920x1080; overridable via `profile` or
                    # edit_decisions.metadata.compose_target — see above).
                    # fit="pad" letterboxes to preserve all content; fit="cover"
                    # scales-to-fill then centre-crops (no bars, for vertical social).
                    if fit_mode == "cover":
                        geom = [
                            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase",
                            f"crop={target_w}:{target_h}",
                        ]
                    else:
                        geom = [
                            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease",
                            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black",
                        ]
                    vf_parts: list[str] = [*geom, "setsar=1", "fps=30"]
                    af_parts: list[str] = []
                    if speed != 1.0:
                        vf_parts.append(f"setpts={1.0/speed}*PTS")
                        af_parts.append(self._build_atempo(speed))

                    cmd.extend(["-filter:v", ",".join(vf_parts)])
                    if af_parts:
                        cmd.extend(["-filter:a", ",".join(af_parts)])

                    cmd.extend([
                        "-c:v", codec,
                        "-crf", str(crf),
                        "-preset", preset,
                        "-pix_fmt", "yuv420p",
                        "-r", "30",
                    ])

                    # Audio handling: some source clips have no audio stream
                    # (Pexels stock often ships silent). If we unconditionally
                    # ask ffmpeg to copy/encode the 0:a stream it errors out.
                    # Probe for an audio stream first — if present, transcode
                    # to AAC; if absent, synthesize a silent stereo track so
                    # concat segments have a consistent stream layout.
                    has_audio = self._has_audio_stream(source)
                    if has_audio:
                        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
                    else:
                        # Inject silent audio via lavfi before the output.
                        # We have to rebuild cmd to add the lavfi input
                        # before the output path and map streams explicitly.
                        cmd = [
                            "ffmpeg", "-y",
                            "-ss", str(in_s),
                            "-async", "1",
                            "-t", str(duration),
                            "-i", str(source),
                            "-f", "lavfi",
                            "-t", str(duration),
                            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                            "-filter:v", ",".join(vf_parts),
                        ]
                        if af_parts:
                            cmd.extend(["-filter:a", ",".join(af_parts)])
                        cmd.extend([
                            "-map", "0:v:0",
                            "-map", "1:a:0",
                            "-c:v", codec,
                            "-crf", str(crf),
                            "-preset", preset,
                            "-pix_fmt", "yuv420p",
                            "-r", "30",
                            "-c:a", "aac",
                            "-b:a", "192k",
                            "-ar", "48000",
                            "-ac", "2",
                        ])

                    cmd.append(str(seg_path))
                    segment_commands.append((cmd, i))

                temp_segments.append(seg_path)
            
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=min(4, len(cuts))) as pool:
                futures = {pool.submit(self.run_command, cmd, timeout=600): cut_id for cmd, cut_id in segment_commands}
                for f in as_completed(futures):
                    f.result()  # raises if failed

            # Step 2: Concat segments
            concat_path = temp_dir / "concat_list.txt"
            with open(concat_path, "w", encoding="utf-8") as f:
                for seg in temp_segments:
                    safe = str(seg.resolve()).replace("\\", "/")
                    safe = safe.replace("'", "'\\''")
                    f.write(f"file '{safe}'\n")

            concat_out = temp_dir / "concat.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_path),
                "-c", "copy",
                str(concat_out),
            ]
            self.run_command(cmd, timeout=600)

            # Step 3: Apply subtitles and/or replace audio
            final_input = concat_out
            vfilters = []

            if caption_contract is not None and caption_contract.get("mode") == "burn_in" and caption_source_path and Path(caption_source_path).exists():
                style = inputs.get("subtitle_style", {})
                ass_style = self._build_subtitle_style(style)
                sub_escaped = str(Path(caption_source_path).resolve()).replace("\\", "/").replace(":", "\\:")
                vfilters.append(f"subtitles='{sub_escaped}':force_style='{ass_style}'")

            cmd = ["ffmpeg", "-y", "-i", str(final_input)]

            if audio_path and Path(audio_path).exists():
                cmd.extend(["-i", audio_path])

            # Determine if profile requires re-encoding (resize/fps change)
            # This must be checked BEFORE choosing copy vs encode, because
            # -s and -r are incompatible with -c:v copy.
            profile_flags: list[str] = []
            if profile_name:
                try:
                    from lib.media_profiles import get_profile
                    p = get_profile(profile_name)
                    profile_flags = ["-s", f"{p.width}x{p.height}", "-r", str(p.fps)]
                except (ImportError, ValueError):
                    pass

            needs_reencode = bool(vfilters) or bool(profile_flags)

            if needs_reencode:
                if vfilters:
                    cmd.extend(["-vf", ",".join(vfilters)])
                cmd.extend(["-c:v", codec, "-crf", str(crf), "-preset", preset])
                cmd.extend(profile_flags)
            else:
                cmd.extend(["-c:v", "copy"])

            if audio_path and Path(audio_path).exists():
                # Use type-based selectors (0:v, 1:a) instead of index-based
                # (0:v:0) because source videos may have audio as stream 0
                # and video as stream 1 (e.g. Kling-generated clips).
                cmd.extend(["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-ar", "48000", "-shortest"])
            else:
                cmd.extend(["-c:a", "copy"])

            cmd.append(str(output_path))
            self.run_command(cmd, timeout=600)

            promotion = None
            if candidate_output_path is not None:
                promotion = self._promote_run_output(
                    candidate=output_path,
                    final_path=final_output_path,
                    run_envelope=run_envelope,
                    inputs=inputs,
                    profile=profile_name,
                    expected_duration_seconds=self._expected_duration(edit_decisions, inputs),
                    stage=str(inputs.get("stage") or "compose"),
                    tool=self.name,
                )
            # Direct/non-durable compose calls still receive the same
            # ffprobe-backed contract. Durable calls use the probe performed
            # during atomic promotion; a test double without that field is
            # intentionally left alone so isolation tests can use byte stubs.
            media_probe = promotion.get("probe") if isinstance(promotion, dict) else None
            if candidate_output_path is None:
                try:
                    from lib.output_promotion import probe_media, validate_media_contract

                    media_probe = probe_media(final_output_path)
                    validate_media_contract(
                        media_probe,
                        profile=profile_name,
                        expected_duration_seconds=self._expected_duration(edit_decisions, inputs),
                    )
                except Exception as exc:
                    return ToolResult(
                        success=False,
                        error=f"FFmpeg output media validation failed: {exc}",
                    )
            return ToolResult(
                success=True,
                data={
                    "operation": "compose",
                    "cut_count": len(cuts),
                    "has_subtitles": subtitle_path is not None,
                    "caption_render_contract": caption_contract,
                    "transcript_verification": transcript_verification,
                    "caption_mode": caption_mode if caption_contract is not None else None,
                    "caption_sidecar": str(subtitle_path) if caption_contract is not None and caption_mode == "sidecar" else None,
                    "has_mixed_audio": audio_path is not None,
                    "profile": profile_name,
                    "output": str(final_output_path),
                    "run_dir": str(run_envelope.root) if run_envelope else None,
                    "staging_dir": str(temp_dir),
                    "output_promotion": promotion,
                    "media_probe": media_probe,
                },
                artifacts=[str(final_output_path)],
            )
        finally:
            # Cleanup temp files
            for f in temp_segments:
                if f.exists():
                    f.unlink()
            for f in [concat_path, concat_out]:
                if f is not None and f.exists():
                    f.unlink()
            if temp_dir.exists():
                import shutil as _shutil
                _shutil.rmtree(temp_dir, ignore_errors=True)
            if candidate_output_path is not None and candidate_output_path.exists():
                try:
                    candidate_output_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _validate_compose_cuts(cuts: list[dict[str, Any]]) -> str | None:
        """Validate and normalize source intervals before invoking FFmpeg."""

        for index, cut in enumerate(cuts):
            if not isinstance(cut, dict):
                return f"Cut {index} must be an object"
            source = cut.get("source")
            if not isinstance(source, (str, Path)) or not str(source).strip():
                return f"Cut {index} source is required"
            values: dict[str, float] = {}
            for field_name, default in (
                ("in_seconds", None),
                ("out_seconds", None),
                ("speed", 1.0),
            ):
                value = cut.get(field_name, default)
                if isinstance(value, bool):
                    return f"Cut {index} {field_name} must be numeric"
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return f"Cut {index} {field_name} must be numeric"
                if not math.isfinite(number):
                    return f"Cut {index} {field_name} must be finite"
                values[field_name] = number
            start = values["in_seconds"]
            end = values["out_seconds"]
            speed = values["speed"]
            if start < 0:
                return f"Cut {index} in_seconds cannot be negative"
            if end <= start:
                return f"Cut {index} must have out_seconds greater than in_seconds"
            if speed <= 0:
                return f"Cut {index} speed must be greater than zero"
            cut.update(values)
        return None

    _REMOTION_SCENE_TYPES = {
        "text_card", "stat_card", "callout", "comparison", "progress", "chart",
    }

    # Maps renderer_family (set at proposal stage) to Remotion composition ID.
    # Each family MUST map to a distinct composition — collapsing defeats visual grammar.
    # Maps renderer_family → Remotion composition ID.
    # Only compositions registered in remotion-composer/src/Root.tsx are valid.
    # Current compositions: Explainer, CinematicRenderer, TalkingHead
    RENDERER_FAMILY_MAP = {
        "explainer-data": "Explainer",
        "explainer-teacher": "Explainer",
        "cinematic-trailer": "CinematicRenderer",
        "documentary-montage": "CinematicRenderer",
        "product-reveal": "Explainer",
        "screen-demo": "Explainer",
        "presenter": "TalkingHead",
        "animation-first": "Explainer",
    }

    @classmethod
    def _get_composition_id(cls, renderer_family: str) -> str:
        """Resolve renderer_family to Remotion composition ID.

        Raises ValueError if renderer_family is not recognized — the caller
        must set it at proposal stage.
        """
        comp = cls.RENDERER_FAMILY_MAP.get(renderer_family)
        if comp is None:
            raise ValueError(
                f"Unknown renderer_family {renderer_family!r}. "
                f"Valid families: {sorted(cls.RENDERER_FAMILY_MAP)}. "
                f"Set renderer_family at proposal stage."
            )
        return comp

    def _render_via_atelier(
        self,
        inputs: dict[str, Any],
        edit_decisions: dict[str, Any],
    ) -> ToolResult:
        """Render a hand-authored, project-local Remotion composition ("atelier" mode).

        Unlike the cut-schema path, atelier mode does NOT route through the
        stock Explainer/CinematicRenderer compositions, the cut.type scene
        registry, or RENDERER_FAMILY_MAP. The agent hand-authors a bespoke
        composition — its own scenes, theme, and motion — and points this
        renderer at the project-local entry. This is the deliberate
        "hand-stitched every time" path: zero reusable creative components,
        a fresh visual language per video.

        Contract — edit_decisions["bespoke"] = {
            "entry":          <path to the project-local Remotion entry .tsx;
                               MUST live under remotion-composer/ so the
                               Remotion bundler can resolve node_modules.
                               Convention: remotion-composer/projects/<slug>/index.tsx>,
            "composition_id": <id registered in that entry's Root>,
            "props_path":     <optional absolute path to a props JSON (--props)>,
            "public_dir":     <optional path to a SMALL per-project public dir,
                               avoids copying the bloated shared public/>,
            "scale":          <optional float, e.g. 0.5 for a fast draft>,
            "crf":            <optional int, e.g. 18 for a crisp final>,
            "concurrency":    <optional int>,
        }
        """
        bespoke = edit_decisions.get("bespoke") or {}
        entry = bespoke.get("entry")
        comp_id = bespoke.get("composition_id")
        if not entry or not comp_id:
            return ToolResult(
                success=False,
                error=(
                    "atelier mode requires edit_decisions.bespoke.entry (path to the "
                    "project-local Remotion entry .tsx) and edit_decisions.bespoke."
                    "composition_id (the id registered in that entry's Root)."
                ),
            )

        composer_dir = Path(__file__).resolve().parent.parent.parent / "remotion-composer"
        if not composer_dir.exists() or not (composer_dir / "node_modules").exists():
            return ToolResult(
                success=False,
                error=(
                    f"remotion-composer or its node_modules is missing at {composer_dir}. "
                    f"Run `cd remotion-composer && npm install` first."
                ),
            )

        entry_path = Path(entry)
        if not entry_path.is_absolute():
            # Resolve relative to repo root first, then to the composer dir.
            repo_root = composer_dir.parent
            cand = (repo_root / entry).resolve()
            entry_path = cand if cand.exists() else (composer_dir / entry).resolve()
        entry_path = entry_path.resolve()
        if not entry_path.exists():
            return ToolResult(success=False, error=f"atelier entry not found: {entry_path}")

        # Remotion's bundler resolves `remotion` and friends by walking up from the
        # entry file to find node_modules — so the entry must live under
        # remotion-composer/ at render time. But OpenMontage's project convention is
        # repo-root projects/<slug>/, where artifacts/assets/renders/ already live.
        # Resolution: keep the source of truth under projects/<slug>/ and auto-stage
        # a directory junction (Windows) / symlink (Unix) at
        # remotion-composer/projects/<slug>/ → projects/<slug>/ so the bundler sees
        # the entry inside the composer tree without us copying files. Junctions are
        # weightless, idempotent across renders, and need no admin/dev-mode on Windows.
        project_root = self._project_root_for_inputs(
            inputs, Path(inputs.get("output_path", "renders/output.mp4"))
        )
        run_envelope = self._run_paths_for_inputs(inputs, project_root)
        try:
            # Even an entry already inside remotion-composer gets a run-local
            # source copy for durable runs. This prevents concurrent agents
            # from overwriting the same bespoke tree while webpack is reading.
            if run_envelope is not None:
                staging_root = (
                    composer_dir / "projects" / ".run_staging" / run_envelope.run_id
                )
                effective_entry = self._stage_atelier_project(
                    entry_path, composer_dir, staging_root=staging_root
                )
            else:
                entry_path.relative_to(composer_dir)
                effective_entry = entry_path
        except ValueError:
            try:
                effective_entry = self._stage_atelier_project(entry_path, composer_dir)
            except Exception as e:
                return ToolResult(
                    success=False,
                    error=(
                        f"atelier auto-stage failed for entry {entry_path}: {e}. "
                        f"Either place the entry under {composer_dir}/projects/<slug>/ "
                        f"directly, or fix the staging permission issue."
                    ),
                )

        output_path = self._resolve_output_path(
            inputs.get("output_path", "renders/output.mp4"),
            project_root=project_root,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["npx", "remotion", "render", str(effective_entry), str(comp_id), str(output_path)]

        props_path = bespoke.get("props_path")
        if props_path:
            pp = Path(props_path).resolve()
            if not pp.exists():
                return ToolResult(success=False, error=f"atelier props_path not found: {pp}")
            # Equals form is required for cross-platform path parsing (see _remotion_render).
            cmd.append(f'--props={pp}')

        public_dir = bespoke.get("public_dir")
        if public_dir:
            pd = Path(public_dir).resolve()
            if pd.exists():
                cmd.append(f"--public-dir={pd}")
        elif run_envelope is not None:
            pd = run_envelope.inputs / "atelier-public"
            pd.mkdir(parents=True, exist_ok=True)
            cmd.append(f"--public-dir={pd.resolve()}")

        if bespoke.get("scale"):
            cmd.append(f"--scale={bespoke['scale']}")
        if bespoke.get("crf") is not None:
            cmd.append(f"--crf={bespoke['crf']}")
        if bespoke.get("concurrency"):
            cmd.append(f"--concurrency={bespoke['concurrency']}")

        try:
            # Run from inside the composer dir so npx resolves the local
            # remotion binary (mirrors _remotion_render).
            self.run_command(cmd, timeout=1800, cwd=composer_dir)
        except Exception as e:
            return ToolResult(success=False, error=f"Atelier (bespoke) Remotion render failed: {e}")

        if not output_path.exists():
            return ToolResult(
                success=False,
                error=f"Atelier render completed but output file missing: {output_path}",
            )

        # --- Atelier post-render review -------------------------------------
        # The cut-schema paths run _run_final_review (technical/visual/audio
        # probes + transcript-vs-script). Atelier MUST do the same so hero
        # renders aren't shipped without the safety net — and additionally
        # enforce the bespoke doctrine: no stock-registry imports, an
        # art-direction declaration must exist. The distinctness review
        # ("could this be any other product's video?") stays human; what we
        # automate here is the *doctrine bypass*, not the taste call.
        final_review = self._run_final_review(
            output_path=output_path,
            edit_decisions=edit_decisions,
            proposal_packet=inputs.get("proposal_packet"),
            asset_manifest=inputs.get("asset_manifest"),
            project_root=project_root,
            narration_transcript_path=inputs.get("narration_transcript_path"),
            script_text=inputs.get("script_text"),
        )

        atelier_checks = self._run_atelier_checks(entry_path, bespoke)
        final_review.setdefault("checks", {})["atelier"] = atelier_checks
        final_review["issues_found"] = list(final_review.get("issues_found", [])) + atelier_checks.get("issues", [])

        # Escalate atelier-critical issues (stock reuse) to the overall status.
        # Missing art-direction is a warning, not a fail — it shows in issues_found.
        if atelier_checks.get("stock_reuse_detected"):
            final_review["status"] = "fail"
            final_review["recommended_action"] = "re_author"

        data: dict[str, Any] = {
            "operation": "render",
            "composition_mode": "atelier",
            "entry": str(entry_path),
            "effective_entry": str(effective_entry) if effective_entry != entry_path else None,
            "composition_id": comp_id,
            "output": str(output_path),
            "run_dir": str(run_envelope.root) if run_envelope else None,
            "final_review": final_review,
            "final_review_status": final_review.get("status"),
        }

        if final_review.get("status") != "pass":
            return ToolResult(
                success=False,
                error=(
                    "Atelier render did not pass final review:\n"
                    + "\n".join(f"  • {i}" for i in final_review.get("issues_found", []))
                ),
                data=data,
                artifacts=[str(output_path)],
            )

        return ToolResult(success=True, data=data, artifacts=[str(output_path)])

    # Source-file extensions that get staged into the composer tree at render time.
    # Anything not in this set lives only under the real project dir (assets, renders,
    # artifacts) and is referenced via --public-dir or absolute paths.
    _ATELIER_STAGE_EXTS = {".tsx", ".ts", ".jsx", ".js", ".css"}

    def _stage_atelier_project(
        self,
        entry_path: Path,
        composer_dir: Path,
        *,
        staging_root: Path | None = None,
    ) -> Path:
        """Auto-stage a bespoke project under remotion-composer/projects/<slug>/.

        The source of truth lives under the repo-root `projects/<slug>/` (where
        artifacts/, assets/, renders/ already are). Remotion's webpack bundler,
        however, resolves modules (`remotion`, `@remotion/*`) by walking up from
        the entry's REAL location — so a directory junction/symlink would
        dereference and webpack would fail to find node_modules. We copy the
        source files into a sibling dir inside the composer tree instead.

        mtime-skip semantics make repeat renders cheap (typical project is a
        handful of small .tsx files). Non-source files (assets, renders, props
        JSON) stay only in the real project dir and are referenced via
        --public-dir or absolute paths in props.

        Resolves the slug as the first path segment under a `projects/` ancestor;
        falls back to the entry's parent directory name. Returns the staged entry
        path.
        """
        import shutil

        real_project_dir = entry_path.parent.resolve()

        # Derive a stable slug. Prefer the first segment under a `projects/` ancestor.
        slug = real_project_dir.name
        try:
            parts = real_project_dir.parts
            if "projects" in parts:
                i = parts.index("projects")
                if i + 1 < len(parts):
                    slug = parts[i + 1]
        except Exception:
            pass

        staging_root = staging_root or composer_dir / "projects"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_dir = staging_root / slug

        # If a stale junction/symlink is in the way from an earlier (failed) attempt,
        # remove it before creating a real staging directory.
        if staging_dir.is_symlink() or (staging_dir.exists() and staging_dir.is_dir()
                                        and staging_dir.resolve() != staging_dir):
            try:
                staging_dir.unlink()
            except (OSError, PermissionError):
                # Some Windows junctions need rmdir
                import shutil
                shutil.rmtree(staging_dir, ignore_errors=True)

        staging_dir.mkdir(parents=True, exist_ok=True)

        # mtime-skip copy of source files only. Mirrors directory structure so
        # relative imports work identically.
        for src in real_project_dir.rglob("*"):
            if not src.is_file():
                continue
            if src.suffix.lower() not in self._ATELIER_STAGE_EXTS:
                continue
            rel = src.relative_to(real_project_dir)
            dst = staging_dir / rel
            try:
                if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                    continue
            except OSError:
                pass
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        return staging_dir / entry_path.name

    # Stock-registry import patterns that violate the atelier doctrine.
    # Any of these inside a bespoke project tree means a creative component
    # was reused instead of hand-stitched. Engine knowledge (the `remotion`
    # package, `@remotion/*`, project-local files) is fine.
    _ATELIER_STOCK_IMPORT_RE = (
        r"""from\s+["']("""
        # parent-traversed paths into the stock src/
        r"""(?:\.\./)+src/(?:components|Explainer|CinematicRenderer|"""
        r"""TitledVideo|TalkingHead|CollageBurst|LyricOverlay|cinematic|crucix|phantom)"""
        # or absolute-ish paths into the same
        r"""|remotion-composer/src/(?:components|Explainer|CinematicRenderer|"""
        r"""TitledVideo|TalkingHead|CollageBurst|LyricOverlay|cinematic|crucix|phantom)"""
        r""")"""
    )

    def _run_atelier_checks(self, entry_path: Path, bespoke: dict[str, Any]) -> dict[str, Any]:
        """Doctrine-enforcement checks specific to atelier mode.

        Returns a dict with two checks:
          - stock_reuse_detected (bool) + offending_imports (list) — CRITICAL,
            fails the render. Catches `import X from "../../src/components/..."`
            and similar reuse of stock creative components.
          - art_direction_declared (bool) + art_direction (str|None) — WARNING.
            Forces step 1 of the bespoke-composition skill (commit to a fresh
            art direction per video) to be written down rather than skipped.
        """
        import re as _re

        issues: list[str] = []
        offending: list[dict[str, str]] = []
        project_dir = entry_path.parent
        pat = _re.compile(self._ATELIER_STOCK_IMPORT_RE)

        try:
            for f in project_dir.rglob("*"):
                if not f.is_file() or f.suffix.lower() not in {".tsx", ".ts", ".jsx", ".js"}:
                    continue
                try:
                    txt = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for m in pat.finditer(txt):
                    offending.append({"file": str(f.relative_to(project_dir)), "import": m.group(1)})
        except Exception as e:  # pragma: no cover — never let the check itself break a render
            issues.append(f"atelier stock-reuse scan errored: {e}")

        stock_reuse_detected = bool(offending)
        if stock_reuse_detected:
            issues.append(
                "atelier doctrine violation: bespoke project imports from the stock "
                "creative registry. Hand-author the scene instead — the registry is "
                "a mechanics codex, not a parts bin. Offending imports: "
                + ", ".join(f"{o['file']} → {o['import']}" for o in offending[:5])
                + ("…" if len(offending) > 5 else "")
            )

        art_direction = bespoke.get("art_direction") or bespoke.get("art_direction_note")
        art_direction_declared = bool(art_direction and str(art_direction).strip())
        if not art_direction_declared:
            issues.append(
                "atelier warning: no bespoke.art_direction declared. Per "
                "skills/meta/bespoke-composition.md step 1, every atelier piece must "
                "commit to a fresh art direction (palette, type, motion, signature "
                "device) before authoring. Pass edit_decisions.bespoke.art_direction "
                "as a short note or a path to art-direction.md."
            )

        return {
            "stock_reuse_detected": stock_reuse_detected,
            "offending_imports": offending,
            "art_direction_declared": art_direction_declared,
            "art_direction": str(art_direction) if art_direction else None,
            "issues": issues,
        }

    @staticmethod
    def _build_theme_from_playbook(
        playbook_name: str | None,
        composition_data: dict | None,
    ) -> dict[str, Any] | None:
        """Derive a Remotion ThemeConfig from a playbook's actual color values.

        Instead of passing a playbook name and hoping Remotion has a matching
        preset, we read the playbook YAML and extract concrete colors/fonts.
        This means custom playbooks, overridden palettes, and per-project
        styles all flow through to Remotion automatically.

        Falls back to extracting colors from edit_decisions metadata if
        no playbook is loadable.
        """
        theme: dict[str, Any] = {}

        # Try to load the playbook YAML
        playbook: dict[str, Any] = {}
        if playbook_name:
            try:
                from styles.playbook_loader import load_playbook
                playbook = load_playbook(playbook_name)
            except Exception:
                pass

        if playbook:
            vl = playbook.get("visual_language", {})
            palette = vl.get("color_palette", {})
            typo = playbook.get("typography", {})

            # Extract primary/accent — may be a list (gradient stops) or string
            primary_raw = palette.get("primary", ["#2563EB"])
            accent_raw = palette.get("accent", ["#F59E0B"])
            primary = primary_raw[0] if isinstance(primary_raw, list) else primary_raw
            accent = accent_raw[0] if isinstance(accent_raw, list) else accent_raw

            bg = palette.get("background", "#FFFFFF")
            text = palette.get("text", "#1F2937")
            surface = palette.get("surface", bg)
            muted = palette.get("muted_text", "#6B7280")

            # Build chart colors from all palette entries
            chart_colors = []
            for key in ["primary", "accent", "secondary", "success", "warning", "info"]:
                val = palette.get(key)
                if val:
                    chart_colors.append(val[0] if isinstance(val, list) else val)
            if len(chart_colors) < 3:
                chart_colors = [primary, accent, "#10B981", "#8B5CF6", "#EC4899", "#06B6D4"]

            theme = {
                "primaryColor": primary,
                "accentColor": accent,
                "backgroundColor": bg,
                "surfaceColor": surface,
                "textColor": text,
                "mutedTextColor": muted,
                "headingFont": typo.get("heading", {}).get("font", "Inter"),
                "bodyFont": typo.get("body", {}).get("font", "Inter"),
                "monoFont": typo.get("code", {}).get("font", "JetBrains Mono"),
                "chartColors": chart_colors[:6],
                "springConfig": {"damping": 20, "stiffness": 120, "mass": 1},
                "transitionDuration": 0.4,
            }

            # Derive caption colors from the palette
            theme["captionHighlightColor"] = primary
            # Caption background: semi-transparent version of the bg color
            theme["captionBackgroundColor"] = (
                f"rgba(255, 255, 255, 0.85)" if bg.upper() in ("#FFFFFF", "#FAFAFA", "#F9FAFB")
                else f"rgba(15, 23, 42, 0.75)"
            )

            # Motion style from playbook
            motion = playbook.get("motion", {})
            pace = motion.get("pace", "moderate")
            if pace == "fast":
                theme["springConfig"] = {"damping": 12, "stiffness": 80, "mass": 1}
                theme["transitionDuration"] = 0.3
            elif pace == "slow":
                theme["springConfig"] = {"damping": 25, "stiffness": 150, "mass": 1}
                theme["transitionDuration"] = 0.6

        # Fallback: try to extract from edit_decisions metadata
        if not theme and composition_data:
            meta = composition_data.get("metadata", {})
            if meta.get("primary_color"):
                theme = {
                    "primaryColor": meta["primary_color"],
                    "accentColor": meta.get("accent_color", "#F59E0B"),
                    "backgroundColor": meta.get("background_color", "#FFFFFF"),
                    "surfaceColor": meta.get("surface_color", "#F9FAFB"),
                    "textColor": meta.get("text_color", "#1F2937"),
                    "mutedTextColor": "#6B7280",
                    "headingFont": meta.get("heading_font", "Inter"),
                    "bodyFont": meta.get("body_font", "Inter"),
                    "monoFont": "JetBrains Mono",
                    "chartColors": meta.get("chart_colors", ["#2563EB", "#F59E0B", "#10B981"]),
                    "springConfig": {"damping": 20, "stiffness": 120, "mass": 1},
                    "transitionDuration": 0.4,
                    "captionHighlightColor": meta["primary_color"],
                    "captionBackgroundColor": "rgba(255, 255, 255, 0.85)",
                }

        return theme if theme else None

    def _needs_remotion(self, cuts: list[dict]) -> bool:
        """Determine whether Remotion should handle this composition.

        Remotion is the DEFAULT composition engine when available.  It handles
        video clips (via <OffthreadVideo>), still images, animated scene types,
        component types, transitions, and mixed content — all in a single
        React-based render pass.

        Returns False (i.e. use FFmpeg) only when Remotion is not
        available. For `operation="render"` the governance default is
        Remotion-first: the renderer family was chosen earlier, and the
        tool should preserve that decision instead of silently
        downgrading to FFmpeg.

        This "Remotion-first" policy means mixed content (video clips +
        animated stills + text cards) is always composed in Remotion, which
        can embed <OffthreadVideo> alongside React components natively.
        """
        # If Remotion isn't installed, fall back to FFmpeg
        if not self._remotion_available():
            return False

        # Any rich content → Remotion (fast path, catches the obvious cases)
        for cut in cuts:
            source = cut.get("source", "")
            if source and Path(source).suffix.lower() in self._IMAGE_EXTENSIONS:
                return True
            if cut.get("type") in self._REMOTION_SCENE_TYPES:
                return True
            if cut.get("animation") or cut.get("transition_in") or cut.get("transition_out"):
                return True
            transform = cut.get("transform", {})
            if transform and transform.get("animation"):
                return True

        # Even for pure-video cuts, default to Remotion — it handles video
        # clips natively via <OffthreadVideo> and gives us transitions,
        # overlays, and profile scaling for free.
        return True

    def _pre_compose_validation(
        self,
        edit_decisions: dict[str, Any],
        resolved_cuts: list[dict],
        scene_plan: list[dict] | None = None,
    ) -> ToolResult | None:
        """Pre-compose quality gate — blocks render on critical violations.

        Checks:
        1. Delivery promise violation: motion-required brief with >70% still cuts → BLOCK
        2. Slideshow risk score "fail" (average ≥ 4.0) → BLOCK
        3. Missing renderer_family → WARN (log only, don't block)

        Returns a failed ToolResult if render should be blocked, None if OK to proceed.
        """
        log = logging.getLogger("video_compose")
        warnings: list[str] = []
        blocks: list[str] = []

        # --- 0. Editorial visual-beat and narration alignment gate ---
        # The cadence is opt-in at the artifact level so legacy low-level
        # compositions remain readable, while creator profiles can make the
        # policy mandatory by writing it into edit_decisions.metadata.
        metadata = edit_decisions.get("metadata") or {}
        cadence = metadata.get("visual_beat_cadence_seconds")
        timeline_report: dict[str, Any] | None = None
        narration_report: dict[str, Any] | None = None
        if cadence is not None:
            declared_duration = (
                edit_decisions.get("total_duration_seconds")
                or metadata.get("target_duration_seconds")
                or max((float(c.get("out_seconds", 0) or 0) for c in resolved_cuts), default=0.0)
            )
            try:
                timeline_report = validate_visual_timeline(
                    resolved_cuts,
                    duration_seconds=float(declared_duration),
                    beat_seconds=float(cadence),
                    minimum_beats=metadata.get("minimum_visual_beats"),
                )
                for warning in timeline_report.get("warnings", []):
                    warnings.append(f"Visual timeline: {warning}")
                for error in timeline_report.get("errors", []):
                    blocks.append(f"Visual timeline violation: {error}")

                narration = (edit_decisions.get("audio") or {}).get("narration")
                if isinstance(narration, dict):
                    narration_report = validate_narration_timeline(
                        narration,
                        float(declared_duration),
                    )
                    for warning in narration_report.get("warnings", []):
                        warnings.append(f"Narration timeline: {warning}")
                    for error in narration_report.get("errors", []):
                        blocks.append(f"Narration timeline violation: {error}")
            except (TypeError, ValueError) as exc:
                blocks.append(f"Visual timeline policy is invalid: {exc}")

        # --- 1. Delivery promise check ---
        delivery_data = edit_decisions.get("metadata", {}).get("delivery_promise")
        if not delivery_data:
            # Also check top-level (proposal_packet nests it at top level)
            delivery_data = edit_decisions.get("delivery_promise")

        if delivery_data:
            try:
                from lib.delivery_promise import DeliveryPromise
                promise = DeliveryPromise.from_dict(delivery_data)
                result = promise.validate_cuts(resolved_cuts)
                if not result["valid"]:
                    for v in result["violations"]:
                        blocks.append(f"Delivery promise violation: {v}")
            except Exception as e:
                log.warning("Could not validate delivery promise: %s", e)
        else:
            warnings.append("No delivery_promise in edit_decisions — skipping promise validation")

        # --- 2. Slideshow risk check ---
        renderer_family = edit_decisions.get("renderer_family")
        scenes = scene_plan or []

        # If no scene_plan passed, try to extract scene info from cuts
        if not scenes and resolved_cuts:
            scenes = [
                {
                    "type": c.get("type", ""),
                    "description": c.get("reason", ""),
                    "shot_language": c.get("shot_language", {}),
                    "shot_intent": c.get("shot_intent"),
                    "narrative_role": c.get("narrative_role"),
                    "information_role": c.get("information_role"),
                    "hero_moment": c.get("hero_moment", False),
                }
                for c in resolved_cuts
            ]

        if scenes:
            try:
                from lib.slideshow_risk import score_slideshow_risk
                render_runtime = edit_decisions.get("render_runtime")
                risk = score_slideshow_risk(
                    scenes, edit_decisions, renderer_family, render_runtime
                )
                if risk["verdict"] == "fail":
                    blocks.append(
                        f"Slideshow risk score {risk['average']:.1f}/5.0 (verdict: fail). "
                        f"Video plan looks like a slideshow — revise scene plan before rendering."
                    )
                elif risk["verdict"] == "revise":
                    warnings.append(
                        f"Slideshow risk score {risk['average']:.1f}/5.0 (verdict: revise). "
                        f"Consider improving scene variety before final render."
                    )
            except Exception as e:
                log.warning("Could not compute slideshow risk: %s", e)

        # --- 3. Missing renderer_family (BLOCK — must be set at proposal) ---
        if not renderer_family:
            blocks.append(
                "No renderer_family in edit_decisions. "
                "renderer_family must be set at proposal stage and locked before compose. "
                "Re-run the proposal stage with a renderer_family selection."
            )

        # Log warnings
        for w in warnings:
            log.warning("[pre-compose] %s", w)

        # Block on critical violations
        if blocks:
            return ToolResult(
                success=False,
                error=(
                    "Pre-compose validation failed — render blocked.\n"
                    + "\n".join(f"  • {b}" for b in blocks)
                    + ("\n\nWarnings:\n" + "\n".join(f"  • {w}" for w in warnings) if warnings else "")
                ),
            )

        return None

    def _render(self, inputs: dict[str, Any]) -> ToolResult:
        """High-level render: assemble edit decisions + asset manifest into final video.

        This is the primary entry point for the compose-director skill.
        It resolves asset IDs and routes to the composition engine:

        - **Remotion (default):** Used for all compositions when available —
          video clips, images, animated scenes, component types, mixed content.
          Remotion embeds video via <OffthreadVideo> and handles transitions,
          overlays, and profile scaling natively.
        - **FFmpeg (fallback):** Used only when Remotion is unavailable, or
          when the agent explicitly calls operation='compose' for simple
          trim/concat operations.

        The agent should pass edit_decisions, asset_manifest, and optionally
        profile, subtitle_path, audio_path, and options.
        """
        edit_decisions = inputs.get("edit_decisions")
        asset_manifest = inputs.get("asset_manifest")
        if not edit_decisions:
            return ToolResult(success=False, error="edit_decisions required for render")

        # --- Runtime routing: honor render_runtime locked at proposal ---
        # Silent swaps are forbidden by governance. Resolve this before any
        # composition-mode branching so `composition_mode="atelier"` cannot
        # accidentally force the Remotion atelier path when HyperFrames or
        # FFmpeg was approved.
        render_runtime = (edit_decisions.get("render_runtime") or "").strip().lower()

        if not render_runtime:
            return ToolResult(
                success=False,
                error=(
                    "render_runtime is not set in edit_decisions. Per governance, "
                    "it MUST be locked at proposal stage (proposal_packet."
                    "production_plan.render_runtime) and carried forward through "
                    "edit_decisions.render_runtime. Valid values: 'remotion', "
                    "'hyperframes', 'ffmpeg'. Re-run the proposal stage with an "
                    "explicit runtime choice — do NOT default this field."
                ),
            )

        if render_runtime not in {"remotion", "hyperframes", "ffmpeg"}:
            return ToolResult(
                success=False,
                error=(
                    f"Unknown render_runtime {render_runtime!r}. "
                    f"Valid values: remotion, hyperframes, ffmpeg. "
                    f"render_runtime must be set at proposal stage."
                ),
            )

        # --- Atelier (bespoke) mode -------------------------------------
        # Hand-authored, project-local Remotion composition. Deliberately
        # bypasses the cut-schema, the stock scene-type registry, and the
        # RENDERER_FAMILY_MAP. This is the "hand-stitched every time" path:
        # the agent writes a fresh composition (its own scenes, theme, motion)
        # under remotion-composer/projects/<slug>/ and points this renderer at
        # it. No reusable creative components; a new visual language per video.
        # Triggered by composition_mode="atelier" (or renderer_family="bespoke").
        remotion_atelier_requested = (
            edit_decisions.get("composition_mode") == "atelier"
            or edit_decisions.get("renderer_family") == "bespoke"
        )
        if render_runtime == "remotion" and remotion_atelier_requested:
            return self._render_via_atelier(inputs, edit_decisions)

        if not asset_manifest:
            return ToolResult(success=False, error="asset_manifest required for render")

        raw_output_path = inputs.get("output_path", "renders/output.mp4")
        project_root = self._project_root_for_inputs(inputs, Path(raw_output_path))
        output_path = self._resolve_output_path(raw_output_path, project_root=project_root)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Keep the manifest immutable for callers, but give each runtime a
        # path-normalized view.  The canonical asset-manifest schema stores
        # local paths relative to the project; every renderer must receive a
        # concrete path tied to this run's project workspace.
        resolved_asset_manifest = dict(asset_manifest)
        resolved_assets: list[dict[str, Any]] = []
        for asset in asset_manifest.get("assets", []) or []:
            resolved_asset = dict(asset)
            if "path" in resolved_asset:
                resolved_asset["path"] = self._resolve_project_path(
                    resolved_asset.get("path"), project_root=project_root
                )
            resolved_assets.append(resolved_asset)
        resolved_asset_manifest["assets"] = resolved_assets

        # Build asset lookup: id -> asset info
        asset_lookup = {a["id"]: a for a in resolved_assets if "id" in a}

        cuts = edit_decisions.get("cuts", [])
        if not cuts:
            return ToolResult(success=False, error="No cuts in edit_decisions")

        # Resolve asset IDs in cuts to file paths
        resolved_cuts = []
        for cut in cuts:
            source_id = cut.get("source", "")
            resolved_cut = dict(cut)
            if source_id in asset_lookup:
                resolved_cut["source"] = asset_lookup[source_id]["path"]
            else:
                resolved_cut["source"] = self._resolve_project_path(
                    resolved_cut.get("source"), project_root=project_root
                )
            resolved_cuts.append(resolved_cut)

        # Audio/subtitle paths are also project-relative artifact references.
        # Normalize them once so all three runtime adapters receive the same
        # paths and no adapter depends on its own working directory.
        normalized_inputs = dict(inputs)
        for key in ("audio_path", "subtitle_path", "narration_transcript_path", "script_path"):
            if normalized_inputs.get(key):
                normalized_inputs[key] = self._resolve_project_path(
                    normalized_inputs[key], project_root=project_root
                )
        inputs = normalized_inputs

        # --- Pre-compose validation gate ---
        scene_plan_input = inputs.get("scene_plan")
        # Callers may pass the complete scene_plan artifact or its scenes list.
        # Normalize here so slideshow-risk scoring never iterates artifact keys
        # as if they were scene objects.
        scene_plan = (
            scene_plan_input.get("scenes", [])
            if isinstance(scene_plan_input, dict)
            else scene_plan_input
        )
        validation_block = self._pre_compose_validation(edit_decisions, resolved_cuts, scene_plan)
        if validation_block is not None:
            return validation_block

        # Also accept profile as "output_profile" (skill convention) or "profile"
        profile = inputs.get("profile") or inputs.get("output_profile")

        planned_duration = (
            edit_decisions.get("total_duration_seconds")
            or (edit_decisions.get("metadata") or {}).get("target_duration_seconds")
        )
        if profile and planned_duration:
            try:
                from lib.media_profiles import validate_duration
                validate_duration(profile, float(planned_duration))
            except (ImportError, ValueError) as exc:
                return ToolResult(success=False, error=str(exc))

        if render_runtime == "hyperframes":
            return self._render_via_hyperframes(
                inputs=inputs,
                edit_decisions=edit_decisions,
                asset_manifest=resolved_asset_manifest,
                resolved_cuts=resolved_cuts,
                output_path=output_path,
                profile=profile,
            )
        if render_runtime == "ffmpeg":
            # Caller explicitly asked for FFmpeg — don't auto-upgrade to Remotion.
            return self._render_via_ffmpeg(
                inputs=inputs,
                edit_decisions=edit_decisions,
                asset_manifest=resolved_asset_manifest,
                resolved_cuts=resolved_cuts,
                output_path=output_path,
                profile=profile,
            )
        # --- Explicit Remotion path (render_runtime == 'remotion') ---
        if self._needs_remotion(resolved_cuts):
            remotion_inputs: dict[str, Any] = {
                "edit_decisions": dict(edit_decisions, cuts=resolved_cuts),
                "output_path": str(output_path),
            }
            for key in ("project_dir", "project_path", "run_id", "stage", "agent_id"):
                if inputs.get(key) is not None:
                    remotion_inputs[key] = inputs[key]
            if profile:
                remotion_inputs["profile"] = profile
            if inputs.get("audio_path") is not None:
                remotion_inputs["audio_path"] = inputs["audio_path"]
            if inputs.get("audio") is not None:
                remotion_inputs["audio"] = inputs["audio"]
            for key in (
                "subtitle_path", "captions", "transcript", "verified_transcript", "require_verified_transcript",
                "transcript_verified", "expected_language", "expected_text", "caption_mode",
                "safe_area", "max_lines", "max_chars_per_line", "caption_font_size",
            ):
                if inputs.get(key) is not None:
                    remotion_inputs[key] = inputs[key]
            # Forward the creator-facing render timeout through the high-level
            # render path (execute(operation="render") -> _render), otherwise it
            # would only take effect on a direct _remotion_render() call.
            if inputs.get("remotion_timeout_ms") is not None:
                remotion_inputs["remotion_timeout_ms"] = inputs["remotion_timeout_ms"]
            if inputs.get("render_timeout_seconds") is not None:
                remotion_inputs["render_timeout_seconds"] = inputs["render_timeout_seconds"]
            for key in ("render_concurrency", "workers", "production_mode", "resource_budget"):
                if inputs.get(key) is not None:
                    remotion_inputs[key] = inputs[key]
            render_result = self._remotion_render(remotion_inputs)

            # Governance: NEVER silently fall back to FFmpeg when Remotion fails.
            # The agent must decide the fallback path, not the tool.
            if not render_result.success:
                renderer_family = edit_decisions.get("renderer_family", "unknown")
                return ToolResult(
                    success=False,
                    error=(
                        f"Remotion render failed for renderer_family={renderer_family!r}. "
                        f"Underlying error: {render_result.error}\n\n"
                        f"This composition requires Remotion (images, text cards, animations). "
                        f"Options:\n"
                        f"  1. Fix Remotion setup (cd remotion-composer && npm install)\n"
                        f"  2. Re-run with operation='compose' for FFmpeg-only (video cuts only)\n"
                        f"  3. Approve a degraded FFmpeg render (still images → Ken Burns)\n\n"
                        f"Per governance: renderer downgrade requires user approval."
                    ),
                )
        else:
            # --- FFmpeg fallback: only when Remotion is unavailable ---
            options = inputs.get("options", {})
            subtitle_burn = options.get("subtitle_burn", True)

            # Resolve subtitle_path from edit_decisions if not provided
            subtitle_path = inputs.get("subtitle_path")
            if subtitle_burn and not subtitle_path:
                ed_subs = edit_decisions.get("subtitles", {})
                if ed_subs.get("enabled") and ed_subs.get("source"):
                    subtitle_path = ed_subs["source"]

            # Build compose inputs
            compose_inputs = dict(inputs)
            compose_inputs["edit_decisions"] = dict(edit_decisions, cuts=resolved_cuts)
            compose_inputs["output_path"] = str(output_path)
            if subtitle_path:
                compose_inputs["subtitle_path"] = subtitle_path
            if profile:
                compose_inputs["profile"] = profile
            for key in (
                "captions", "transcript", "verified_transcript", "require_verified_transcript",
                "transcript_verified", "expected_language", "expected_text", "caption_mode",
                "safe_area", "max_lines", "max_chars_per_line", "caption_font_size",
                "max_words_per_cue",
            ):
                if inputs.get(key) is not None:
                    compose_inputs[key] = inputs[key]

            render_result = self._compose(compose_inputs)

        # --- Post-render: mandatory final self-review ---
        if render_result.success and output_path.exists():
            final_review = self._run_final_review(
                output_path,
                edit_decisions,
                inputs.get("proposal_packet"),
                asset_manifest=resolved_asset_manifest,
                project_root=project_root,
                narration_transcript_path=inputs.get("narration_transcript_path"),
                script_text=inputs.get("script_text") or self._read_text_file(
                    inputs.get("script_path")
                ),
            )

            # Attach final_review to the ToolResult data so the compose-director
            # skill can include it in the checkpoint alongside the render_report.
            if render_result.data is None:
                render_result.data = {}
            render_result.data["final_review"] = final_review
            render_result.data["final_review_status"] = final_review["status"]

            # If the self-review says fail, downgrade the ToolResult
            if final_review["status"] != "pass":
                return ToolResult(
                    success=False,
                    error=(
                        "Post-render self-review FAILED. The output is not presentable.\n"
                        + "\n".join(f"  • {i}" for i in final_review.get("issues_found", []))
                    ),
                    data=render_result.data,
                )

        return render_result

    def _render_via_hyperframes(
        self,
        *,
        inputs: dict[str, Any],
        edit_decisions: dict[str, Any],
        asset_manifest: dict[str, Any],
        resolved_cuts: list[dict],
        output_path: Path,
        profile: Optional[str],
    ) -> ToolResult:
        """Delegate to hyperframes_compose and run the mandatory final self-review.

        Governance: if HyperFrames is unavailable or fails, return a structured
        blocker — do NOT silently route to Remotion or FFmpeg. The agent must
        surface the blocker and get user approval before any runtime swap.
        """
        if not self._hyperframes_available():
            return ToolResult(
                success=False,
                error=(
                    "render_runtime='hyperframes' was locked at proposal, but "
                    "the HyperFrames runtime is not available on this machine. "
                    "Per governance this is a BLOCKER — surface it to the user "
                    "per AGENT_GUIDE.md > 'Escalate Blockers Explicitly' and wait "
                    "for approval before switching runtime. Requirements: "
                    "Node.js >= 22, FFmpeg, and npx on PATH. See "
                    "tools/video/hyperframes_compose.py for the specific missing piece."
                ),
            )

        try:
            from tools.video.hyperframes_compose import HyperFramesCompose
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Could not import hyperframes_compose: {e}",
            )

        project_root = self._project_root_for_inputs(inputs, output_path)
        run_envelope = self._run_paths_for_inputs(inputs, project_root)
        final_output_path = output_path
        candidate_output_path = self._candidate_output_for_run(run_envelope, final_output_path)
        if candidate_output_path is not None:
            output_path = candidate_output_path
        workspace_path = (
            inputs.get("workspace_path")
            or str(
                run_envelope.work / "hyperframes"
                if run_envelope is not None
                else output_path.parent.parent / "hyperframes"
            )
        )

        # Pass the playbook through so the style bridge can emit CSS vars.
        playbook_data = inputs.get("playbook")
        if not playbook_data:
            playbook_name = (
                inputs.get("playbook_name")
                or (edit_decisions.get("metadata") or {}).get("playbook")
            )
            if playbook_name:
                try:
                    from styles.playbook_loader import load_playbook  # type: ignore
                    playbook_data = load_playbook(playbook_name)
                except Exception:
                    playbook_data = None

        hf_inputs: dict[str, Any] = {
            "operation": "render",
            "workspace_path": workspace_path,
            "output_path": str(output_path),
            "edit_decisions": dict(edit_decisions, cuts=resolved_cuts),
            "asset_manifest": asset_manifest,
        }
        if project_root is not None:
            hf_inputs["project_dir"] = str(project_root)
        if run_envelope is not None:
            hf_inputs["run_id"] = run_envelope.run_id
        if playbook_data:
            hf_inputs["playbook"] = playbook_data
        if profile:
            hf_inputs["profile"] = profile
        if "quality" in inputs:
            hf_inputs["quality"] = inputs["quality"]
        if "fps" in inputs:
            hf_inputs["fps"] = inputs["fps"]
        if "strict" in inputs:
            hf_inputs["strict"] = inputs["strict"]
        for key in ("production_mode", "offline", "workers", "resource_budget"):
            if key in inputs:
                hf_inputs[key] = inputs[key]
        if "skip_contrast" in inputs:
            hf_inputs["skip_contrast"] = inputs["skip_contrast"]
        for key in (
            "subtitle_path", "captions", "transcript", "verified_transcript",
            "require_verified_transcript", "transcript_verified", "expected_language",
            "expected_text", "caption_mode", "safe_area", "max_lines",
            "max_chars_per_line", "caption_font_size", "max_words_per_cue",
        ):
            if inputs.get(key) is not None:
                hf_inputs[key] = inputs[key]

        render_result = HyperFramesCompose().execute(hf_inputs)

        if not render_result.success:
            if candidate_output_path is not None and candidate_output_path.exists():
                candidate_output_path.unlink(missing_ok=True)
            return ToolResult(
                success=False,
                error=(
                    f"HyperFrames render failed: {render_result.error}. "
                    "Per governance: do NOT silently fall back to Remotion or "
                    "FFmpeg. Surface the failure to the user along with the "
                    "hyperframes_compose step log before proposing a swap."
                ),
                data=render_result.data,
            )

        # Post-render: mandatory final self-review (identical contract to the Remotion path).
        if output_path.exists():
            final_review = self._run_final_review(
                output_path,
                edit_decisions,
                inputs.get("proposal_packet"),
                asset_manifest=asset_manifest,
                project_root=self._project_root_for_inputs(inputs, output_path),
                narration_transcript_path=inputs.get("narration_transcript_path"),
                script_text=inputs.get("script_text") or self._read_text_file(
                    inputs.get("script_path")
                ),
            )
            if render_result.data is None:
                render_result.data = {}
            render_result.data["final_review"] = final_review
            render_result.data["final_review_status"] = final_review["status"]
            if final_review["status"] != "pass":
                if candidate_output_path is not None and candidate_output_path.exists():
                    candidate_output_path.unlink(missing_ok=True)
                return ToolResult(
                    success=False,
                    error=(
                        "Post-render self-review FAILED (HyperFrames). The output is not presentable.\n"
                        + "\n".join(f"  • {i}" for i in final_review.get("issues_found", []))
                    ),
                    data=render_result.data,
                )

        promotion = None
        if candidate_output_path is not None:
            try:
                promotion = self._promote_run_output(
                    candidate=output_path,
                    final_path=final_output_path,
                    run_envelope=run_envelope,
                    inputs=inputs,
                    profile=profile,
                    expected_duration_seconds=self._expected_duration(edit_decisions, inputs),
                    stage=str(inputs.get("stage") or "compose"),
                    tool="hyperframes_compose",
                )
            except Exception as exc:
                if candidate_output_path.exists():
                    candidate_output_path.unlink(missing_ok=True)
                return ToolResult(
                    success=False,
                    error=f"HyperFrames output promotion failed: {exc}",
                    data=render_result.data,
                )
            if isinstance(render_result.data, dict):
                render_result.data["output"] = str(final_output_path)
                render_result.data["output_promotion"] = promotion
                review = render_result.data.get("final_review")
                if isinstance(review, dict):
                    review["output_path"] = str(final_output_path)

        return render_result

    def _render_via_ffmpeg(
        self,
        *,
        inputs: dict[str, Any],
        edit_decisions: dict[str, Any],
        asset_manifest: dict[str, Any] | None = None,
        resolved_cuts: list[dict],
        output_path: Path,
        profile: Optional[str],
    ) -> ToolResult:
        """Explicit FFmpeg-only render path.

        Use when the proposal locked `render_runtime="ffmpeg"` — e.g. simple
        source-footage concat/trim jobs that don't benefit from composition.
        Still runs the mandatory final self-review.
        """
        options = inputs.get("options", {})
        subtitle_burn = options.get("subtitle_burn", True)

        subtitle_path = inputs.get("subtitle_path")
        if subtitle_burn and not subtitle_path:
            ed_subs = edit_decisions.get("subtitles", {})
            if ed_subs.get("enabled") and ed_subs.get("source"):
                subtitle_path = ed_subs["source"]

        compose_inputs = dict(inputs)
        compose_inputs["edit_decisions"] = dict(edit_decisions, cuts=resolved_cuts)
        compose_inputs["output_path"] = str(output_path)
        if subtitle_path:
            compose_inputs["subtitle_path"] = subtitle_path
        for key in (
            "captions", "transcript", "verified_transcript", "require_verified_transcript",
            "transcript_verified", "expected_language", "expected_text", "caption_mode",
            "safe_area", "max_lines", "max_chars_per_line", "caption_font_size",
            "max_words_per_cue",
        ):
            if inputs.get(key) is not None:
                compose_inputs[key] = inputs[key]
        if profile:
            compose_inputs["profile"] = profile

        render_result = self._compose(compose_inputs)

        if render_result.success and output_path.exists():
            final_review = self._run_final_review(
                output_path,
                edit_decisions,
                inputs.get("proposal_packet"),
                asset_manifest=asset_manifest,
                project_root=self._project_root_for_inputs(inputs, output_path),
                narration_transcript_path=inputs.get("narration_transcript_path"),
                script_text=inputs.get("script_text") or self._read_text_file(
                    inputs.get("script_path")
                ),
            )
            if render_result.data is None:
                render_result.data = {}
            render_result.data["final_review"] = final_review
            render_result.data["final_review_status"] = final_review["status"]
            if final_review["status"] != "pass":
                return ToolResult(
                    success=False,
                    error=(
                        "Post-render self-review FAILED (FFmpeg). The output is not presentable.\n"
                        + "\n".join(f"  • {i}" for i in final_review.get("issues_found", []))
                    ),
                    data=render_result.data,
                )

        return render_result

    def _prepare_remotion_captions(
        self, props: dict[str, Any], inputs: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Attach word props plus the shared caption contract to Remotion."""

        from lib.caption_contracts import (
            CaptionContractError,
            build_caption_render_contract,
            cues_from_transcript,
            load_caption_cues,
            validate_verified_transcript,
        )

        transcript = inputs.get("transcript") or inputs.get("verified_transcript")
        verification: dict[str, Any] | None = None
        words: list[dict[str, Any]] = []
        raw_cues: list[dict[str, Any]] | None = None
        try:
            require_verified = (
                strict_bool(inputs["require_verified_transcript"], "require_verified_transcript")
                if "require_verified_transcript" in inputs
                else False
            )
            transcript_verified = (
                strict_bool(inputs["transcript_verified"], "transcript_verified")
                if "transcript_verified" in inputs
                else False
            )
        except MediaContractError as exc:
            raise CaptionContractError(str(exc)) from exc

        def words_from_transcript(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
            output: list[dict[str, Any]] = []
            for segment in payload.get("segments") or payload.get("word_timestamps") or []:
                if not isinstance(segment, Mapping):
                    continue
                segment_words = segment.get("words")
                if isinstance(segment_words, list) and segment_words:
                    for item in segment_words:
                        if not isinstance(item, Mapping):
                            continue
                        text = str(item.get("word") or item.get("text") or "").strip()
                        if not text:
                            continue
                        output.append({
                            "word": text,
                            "startMs": int(round(float(item.get("start", 0)) * 1000)),
                            "endMs": int(round(float(item.get("end", 0)) * 1000)),
                        })
                elif segment.get("text"):
                    text_words = str(segment["text"]).split()
                    start = float(segment.get("start", 0) or 0)
                    end = float(segment.get("end", start) or start)
                    per_word = max(0.001, (end - start) / max(len(text_words), 1))
                    for index, text in enumerate(text_words):
                        output.append({
                            "word": text,
                            "startMs": int(round((start + index * per_word) * 1000)),
                            "endMs": int(round((start + (index + 1) * per_word) * 1000)),
                        })
            return output

        if isinstance(transcript, dict):
            if require_verified:
                payload = dict(transcript)
                payload.setdefault("segments", [])
                if "verified" not in payload and "verification_status" not in payload:
                    payload["verified"] = transcript_verified
                verification = validate_verified_transcript(
                    payload,
                    expected_language=inputs.get("expected_language"),
                    expected_text=inputs.get("expected_text"),
                )
                if verification.get("valid") is not True:
                    raise CaptionContractError(
                        "; ".join(verification.get("errors") or ["verified transcript validation failed"])
                    )
            words = words_from_transcript(transcript)
            raw_cues = cues_from_transcript(
                transcript,
                max_words_per_cue=int(inputs.get("max_words_per_cue", 6) or 6),
                max_chars_per_line=int(inputs.get("max_chars_per_line", 42) or 42),
            )
        elif isinstance(props.get("captions"), list) and props.get("captions"):
            words = [dict(item) for item in props["captions"] if isinstance(item, Mapping)]
            raw_cues = []
            page_size = int(inputs.get("max_words_per_cue", props.get("wordsPerPage", 6)) or 6)
            for offset in range(0, len(words), page_size):
                page = words[offset : offset + page_size]
                if not page:
                    continue
                raw_cues.append({
                    "index": len(raw_cues) + 1,
                    "start": float(page[0].get("startMs", 0)) / 1000,
                    "end": float(page[-1].get("endMs", 0)) / 1000,
                    "text": " ".join(str(item.get("word") or "") for item in page).strip(),
                    "words": [
                        {
                            "word": str(item.get("word") or ""),
                            "start": float(item.get("startMs", 0)) / 1000,
                            "end": float(item.get("endMs", 0)) / 1000,
                        }
                        for item in page
                    ],
                })
        else:
            subtitle_path = inputs.get("subtitle_path")
            if subtitle_path:
                raw_cues = load_caption_cues(subtitle_path)
                for cue in raw_cues:
                    start = float(cue["start"])
                    end = float(cue["end"])
                    text_words = str(cue.get("text") or "").split()
                    per_word = max(0.001, (end - start) / max(len(text_words), 1))
                    words.extend(
                        {
                            "word": text,
                            "startMs": int(round((start + index * per_word) * 1000)),
                            "endMs": int(round((start + (index + 1) * per_word) * 1000)),
                        }
                        for index, text in enumerate(text_words)
                    )

        if not words or not raw_cues:
            if bool((props.get("subtitles") or {}).get("enabled")) or require_verified:
                raise CaptionContractError("captions are expected but no renderable caption cues were provided")
            return None, verification
        props["captions"] = words
        profile_name = inputs.get("profile")
        width, height, fps = 1920, 1080, int(inputs.get("fps", 30) or 30)
        if profile_name:
            try:
                from lib.media_profiles import get_profile
                profile = get_profile(profile_name)
                width, height, fps = int(profile.width), int(profile.height), int(profile.fps)
            except (ImportError, ValueError):
                pass
        cuts = props.get("cuts") or []
        duration = max((float(cut.get("out_seconds") or 0) for cut in cuts if isinstance(cut, Mapping)), default=0.0)
        if duration <= 0:
            duration = max(float(cue.get("end") or 0) for cue in raw_cues)
        max_lines = int(inputs.get("max_lines", 2) or 2)
        max_chars = int(inputs.get("max_chars_per_line", 42) or 42)
        font_size = int(inputs.get("caption_font_size", props.get("fontSize", 42)) or 42)
        contract = build_caption_render_contract(
            runtime="remotion",
            mode="burn_in",
            cues=raw_cues,
            width=width,
            height=height,
            duration_seconds=duration,
            fps=fps,
            safe_area=inputs.get("safe_area"),
            max_chars_per_line=max_chars,
            max_lines=max_lines,
            font_size=font_size,
            style={"words_per_page": props.get("wordsPerPage", 6), "caption_position": "bottom-center"},
            transcript_verification=verification,
            profile_name=profile_name,
        )
        props["captionContract"] = contract
        return contract, verification

    def _remotion_render(self, inputs: dict[str, Any]) -> ToolResult:
        """Render via Remotion (requires Node.js + npx).

        Handles compositions with still images, animated scenes, component
        types, and transitions using React-based frame-accurate rendering.
        Accepts edit_decisions (with resolved file paths) or raw composition_data.
        """
        import shutil

        if not shutil.which("npx"):
            return ToolResult(
                success=False,
                error="npx not found. Install Node.js to use Remotion rendering.",
            )

        composition_data = inputs.get("edit_decisions") or inputs.get("composition_data")
        if not composition_data:
            return ToolResult(
                success=False,
                error="edit_decisions or composition_data required for remotion_render",
            )

        import os as _os
        raw_output_path = inputs.get("output_path", "renders/remotion_output.mp4")
        project_root = self._project_root_for_inputs(inputs, Path(raw_output_path))
        output_path = self._resolve_output_path(raw_output_path, project_root=project_root)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        run_envelope = self._run_paths_for_inputs(inputs, project_root)
        final_output_path = output_path
        candidate_output_path = self._candidate_output_for_run(run_envelope, final_output_path)
        if candidate_output_path is not None:
            output_path = candidate_output_path
        # Absolutise so the CLI can resolve the output regardless of cwd.
        output_path = output_path.resolve()

        # Deep-copy props so we don't mutate the original
        props = json.loads(json.dumps(composition_data))
        try:
            caption_contract, transcript_verification = self._prepare_remotion_captions(props, inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"Caption render contract rejected: {exc}")

        # remotion-composer lives at project root
        composer_dir = Path(__file__).resolve().parent.parent.parent / "remotion-composer"
        repo_root = composer_dir.parent
        if run_envelope is not None:
            # Never share public/staged assets between projects or runs. The
            # run envelope is retained for replay and audit; only legacy,
            # non-project calls use the composer-level scratch directory.
            public_dir = run_envelope.inputs / f"remotion-public-{uuid.uuid4().hex}"
        else:
            public_dir = composer_dir / "public"
        staged_dir = public_dir / "staged_assets"
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_names: dict[str, str] = {}

        def _staged_name(source_path: Path) -> str:
            key = str(source_path.resolve())
            existing = staged_names.get(key)
            if existing:
                return existing
            # Prefixing with the first source index prevents two different
            # assets named ``logo.png`` from silently overwriting each other.
            name = f"{len(staged_names):04d}_{source_path.name}"
            staged_names[key] = name
            return name

        for cut in props.get("cuts", []):
            source = cut.get("source", "")
            if source and not source.startswith(("http://", "https://")):
                resolved = Path(
                    self._resolve_project_path(source, project_root=project_root)
                ).resolve()
                if resolved.exists():
                    try:
                        staged_name = _staged_name(resolved)
                        staged_file = staged_dir / staged_name
                        if not staged_file.exists() or staged_file.stat().st_mtime < resolved.stat().st_mtime:
                            shutil.copy2(resolved, staged_file)
                        cut["source"] = f"staged_assets/{staged_name}"
                    except Exception:
                        posix = resolved.as_posix()
                        cut["source"] = f"file:///{posix}" if not posix.startswith("/") else f"file://{posix}"

        # Handle narration audio staging
        audio_input = inputs.get("audio_path") or inputs.get("audio")
        if audio_input and isinstance(audio_input, str):
            audio_resolved = Path(
                self._resolve_project_path(audio_input, project_root=project_root)
            ).resolve()
            if audio_resolved.exists():
                staged_name = _staged_name(audio_resolved)
                staged_audio = staged_dir / staged_name
                if not staged_audio.exists() or staged_audio.stat().st_mtime < audio_resolved.stat().st_mtime:
                    shutil.copy2(audio_resolved, staged_audio)
                props.setdefault("audio", {})
                if not isinstance(props["audio"], dict):
                    props["audio"] = {}
                props["audio"].setdefault("narration", {})
                if not isinstance(props["audio"]["narration"], dict):
                    props["audio"]["narration"] = {}
                props["audio"]["narration"]["src"] = f"staged_assets/{staged_name}"
                props["audio"]["narration"]["volume"] = props["audio"]["narration"].get("volume", 1.0)

        # Build a custom themeConfig from the playbook's actual colors.
        # This ensures every video gets a unique visual identity derived
        # from its production decisions — not picked from a preset menu.
        if "themeConfig" not in props:
            playbook_name = (
                props.get("playbook")
                or props.get("theme")
                or props.get("metadata", {}).get("playbook")
            )
            theme_config = self._build_theme_from_playbook(playbook_name, composition_data)
            if theme_config:
                props["themeConfig"] = theme_config

        # Durable runs keep their props in the run envelope. Legacy direct
        # calls retain the historical composer-level scratch location.
        out_dir = run_envelope.props if run_envelope is not None else composer_dir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        props_path = out_dir / (
            f"props_{output_path.stem}_{uuid.uuid4().hex}.json"
            if run_envelope is not None
            else f"props_{output_path.stem}.json"
        )
        with open(props_path, "w", encoding="utf-8") as f:
            json.dump(props, f)

        # remotion-composer lives at project root
        if not composer_dir.exists():
            return ToolResult(
                success=False,
                error=f"Remotion composer project not found at {composer_dir}",
            )

        # Route to the correct Remotion composition based on renderer_family.
        # This prevents all pipelines from collapsing into the Explainer visual grammar.
        renderer_family = (composition_data or {}).get("renderer_family", "explainer-data")
        composition_id = self._get_composition_id(renderer_family)

        # Remotion's browser workers consume real CPU/RAM.  The old fixed
        # ``--concurrency=8`` ignored the host profile and could saturate a
        # two-vCPU runner while four independent projects were in flight.  Use
        # the same conservative policy as HyperFrames, with an explicit
        # bounded override for operators who have measured a larger machine.
        profile_name = inputs.get("profile") or composition_data.get("profile")
        planned_duration_seconds = max(
            (float(cut.get("out_seconds") or 0) for cut in composition_data.get("cuts", [])),
            default=0.0,
        )
        requested_concurrency = inputs.get("render_concurrency")
        if requested_concurrency is None:
            requested_concurrency = inputs.get("workers")
        if requested_concurrency is None:
            requested_concurrency = composition_data.get("concurrency")
        try:
            from lib.hyperframes_contracts import select_worker_policy

            worker_policy = select_worker_policy(
                {
                    "cuts": composition_data.get("cuts", []),
                    "duration_seconds": planned_duration_seconds,
                    "profile": profile_name,
                },
                requested_workers=requested_concurrency,
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Remotion worker policy failed: {exc}")

        remotion_bin = composer_dir / "node_modules" / ".bin" / ("remotion.cmd" if sys.platform == "win32" else "remotion")
        bin_cmd = str(remotion_bin) if remotion_bin.exists() else "npx"
        props_abs = str(props_path.resolve())

        cmd = [
            bin_cmd,
        ]
        if bin_cmd == "npx":
            cmd.append("remotion")
        cmd.extend([
            "render",
            str(composer_dir / "src" / "index.tsx"),
            composition_id,
            str(output_path),
            f"--props={props_abs}",
            f"--public-dir={str(public_dir.resolve()) if run_envelope is not None else 'public'}",
            "--gl=angle",
            f"--concurrency={worker_policy['workers']}",
        ])

        # Apply media profile dimensions
        if profile_name:
            try:
                from lib.media_profiles import get_profile
                p = get_profile(profile_name)
                cmd.extend(["--width", str(p.width), "--height", str(p.height)])
            except (ImportError, ValueError):
                pass

        # Optional creator-facing render timeout. Remotion's `--timeout` (ms)
        # governs headless-browser setup and delayRender(); on slow machines or
        # restricted networks the default 30s browser setup times out with an
        # opaque failure. Pass it through and give the subprocess enough headroom
        # so run_command() does not kill Remotion before its own timeout fires.
        remotion_timeout_ms = inputs.get("remotion_timeout_ms")
        if profile_name == "ilearnzed_long_form":
            # Rendering cost grows with the authored timeline. Keep a generous
            # baseline, then scale the process safety window for longer lessons
            # so duration itself never becomes an implicit studio limit.
            subprocess_timeout = max(1800, int(planned_duration_seconds * 4 + 300))
        else:
            subprocess_timeout = 600
        if inputs.get("render_timeout_seconds") is not None:
            try:
                subprocess_timeout = max(1, int(inputs["render_timeout_seconds"]))
            except (TypeError, ValueError):
                pass
        if remotion_timeout_ms:
            try:
                ms = int(remotion_timeout_ms)
                cmd.append(f"--timeout={ms}")
                subprocess_timeout = max(subprocess_timeout, ms // 1000 + 60)
            except (TypeError, ValueError):
                pass

        render_completed = False
        try:
            # Invoke from inside the composer dir so npx can resolve the
            # local remotion binary via node_modules/.bin. Without this,
            # Windows npx cannot locate the CLI and returns "could not
            # determine executable to run".
            self.run_command(cmd, timeout=subprocess_timeout, cwd=composer_dir)
            render_completed = True
        except subprocess.CalledProcessError as e:
            # run_command uses check=True + capture_output, so the useful
            # Remotion diagnostics live in stderr/stdout — surface the tail
            # instead of the bare "returned non-zero exit status 1".
            detail = (e.stderr or e.stdout or "").strip()
            tail = "\n".join(detail.splitlines()[-25:]) if detail else "(no output captured)"
            return ToolResult(
                success=False,
                error=f"Remotion render failed (exit {e.returncode}):\n{tail}",
            )
        except subprocess.TimeoutExpired as e:
            return ToolResult(
                success=False,
                error=(
                    f"Remotion render timed out after {e.timeout}s. If the headless "
                    "browser is slow to start, raise remotion_timeout_ms (ms)."
                ),
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Remotion render failed: {e}")
        finally:
            if run_envelope is None:
                if props_path.exists():
                    props_path.unlink()
                if staged_dir.exists():
                    import shutil as _shutil
                    _shutil.rmtree(staged_dir, ignore_errors=True)
            if (
                candidate_output_path is not None
                and candidate_output_path.exists()
                and not render_completed
            ):
                try:
                    candidate_output_path.unlink()
                except OSError:
                    pass

        if not output_path.exists():
            if candidate_output_path is not None and candidate_output_path.exists():
                try:
                    candidate_output_path.unlink()
                except OSError:
                    pass
            return ToolResult(
                success=False,
                error=f"Remotion render completed but output file missing: {output_path}",
            )

        promotion = None
        if candidate_output_path is not None:
            try:
                promotion = self._promote_run_output(
                    candidate=output_path,
                    final_path=final_output_path,
                    run_envelope=run_envelope,
                    inputs=inputs,
                    profile=profile_name,
                    expected_duration_seconds=self._expected_duration(composition_data, inputs),
                    stage=str(inputs.get("stage") or "compose"),
                    tool=self.name,
                )
            except Exception:
                if candidate_output_path.exists():
                    try:
                        candidate_output_path.unlink()
                    except OSError:
                        pass
                raise

        media_probe = promotion.get("probe") if isinstance(promotion, dict) else None
        if candidate_output_path is None:
            try:
                from lib.output_promotion import probe_media, validate_media_contract

                media_probe = probe_media(final_output_path)
                validate_media_contract(
                    media_probe,
                    profile=profile_name,
                    expected_duration_seconds=self._expected_duration(composition_data, inputs),
                )
            except Exception as exc:
                return ToolResult(
                    success=False,
                    error=f"Remotion output media validation failed: {exc}",
                )

        return ToolResult(
            success=True,
            data={
                "operation": "remotion_render",
                "output": str(final_output_path),
                "profile": profile_name,
                "run_dir": str(run_envelope.root) if run_envelope else None,
                "props_path": str(props_path),
                "public_dir": str(public_dir),
                "worker_policy": worker_policy,
                "output_promotion": promotion,
                "media_probe": media_probe,
                "caption_render_contract": caption_contract,
                "transcript_verification": transcript_verification,
            },
            artifacts=[str(final_output_path)],
        )

    # ------------------------------------------------------------------
    # Final self-review — mandatory post-render inspection
    # ------------------------------------------------------------------

    # Punctuation/SSML-leak words that should NEVER appear in rendered audio.
    # When a TTS engine reads a literal "..." as the word "dot", or a "—" as
    # "hyphen", those leak into the transcript. Catching these in the final
    # review is the difference between catching a bad voice render in-tool
    # vs. shipping a video that says "dot dot dot" twelve times. CRITICAL.
    _TTS_PUNCTUATION_LEAK_WORDS = {
        "dot", "dots", "ellipsis", "period", "periods",
        "comma", "commas", "semicolon", "colon",
        "dash", "hyphen", "emdash", "endash",
        "parenthesis", "bracket", "brace",
        "asterisk", "slash", "backslash",
        "exclamation", "question mark",
    }

    @staticmethod
    def _read_text_file(path: str | Path | None) -> str | None:
        """Read a small text file if given a path; None-safe and exception-safe."""
        if not path:
            return None
        try:
            return Path(path).read_text(encoding="utf-8")
        except Exception:
            return None

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        """Split text into comparable word tokens (lowercased, punctuation
        stripped, numeric-word-aware). Empty tokens dropped."""
        import re

        # Preserve hyphenated words as single tokens ("many-worlds" -> "many-worlds").
        # Drop everything except letters, digits, hyphens, apostrophes.
        cleaned = re.sub(r"[^A-Za-z0-9\-' ]+", " ", text.lower())
        return [t for t in cleaned.split() if t and t != "-"]

    @staticmethod
    def _normalise_language(value: Any) -> str | None:
        """Return a comparable BCP-47-ish language code.

        Providers and STT engines use a mixture of ``language``, ``locale``
        and ``language_code`` fields.  The release gate compares the language
        tag and its base language (``en-US`` and ``en`` are compatible) while
        preserving the original value in the evidence record.
        """
        if value in (None, ""):
            return None
        raw = str(value).strip().replace("_", "-")
        if not raw:
            return None
        return raw.lower()

    @classmethod
    def _language_matches(cls, expected: Any, observed: Any) -> bool | None:
        expected_code = cls._normalise_language(expected)
        observed_code = cls._normalise_language(observed)
        if not expected_code or not observed_code:
            return None
        return observed_code == expected_code or observed_code.split("-", 1)[0] == expected_code.split("-", 1)[0]

    @staticmethod
    def _voice_identity_from_payload(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """Extract one declared immutable voice identity from an artifact."""
        if not isinstance(payload, Mapping):
            return None
        candidates: list[Mapping[str, Any]] = []
        for key in ("voice_identity", "voice_selection", "voice_contract"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                candidates.append(value)
        production_plan = payload.get("production_plan")
        if isinstance(production_plan, Mapping):
            for key in ("voice_identity", "voice_selection", "voice_contract"):
                value = production_plan.get(key)
                if isinstance(value, Mapping):
                    candidates.append(value)
        performance = payload.get("voice_performance")
        if isinstance(performance, Mapping):
            candidates.append(performance)
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            candidates.append(metadata)
        # Legacy edit artifacts persist the three provider fields in metadata.
        if any(payload.get(key) not in (None, "") for key in ("provider", "tts_provider", "voice_id", "tts_voice", "voice")):
            candidates.append(payload)
        try:
            from lib.voice_contracts import normalize_voice_identity, VoiceContractError

            for candidate in candidates:
                try:
                    return normalize_voice_identity(candidate).contract()
                except VoiceContractError:
                    continue
        except Exception:
            return None
        return None

    @classmethod
    def _expected_voice_identity(
        cls,
        edit_decisions: Mapping[str, Any] | None,
        proposal_packet: Mapping[str, Any] | None,
        asset_manifest: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        for payload in (edit_decisions, proposal_packet, asset_manifest):
            identity = cls._voice_identity_from_payload(payload)
            if identity:
                return identity
        return None

    @classmethod
    def _expected_language(
        cls,
        edit_decisions: Mapping[str, Any] | None,
        proposal_packet: Mapping[str, Any] | None,
        expected_language: Any = None,
    ) -> str | None:
        candidates: list[Any] = [expected_language]
        for payload in (edit_decisions, proposal_packet):
            if not isinstance(payload, Mapping):
                continue
            candidates.extend([
                payload.get("expected_language"),
                payload.get("language"),
                payload.get("locale"),
                payload.get("language_code"),
            ])
            metadata = payload.get("metadata")
            if isinstance(metadata, Mapping):
                candidates.extend([
                    metadata.get("expected_language"),
                    metadata.get("language"),
                    metadata.get("locale"),
                    metadata.get("language_code"),
                ])
            identity = cls._voice_identity_from_payload(payload)
            if identity:
                candidates.extend([identity.get("locale"), identity.get("language")])
            if isinstance(payload.get("production_plan"), Mapping):
                plan = payload["production_plan"]
                candidates.extend([plan.get("expected_language"), plan.get("language"), plan.get("locale")])
        for value in candidates:
            normalized = cls._normalise_language(value)
            if normalized and normalized not in {"und", "unknown"}:
                return normalized
        return None

    @classmethod
    def _editorial_visual_contract(
        cls,
        edit_decisions: Mapping[str, Any] | None,
        asset_manifest: Mapping[str, Any] | None,
        *,
        output_path: Path,
        duration_seconds: float,
        width: int = 0,
        height: int = 0,
    ) -> dict[str, Any]:
        """Validate declared visual bounds, source paths, and placeholders.

        Decoded frame analysis can prove what pixels were emitted, but it
        cannot infer whether an authored overlay was meant to be inside the
        frame or whether a cut still points at a missing asset.  This small
        deterministic contract checks those declarations alongside the real
        frame probe and fails closed on obvious placeholder copy.
        """
        report: dict[str, Any] = {
            "valid": True,
            "overlay_bounds_checked": 0,
            "missing_sources": [],
            "placeholder_tokens": [],
            "errors": [],
            "warnings": [],
        }
        if not isinstance(edit_decisions, Mapping):
            return report

        assets_by_id: dict[str, Mapping[str, Any]] = {}
        if isinstance(asset_manifest, Mapping):
            for row in asset_manifest.get("assets") or []:
                if isinstance(row, Mapping) and row.get("id"):
                    assets_by_id[str(row["id"])] = row

        def _candidate_paths(raw: Any) -> list[Path]:
            if not isinstance(raw, str) or not raw.strip() or raw.startswith(("http://", "https://", "s3://", "gs://")):
                return []
            value = Path(raw).expanduser()
            if value.is_absolute():
                return [value]
            # Typical durable layout is <project>/artifacts and
            # <project>/renders; include both the output parent and project
            # parent without probing the process cwd.
            return [
                output_path.parent / value,
                output_path.parent.parent / value,
                output_path.parent.parent.parent / value,
            ]

        def _check_source(label: str, raw: Any) -> None:
            if not isinstance(raw, str) or not raw.strip():
                return
            if raw in assets_by_id:
                raw = assets_by_id[raw].get("path")
            candidates = _candidate_paths(raw)
            if candidates and not any(path.is_file() for path in candidates):
                report["missing_sources"].append(label)
                report["errors"].append(f"{label} references a missing local visual asset: {raw}")

        for index, cut in enumerate(edit_decisions.get("cuts") or []):
            if not isinstance(cut, Mapping):
                continue
            _check_source(f"cuts[{index}].source", cut.get("source"))

        # Validate the optional overlay geometry in either pixel coordinates
        # or normalized 0..1 coordinates.  Time ranges are bounded by the
        # actual probed output duration as well.
        for index, overlay in enumerate(edit_decisions.get("overlays") or []):
            if not isinstance(overlay, Mapping):
                continue
            position = overlay.get("position") if isinstance(overlay.get("position"), Mapping) else overlay
            try:
                x = float(position.get("x"))
                y = float(position.get("y"))
            except (TypeError, ValueError, AttributeError):
                report["errors"].append(f"overlays[{index}] has invalid x/y bounds")
                continue
            width_value = position.get("width")
            height_value = position.get("height")
            try:
                overlay_width = float(width_value) if width_value is not None else 0.0
                overlay_height = float(height_value) if height_value is not None else 0.0
            except (TypeError, ValueError):
                report["errors"].append(f"overlays[{index}] has invalid width/height bounds")
                continue
            normalized = max(abs(x), abs(y), abs(overlay_width), abs(overlay_height)) <= 1.0 and (width_value is not None or height_value is not None)
            frame_width = 1.0 if normalized else float(width or 0)
            frame_height = 1.0 if normalized else float(height or 0)
            report["overlay_bounds_checked"] += 1
            if x < 0 or y < 0 or (frame_width and x + overlay_width > frame_width + 1e-6) or (frame_height and y + overlay_height > frame_height + 1e-6):
                report["errors"].append(
                    f"overlays[{index}] is outside the frame ({x:g},{y:g},{overlay_width:g}x{overlay_height:g})"
                )
            if overlay.get("start_seconds") is not None or overlay.get("end_seconds") is not None:
                try:
                    start = float(overlay.get("start_seconds", 0) or 0)
                    end = float(overlay.get("end_seconds", duration_seconds) or duration_seconds)
                    if start < 0 or end <= start or (duration_seconds > 0 and end > duration_seconds + 0.05):
                        report["errors"].append(f"overlays[{index}] has an invalid time range {start:g}-{end:g}s")
                except (TypeError, ValueError):
                    report["errors"].append(f"overlays[{index}] has a non-numeric time range")
            _check_source(f"overlays[{index}].asset_id", overlay.get("asset_id"))

        placeholder_patterns = (
            re.compile(r"\b(?:todo|tbd|lorem ipsum|sample text|dummy text|replace[_ -]?me)\b", re.IGNORECASE),
            re.compile(r"\[\s*(?:placeholder|insert [^\]]+)\s*\]", re.IGNORECASE),
        )

        def _scan(value: Any, path: str = "edit_decisions") -> None:
            if isinstance(value, str):
                for pattern in placeholder_patterns:
                    if pattern.search(value):
                        report["placeholder_tokens"].append({"path": path, "value": value[:160]})
                        report["errors"].append(f"placeholder copy remains at {path}")
                        break
            elif isinstance(value, Mapping):
                for key, item in value.items():
                    _scan(item, f"{path}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    _scan(item, f"{path}[{index}]")

        _scan(edit_decisions)
        report["errors"] = list(dict.fromkeys(report["errors"]))
        report["valid"] = not report["errors"]
        return report

    @classmethod
    def _compare_transcript_to_script(
        cls,
        transcript_path: Path | None,
        script_text: str | None,
        *,
        expected_language: str | None = None,
        expected_voice_identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compare a word-level transcript against the source script.

        Purpose: catch TTS failures that look fine on audio-volume/duration
        checks but produce garbage content. The canonical example is
        Chirp3-HD reading ellipses ("...") literally as the word "dot" — our
        volume check says "narration present, not clipped" and the video
        ships. This check diffs the actual transcribed audio against what
        was supposed to be said, and flags:

        - Spurious punctuation-leak words ("dot", "comma", "hyphen", etc.)
          that appear in audio but not script → CRITICAL
        - Overall word-accuracy ratio against script → SUGGESTION if < 0.9

        Returns the transcript_comparison section of final_review, or a
        placeholder with an issue describing why the check couldn't run
        (missing transcript, missing script) so the review never goes
        silently quiet on this contract.
        """
        result: dict[str, Any] = {
            "transcript_matches_script": False,
            "word_accuracy": None,
            "script_word_count": 0,
            "transcript_word_count": 0,
            "spurious_punctuation_words": [],
            "language_expected": cls._normalise_language(expected_language),
            "language_observed": None,
            "language_match": None,
            "voice_identity_observed": None,
            "voice_identity_match": None,
            "issues": [],
        }

        if not transcript_path or not Path(transcript_path).is_file():
            result["issues"].append(
                "transcript_comparison skipped: narration_transcript not provided"
            )
            return result
        if not script_text:
            result["issues"].append(
                "transcript_comparison skipped: script_text not provided"
            )
            return result

        try:
            transcript_data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
        except Exception as e:
            result["issues"].append(f"transcript_comparison could not parse transcript: {e}")
            return result

        if not isinstance(transcript_data, Mapping):
            result["issues"].append("transcript_comparison transcript must be a JSON object")
            return result

        observed_language = cls._normalise_language(
            transcript_data.get("language")
            or transcript_data.get("locale")
            or transcript_data.get("language_code")
        )
        result["language_observed"] = observed_language
        result["language_match"] = cls._language_matches(expected_language, observed_language)
        if expected_language and not observed_language:
            result["issues"].append(
                f"transcript language evidence is missing (expected {expected_language})"
            )
        elif result["language_match"] is False:
            result["issues"].append(
                f"transcript language {observed_language!r} does not match expected {expected_language!r}"
            )

        if expected_voice_identity:
            observed_voice = cls._voice_identity_from_payload(transcript_data)
            result["voice_identity_observed"] = observed_voice
            if observed_voice:
                try:
                    from lib.voice_contracts import normalize_voice_identity

                    result["voice_identity_match"] = (
                        normalize_voice_identity(observed_voice).identity_key
                        == normalize_voice_identity(expected_voice_identity).identity_key
                    )
                except Exception:
                    result["voice_identity_match"] = False
                if result["voice_identity_match"] is False:
                    result["issues"].append(
                        "transcript voice identity does not match the approved narration voice"
                    )

        transcript_words = [
            w.get("word", "").strip() for w in transcript_data.get("word_timestamps", [])
        ]
        transcript_tokens = cls._tokenize(" ".join(transcript_words))
        script_tokens = cls._tokenize(script_text)

        result["script_word_count"] = len(script_tokens)
        result["transcript_word_count"] = len(transcript_tokens)

        if not script_tokens or not transcript_tokens:
            result["issues"].append(
                f"transcript_comparison: empty token set "
                f"(script={len(script_tokens)}, transcript={len(transcript_tokens)})"
            )
            return result

        # --- Punctuation-leak detection (TTS reading literal punctuation) ---
        script_set = set(script_tokens)
        leak_occurrences: dict[str, int] = {}
        for token in transcript_tokens:
            if token in cls._TTS_PUNCTUATION_LEAK_WORDS and token not in script_set:
                leak_occurrences[token] = leak_occurrences.get(token, 0) + 1

        if leak_occurrences:
            formatted = ", ".join(
                f"{w!r}×{n}" for w, n in sorted(leak_occurrences.items(), key=lambda x: -x[1])
            )
            result["spurious_punctuation_words"] = [
                {"word": w, "count": n} for w, n in leak_occurrences.items()
            ]
            result["issues"].append(
                f"TTS punctuation leak: transcript contains {formatted} — "
                f"these words are NOT in the script, which means the voice "
                f"engine is reading literal punctuation aloud. Rewrite the "
                f"script to eliminate the corresponding characters (ellipses, "
                f"em-dashes, etc.) and regenerate narration."
            )

        # --- Word accuracy via set overlap (cheap & ordering-insensitive) ---
        # We don't penalize small word-order differences or minor TTS
        # hallucinations; we just want to know "did 90%+ of the script's
        # content make it into the audio." Using set overlap on the script
        # side is robust to transcription noise.
        matched = sum(1 for t in script_tokens if t in set(transcript_tokens))
        accuracy = matched / max(1, len(script_tokens))
        result["word_accuracy"] = round(accuracy, 3)
        result["transcript_matches_script"] = accuracy >= 0.9 and not leak_occurrences

        if accuracy < 0.9:
            result["issues"].append(
                f"Low transcript-to-script match: only {accuracy:.0%} of script "
                f"words appear in the transcribed audio ({matched}/"
                f"{len(script_tokens)}). Narration may be truncated, mispronounced, "
                f"or the wrong script was used."
            )

        return result

    def _run_final_review(
        self,
        output_path: Path,
        edit_decisions: dict[str, Any] | None = None,
        proposal_packet: dict[str, Any] | None = None,
        asset_manifest: dict[str, Any] | None = None,
        narration_transcript_path: str | Path | None = None,
        script_text: str | None = None,
        project_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """Run post-render self-review and produce a final_review artifact.

        This is the governance contract: the compose runtime MUST inspect
        the actual rendered output before marking the stage complete.
        Never claim a video is ready without a real probe + frame sample.

        When `proposal_packet` is provided, its
        `production_plan.render_runtime` is compared against
        `edit_decisions.render_runtime` so `runtime_swap_detected` can
        actually flip. Without it, we fall back to
        `edit_decisions.metadata.proposal_render_runtime` (which the edit
        director can set explicitly to opt into swap detection).

        ``project_root`` anchors relative artifact references during review.
        Backlot normally calls the renderer from the repository root, while
        subtitle/media paths are stored relative to the project workspace.

        Returns a dict conforming to final_review.schema.json.
        """
        log = logging.getLogger("video_compose.final_review")
        resolved_project_root: Path | None = None
        if project_root is not None:
            try:
                candidate_root = Path(project_root).expanduser().resolve()
                if candidate_root.is_dir():
                    resolved_project_root = candidate_root
            except (OSError, TypeError, ValueError):
                resolved_project_root = None
        issues: list[str] = []

        # A final review is an evidence record, not an optimistic status bit.
        # Capture its identity and the candidate checksum so the render report
        # can prove exactly which bytes were inspected.
        review_id = str(uuid.uuid4())
        reviewed_at = datetime.now(timezone.utc).isoformat()
        output_sha256: str | None = None
        try:
            if output_path.is_file():
                digest = hashlib.sha256()
                with output_path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                output_sha256 = digest.hexdigest()
        except OSError as exc:
            issues.append(f"Could not hash rendered output: {exc}")
        audio_expected_declared = bool(
            isinstance(edit_decisions, Mapping)
            and isinstance(edit_decisions.get("audio"), Mapping)
            and edit_decisions.get("audio", {}).get("narration")
        )

        # --- 1. Technical probe via ffprobe ---
        technical_probe: dict[str, Any] = {
            "valid_container": False,
            "issues": [],
        }
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(output_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0:
                probe_data = json.loads(proc.stdout)
                fmt = probe_data.get("format", {})
                streams = probe_data.get("streams", [])
                video_stream = next(
                    (s for s in streams if s.get("codec_type") == "video"), {}
                )
                audio_stream = next(
                    (s for s in streams if s.get("codec_type") == "audio"), {}
                )

                duration = float(fmt.get("duration", 0))
                width = int(video_stream.get("width", 0))
                height = int(video_stream.get("height", 0))
                fps_str = video_stream.get("r_frame_rate", "0/1")
                fps = self._parse_probe_fps(fps_str)

                technical_probe = {
                    "valid_container": bool(video_stream),
                    "duration_seconds": round(duration, 2),
                    "resolution": f"{width}x{height}",
                    "fps": fps,
                    "has_audio": bool(audio_stream),
                    "codec": video_stream.get("codec_name", "unknown"),
                    "file_size_bytes": int(fmt.get("size", 0)),
                    "stream_count": len(streams),
                    "audio_channels": int(audio_stream.get("channels") or 0),
                    "audio_channel_layout": audio_stream.get("channel_layout"),
                    "audio_tags": dict(audio_stream.get("tags") or {}) if isinstance(audio_stream, Mapping) else {},
                    "issues": [],
                }

                # Sanity checks
                if duration < 1.0:
                    technical_probe["issues"].append(
                        f"Output is only {duration:.1f}s — suspiciously short"
                    )

                # Check target duration from edit_decisions
                target_dur = None
                if edit_decisions:
                    target_dur = (
                        edit_decisions.get("total_duration_seconds")
                        or edit_decisions.get("metadata", {}).get("target_duration_seconds")
                    )
                if target_dur and target_dur > 0:
                    drift_pct = abs(duration - target_dur) / target_dur
                    if drift_pct > 0.25:
                        technical_probe["issues"].append(
                            f"Duration drift: rendered {duration:.1f}s vs target {target_dur}s "
                            f"({drift_pct:.0%} off). Review pacing or trim."
                        )
                    technical_probe["target_duration"] = target_dur
                    technical_probe["duration_drift_pct"] = round(drift_pct * 100, 1)
                if width < 320 or height < 240:
                    technical_probe["issues"].append(
                        f"Resolution {width}x{height} is very low"
                    )
                if not audio_stream:
                    technical_probe["issues"].append("No audio stream in output")
            else:
                technical_probe["issues"].append(
                    f"ffprobe failed with exit code {proc.returncode}"
                )
        except FileNotFoundError:
            technical_probe["issues"].append("ffprobe not found — cannot validate output")
        except Exception as e:
            technical_probe["issues"].append(f"ffprobe error: {e}")

        if not audio_expected_declared:
            technical_probe["issues"] = [
                item for item in technical_probe.get("issues", [])
                if "no audio stream" not in str(item).lower()
            ]
            if not technical_probe.get("has_audio"):
                technical_probe.setdefault("warnings", []).append(
                    "No audio stream declared; audio QA was not required for this render"
                )
        issues.extend(technical_probe.get("issues", []))

        # --- 2. Visual spotcheck: sample the editorial beats ---
        # Four QA snapshots are useful for a rough container check, but they
        # cannot prove that a 30-second edit with 15 visual beats rendered
        # correctly. Sample every authored beat when practical, with a cap for
        # long-form renders so review remains bounded.
        visual_spotcheck: dict[str, Any] = {
            "frames_sampled": 0,
            "frame_paths": [],
            "black_frames_detected": False,
            "broken_overlays": False,
            "missing_assets": False,
            "unreadable_text": False,
            "issues": [],
        }
        duration = technical_probe.get("duration_seconds", 0)
        if duration > 0 and technical_probe.get("valid_container"):
            try:
                frame_dir = output_path.parent / ".final_review_frames"
                frame_dir.mkdir(parents=True, exist_ok=True)
                review_sample_count = max(4, min(60, int(math.ceil(duration / 2.0))))
                sample_points: list[float] = []
                if edit_decisions:
                    primary_cuts = sorted(
                        [
                            c for c in edit_decisions.get("cuts", [])
                            if c.get("layer", "primary") == "primary"
                        ]
                        or edit_decisions.get("cuts", []),
                        key=lambda c: float(c.get("in_seconds", 0) or 0),
                    )
                    sample_points = [
                        max(
                            0.01,
                            min(
                                0.99,
                                ((float(c.get("in_seconds", 0) or 0) + float(c.get("out_seconds", 0) or 0)) / 2.0)
                                / duration,
                            ),
                        )
                        for c in primary_cuts[:review_sample_count]
                    ]
                if len(sample_points) < review_sample_count:
                    sample_points.extend(
                        (index + 0.5) / review_sample_count
                        for index in range(review_sample_count - len(sample_points))
                    )
                visual_spotcheck["review_sample_count"] = review_sample_count
                frame_paths = []
                for i, pct in enumerate(sample_points):
                    ts = round(duration * pct, 2)
                    frame_path = frame_dir / f"review_frame_{i}.png"
                    cmd = [
                        "ffmpeg", "-y", "-ss", str(ts),
                        "-i", str(output_path),
                        "-frames:v", "1", "-q:v", "2",
                        str(frame_path),
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=15)
                    if frame_path.exists():
                        frame_paths.append(str(frame_path))

                visual_spotcheck["frames_sampled"] = len(frame_paths)
                visual_spotcheck["frame_paths"] = frame_paths

                if len(frame_paths) < review_sample_count:
                    visual_spotcheck["issues"].append(
                        f"Only {len(frame_paths)}/{review_sample_count} editorial review frames extracted — some timestamps may be out of range"
                    )
                if visual_spotcheck["black_frames_detected"]:
                    visual_spotcheck["issues"].append(
                        "Black frame detected — possible missing asset or failed render segment"
                    )
            except Exception as e:
                visual_spotcheck["issues"].append(f"Frame sampling error: {e}")

        issues.extend(visual_spotcheck.get("issues", []))

        # Decode and inspect actual frame samples.  File size is not a valid
        # proxy for black/frozen/duplicate/corrupt media, so use FFmpeg's
        # decoded samples and black/freezedetect filters instead.
        video_quality: dict[str, Any] = {}
        try:
            from lib.video_quality import inspect_video

            selected_profile = None
            if isinstance(edit_decisions, Mapping):
                selected_profile = edit_decisions.get("output_profile") or edit_decisions.get("profile")
            if not selected_profile and isinstance(proposal_packet, Mapping):
                selected_profile = (proposal_packet.get("production_plan") or {}).get("output_profile")
            qa_policy = (
                (edit_decisions.get("metadata") or {}).get("qa_policy")
                if isinstance(edit_decisions, Mapping)
                else None
            )
            allowed_static_holds = (
                qa_policy.get("allowed_static_holds")
                if isinstance(qa_policy, Mapping)
                else None
            )
            video_quality = inspect_video(
                output_path,
                profile=str(selected_profile).strip().lower() if selected_profile else None,
                sample_count=int(visual_spotcheck.get("review_sample_count") or 12),
                allowed_static_holds=(
                    list(allowed_static_holds)
                    if isinstance(allowed_static_holds, list)
                    else None
                ),
            )
            visual_spotcheck["video_quality"] = video_quality
            visual_spotcheck["black_frames_detected"] = bool(
                any(sample.get("black") for sample in video_quality.get("samples", []))
                or video_quality.get("black_intervals")
            )
            for error in video_quality.get("errors", []):
                # Audio expectations are handled by audio_spotcheck below.
                if str(error).lower().startswith("no audio stream"):
                    continue
                issues.append(f"Video quality violation: {error}")
        except Exception as exc:
            visual_spotcheck["video_quality"] = {"valid": False, "errors": [str(exc)]}
            issues.append(f"Video quality analysis error: {exc}")

        probed_width = probed_height = 0
        resolution_value = technical_probe.get("resolution")
        if isinstance(resolution_value, str) and "x" in resolution_value:
            try:
                probed_width, probed_height = (int(part) for part in resolution_value.split("x", 1))
            except (TypeError, ValueError):
                probed_width = probed_height = 0
        visual_contract = self._editorial_visual_contract(
            edit_decisions,
            asset_manifest,
            output_path=output_path,
            duration_seconds=float(technical_probe.get("duration_seconds") or 0),
            width=probed_width,
            height=probed_height,
        )
        visual_spotcheck["visual_contract"] = visual_contract
        visual_spotcheck["missing_assets"] = bool(visual_contract.get("missing_sources"))
        for error in visual_contract.get("errors", []):
            issues.append(f"Visual contract violation: {error}")

        # --- 2b. Editorial timeline and narration audit ---
        timeline_report: dict[str, Any] | None = None
        narration_report: dict[str, Any] | None = None
        if edit_decisions:
            metadata = edit_decisions.get("metadata") or {}
            cadence = metadata.get("visual_beat_cadence_seconds")
            if cadence is not None:
                declared_duration = (
                    edit_decisions.get("total_duration_seconds")
                    or metadata.get("target_duration_seconds")
                    or duration
                )
                try:
                    timeline_report = validate_visual_timeline(
                        edit_decisions.get("cuts", []),
                        duration_seconds=float(declared_duration),
                        beat_seconds=float(cadence),
                        minimum_beats=metadata.get("minimum_visual_beats"),
                    )
                    timeline_report["enforced"] = True
                    for error in timeline_report.get("errors", []):
                        issues.append(f"Visual timeline violation: {error}")

                    narration = (edit_decisions.get("audio") or {}).get("narration")
                    if isinstance(narration, dict):
                        narration_report = validate_narration_timeline(
                            narration,
                            float(declared_duration),
                        )
                        narration_report["enforced"] = True
                        for error in narration_report.get("errors", []):
                            issues.append(f"Narration timeline violation: {error}")
                except (TypeError, ValueError) as exc:
                    timeline_report = {
                        "valid": False,
                        "enforced": True,
                        "errors": [f"Visual timeline policy is invalid: {exc}"],
                        "warnings": [],
                    }
                    issues.append(f"Visual timeline violation: {exc}")

        # --- 3. Audio spotcheck ---
        audio_spotcheck: dict[str, Any] = {
            "narration_present": False,
            "music_present": False,
            "unexpected_silence": False,
            "clipping_detected": False,
            "mix_intelligible": True,
            "issues": [],
            "audio_quality": None,
            "language_expected": None,
            "language_observed": None,
            "language_match": None,
        }
        if technical_probe.get("has_audio") and duration > 0:
            try:
                # Use ffmpeg volumedetect to check audio levels
                cmd = [
                    "ffmpeg", "-i", str(output_path),
                    "-af", "volumedetect", "-f", "null", "-",
                ]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60
                )
                stderr = proc.stderr or ""
                # Parse mean_volume and max_volume
                mean_vol = None
                max_vol = None
                for line in stderr.split("\n"):
                    if "mean_volume:" in line:
                        try:
                            mean_vol = float(line.split("mean_volume:")[1].strip().split()[0])
                        except (ValueError, IndexError):
                            pass
                    if "max_volume:" in line:
                        try:
                            max_vol = float(line.split("max_volume:")[1].strip().split()[0])
                        except (ValueError, IndexError):
                            pass

                if mean_vol is not None:
                    if mean_vol < -60:
                        audio_spotcheck["unexpected_silence"] = True
                        audio_spotcheck["issues"].append(
                            f"Mean volume {mean_vol:.1f} dB — effectively silent"
                        )
                    # Assume narration present if mean volume is reasonable
                    if mean_vol > -40:
                        audio_spotcheck["narration_present"] = True
                    # Assume music present if audio exists (conservative)
                    if mean_vol > -50:
                        audio_spotcheck["music_present"] = True

                if max_vol is not None and max_vol > -0.5:
                    audio_spotcheck["clipping_detected"] = True
                    audio_spotcheck["issues"].append(
                        f"Max volume {max_vol:.1f} dB — possible clipping"
                    )
            except Exception as e:
                audio_spotcheck["issues"].append(f"Audio analysis error: {e}")

        issues.extend(audio_spotcheck.get("issues", []))

        # When an output profile is known, measure the final muxed stream
        # against its LUFS/true-peak/silence/channel contract.  This is kept
        # separate from the legacy volumedetect spotcheck so both the evidence
        # and the actionable failure reason remain visible.
        audio_expected = audio_expected_declared
        selected_audio_profile = None
        if isinstance(edit_decisions, Mapping):
            selected_audio_profile = edit_decisions.get("output_profile") or edit_decisions.get("profile")
        if not selected_audio_profile and isinstance(proposal_packet, Mapping):
            selected_audio_profile = (proposal_packet.get("production_plan") or {}).get("output_profile")
        if technical_probe.get("has_audio") and selected_audio_profile:
            try:
                from tools.analysis.audio_quality import probe_audio_quality

                audio_quality = probe_audio_quality(
                    output_path,
                    profile=str(selected_audio_profile).strip().lower(),
                )
                audio_spotcheck["audio_quality"] = audio_quality
                for error in audio_quality.get("errors", []):
                    issues.append(f"Audio quality violation: {error}")
            except Exception as exc:
                audio_spotcheck["audio_quality"] = {"valid": False, "errors": [str(exc)]}
                issues.append(f"Audio quality analysis error: {exc}")
        if audio_expected and not technical_probe.get("has_audio"):
            message = "Narration/audio is declared but the final output has no audio stream"
            audio_spotcheck["issues"].append(message)
            issues.append(message)

        # Language and voice are immutable content contracts.  Compare the
        # actual transcript/stream evidence and every declared voice identity
        # before a render can be treated as an accurate narration result.
        expected_language = self._expected_language(
            edit_decisions,
            proposal_packet,
            (edit_decisions or {}).get("expected_language") if isinstance(edit_decisions, Mapping) else None,
        )
        expected_voice = self._expected_voice_identity(
            edit_decisions,
            proposal_packet,
            asset_manifest,
        )
        transcript_comparison = self._compare_transcript_to_script(
            Path(narration_transcript_path) if narration_transcript_path else None,
            script_text,
            expected_language=expected_language,
            expected_voice_identity=expected_voice,
        )
        voice_over: dict[str, Any] = {
            "identity_expected": expected_voice,
            "identity_observed": None,
            "identity_match": None,
            "language_expected": expected_language,
            "language_observed": None,
            "language_match": None,
            "identity_evidence": [],
            "issues": [],
        }
        if expected_voice or isinstance(asset_manifest, Mapping):
            try:
                from lib.voice_contracts import validate_voice_propagation

                declared_artifacts: dict[str, Mapping[str, Any]] = {}
                for name, payload in (
                    ("proposal_packet", proposal_packet),
                    ("edit_decisions", edit_decisions),
                    ("asset_manifest", asset_manifest),
                ):
                    if isinstance(payload, Mapping):
                        declared_artifacts[name] = payload
                        identity = self._voice_identity_from_payload(payload)
                        if identity:
                            voice_over["identity_evidence"].append({"artifact": name, "identity": identity})
                            if voice_over["identity_observed"] is None:
                                voice_over["identity_observed"] = identity
                propagation = validate_voice_propagation(declared_artifacts, expected=expected_voice)
                voice_over["identity_match"] = bool(propagation.get("valid")) if propagation.get("checked") else None
                for error in propagation.get("errors", []):
                    voice_over["issues"].append(f"Voice identity mismatch: {error}")
            except Exception as exc:
                voice_over["identity_match"] = False
                voice_over["issues"].append(f"Voice identity QA could not run: {exc}")

        stream_language = None
        tags = technical_probe.get("audio_tags") if isinstance(technical_probe, Mapping) else {}
        if isinstance(tags, Mapping):
            stream_language = tags.get("language") or tags.get("LANGUAGE") or tags.get("locale")
        transcript_language = None
        if isinstance(transcript_comparison, Mapping):
            transcript_language = transcript_comparison.get("language_observed")
        observed_language = transcript_language or self._normalise_language(stream_language)
        voice_over["language_observed"] = observed_language
        voice_over["language_match"] = self._language_matches(expected_language, observed_language)
        audio_spotcheck["language_expected"] = expected_language
        audio_spotcheck["language_observed"] = observed_language
        audio_spotcheck["language_match"] = voice_over["language_match"]
        if expected_language and not observed_language:
            voice_over["issues"].append(
                f"Audio language evidence is missing (expected {expected_language})"
            )
        elif voice_over["language_match"] is False:
            voice_over["issues"].append(
                f"Audio language {observed_language!r} does not match expected {expected_language!r}"
            )
        issues.extend(voice_over["issues"])

        # --- 4. Promise preservation ---
        promise_preservation: dict[str, Any] = {
            "delivery_promise_honored": True,
            "silent_downgrade_detected": False,
            "runtime_swap_detected": False,
            "issues": [],
        }
        if edit_decisions:
            renderer_family = edit_decisions.get("renderer_family", "")
            promise_preservation["renderer_family_used"] = renderer_family

            # Runtime governance — record what actually ran and flag a swap.
            # Three sources of truth, in priority order:
            #   1. proposal_packet.production_plan.render_runtime (authoritative)
            #   2. edit_decisions.metadata.proposal_render_runtime (if edit stage
            #      explicitly copied it to opt into in-tool swap detection)
            #   3. edit_decisions.render_runtime itself (cannot detect a swap in
            #      this case — reviewer does cross-artifact comparison instead)
            render_runtime_edit = (edit_decisions.get("render_runtime") or "").strip().lower()
            if render_runtime_edit:
                promise_preservation["render_runtime_used"] = render_runtime_edit

                proposal_runtime: str | None = None
                runtime_source: str | None = None
                if proposal_packet:
                    pp_runtime = (
                        (proposal_packet.get("production_plan") or {}).get("render_runtime")
                        or ""
                    ).strip().lower()
                    if pp_runtime:
                        proposal_runtime = pp_runtime
                        runtime_source = "proposal_packet.production_plan.render_runtime"
                if proposal_runtime is None:
                    md_runtime = (
                        (edit_decisions.get("metadata") or {}).get("proposal_render_runtime")
                        or ""
                    ).strip().lower()
                    if md_runtime:
                        proposal_runtime = md_runtime
                        runtime_source = "edit_decisions.metadata.proposal_render_runtime"

                if proposal_runtime is None:
                    promise_preservation["runtime_swap_check"] = (
                        "skipped — no proposal_packet or proposal_render_runtime "
                        "metadata provided. Reviewer skill does cross-artifact "
                        "comparison separately."
                    )
                elif proposal_runtime != render_runtime_edit:
                    promise_preservation["runtime_swap_detected"] = True
                    promise_preservation["runtime_swap_check"] = (
                        f"detected — source: {runtime_source}"
                    )
                    promise_preservation["issues"].append(
                        f"render_runtime changed between proposal ({proposal_runtime}) "
                        f"and compose ({render_runtime_edit}) — this is a contract "
                        f"violation unless a render_runtime_selection decision was logged."
                    )
                else:
                    promise_preservation["runtime_swap_check"] = (
                        f"ok — proposal and edit agree ({runtime_source})"
                    )

            delivery_data = (
                edit_decisions.get("metadata", {}).get("delivery_promise")
                or edit_decisions.get("delivery_promise")
            )
            if delivery_data:
                try:
                    from lib.delivery_promise import DeliveryPromise
                    promise = DeliveryPromise.from_dict(delivery_data)
                    cuts = edit_decisions.get("cuts", [])
                    result = promise.validate_cuts(cuts)
                    motion_ratio = result.get("motion_ratio", 0)
                    promise_preservation["motion_ratio_actual"] = round(motion_ratio, 3)

                    if not result["valid"]:
                        promise_preservation["delivery_promise_honored"] = False
                        for v in result["violations"]:
                            promise_preservation["issues"].append(v)

                    # Detect silent downgrade: motion-led promise but <50% motion
                    if (delivery_data.get("type") == "motion_led"
                            and motion_ratio < 0.5):
                        promise_preservation["silent_downgrade_detected"] = True
                        promise_preservation["issues"].append(
                            f"Motion-led promise but only {motion_ratio:.0%} motion — "
                            f"silent downgrade to still-led"
                        )
                except Exception as e:
                    promise_preservation["issues"].append(
                        f"Could not validate delivery promise: {e}"
                    )

        issues.extend(promise_preservation.get("issues", []))

        # --- 5. Subtitle check ---
        subtitle_check: dict[str, Any] = {
            "subtitles_expected": False,
            "subtitles_present": False,
            "issues": [],
        }
        if edit_decisions:
            ed_subs = edit_decisions.get("subtitles", {})
            subtitle_check["subtitles_expected"] = bool(ed_subs.get("enabled"))

            # Check if output has subtitle stream
            if technical_probe.get("valid_container"):
                try:
                    cmd = [
                        "ffprobe", "-v", "quiet", "-print_format", "json",
                        "-show_streams", "-select_streams", "s",
                        str(output_path),
                    ]
                    proc = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=15
                    )
                    if proc.returncode == 0:
                        sub_data = json.loads(proc.stdout)
                        sub_streams = sub_data.get("streams", [])
                        subtitle_check["subtitles_present"] = len(sub_streams) > 0

                    # If subtitles were expected but not found as a stream,
                    # they may be burned in (which is fine — not a failure)
                    if (subtitle_check["subtitles_expected"]
                            and not subtitle_check["subtitles_present"]):
                        # Check if subtitle_path was used (burned in)
                        sub_source = ed_subs.get("source")
                        resolved_sub_source = self._resolve_project_path(
                            sub_source,
                            project_root=resolved_project_root,
                        )
                        if resolved_sub_source and Path(resolved_sub_source).is_file():
                            # Burned-in subtitles are not detectable as streams
                            subtitle_check["subtitles_present"] = True
                            subtitle_check["coverage_ratio"] = 1.0
                        else:
                            subtitle_check["issues"].append(
                                "Subtitles expected but not found in output and "
                                "no subtitle source file exists for burn-in"
                            )
                except Exception as e:
                    subtitle_check["issues"].append(f"Subtitle check error: {e}")

        issues.extend(subtitle_check.get("issues", []))

        # --- 6. Transcript-vs-script comparison ---
        # Catches content-level TTS failures (the classic "Chirp reads `...`
        # as the word 'dot'" trap) that volume-based audio checks miss.
        # Only runs when caller provides both the transcript and script; when
        # skipped, issues list records that so the silence is visible.
        # A transcript comparison is required when narration/script evidence
        # is declared.  For silent motion-graphics fixtures with no such
        # inputs, retain the explicit "skipped" note in the artifact without
        # turning an optional check into a false render failure.
        if audio_expected or narration_transcript_path or script_text:
            issues.extend(transcript_comparison.get("issues", []))
        if audio_expected and (
            not narration_transcript_path or not Path(narration_transcript_path).is_file()
        ):
            issues.append("Narration is declared but verified transcript evidence is missing")

        # --- 7. Determine overall status ---
        critical_issues = [
            i for i in issues
            if any(kw in i.lower() for kw in [
                "silent downgrade", "delivery promise violation",
                "effectively silent", "ffprobe failed", "suspiciously short",
                "tts punctuation leak",  # reading literal punctuation aloud
                "visual timeline violation", "narration timeline violation",
                "video quality violation", "audio quality violation",
                "no audio stream", "transcript evidence is missing",
                "could not hash rendered output", "frame filter inspection",
            ])
        ]

        if critical_issues:
            status = "revise"
            recommended_action = "re_render"
        elif visual_contract.get("missing_sources"):
            status = "revise"
            recommended_action = "revise_assets"
        elif visual_contract.get("placeholder_tokens"):
            status = "revise"
            recommended_action = "re_author"
        elif issues:
            status = "revise"
            recommended_action = "revise_edit"
        else:
            status = "pass"
            recommended_action = "present_to_user"

        if not technical_probe.get("valid_container"):
            status = "fail"
            recommended_action = "re_render"

        final_review = {
            "version": "1.0",
            "review_id": review_id,
            "reviewed_at": reviewed_at,
            "output_sha256": output_sha256,
            "output_path": str(output_path),
            "status": status,
            "checks": {
                "technical_probe": technical_probe,
                "visual_spotcheck": visual_spotcheck,
                "audio_spotcheck": audio_spotcheck,
                "visual_timeline": timeline_report or {
                    "valid": True,
                    "enforced": False,
                    "note": "No visual_beat_cadence_seconds policy was declared in edit_decisions.metadata.",
                },
                "narration_timeline": narration_report or {
                    "valid": True,
                    "enforced": False,
                    "note": "No segmented narration timeline was declared.",
                },
                "promise_preservation": promise_preservation,
                "subtitle_check": subtitle_check,
                "transcript_comparison": transcript_comparison,
                "voice_over": voice_over,
            },
            "issues_found": issues,
            "recommended_action": recommended_action,
        }
        # Carry the same durable identity into the review when the caller
        # supplied it on edit/proposal metadata.  This lets the manifest
        # executor reject a review copied from another project/run.
        identity_source = edit_decisions if isinstance(edit_decisions, Mapping) else {}
        identity_metadata = identity_source.get("metadata") if isinstance(identity_source.get("metadata"), Mapping) else {}
        proposal_plan = (proposal_packet or {}).get("production_plan") if isinstance(proposal_packet, Mapping) else {}
        for field in ("project_id", "pipeline_type", "run_id", "attempt"):
            value = identity_source.get(field) or identity_metadata.get(field)
            if value in (None, "") and isinstance(proposal_packet, Mapping):
                value = proposal_packet.get(field)
            if value in (None, "") and isinstance(proposal_plan, Mapping):
                value = proposal_plan.get(field)
            if value not in (None, ""):
                final_review[field] = value
        selected_review_profile = (
            identity_source.get("output_profile")
            or identity_source.get("profile")
            or (proposal_plan or {}).get("output_profile")
            or (proposal_plan or {}).get("profile")
        ) if isinstance(proposal_plan, Mapping) else identity_source.get("output_profile") or identity_source.get("profile")
        if selected_review_profile:
            final_review["output_profile"] = str(selected_review_profile)
            final_review["profile"] = str(selected_review_profile)
        if expected_voice:
            final_review["voice_identity"] = expected_voice

        log.info(
            "Final review: status=%s, issues=%d, action=%s",
            status, len(issues), recommended_action,
        )

        return final_review

    @staticmethod
    def _parse_probe_fps(fps_str: str) -> float:
        """Parse ffprobe fps string like '30/1' or '24000/1001'."""
        try:
            if "/" in fps_str:
                num, den = fps_str.split("/")
                return round(int(num) / max(int(den), 1), 2)
            return float(fps_str)
        except (ValueError, ZeroDivisionError):
            return 0.0

    def _caption_contract_for_inputs(
        self,
        inputs: dict[str, Any],
        *,
        subtitle_path: str | Path | None,
        width: int,
        height: int,
        duration_seconds: float,
        runtime: str = "ffmpeg",
        mode: str | None = None,
        style: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve and certify captions before any FFmpeg command is run."""

        from lib.caption_contracts import (
            CaptionContractError,
            build_caption_render_contract,
            cues_from_transcript,
            load_caption_cues,
            validate_verified_transcript,
        )

        transcript = inputs.get("transcript") or inputs.get("verified_transcript")
        raw_cues = inputs.get("captions")
        verification: dict[str, Any] | None = None
        try:
            require_verified = (
                strict_bool(inputs["require_verified_transcript"], "require_verified_transcript")
                if "require_verified_transcript" in inputs
                else False
            )
            transcript_verified = (
                strict_bool(inputs["transcript_verified"], "transcript_verified")
                if "transcript_verified" in inputs
                else False
            )
        except MediaContractError as exc:
            raise CaptionContractError(str(exc)) from exc
        if isinstance(transcript, dict):
            if raw_cues is None and isinstance(transcript.get("segments"), list):
                raw_cues = cues_from_transcript(
                    transcript,
                    max_words_per_cue=int(inputs.get("max_words_per_cue", 6) or 6),
                    max_chars_per_line=int(inputs.get("max_chars_per_line", 42) or 42),
                )
        if require_verified:
            payload = dict(transcript or {}) if isinstance(transcript, dict) else {}
            payload.setdefault("segments", [])
            if "verified" not in payload and "verification_status" not in payload:
                payload["verified"] = transcript_verified
            verification = validate_verified_transcript(
                payload,
                expected_language=inputs.get("expected_language"),
                expected_text=inputs.get("expected_text"),
            )
            if verification.get("valid") is not True:
                raise CaptionContractError(
                    "; ".join(verification.get("errors") or ["verified transcript validation failed"])
                )
        if raw_cues is None and subtitle_path:
            raw_cues = load_caption_cues(subtitle_path)
        if raw_cues is None:
            return None, verification
        render_mode = str(mode or inputs.get("caption_mode", "burn_in")).strip().lower()
        if render_mode not in {"burn_in", "sidecar"}:
            raise CaptionContractError("FFmpeg caption_mode must be 'burn_in' or 'sidecar'")
        contract = build_caption_render_contract(
            runtime=runtime,
            mode=render_mode,
            cues=raw_cues,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            fps=int(inputs.get("fps", 30) or 30),
            safe_area=inputs.get("safe_area"),
            max_chars_per_line=int(inputs.get("max_chars_per_line", 42) or 42),
            max_lines=int(inputs.get("max_lines", 2) or 2),
            font_size=int((style or {}).get("font_size", inputs.get("caption_font_size", 42)) or 42),
            style=style,
            transcript_verification=verification,
            profile_name=inputs.get("profile") or inputs.get("output_profile"),
        )
        return contract, verification

    @staticmethod
    def _probe_video_dimensions(path: str | Path) -> tuple[int, int, float]:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        fmt = payload.get("format") or {}
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = float(fmt.get("duration") or stream.get("duration") or 0)
        if width < 1 or height < 1 or duration <= 0:
            raise ValueError("ffprobe returned incomplete video dimensions/duration")
        return width, height, duration

    @staticmethod
    def _write_caption_srt(contract: Mapping[str, Any], path: Path) -> Path:
        """Materialize inline/transcript cues for FFmpeg's subtitles filter."""

        def stamp(seconds: float) -> str:
            total_ms = int(round(max(0.0, float(seconds)) * 1000))
            hours, rem = divmod(total_ms, 3_600_000)
            minutes, rem = divmod(rem, 60_000)
            secs, millis = divmod(rem, 1_000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        lines: list[str] = []
        for index, cue in enumerate(contract.get("cues") or [], start=1):
            lines.extend(
                [
                    str(index),
                    f"{stamp(cue['start'])} --> {stamp(cue['end'])}",
                    str(cue["text"]),
                    "",
                ]
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _burn_subtitles(self, inputs: dict[str, Any]) -> ToolResult:
        """Burn subtitle file into video."""
        input_path = Path(inputs["input_path"])
        subtitle_value = inputs.get("subtitle_path")
        subtitle_path = Path(subtitle_value) if subtitle_value else None
        output_path = Path(inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_subtitled"))))

        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")
        if subtitle_path is not None and not subtitle_path.exists():
            return ToolResult(success=False, error=f"Subtitle file not found: {subtitle_path}")
        if subtitle_path is None and inputs.get("captions") is None and inputs.get("transcript") is None:
            return ToolResult(success=False, error="Provide subtitle_path, captions, or transcript")

        style = inputs.get("subtitle_style", {})
        try:
            width, height, duration = self._probe_video_dimensions(input_path)
            caption_contract, transcript_verification = self._caption_contract_for_inputs(
                inputs,
                subtitle_path=subtitle_path,
                width=width,
                height=height,
                duration_seconds=duration,
                runtime="ffmpeg",
                mode="burn_in",
                style=style,
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Caption render contract rejected: {exc}")
        if caption_contract is None:
            return ToolResult(success=False, error="Caption render contract contains no cues")
        temporary_caption = None
        if subtitle_path is None:
            temporary_caption = output_path.parent / f"._captions_{uuid.uuid4().hex}.srt"
            subtitle_path = self._write_caption_srt(caption_contract, temporary_caption)
        style = dict(style or {})
        style["margin_v"] = max(int(style.get("margin_v", 0) or 0), int(caption_contract["safe_area"]["pixels"]["bottom"]))
        ass_style = self._build_subtitle_style(style)
        sub_escaped = str(subtitle_path.resolve()).replace("\\", "/").replace(":", "\\:")
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 23)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", f"subtitles='{sub_escaped}':force_style='{ass_style}'",
            "-c:v", codec, "-crf", str(crf),
            "-c:a", "copy",
            str(output_path),
        ]

        try:
            self.run_command(cmd, timeout=600)
        finally:
            if temporary_caption is not None:
                temporary_caption.unlink(missing_ok=True)

        if not output_path.exists():
            return ToolResult(success=False, error="FFmpeg subtitle burn produced no output")

        return ToolResult(
            success=True,
            data={
                "operation": "burn_subtitles",
                "output": str(output_path),
                "caption_render_contract": caption_contract,
                "transcript_verification": transcript_verification,
                "caption_mode": "burn_in",
            },
            artifacts=[str(output_path)],
        )

    def _overlay(self, inputs: dict[str, Any]) -> ToolResult:
        """Composite overlay images/videos on top of base video."""
        input_path = Path(inputs["input_path"])
        overlays = inputs.get("overlays", [])
        output_path = Path(inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_overlay"))))
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 23)

        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")
        if not overlays:
            return ToolResult(success=False, error="No overlays provided")

        # Build complex filter for each overlay
        input_args = ["-i", str(input_path)]
        filter_parts = []
        prev_label = "0:v"

        for i, ov in enumerate(overlays):
            asset_path = Path(ov["asset_path"])
            if not asset_path.exists():
                return ToolResult(success=False, error=f"Overlay asset not found: {asset_path}")

            input_args.extend(["-i", str(asset_path)])

            x = int(ov.get("x", 0))
            y = int(ov.get("y", 0))
            start = ov.get("start_seconds", 0)
            end = ov.get("end_seconds")
            opacity = ov.get("opacity", 1.0)

            overlay_input = f"{i + 1}:v"

            # Scale overlay if dimensions specified
            if "width" in ov and "height" in ov:
                w = int(ov["width"])
                h = int(ov["height"])
                filter_parts.append(f"[{overlay_input}]scale={w}:{h}[ov_scaled_{i}]")
                overlay_input = f"ov_scaled_{i}"

            # Build enable expression for timed overlays
            enable = f"between(t,{start},{end})" if end else f"gte(t,{start})"
            out_label = f"v{i}"

            filter_parts.append(
                f"[{prev_label}][{overlay_input}]overlay={x}:{y}:enable='{enable}'[{out_label}]"
            )
            prev_label = out_label

        filter_complex = ";".join(filter_parts)

        cmd = ["ffmpeg", "-y"]
        cmd.extend(input_args)
        cmd.extend(["-filter_complex", filter_complex])
        cmd.extend(["-map", f"[{prev_label}]", "-map", "0:a?"])
        cmd.extend(["-c:v", codec, "-crf", str(crf), "-c:a", "copy"])
        cmd.append(str(output_path))

        self.run_command(cmd, timeout=600)

        return ToolResult(
            success=True,
            data={
                "operation": "overlay",
                "overlay_count": len(overlays),
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
        )

    def _encode(self, inputs: dict[str, Any]) -> ToolResult:
        """Re-encode video with a specific profile/codec settings."""
        input_path = Path(inputs["input_path"])
        output_path = Path(inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_encoded"))))
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 23)
        preset = inputs.get("preset", "medium")
        profile_name = inputs.get("profile")

        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-c:v", codec, "-crf", str(crf), "-preset", preset,
            "-c:a", "aac", "-b:a", "192k",
        ]

        # Apply media profile if specified
        if profile_name:
            try:
                from lib.media_profiles import get_profile, ffmpeg_output_args
                profile = get_profile(profile_name)
                cmd.extend(["-s", f"{profile.width}x{profile.height}"])
                cmd.extend(["-r", str(profile.fps)])
            except (ImportError, ValueError):
                pass  # proceed without profile

        cmd.append(str(output_path))
        self.run_command(cmd, timeout=600)

        return ToolResult(
            success=True,
            data={
                "operation": "encode",
                "codec": codec,
                "crf": crf,
                "profile": profile_name,
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
        )

    @staticmethod
    def _resolve_subtitle_style(
        explicit_style: dict | None,
        edit_decisions: dict | None,
        playbook: dict | None,
    ) -> dict:
        """Resolve subtitle style with layered priority.

        Priority: explicit_style > edit_decisions.subtitles.style > playbook > defaults.
        This prevents every video from looking identical (Arial bold white).
        """
        # Start with minimal fallback defaults
        resolved = {
            "font": "Inter",
            "font_size": 28,
            "bold": True,
            "outline_width": 2,
            "shadow": 0,
            "margin_v": 40,
            "alignment": 2,
        }

        # Layer 1: Playbook-derived style
        if playbook:
            typo = playbook.get("typography", {})
            colors = playbook.get("visual_language", {}).get("color_palette", {})
            if typo.get("body", {}).get("family"):
                resolved["font"] = typo["body"]["family"]
            if colors.get("text"):
                resolved["primary_color"] = colors["text"]
            if colors.get("background"):
                resolved["outline_color"] = colors["background"]
                # Semi-transparent background for readability
                bg = colors["background"]
                resolved["back_color"] = bg

        # Layer 2: edit_decisions subtitle style
        if edit_decisions:
            ed_style = edit_decisions.get("subtitles", {}).get("style", {})
            for k, v in ed_style.items():
                if v is not None:
                    resolved[k] = v

        # Layer 3: Explicit override (highest priority)
        if explicit_style:
            for k, v in explicit_style.items():
                if v is not None:
                    resolved[k] = v

        return resolved

    @staticmethod
    def _build_subtitle_style(style: dict) -> str:
        """Build ASS force_style string from style dict."""
        parts = []
        parts.append(f"FontName={style.get('font', 'Inter')}")
        parts.append(f"FontSize={style.get('font_size', 28)}")
        parts.append(f"Bold={1 if style.get('bold', True) else 0}")
        if style.get("primary_color"):
            parts.append(f"PrimaryColour={style['primary_color']}")
        if style.get("outline_color"):
            parts.append(f"OutlineColour={style['outline_color']}")
        if style.get("back_color"):
            parts.append(f"BackColour={style['back_color']}")
        border_style = style.get("border_style", 1)
        parts.append(f"BorderStyle={border_style}")
        parts.append(f"Outline={style.get('outline_width', 2)}")
        parts.append(f"Shadow={style.get('shadow', 0)}")
        parts.append(f"MarginV={style.get('margin_v', 40)}")
        parts.append(f"Alignment={style.get('alignment', 2)}")
        return ",".join(parts)

    @staticmethod
    def _build_atempo(factor: float) -> str:
        """Build atempo filter chain for audio speed adjustment."""
        filters = []
        remaining = factor
        while remaining > 100.0:
            filters.append("atempo=100.0")
            remaining /= 100.0
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
        filters.append(f"atempo={remaining:.4f}")
        return ",".join(filters)
