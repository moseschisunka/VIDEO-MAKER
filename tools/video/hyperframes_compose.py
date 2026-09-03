"""HyperFrames composition tool — HTML/CSS/GSAP render path.

Sibling to `video_compose` (FFmpeg + Remotion). This tool owns the HyperFrames
runtime end-to-end: workspace materialization, `hyperframes lint`,
`hyperframes validate`, and `hyperframes render`. It is invoked by
`video_compose` when `edit_decisions.render_runtime == "hyperframes"`, and
can also be called directly by pipelines that want HyperFrames-specific
operations (lint-only, validate-only, scaffold-only).

This tool deliberately does NOT attempt parity with every Remotion scene
component. See `skills/core/hyperframes.md` for what is in scope in Phase 1
and what remains Remotion-only.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ResumeSupport,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


log = logging.getLogger("hyperframes_compose")


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


class HyperFramesCompose(BaseTool):
    name = "hyperframes_compose"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "hyperframes"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:npx", "cmd:ffmpeg"]
    install_instructions = (
        "Requires Node.js >= 22 (https://nodejs.org/) and FFmpeg "
        "(https://ffmpeg.org/download.html). The HyperFrames CLI is fetched "
        "on first use via `npx hyperframes` (npm package: `hyperframes`). "
        "Note: the upstream monorepo develops the package as `@hyperframes/cli`, "
        "but it publishes to npm as `hyperframes`. `npx @hyperframes/cli` "
        "returns 404 -- do NOT use that form. Verify setup with "
        "`npx hyperframes doctor` or run the `doctor` operation on this tool."
    )
    agent_skills = [
        "hyperframes",
        "hyperframes-cli",
        "hyperframes-registry",
        "website-to-hyperframes",
        "gsap-core",
        "gsap-timeline",
    ]

    capabilities = [
        "hyperframes_render",
        "hyperframes_lint",
        "hyperframes_validate",
        "hyperframes_doctor",
        "scaffold_workspace",
        "add_block",
    ]

    best_for = [
        "HTML/CSS/GSAP composition: kinetic typography, product promos, launch reels",
        "Motion-graphics-heavy briefs where the scene library in remotion-composer/ doesn't fit",
        "Website-to-video / UI-driven compositions",
        "Registry-block-driven scenes (hyperframes add data-chart, grain-overlay, etc.)",
    ]
    not_good_for = [
        "Word-level/karaoke caption burn (stays on Remotion; segment captions are supported)",
        "Avatar / lip-sync presenter (stays on Remotion in Phase 1)",
        "Existing React scene stack (text_card, stat_card, chart, comparison): reuse Remotion",
    ]
    fallback_tools = ["video_compose"]

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "render",
                    "lint",
                    "validate",
                    "inspect",
                    "doctor",
                    "scaffold_workspace",
                    "add_block",
                ],
                "description": (
                    "render: materialize workspace + lint + validate + render to MP4. "
                    "lint: run `hyperframes lint` on an existing workspace. "
                    "validate: run `hyperframes validate` (browser-based). "
                    "doctor: run `hyperframes doctor` to check environment. "
                    "scaffold_workspace: materialize HTML/CSS/assets but do not render. "
                    "add_block: run `hyperframes add <name>` to install a registry "
                    "block or component into an existing workspace."
                ),
            },
            "block_name": {
                "type": "string",
                "description": (
                    "Registry block or component name for operation='add_block' "
                    "(e.g. 'data-chart', 'grain-overlay', 'shimmer-sweep'). "
                    "See https://hyperframes.heygen.com/catalog for the list."
                ),
            },
            "workspace_path": {
                "type": "string",
                "description": (
                    "Target HyperFrames workspace directory. Typically "
                    "`projects/<name>/hyperframes/`. Required for every op "
                    "except doctor."
                ),
            },
            "output_path": {
                "type": "string",
                "description": "Output MP4 path. Used by operation='render'.",
            },
            "edit_decisions": {
                "type": "object",
                "description": (
                    "Full edit_decisions artifact — required for render and "
                    "scaffold_workspace. Used to generate index.html + CSS."
                ),
            },
            "asset_manifest": {
                "type": "object",
                "description": (
                    "Full asset_manifest artifact — required for render and "
                    "scaffold_workspace. Used to resolve asset IDs to file paths."
                ),
            },
            "playbook": {
                "type": "object",
                "description": (
                    "Loaded playbook dict. Used to drive the style bridge "
                    "(CSS custom properties, typography, motion defaults)."
                ),
            },
            "profile": {
                "type": "string",
                "description": "Media profile name (youtube_landscape, tiktok_vertical, etc.).",
            },
            "quality": {
                "type": "string",
                "enum": ["draft", "standard", "high"],
                "default": "standard",
                "description": "Render quality. `draft` for iterating, `high` for delivery.",
            },
            "fps": {
                "type": "integer",
                "enum": [24, 30, 60],
                "default": 30,
            },
            "strict": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, fail the render on any lint error. Matches "
                    "`hyperframes render --strict`."
                ),
            },
            "production_mode": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Enable the production HyperFrames contract: canonical edit "
                    "mapping, strict lint/validate/inspect ordering, local runtime "
                    "assets, hashed staging, and fail-closed unsupported cuts."
                ),
            },
            "offline": {
                "type": "boolean",
                "default": False,
                "description": "Require an already-installed local HyperFrames runtime; never contact npm.",
            },
            "workers": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional worker request; production policy caps video-heavy compositions safely.",
            },
            "resource_budget": {
                "type": "object",
                "description": "Optional caps for timeout, memory, disk, and process count.",
            },
            "skip_contrast": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Skip the WCAG contrast audit during validate. Acceptable "
                    "while iterating; forbidden for final delivery."
                ),
            },
            "subtitle_path": {
                "type": "string",
                "description": "SRT, WebVTT, or SubtitleGen JSON sidecar to render as segment captions.",
            },
            "captions": {
                "type": "array",
                "description": "Neutral caption cues with start/end/text (or words).",
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
                "enum": ["overlay", "sidecar"],
                "default": "overlay",
                "description": "HyperFrames renders segment captions as an overlay; sidecar is retained in the workspace.",
            },
            "safe_area": {
                "type": "object",
                "description": "Caption safe-area ratios: left/right/top/bottom or *_ratio (0..0.5).",
            },
            "max_lines": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "max_chars_per_line": {"type": "integer", "default": 42, "minimum": 1, "maximum": 200},
            "caption_font_size": {"type": "integer", "default": 42, "minimum": 8, "maximum": 240},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=4, ram_mb=3072, vram_mb=0, disk_mb=2000, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=0)
    resume_support = ResumeSupport.FROM_START
    idempotency_key_fields = ["operation", "workspace_path", "edit_decisions"]
    side_effects = [
        "writes HTML/CSS/JS files into workspace_path",
        "copies asset files into workspace_path/assets/",
        "writes MP4 to output_path",
    ]
    user_visible_verification = [
        "Play the rendered MP4 and verify scene pacing, typography, and audio",
        "Inspect workspace_path/index.html in a browser via `npx hyperframes preview`",
    ]

    # ------------------------------------------------------------------
    # Status / availability
    # ------------------------------------------------------------------

    _NODE_FLOOR_MAJOR = 22
    _NPM_PACKAGE = "hyperframes"  # published npm name (NOT @hyperframes/cli — that's 404)
    # Process-level cache for the npm resolve check. Shape:
    #   {"version": "0.4.5"}   → package resolves
    #   {"error": "<short>"}   → resolution failed (offline, unpublished, etc.)
    # We cache per-process so the first call pays ~2-5s and subsequent calls
    # (get_info spam from the registry) are free.
    _npm_resolve_cache: Optional[dict[str, str]] = None
    _offline_runtime_cache: Optional[dict[str, str]] = None
    _offline_mode: bool = False

    @classmethod
    def _node_major_version(cls) -> Optional[int]:
        """Return Node.js major version, or None if node isn't installed."""
        node = shutil.which("node")
        if not node:
            return None
        try:
            out = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=5
            )
            if out.returncode != 0:
                return None
            match = re.match(r"v?(\d+)\.", out.stdout.strip())
            if not match:
                return None
            return int(match.group(1))
        except (OSError, subprocess.SubprocessError):
            return None

    @classmethod
    def _resolve_npm_package(cls) -> dict[str, str]:
        """Verify the `hyperframes` npm package actually resolves.

        `_runtime_check` previously only verified that node/ffmpeg/npx existed
        on PATH, which meant `runtime_available: True` on any machine with
        Node + FFmpeg — even offline, even if npm was down, even if the
        package was unpublished. This method performs a cheap
        `npm view hyperframes version` (5s timeout) and caches the answer
        for the rest of the process.

        Returns {"version": "X.Y.Z"} on success, {"error": "<short>"} on any
        failure (404, timeout, network error, npm missing). Never raises.
        """
        if cls._npm_resolve_cache is not None:
            return cls._npm_resolve_cache

        npm = shutil.which("npm")
        if not npm:
            cls._npm_resolve_cache = {"error": "npm not on PATH"}
            return cls._npm_resolve_cache

        try:
            proc = cls._run_bounded_process(
                [npm, "view", cls._NPM_PACKAGE, "version"],
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            cls._npm_resolve_cache = {"error": "timeout (5s) — offline or slow registry"}
            return cls._npm_resolve_cache
        except (OSError, subprocess.SubprocessError) as e:
            cls._npm_resolve_cache = {"error": f"npm view failed: {type(e).__name__}"}
            return cls._npm_resolve_cache

        # `_run_bounded_process` returns a synthetic 124 result after it has
        # terminated the complete process tree.  Keep the public diagnostic
        # identical to the historical timeout path while avoiding a Windows
        # `npm.CMD` child retaining stdout/stderr pipes indefinitely.
        if proc.returncode == 124:
            cls._npm_resolve_cache = {"error": "timeout (5s) — offline or slow registry"}
            return cls._npm_resolve_cache

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            # Most common failure is 404 (package unpublished or name wrong).
            if "404" in stderr or "E404" in stderr:
                cls._npm_resolve_cache = {
                    "error": f"npm package `{cls._NPM_PACKAGE}` not found (404)"
                }
            else:
                tail = stderr.splitlines()[-1][:200] if stderr else f"exit {proc.returncode}"
                cls._npm_resolve_cache = {"error": f"npm view failed: {tail}"}
            return cls._npm_resolve_cache

        version = (proc.stdout or "").strip()
        if not version:
            cls._npm_resolve_cache = {"error": "npm view returned empty version"}
        else:
            cls._npm_resolve_cache = {"version": version}
        return cls._npm_resolve_cache

    def _runtime_check(self) -> dict[str, Any]:
        """Return availability state for the HyperFrames runtime.

        Checks BOTH local binaries (node >= 22, ffmpeg, npx) AND that the
        `hyperframes` npm package actually resolves. A missing/404 package
        counts as unavailable — `runtime_available: True` means the runtime
        can genuinely run end-to-end, not just that the local tooling exists.
        """
        node_major = self._node_major_version()
        ffmpeg_ok = shutil.which("ffmpeg") is not None
        npx_ok = shutil.which("npx") is not None

        reasons: list[str] = []
        if node_major is None:
            reasons.append("node not found on PATH")
        elif node_major < self._NODE_FLOOR_MAJOR:
            reasons.append(
                f"node major version {node_major} < required {self._NODE_FLOOR_MAJOR}"
            )
        if not npx_ok:
            reasons.append("npx not found on PATH")
        if not ffmpeg_ok:
            reasons.append("ffmpeg not found on PATH")

        # Only probe npm if the local tooling is actually usable — otherwise
        # a missing-node run would also show a confusing npm error.
        npm_resolve: dict[str, str] = {}
        if not reasons:
            if getattr(self, "_offline_mode", False):
                npm_resolve = self._offline_package_check()
            else:
                npm_resolve = self._resolve_npm_package()
            if "error" in npm_resolve:
                reasons.append(
                    f"npm package `{self._NPM_PACKAGE}` not resolvable: "
                    f"{npm_resolve['error']}"
                )

        return {
            "runtime_available": not reasons,
            "node_major": node_major,
            "ffmpeg_available": ffmpeg_ok,
            "npx_available": npx_ok,
            "npm_package": self._NPM_PACKAGE,
            "npm_package_version": npm_resolve.get("version"),
            "npm_resolve_error": npm_resolve.get("error"),
            "offline": bool(getattr(self, "_offline_mode", False)),
            "reasons": reasons,
        }

    @classmethod
    def _offline_package_check(cls) -> dict[str, str]:
        """Check only local package caches; never query the public registry."""
        if cls._offline_runtime_cache is not None:
            return cls._offline_runtime_cache
        candidates: list[Path] = []
        binary = shutil.which("hyperframes")
        if binary:
            candidates.append(Path(binary))
        repo_root = Path(__file__).resolve().parents[2]
        roots = (Path.cwd(), repo_root)
        for root in roots:
            for name in ("hyperframes", "hyperframes.cmd", "hyperframes.ps1"):
                candidates.append(root / "node_modules" / ".bin" / name)
        for path in candidates:
            if path.is_file():
                version = None
                for package_json in (
                    path.parent.parent / "hyperframes" / "package.json",
                    path.parent.parent.parent / "hyperframes" / "package.json",
                ):
                    try:
                        payload = json.loads(package_json.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict) and payload.get("version"):
                        version = str(payload["version"])
                        break
                cls._offline_runtime_cache = {
                    "version": version or "local-cache",
                    "source": str(path),
                }
                return cls._offline_runtime_cache

        # `npx --offline` can execute a package stored in its content-addressed
        # cache even when no project-local .bin wrapper exists.  Resolve the
        # cache roots from npm's environment/default locations without running
        # any registry command, then inspect only package metadata.
        cache_roots: list[Path] = []
        for key in ("npm_config_cache", "NPM_CONFIG_CACHE"):
            raw = os.environ.get(key)
            if raw:
                cache_roots.append(Path(raw).expanduser())
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            cache_roots.append(Path(local_app_data) / "npm-cache")
        cache_roots.extend([Path.home() / ".npm", Path.home() / "AppData" / "Local" / "npm-cache"])
        seen: set[Path] = set()
        package_candidates: list[Path] = []
        for root in cache_roots:
            try:
                root = root.resolve()
            except OSError:
                continue
            if root in seen:
                continue
            seen.add(root)
            npx_root = root / "_npx"
            if not npx_root.is_dir():
                continue
            try:
                package_candidates.extend(npx_root.glob("*/node_modules/hyperframes/package.json"))
            except OSError:
                continue
        package_candidates.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
        for package_json in package_candidates:
            try:
                payload = json.loads(package_json.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("name") == cls._NPM_PACKAGE and payload.get("version"):
                cls._offline_runtime_cache = {
                    "version": str(payload["version"]),
                    "source": str(package_json.parent),
                }
                return cls._offline_runtime_cache

        cls._offline_runtime_cache = {"error": "no local hyperframes executable/cache found"}
        return cls._offline_runtime_cache

    def get_status(self) -> ToolStatus:
        check = self._runtime_check()
        return ToolStatus.AVAILABLE if check["runtime_available"] else ToolStatus.UNAVAILABLE

    def get_info(self, *, include_status: bool = True) -> dict[str, Any]:
        info = super().get_info(include_status=include_status)
        check = self._runtime_check() if include_status else {
            "runtime_available": False,
            "node_major": None,
            "ffmpeg_available": shutil.which("ffmpeg") is not None,
            "npx_available": shutil.which("npx") is not None,
            "npm_package": self._NPM_PACKAGE,
            "npm_package_version": None,
            "npm_resolve_error": "deferred to deep diagnostics",
            "reasons": ["live npm package resolution deferred to deep diagnostics"],
        }
        info["hyperframes_runtime"] = check
        if not check["runtime_available"]:
            info["setup_offer"] = {
                "effort": (
                    "1-minute fix"
                    if check["npx_available"] and check["ffmpeg_available"]
                    else "5-minute fix (install Node 22+ and/or FFmpeg)"
                ),
                "install_instructions": self.install_instructions,
                "unlocks": (
                    "HTML/CSS/GSAP composition runtime — kinetic typography, "
                    "product promos, registry blocks, website-to-video."
                ),
            }
        return info

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        ed = inputs.get("edit_decisions") or {}
        cuts = ed.get("cuts", [])
        total = 0.0
        for c in cuts:
            out_s = float(c.get("out_seconds", 0) or 0)
            in_s = float(c.get("in_seconds", 0) or 0)
            total += max(0.0, out_s - in_s)
        return 30.0 + total * 0.5

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs["operation"]
        start = time.time()
        try:
            # Keep the public ``offline`` contract consistent across every
            # operation.  ``doctor`` and ``render`` set this flag in their
            # own paths, but direct lint/validate/inspect/add calls must also
            # avoid an implicit npm registry lookup when the caller explicitly
            # requested cached-only execution.  Preserve an instance-level
            # diagnostic override when the key is omitted (the opt-in QA
            # harness uses this to exercise a cached runtime across steps).
            if "offline" in inputs:
                self._offline_mode = bool(inputs.get("offline"))
            if operation == "doctor":
                result = self._doctor(inputs)
            elif operation == "scaffold_workspace":
                result = self._scaffold(inputs)
            elif operation == "lint":
                result = self._lint(inputs)
            elif operation == "validate":
                result = self._validate(inputs)
            elif operation == "inspect":
                result = self._inspect(inputs)
            elif operation == "render":
                result = self._render(inputs)
            elif operation == "add_block":
                result = self._add_block(inputs)
            else:
                return ToolResult(success=False, error=f"Unknown operation: {operation}")
        except Exception as e:
            log.exception("hyperframes_compose failed")
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")

        result.duration_seconds = round(time.time() - start, 2)
        return result

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _doctor(self, inputs: dict[str, Any]) -> ToolResult:
        """Probe the environment. Reports node/ffmpeg/npx plus CLI doctor output."""
        self._offline_mode = bool(inputs.get("offline"))
        check = self._runtime_check()
        out: dict[str, Any] = {"runtime_check": check}

        if not check["runtime_available"]:
            return ToolResult(
                success=False,
                error=(
                    "HyperFrames runtime floor not met: "
                    + "; ".join(check["reasons"])
                ),
                data=out,
            )

        # Ask the CLI itself for a deeper check. This also warms the npm
        # cache so the first real render doesn't pay the download cost.
        try:
            # The CLI historically returned a human-readable report and an
            # exit code of zero even when optional checks were missing.  Use
            # the JSON report when available so the adapter can distinguish
            # required render prerequisites from optional provider fallbacks.
            proc = self._run_hf(["doctor", "--json"], cwd=None, timeout=180, check=False)
            payload = self._parse_json_output(proc.stdout or "")
            if isinstance(payload, dict):
                checks = payload.get("checks")
                if isinstance(checks, list):
                    failed = [
                        item
                        for item in checks
                        if isinstance(item, dict) and item.get("ok") is False
                    ]
                    optional_failures = [
                        item
                        for item in failed
                        if (
                            "optional" in str(item.get("detail", "")).lower()
                            or str(item.get("name", "")).strip().lower() in {"docker", "docker running"}
                        )
                    ]
                    required_failures = [
                        item for item in failed if item not in optional_failures
                    ]
                else:
                    failed = []
                    optional_failures = []
                    required_failures = []
                out["cli_doctor"] = {
                    "exit_code": proc.returncode,
                    "ok": bool(payload.get("ok")),
                    "checks": checks if isinstance(checks, list) else [],
                    "failed_checks": failed,
                    "optional_failures": optional_failures,
                    "required_failures": required_failures,
                    "package_version": (payload.get("_meta") or {}).get("version")
                    if isinstance(payload.get("_meta"), dict)
                    else None,
                    "stdout_tail": (proc.stdout or "")[-4000:],
                    "stderr_tail": (proc.stderr or "")[-4000:],
                    "json": True,
                }
                # Docker, local transcription, TTS, and BGM are optional
                # integrations for the HyperFrames renderer.  A failed core
                # browser/codec/runtime check remains a hard blocker.
                required_ok = not required_failures
                return ToolResult(
                    success=required_ok,
                    data=out,
                    error=(
                        "HyperFrames doctor required checks failed: "
                        + "; ".join(
                            str(item.get("name") or item.get("detail") or "unknown")
                            for item in required_failures
                        )
                        if not required_ok
                        else None
                    ),
                )

            out["cli_doctor"] = {
                "exit_code": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-4000:],
                "stderr_tail": (proc.stderr or "")[-4000:],
                "json": False,
            }
            # A non-JSON doctor report cannot prove the required checks.  Do
            # not turn a successful process exit into a production-ready
            # verdict; preserve the report for diagnosis instead.
            return ToolResult(
                success=False,
                data=out,
                error="hyperframes doctor did not return a machine-readable report",
            )
        except Exception as e:
            out["cli_doctor_error"] = str(e)
            return ToolResult(
                success=False,
                error=f"hyperframes doctor failed: {e}",
                data=out,
            )

    def _scaffold(self, inputs: dict[str, Any]) -> ToolResult:
        """Materialize the HyperFrames workspace from OpenMontage artifacts.

        This does NOT call `hyperframes init` — we want full control over the
        generated files so they map cleanly to edit_decisions. `init` is
        meant for humans bootstrapping a project by hand.
        """
        workspace = self._resolve_workspace(inputs)
        edit_decisions = inputs.get("edit_decisions") or {}
        asset_manifest = inputs.get("asset_manifest") or {}
        playbook = inputs.get("playbook") or {}
        profile_name = inputs.get("profile")
        production_mode = bool(inputs.get("production_mode") or inputs.get("strict"))
        mapping: dict[str, Any] | None = None

        if not edit_decisions.get("cuts"):
            return ToolResult(
                success=False,
                error="edit_decisions with non-empty cuts[] is required for scaffold_workspace",
            )

        width, height, fps = self._resolve_dimensions(profile_name, inputs.get("fps", 30))

        if production_mode:
            try:
                from lib.hyperframes_contracts import build_edit_mapping

                mapping = build_edit_mapping(edit_decisions, asset_manifest, strict=True)
            except Exception as exc:
                return ToolResult(
                    success=False,
                    error=f"Canonical HyperFrames edit contract failed: {exc}",
                    data={"operation": "scaffold_workspace", "contract": "rejected"},
                )

        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "compositions").mkdir(exist_ok=True)
        assets_dir = workspace / "assets"
        assets_dir.mkdir(exist_ok=True)

        # Resolve asset IDs → file paths + copy into workspace.
        resolved_cuts, asset_copies = self._resolve_and_stage_assets(
            edit_decisions.get("cuts", []),
            asset_manifest.get("assets", []),
            workspace,
            hash_names=production_mode,
        )

        audio_refs = self._resolve_audio_refs(
            edit_decisions,
            asset_manifest.get("assets", []),
            workspace,
            hash_names=production_mode,
            total_duration=self._compute_total_duration(resolved_cuts),
        )

        # Style bridge: playbook → CSS custom properties + DESIGN.md.
        css_vars, design_md = self._style_bridge(playbook, edit_decisions)

        # Write a local runtime dependency.  Production workspaces must be
        # renderable after installation without fetching a public CDN.
        local_runtime = self._ensure_local_runtime(workspace)

        # Write hyperframes.json (registry config).  The registry URL is used
        # only by the explicit add_block operation; the render workspace never
        # loads it at runtime.
        (workspace / "hyperframes.json").write_text(
            json.dumps(
                {
                    "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
                    "runtime_dependencies": {"gsap": local_runtime},
                    "paths": {
                        "blocks": "compositions",
                        "components": "compositions/components",
                        "assets": "assets",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Write DESIGN.md (convenience file for human review + workspace context).
        if design_md:
            (workspace / "DESIGN.md").write_text(design_md, encoding="utf-8")

        # Write index.html — the main composition.
        total_duration = self._compute_total_duration(resolved_cuts)
        try:
            caption_contract, caption_sidecar = self._build_caption_contract(
                inputs,
                edit_decisions,
                asset_manifest.get("assets", []),
                workspace,
                width=width,
                height=height,
                fps=fps,
                duration_seconds=total_duration,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Caption render contract rejected: {exc}",
                data={"operation": "scaffold_workspace", "caption_contract": None},
            )
        if caption_contract is not None:
            (workspace / "caption_render_contract.json").write_text(
                json.dumps(caption_contract, indent=2) + "\n", encoding="utf-8"
            )
        html = self._generate_index_html(
            cuts=resolved_cuts,
            audio_refs=audio_refs,
            width=width,
            height=height,
            total_duration=total_duration,
            css_vars=css_vars,
            title=edit_decisions.get("metadata", {}).get("title")
            or f"OpenMontage {edit_decisions.get('renderer_family', 'composition')}",
            fps=fps,
            mapping=mapping,
            caption_contract=caption_contract,
        )
        (workspace / "index.html").write_text(html, encoding="utf-8")

        motion_sidecar = None
        audio_plan = None
        if mapping is not None:
            from lib.hyperframes_contracts import build_audio_plan, write_motion_sidecar

            motion_sidecar = write_motion_sidecar(workspace, mapping)
            audio_plan = mapping.get("audio")
            (workspace / "audio_plan.json").write_text(
                json.dumps(audio_plan, indent=2) + "\n", encoding="utf-8"
            )
            (workspace / "EDIT_MAPPING.json").write_text(
                json.dumps(mapping, indent=2) + "\n", encoding="utf-8"
            )

        return ToolResult(
            success=True,
            data={
                "operation": "scaffold_workspace",
                "workspace": str(workspace),
                "width": width,
                "height": height,
                "fps": fps,
                "total_duration_seconds": total_duration,
                "cut_count": len(resolved_cuts),
                "asset_copies": asset_copies,
                "production_mode": production_mode,
                "edit_mapping": mapping,
                "audio_plan": audio_plan,
                "local_runtime": local_runtime,
                "motion_sidecar": str(motion_sidecar) if motion_sidecar else None,
                "caption_render_contract": caption_contract,
                "caption_sidecar": str(caption_sidecar) if caption_sidecar else None,
            },
            artifacts=[
                str(workspace / "index.html"),
                *([str(motion_sidecar)] if motion_sidecar else []),
                *([str(workspace / "audio_plan.json")] if audio_plan is not None else []),
                *([str(workspace / "caption_render_contract.json")] if caption_contract is not None else []),
                *([str(caption_sidecar)] if caption_sidecar is not None else []),
            ],
        )

    def _lint(self, inputs: dict[str, Any]) -> ToolResult:
        workspace = self._resolve_workspace(inputs)
        if not (workspace / "index.html").exists():
            return ToolResult(
                success=False,
                error=f"No index.html in {workspace}. Run scaffold_workspace first.",
            )
        # HyperFrames 0.8.x does not expose the `--strict` flag on `lint`
        # (it is supported by `inspect`).  Production strictness is enforced
        # from the machine-readable finding counts below instead of passing an
        # unsupported flag that would make every render fail immediately.
        production_mode = bool(inputs.get("strict") or inputs.get("production_mode"))
        args = ["lint", "--json"]
        proc = self._run_hf(args, cwd=workspace, timeout=120, check=False)
        data: dict[str, Any] = {"exit_code": proc.returncode}
        payload = self._parse_json_output(proc.stdout)
        if payload is not None:
            data["report"] = payload
        else:
            data["stdout_tail"] = (proc.stdout or "")[-4000:]
        data["stderr_tail"] = (proc.stderr or "")[-2000:]
        findings = payload.get("findings") if isinstance(payload, dict) else None
        if not isinstance(findings, list):
            # A successful CLI may omit both arrays when there are no
            # findings.  Normalize that shape to an empty list instead of
            # leaking ``None`` into the severity-count comprehensions below.
            candidate = payload.get("issues") if isinstance(payload, dict) else None
            findings = candidate if isinstance(candidate, list) else []
        errors = payload.get("errorCount") if isinstance(payload, dict) else None
        warnings = payload.get("warningCount") if isinstance(payload, dict) else None
        if not isinstance(errors, int):
            errors = sum(
                1
                for item in findings
                if isinstance(item, dict)
                and str(item.get("severity", item.get("level", "error"))).lower() in {"error", "fatal"}
            )
        if not isinstance(warnings, int):
            warnings = sum(
                1
                for item in findings
                if isinstance(item, dict)
                and str(item.get("severity", item.get("level", ""))).lower() in {"warning", "warn"}
            )
        data["error_count"] = int(errors)
        data["warning_count"] = int(warnings)
        # In production both errors and warnings are blockers.  If the CLI
        # does not return JSON, a zero exit code is insufficient evidence.
        structured_ok = payload is not None
        ok = proc.returncode == 0 and (
            not production_mode or (structured_ok and int(errors) == 0 and int(warnings) == 0)
        )
        return ToolResult(
            success=ok,
            data=data,
            error=(
                None
                if ok
                else (
                    f"hyperframes lint exit {proc.returncode}"
                    if proc.returncode != 0
                    else f"HyperFrames lint found {int(errors)} error(s) and {int(warnings)} warning(s)"
                )
            ),
        )

    def _validate(self, inputs: dict[str, Any]) -> ToolResult:
        workspace = self._resolve_workspace(inputs)
        if not (workspace / "index.html").exists():
            return ToolResult(
                success=False,
                error=f"No index.html in {workspace}. Run scaffold_workspace first.",
            )
        args = ["validate", "--json"]
        if inputs.get("skip_contrast"):
            args.append("--no-contrast")
        proc = self._run_hf(args, cwd=workspace, timeout=300, check=False)
        data: dict[str, Any] = {"exit_code": proc.returncode}
        payload = self._parse_json_output(proc.stdout)
        if payload is not None:
            data["report"] = payload
        else:
            data["stdout_tail"] = (proc.stdout or "")[-4000:]
        data["stderr_tail"] = (proc.stderr or "")[-2000:]
        production_mode = bool(inputs.get("strict") or inputs.get("production_mode"))
        errors = payload.get("errors") if isinstance(payload, dict) else []
        warnings = payload.get("warnings") if isinstance(payload, dict) else []
        if not isinstance(errors, list):
            errors = []
        if not isinstance(warnings, list):
            warnings = []
        data["error_count"] = len(errors)
        data["warning_count"] = len(warnings)
        contrast_failures = int(payload.get("contrastFailures", 0) or 0) if isinstance(payload, dict) else 0
        data["contrast_failure_count"] = contrast_failures
        # HyperFrames returns exit 0 for advisory browser warnings.  In a
        # production render those warnings are still evidence of timing,
        # media, or accessibility drift and must stop promotion.
        report_ok = not isinstance(payload, dict) or payload.get("ok", True) is not False
        ok = proc.returncode == 0 and report_ok and not errors and contrast_failures == 0
        if production_mode:
            ok = ok and not warnings
        return ToolResult(
            success=ok,
            data=data,
            error=(
                None
                if ok
                else (
                    f"hyperframes validate exit {proc.returncode}"
                    if proc.returncode != 0
                    else f"HyperFrames validate found {len(errors)} error(s), {len(warnings)} warning(s), and {contrast_failures} contrast failure(s)"
                )
            ),
        )

    def _inspect(self, inputs: dict[str, Any]) -> ToolResult:
        """Run HyperFrames' seek/layout/motion inspection gate."""
        workspace = self._resolve_workspace(inputs)
        if not (workspace / "index.html").exists():
            return ToolResult(
                success=False,
                error=f"No index.html in {workspace}. Run scaffold_workspace first.",
            )
        args = ["inspect", "--json"]
        if inputs.get("strict") or inputs.get("production_mode"):
            args.append("--strict")
        proc = self._run_hf(args, cwd=workspace, timeout=300, check=False)
        data: dict[str, Any] = {"exit_code": proc.returncode}
        payload = self._parse_json_output(proc.stdout)
        if payload is not None:
            data["report"] = payload
            issues = payload.get("issues") if isinstance(payload, dict) else None
            critical = [item for item in issues or [] if isinstance(item, dict) and str(item.get("severity", "error")).lower() == "error"]
            data["critical_issue_count"] = len(critical)
        else:
            data["stdout_tail"] = (proc.stdout or "")[-4000:]
        data["stderr_tail"] = (proc.stderr or "")[-2000:]
        ok = proc.returncode == 0
        return ToolResult(
            success=ok,
            data=data,
            error=None if ok else f"hyperframes inspect exit {proc.returncode}",
        )

    def _add_block(self, inputs: dict[str, Any]) -> ToolResult:
        """Install a registry block or component via `hyperframes add`.

        Blocks are standalone sub-compositions (own dimensions, duration, timeline)
        that land at `compositions/<name>.html`. Components are effect snippets
        that land at `compositions/components/<name>.html`. After install, the
        caller is responsible for wiring the block into `index.html` via
        `data-composition-src` or pasting the component's snippet — see
        `.agents/skills/hyperframes-registry/SKILL.md`.
        """
        workspace = self._require_workspace(inputs)
        block = (inputs.get("block_name") or "").strip()
        if not block:
            return ToolResult(
                success=False,
                error="block_name is required for operation='add_block'",
            )
        if not workspace.exists():
            return ToolResult(
                success=False,
                error=(
                    f"Workspace {workspace} does not exist. Run "
                    "operation='scaffold_workspace' first."
                ),
            )
        args = ["add", block, "--json", "--no-clipboard"]
        proc = self._run_hf(args, cwd=workspace, timeout=300, check=False)
        data: dict[str, Any] = {
            "operation": "add_block",
            "block_name": block,
            "workspace": str(workspace),
            "exit_code": proc.returncode,
        }
        payload = self._parse_json_output(proc.stdout)
        if payload is not None:
            data["report"] = payload
        else:
            data["stdout_tail"] = (proc.stdout or "")[-4000:]
        data["stderr_tail"] = (proc.stderr or "")[-2000:]
        ok = proc.returncode == 0
        return ToolResult(
            success=ok,
            data=data,
            error=None if ok else f"hyperframes add {block} exit {proc.returncode}",
        )

    def _render(self, inputs: dict[str, Any]) -> ToolResult:
        """Full pipeline: scaffold → lint → validate → inspect → render."""
        self._offline_mode = bool(inputs.get("offline"))
        production_mode = bool(inputs.get("production_mode") or inputs.get("strict"))
        runtime_ok = self._runtime_check()
        if not runtime_ok["runtime_available"]:
            return ToolResult(
                success=False,
                error=(
                    "HyperFrames runtime not available: "
                    + "; ".join(runtime_ok["reasons"])
                    + ". Per governance, this is a blocker — do NOT silently "
                    "fall back to another runtime without user approval."
                ),
                data={"runtime_check": runtime_ok},
            )

        initial_workspace = Path(inputs["workspace_path"]).expanduser().resolve() if inputs.get("workspace_path") else None
        raw_output_path = inputs.get("output_path")
        run_envelope = None
        project_root = Path(inputs["project_dir"]).expanduser().resolve() if inputs.get("project_dir") else None
        try:
            if project_root is None and initial_workspace is not None:
                for candidate in (initial_workspace, *initial_workspace.parents):
                    if (candidate / "work_order.json").is_file() or (candidate / "project.json").is_file():
                        project_root = candidate
                        break
            run_id = inputs.get("run_id")
            if project_root is not None and not run_id:
                for identity_name in ("work_order.json", "project.json"):
                    try:
                        payload = json.loads((project_root / identity_name).read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict) and payload.get("run_id"):
                        run_id = payload["run_id"]
                        break
            if project_root is not None and run_id:
                from lib.paths import run_paths
                run_envelope = run_paths(project_root, str(run_id))
        except (OSError, ValueError) as exc:
            return ToolResult(success=False, error=f"run-scoped HyperFrames paths are invalid: {exc}")

        try:
            workspace = self._resolve_workspace(
                dict(inputs, project_dir=str(project_root) if project_root is not None else inputs.get("project_dir"), run_id=run_id)
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not raw_output_path:
            raw_output_path = workspace / "renders" / "final.mp4"

        raw_path = Path(raw_output_path).expanduser()
        if project_root is not None and not raw_path.is_absolute():
            raw_path = project_root / raw_path
        final_output_path = raw_path.resolve()
        output_path = final_output_path
        if run_envelope is not None:
            candidate_root = run_envelope.candidates.resolve()
            if not output_path.is_relative_to(candidate_root):
                from lib.output_promotion import candidate_path
                output_path = candidate_path(candidate_root, final_output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        steps: dict[str, Any] = {}

        # 1. Scaffold — generate HTML/CSS/assets.
        scaffold_inputs = dict(inputs, workspace_path=str(workspace))
        if project_root is not None:
            scaffold_inputs["project_dir"] = str(project_root)
        if run_id:
            scaffold_inputs["run_id"] = str(run_id)
        scaffold = self._scaffold(scaffold_inputs)
        steps["scaffold"] = scaffold.data
        if not scaffold.success:
            return ToolResult(
                success=False,
                error=f"Scaffold failed: {scaffold.error}",
                data={"steps": steps},
            )
        if production_mode:
            try:
                from lib.hyperframes_contracts import workspace_digest

                steps["workspace"] = {
                    "path": str(workspace),
                    "digest_before_checks": workspace_digest(workspace),
                    "isolated": bool(run_envelope is not None),
                }
            except Exception as exc:
                return ToolResult(success=False, error=f"Workspace integrity check failed: {exc}", data={"steps": steps})

        # 2. Lint — static contract checks.
        lint = self._lint({"workspace_path": str(workspace), "strict": production_mode, "production_mode": production_mode})
        steps["lint"] = lint.data
        if not lint.success:
            if production_mode:
                return ToolResult(
                    success=False,
                    error=f"Lint failed (strict mode): {lint.error}",
                    data={"steps": steps},
                )
            log.warning("hyperframes lint reported issues (non-strict mode, continuing)")

        # 3. Validate — browser-based contract + contrast.
        validate = self._validate(
            {
                "workspace_path": str(workspace),
                "skip_contrast": inputs.get("skip_contrast", False),
                "production_mode": production_mode,
            }
        )
        steps["validate"] = validate.data
        if not validate.success:
            return ToolResult(
                success=False,
                error=(
                    f"Validate failed: {validate.error}. HyperFrames render "
                    f"is blocked — fix the composition and re-run."
                ),
                data={"steps": steps},
            )

        # 4. Inspect — seek/layout/motion checks.  This is mandatory for a
        # production render and intentionally precedes any renderer call.
        if production_mode:
            inspect = self._inspect(
                {
                    "workspace_path": str(workspace),
                    "strict": True,
                    "production_mode": True,
                }
            )
            steps["inspect"] = inspect.data
            if not inspect.success:
                return ToolResult(
                    success=False,
                    error=f"Inspect failed: {inspect.error}. HyperFrames render is blocked.",
                    data={"steps": steps},
                )

        # 5. Render.
        width, height, fps = self._resolve_dimensions(
            inputs.get("profile"), inputs.get("fps", 30)
        )
        quality = inputs.get("quality", "standard")
        args = [
            "render",
            "--output", str(output_path),
            "--fps", str(fps),
            "--quality", quality,
        ]
        worker_policy = None
        if production_mode:
            try:
                from lib.hyperframes_contracts import select_worker_policy

                mapping = (scaffold.data or {}).get("edit_mapping") if isinstance(scaffold.data, dict) else None
                if mapping:
                    worker_policy = select_worker_policy(
                        mapping, requested_workers=inputs.get("workers")
                    )
                    args.extend(["--workers", str(worker_policy["workers"])])
            except Exception as exc:
                return ToolResult(success=False, error=f"Worker policy failed: {exc}", data={"steps": steps})
        steps["worker_policy"] = worker_policy
        resource_budget = inputs.get("resource_budget") if isinstance(inputs.get("resource_budget"), dict) else {}
        render_timeout = int(resource_budget.get("render_timeout_seconds", 1800) or 1800)
        if render_timeout < 1:
            return ToolResult(success=False, error="resource_budget.render_timeout_seconds must be positive", data={"steps": steps})
        proc = self._run_hf(args, cwd=workspace, timeout=render_timeout, check=False)
        steps["render"] = {
            "exit_code": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-4000:],
            "stderr_tail": (proc.stderr or "")[-4000:],
        }
        if proc.returncode != 0:
            if run_envelope is not None and output_path.exists() and output_path != final_output_path:
                output_path.unlink(missing_ok=True)
            return ToolResult(
                success=False,
                error=f"hyperframes render exit {proc.returncode}",
                data={"steps": steps},
            )

        if not output_path.exists():
            return ToolResult(
                success=False,
                error=(
                    f"hyperframes render exited 0 but output file missing: "
                    f"{output_path}. Check stdout_tail for the real path."
                ),
                data={"steps": steps},
            )

        promotion = None
        media_probe = None
        if run_envelope is not None and output_path != final_output_path:
            try:
                from lib.output_promotion import promote_candidate
                from lib.run_record import read_run_record

                run_record = read_run_record(
                    run_envelope.root.parent.parent, run_envelope.run_id
                )
                run_metadata = run_record.get("metadata") or {}
                run_started_at = run_metadata.get("attempt_started_at") or run_record.get("started_at")

                promotion = promote_candidate(
                    output_path,
                    final_output_path,
                    profile=inputs.get("profile"),
                    expected_duration_seconds=self._compute_total_duration(
                        (inputs.get("edit_decisions") or {}).get("cuts", [])
                    ) or None,
                    provenance={
                        "project_id": run_envelope.root.parent.parent.name,
                        "run_id": run_envelope.run_id,
                        "stage": str(inputs.get("stage") or "compose"),
                        "tool": self.name,
                        "run_record_ref": f"runs/{run_envelope.run_id}/run.json",
                    },
                    run_started_at=run_started_at,
                )
                media_probe = promotion.get("probe") if isinstance(promotion, dict) else None
            except Exception as exc:
                if output_path.exists():
                    output_path.unlink(missing_ok=True)
                return ToolResult(
                    success=False,
                    error=f"HyperFrames output promotion failed: {exc}",
                    data={"steps": steps},
                )
        # A workspace-only invocation without a project/profile is a local
        # scaffold smoke path (used by tooling tests). Durable production
        # runs and profile-bound renders must always pass the ffprobe gate.
        if media_probe is None and (
            run_envelope is not None or project_root is not None or inputs.get("profile")
            or inputs.get("expected_duration_seconds") is not None
        ):
            try:
                from lib.output_promotion import probe_media, validate_media_contract

                media_probe = probe_media(final_output_path)
                validate_media_contract(
                    media_probe,
                    profile=inputs.get("profile"),
                    expected_duration_seconds=self._compute_total_duration(
                        (inputs.get("edit_decisions") or {}).get("cuts", [])
                    ) or None,
                )
            except Exception as exc:
                return ToolResult(
                    success=False,
                    error=f"HyperFrames output media validation failed: {exc}",
                    data={"steps": steps},
                )

        return ToolResult(
            success=True,
            data={
                "operation": "render",
                "output": str(final_output_path),
                "workspace": str(workspace),
                "width": width,
                "height": height,
                "fps": fps,
                "quality": quality,
                "steps": steps,
                "run_dir": str(run_envelope.root) if run_envelope else None,
                "output_promotion": promotion,
                "media_probe": media_probe,
                "worker_policy": worker_policy,
                "resource_budget": resource_budget,
                "offline": bool(inputs.get("offline")),
                "runtime_check": runtime_ok,
                "caption_render_contract": (scaffold.data or {}).get("caption_render_contract") if isinstance(scaffold.data, dict) else None,
                "caption_sidecar": (scaffold.data or {}).get("caption_sidecar") if isinstance(scaffold.data, dict) else None,
                "workspace_digest": steps.get("workspace", {}).get("digest_before_checks") if isinstance(steps.get("workspace"), dict) else None,
                "render_report": {
                    "runtime": "hyperframes",
                    "runtime_package": runtime_ok.get("npm_package"),
                    "runtime_version": runtime_ok.get("npm_package_version"),
                    "workspace": str(workspace),
                    "workspace_digest": steps.get("workspace", {}).get("digest_before_checks") if isinstance(steps.get("workspace"), dict) else None,
                    "worker_policy": worker_policy,
                    "steps": ["scaffold", "lint", "validate", *(["inspect"] if production_mode else []), "render"],
                },
            },
            artifacts=[
                str(final_output_path),
                *([
                    str(Path(str((scaffold.data or {}).get("workspace"))) / "caption_render_contract.json")
                ] if isinstance(scaffold.data, dict) and (scaffold.data or {}).get("caption_render_contract") else []),
                *([
                    str((scaffold.data or {}).get("caption_sidecar"))
                ] if isinstance(scaffold.data, dict) and (scaffold.data or {}).get("caption_sidecar") else []),
            ],
        )

    # ------------------------------------------------------------------
    # Workspace generation helpers
    # ------------------------------------------------------------------

    def _resolve_workspace(self, inputs: dict[str, Any]) -> Path:
        """Resolve a workspace, preferring the immutable run envelope.

        A production run may not write to a caller-selected shared directory.
        When a project/run identity is present, the canonical location is
        ``runs/<uuid>/work/hyperframes`` and any explicit path must remain
        inside that envelope.
        """
        raw = inputs.get("workspace_path")
        project_dir = inputs.get("project_dir")
        run_id = inputs.get("run_id")
        production_mode = bool(inputs.get("production_mode") or inputs.get("strict"))
        if project_dir and run_id:
            try:
                from lib.paths import run_paths

                envelope = run_paths(Path(project_dir).expanduser().resolve(), str(run_id))
            except Exception as exc:
                raise ValueError(f"invalid run-scoped HyperFrames workspace: {exc}") from exc
            canonical = (envelope.work / "hyperframes").resolve()
            if not raw:
                return canonical
            requested = Path(raw).expanduser().resolve()
            try:
                requested.relative_to(envelope.work.resolve())
            except ValueError as exc:
                if production_mode:
                    raise ValueError(
                        "production HyperFrames workspace must remain inside the run work envelope"
                    ) from exc
            else:
                return requested
        if not raw:
            raise ValueError("workspace_path is required for this operation")
        return Path(raw).expanduser().resolve()

    @staticmethod
    def _require_workspace(inputs: dict[str, Any]) -> Path:
        raw = inputs.get("workspace_path")
        if not raw:
            raise ValueError("workspace_path is required for this operation")
        return Path(raw).resolve()

    @staticmethod
    def _resolve_dimensions(
        profile_name: Optional[str], fps_in: int
    ) -> tuple[int, int, int]:
        """Resolve output dimensions from the media profile, with a safe default."""
        if profile_name:
            try:
                from lib.media_profiles import get_profile  # type: ignore
                p = get_profile(profile_name)
                return int(p.width), int(p.height), int(p.fps)
            except Exception:
                pass
        return 1920, 1080, int(fps_in)

    @staticmethod
    def _compute_total_duration(cuts: list[dict]) -> float:
        if not cuts:
            return 0.0
        return max(float(c.get("out_seconds", 0) or 0) for c in cuts)

    def _build_caption_contract(
        self,
        inputs: dict[str, Any],
        edit_decisions: dict[str, Any],
        assets: list[dict],
        workspace: Path,
        *,
        width: int,
        height: int,
        fps: int,
        duration_seconds: float,
    ) -> tuple[dict[str, Any] | None, Path | None]:
        """Resolve one caption source and certify it for the HyperFrames run."""

        from lib.caption_contracts import (
            CaptionContractError,
            build_caption_render_contract,
            cues_from_transcript,
            load_caption_cues,
            validate_verified_transcript,
        )

        subtitles = edit_decisions.get("subtitles") if isinstance(edit_decisions, dict) else {}
        subtitles = subtitles if isinstance(subtitles, dict) else {}
        source_value = inputs.get("subtitle_path") or subtitles.get("source")
        raw_cues = inputs.get("captions")
        transcript = inputs.get("transcript") or inputs.get("verified_transcript")
        require_verified = bool(inputs.get("require_verified_transcript", False))
        production_mode = bool(inputs.get("production_mode") or inputs.get("strict"))
        verification: dict[str, Any] | None = None

        if isinstance(transcript, dict):
            candidate_segments = transcript.get("segments") or transcript.get("word_timestamps")
            if isinstance(candidate_segments, list) and candidate_segments:
                if raw_cues is None:
                    raw_cues = cues_from_transcript(
                        transcript,
                        max_words_per_cue=int(inputs.get("max_words_per_cue", 6) or 6),
                        max_chars_per_line=int(inputs.get("max_chars_per_line", 42) or 42),
                    )
        if require_verified:
            payload = dict(transcript or {}) if isinstance(transcript, dict) else {}
            if "segments" not in payload:
                payload["segments"] = []
            if "verified" not in payload and "verification_status" not in payload:
                payload["verified"] = bool(inputs.get("transcript_verified", False))
            verification = validate_verified_transcript(
                payload,
                expected_language=inputs.get("expected_language"),
                expected_text=inputs.get("expected_text"),
            )
            if verification.get("valid") is not True:
                raise CaptionContractError(
                    "; ".join(verification.get("errors") or ["verified transcript validation failed"])
                )

        # Resolve an asset-manifest ID or a project-relative sidecar path.
        source_path: Path | None = None
        if source_value:
            asset_lookup = {str(item.get("id")): item for item in assets if isinstance(item, dict) and item.get("id")}
            candidate = asset_lookup.get(str(source_value))
            source_path = Path(str(candidate.get("path"))) if candidate and candidate.get("path") else Path(str(source_value))
            if not source_path.is_absolute() and inputs.get("project_dir"):
                source_path = Path(str(inputs["project_dir"])) / source_path
            source_path = source_path.expanduser().resolve()
            if not source_path.is_file():
                raise CaptionContractError(f"caption file not found: {source_path}")
            if raw_cues is None:
                raw_cues = load_caption_cues(source_path)

        if raw_cues is None:
            enabled = bool(subtitles.get("enabled"))
            if enabled and (production_mode or require_verified):
                raise CaptionContractError(
                    "subtitles are enabled but no caption cues, transcript, or subtitle_path was provided"
                )
            return None, None

        if not isinstance(raw_cues, list):
            raise CaptionContractError("captions must be an array")
        caption_mode = str(inputs.get("caption_mode", "overlay")).strip().lower()
        if caption_mode not in {"overlay", "sidecar"}:
            raise CaptionContractError("HyperFrames caption_mode must be 'overlay' or 'sidecar'")
        contract = build_caption_render_contract(
            runtime="hyperframes",
            mode=caption_mode,
            cues=raw_cues,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            fps=fps,
            safe_area=inputs.get("safe_area"),
            max_chars_per_line=int(inputs.get("max_chars_per_line", 42) or 42),
            max_lines=int(inputs.get("max_lines", 2) or 2),
            font_size=int(inputs.get("caption_font_size", 42) or 42),
            style={"caption_position": "bottom-center", "render_mode": caption_mode},
            transcript_verification=verification,
            profile_name=inputs.get("profile"),
        )

        sidecar_path: Path | None = None
        captions_dir = workspace / "captions"
        captions_dir.mkdir(parents=True, exist_ok=True)
        if source_path is not None:
            sidecar_path = captions_dir / source_path.name
            if not self._is_inside(source_path, workspace):
                self._copy_atomic(source_path, sidecar_path)
            else:
                sidecar_path = source_path
        else:
            sidecar_path = captions_dir / "captions.caption.json"
            sidecar_path.write_text(
                json.dumps({"cues": contract["cues"], "contract": contract}, indent=2) + "\n",
                encoding="utf-8",
            )
        return contract, sidecar_path

    def _resolve_and_stage_assets(
        self,
        cuts: list[dict],
        assets: list[dict],
        workspace: Path,
        *,
        hash_names: bool = False,
    ) -> tuple[list[dict], list[dict[str, str]]]:
        """Resolve asset IDs in cuts[].source, copy files into workspace/assets/.

        HyperFrames resolves `src=` relative to the composition HTML file, so
        every asset must live inside the workspace tree. Copying is simpler
        (and portable) than symlinking, at the cost of disk space — these
        are regenerable under `projects/`.
        """
        asset_lookup = {a["id"]: a for a in assets if "id" in a}
        assets_dir = workspace / "assets"
        copies: list[dict[str, str]] = []
        resolved: list[dict] = []
        for cut in cuts:
            source = cut.get("source", "")
            resolved_cut = dict(cut)
            if source in asset_lookup:
                resolved_cut["source"] = asset_lookup[source].get("path", source)
            src_path = Path(resolved_cut["source"]).expanduser() if resolved_cut.get("source") else None
            if src_path and src_path.exists() and not self._is_inside(src_path, workspace):
                dest = assets_dir / self._staged_name(src_path, hash_names=hash_names)
                if not dest.exists() or dest.stat().st_size != src_path.stat().st_size:
                    self._copy_atomic(src_path, dest)
                resolved_cut["source"] = str(dest)
                copies.append({"from": str(src_path), "to": str(dest)})
            resolved.append(resolved_cut)
        return resolved, copies

    def _resolve_audio_refs(
        self,
        edit_decisions: dict[str, Any],
        assets: list[dict],
        workspace: Path,
        *,
        hash_names: bool = False,
        total_duration: float | None = None,
    ) -> dict[str, Any]:
        """Resolve and stage narration, music, and SFX while preserving stems."""
        asset_lookup = {a["id"]: a for a in assets if "id" in a}
        assets_dir = workspace / "assets"
        out: dict[str, Any] = {"narration": [], "music": None, "sfx": [], "plan": None}
        duration = float(total_duration or self._compute_total_duration(edit_decisions.get("cuts", [])) or 1)
        try:
            from lib.hyperframes_contracts import build_audio_plan

            plan = build_audio_plan(edit_decisions, {"assets": assets}, duration)
        except Exception:
            # Non-production fixture scaffolds may carry intentionally partial
            # audio metadata. Preserve the existing permissive smoke path.
            audio = edit_decisions.get("audio") if isinstance(edit_decisions, dict) else {}
            plan = {"narration": [], "music": None, "sfx": []}
            if isinstance(audio, dict):
                plan["narration"] = list(audio.get("narration", {}).get("segments", []) or [])
                plan["music"] = audio.get("music")
                plan["sfx"] = list(audio.get("sfx", []) or [])

        def stage(asset_id: Any) -> Path | None:
            aid = str(asset_id or "")
            item = asset_lookup.get(aid)
            src = Path(str(item.get("path") or "")).expanduser() if item else Path("")
            if not item or not src.exists() or not src.is_file():
                return None
            if self._is_inside(src, workspace):
                return src
            dest = assets_dir / self._staged_name(src, hash_names=hash_names)
            if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                self._copy_atomic(src, dest)
            return dest

        for segment in plan.get("narration", []) or []:
            path = stage(segment.get("asset_id")) if isinstance(segment, dict) else None
            if path is None:
                continue
            item = dict(segment)
            item["src"] = str(path)
            out["narration"].append(item)
        music = plan.get("music")
        if isinstance(music, dict):
            path = stage(music.get("asset_id"))
            if path is not None:
                out["music"] = {**music, "src": str(path)}
        for sfx in plan.get("sfx", []) or []:
            path = stage(sfx.get("asset_id")) if isinstance(sfx, dict) else None
            if path is not None:
                out["sfx"].append({**dict(sfx), "src": str(path)})
        out["plan"] = plan
        return out

    @staticmethod
    def _staged_name(path: Path, *, hash_names: bool) -> str:
        if not hash_names:
            return path.name
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return f"{digest.hexdigest()[:16]}_{path.name}"
        except OSError:
            return path.name

    @staticmethod
    def _copy_atomic(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _ensure_local_runtime(workspace: Path) -> str:
        """Ensure a deterministic local GSAP-compatible runtime is present.

        A checked-in/vendor GSAP bundle is preferred when supplied by a
        deployment image.  For a clean OpenMontage workspace we materialise a
        small seekable subset locally; it implements the timeline operations
        emitted by this adapter and, critically, never relies on a network
        request during render.
        """
        vendor = workspace / "vendor"
        vendor.mkdir(parents=True, exist_ok=True)
        destination = vendor / "gsap.min.js"
        if destination.is_file() and destination.stat().st_size > 1024:
            try:
                marker = destination.read_text(encoding="utf-8", errors="ignore")[:256]
            except OSError:
                marker = ""
            # Replace the historical compatibility shim automatically.  It is
            # useful for isolated unit tests, but it is not an acceptable
            # production animation engine (HyperFrames calls the full GSAP
            # timeline API during browser validation).
            if "OpenMontage local seekable GSAP subset" not in marker:
                return "vendor/gsap.min.js"
        configured = os.environ.get("OPENMONTAGE_GSAP_PATH")
        candidates = [Path(configured).expanduser()] if configured else []
        repo_root = Path(__file__).resolve().parents[2]
        candidates.extend(
            [
                workspace / "node_modules" / "gsap" / "dist" / "gsap.min.js",
                repo_root / "node_modules" / "gsap" / "dist" / "gsap.min.js",
                repo_root / "tools" / "video" / "vendor" / "gsap.min.js",
            ]
        )
        for candidate in candidates:
            if candidate.is_file() and candidate.stat().st_size > 1024:
                temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
                try:
                    shutil.copy2(candidate, temporary)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                return "vendor/gsap.min.js"
        runtime = r"""/* OpenMontage local seekable GSAP subset; no network dependency. */
(function (global) {
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function resolve(target) {
    if (typeof target !== 'string') return target;
    try { return Array.prototype.slice.call(global.document.querySelectorAll(target)); } catch (_) { return []; }
  }
  function nodesFor(target) {
    var value = resolve(target);
    if (value == null) return [];
    if (Array.isArray(value)) return value;
    if (value.length != null && typeof value !== 'string' && !value.nodeType) return Array.prototype.slice.call(value);
    return [value];
  }
  function Timeline(opts) {
    this.ops = [];
    this._paused = !(opts && opts.paused === false);
    this._time = 0;
    this._timeScale = 1;
    this._killed = false;
  }
  Timeline.prototype._add = function (kind, selector, vars, at) {
    var start = Number(at || 0);
    var copy = Object.assign({}, vars || {});
    var duration = kind === 'set' ? 0 : Math.max(0.0001, Number(copy.duration || 0.0001));
    this.ops.push({ kind: kind, selector: selector, vars: copy, at: start, duration: duration, starts: new WeakMap() });
    return this;
  };
  Timeline.prototype.from = function (selector, vars, at) { return this._add('from', selector, vars, at); };
  Timeline.prototype.to = function (selector, vars, at) { return this._add('to', selector, vars, at); };
  Timeline.prototype.fromTo = function (selector, fromVars, toVars, at) {
    var merged = Object.assign({}, toVars || {});
    merged.__from = Object.assign({}, fromVars || {});
    return this._add('fromTo', selector, merged, at);
  };
  Timeline.prototype.set = function (selector, vars, at) { return this._add('set', selector, vars, at); };
  Timeline.prototype.add = function (child, at) {
    if (!child || !Array.isArray(child.ops)) return this;
    var shift = Number(at || 0);
    for (var i = 0; i < child.ops.length; i++) {
      var op = child.ops[i];
      this.ops.push({ kind: op.kind, selector: op.selector, vars: Object.assign({}, op.vars), at: op.at + shift, duration: op.duration, starts: new WeakMap() });
    }
    return this;
  };
  function numericStyle(node, key, fallback) {
    var value = Number(node.dataset['omBase' + key]);
    if (isFinite(value)) return value;
    if (key === 'opacity') {
      value = Number(global.getComputedStyle(node).opacity);
      return isFinite(value) ? value : fallback;
    }
    return fallback;
  }
  function setNumeric(node, key, value, transform) {
    if (key === 'opacity' || key === 'volume') {
      node.style[key] = String(value);
      return;
    }
    transform[key] = value;
  }
  function renderTransform(node, transform) {
    var parts = [];
    if (isFinite(transform.x) || isFinite(transform.y)) parts.push('translate(' + (Number(transform.x || 0)) + 'px,' + (Number(transform.y || 0)) + 'px)');
    if (isFinite(transform.scale)) parts.push('scale(' + Number(transform.scale || 0) + ')');
    if (isFinite(transform.rotation)) parts.push('rotate(' + Number(transform.rotation || 0) + 'deg)');
    node.style.transform = parts.join(' ') || '';
  }
  Timeline.prototype.seek = function (time) {
    var now = Math.max(0, Number(time || 0));
    this._time = now;
    if (this._killed) return this;
    var transforms = new WeakMap();
    this.ops.forEach(function (op) {
      nodesFor(op.selector).forEach(function (node) {
        var transform = transforms.get(node);
        if (!transform) { transform = {}; transforms.set(node, transform); }
        var keys = Object.keys(op.vars);
        keys.forEach(function (key) {
          if (key === 'duration' || key === 'ease' || key === 'overwrite' || key === 'repeat' || key === 'yoyo' || key === '__from') return;
          var value = op.vars[key];
          if (typeof value !== 'number') {
            if (op.kind === 'set' && now >= op.at) node.style[key] = value;
            return;
          }
          if (!op.starts.has(node)) op.starts.set(node, {});
          var startValues = op.starts.get(node);
          if (!Object.prototype.hasOwnProperty.call(startValues, key)) {
            var explicitFrom = op.kind === 'fromTo' && op.vars.__from && typeof op.vars.__from[key] === 'number' ? op.vars.__from[key] : null;
            startValues[key] = explicitFrom == null ? numericStyle(node, key, key === 'opacity' ? 1 : (key === 'scale' ? 1 : 0)) : explicitFrom;
          }
          var from = startValues[key];
          var to = value;
          if (op.kind === 'from') {
            to = key === 'opacity' ? 1 : (key === 'scale' ? 1 : 0);
          }
          var p = op.duration === 0 ? (now >= op.at ? 1 : 0) : clamp((now - op.at) / op.duration, 0, 1);
          setNumeric(node, key, from + (to - from) * p, transform);
          node.dataset['omBase' + key] = String(op.kind === 'from' ? to : (p >= 1 ? to : from));
        });
        renderTransform(node, transform);
      });
    });
    return this;
  };
  Timeline.prototype.totalDuration = function () {
    var end = 0;
    this.ops.forEach(function (op) { end = Math.max(end, op.at + op.duration); });
    return end;
  };
  Timeline.prototype.duration = function (value) { if (value == null) return this.totalDuration(); return this; };
  Timeline.prototype.totalTime = function (value) { if (value == null) return this._time; return this.seek(value); };
  Timeline.prototype.time = function (value) { if (value == null) return this._time; return this.seek(value); };
  Timeline.prototype.progress = function (value) { var d = this.totalDuration(); if (value == null) return d ? this._time / d : 0; return this.seek(Number(value) * d); };
  Timeline.prototype.timeScale = function (value) { if (value == null) return this._timeScale; this._timeScale = Number(value) || 1; return this; };
  Timeline.prototype.paused = function (value) { if (value == null) return this._paused; this._paused = Boolean(value); return this; };
  Timeline.prototype.pause = function (value) { this._paused = true; if (value != null) this.seek(value); return this; };
  Timeline.prototype.play = function (value) { this._paused = false; if (value != null) this.seek(value); return this; };
  Timeline.prototype.restart = function () { this._paused = false; return this.seek(0); };
  Timeline.prototype.getChildren = function () { return []; };
  Timeline.prototype.kill = function () { this._killed = true; this.ops = []; return this; };
  Timeline.prototype.eventCallback = function () { return this; };
  function make(method, target, vars, at) { var tl = new Timeline({ paused: true }); return tl[method](target, vars, at); }
  global.gsap = {
    timeline: function (opts) { return new Timeline(opts || {}); },
    to: function (target, vars) { return make('to', target, vars, 0); },
    from: function (target, vars) { return make('from', target, vars, 0); },
    fromTo: function (target, fromVars, toVars) { var tl = new Timeline({ paused: true }); return tl.fromTo(target, fromVars, toVars, 0); },
    set: function (target, vars) { return make('set', target, vars, 0); }
  };
})(window);
"""
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        try:
            temporary.write_text(runtime, encoding="utf-8", newline="\n")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return "vendor/gsap.min.js"

    @staticmethod
    def _is_inside(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _style_bridge(
        self,
        playbook: dict[str, Any],
        edit_decisions: dict[str, Any],
    ) -> tuple[dict[str, str], str]:
        """Bridge OpenMontage playbook → HyperFrames CSS vars + DESIGN.md.

        Delegates to `lib/hyperframes_style_bridge.py` so the logic is
        shareable and testable. Falls back to a safe built-in default when
        the bridge module isn't available.
        """
        try:
            from lib.hyperframes_style_bridge import style_bridge  # type: ignore
            return style_bridge(playbook, edit_decisions)
        except Exception as e:
            log.debug("style_bridge fallback: %s", e)

        vl = (playbook or {}).get("visual_language", {})
        palette = vl.get("color_palette", {})
        typo = (playbook or {}).get("typography", {})

        def _first(raw: Any, default: str) -> str:
            if isinstance(raw, list) and raw:
                return str(raw[0])
            if isinstance(raw, str) and raw:
                return raw
            return default

        bg = _first(palette.get("background"), "#0B0F1A")
        fg = _first(palette.get("text"), "#F5F5F5")
        accent = _first(palette.get("accent"), "#F59E0B")
        primary = _first(palette.get("primary"), "#2563EB")
        heading = typo.get("heading", {}).get("font") or typo.get("heading", {}).get("family") or "Inter"
        body = typo.get("body", {}).get("font") or typo.get("body", {}).get("family") or "Inter"

        css_vars = {
            "--color-bg": bg,
            "--color-fg": fg,
            "--color-accent": accent,
            "--color-primary": primary,
            "--font-heading": heading,
            "--font-body": body,
            "--ease-primary": "cubic-bezier(0.65, 0, 0.35, 1)",
            "--duration-entrance": "0.6s",
        }
        design_md = (
            "# DESIGN\n\n"
            "Generated by OpenMontage HyperFrames style bridge (fallback).\n\n"
            f"- Background: `{bg}`\n"
            f"- Foreground: `{fg}`\n"
            f"- Accent: `{accent}`\n"
            f"- Primary: `{primary}`\n"
            f"- Heading font: `{heading}`\n"
            f"- Body font: `{body}`\n"
        )
        return css_vars, design_md

    # ------------------------------------------------------------------
    # HTML generation (minimal, Phase 1)
    # ------------------------------------------------------------------

    def _generate_index_html(
        self,
        cuts: list[dict],
        audio_refs: dict[str, Any],
        width: int,
        height: int,
        total_duration: float,
        css_vars: dict[str, str],
        title: str,
        *,
        fps: int = 30,
        mapping: dict[str, Any] | None = None,
        caption_contract: dict[str, Any] | None = None,
    ) -> str:
        """Emit a HyperFrames-contract-compliant index.html.

        Phase 1 covers the minimum required for smoke-testing the runtime:
        - still images (img.clip)
        - video clips (video.clip, muted playsinline + separate audio if needed)
        - text cards (div.clip with styled <h1>)
        - narration segments (audio)
        - music bed (audio, lower volume)

        Richer scene types (registry blocks, kinetic typography) are authored
        by the agent directly into compositions/ — this generator just
        provides a functional starting skeleton.
        """
        vars_css = "\n      ".join(f"{k}: {v};" for k, v in css_vars.items())

        clip_html: list[str] = []
        entrance_tweens: list[str] = []
        for i, cut in enumerate(cuts):
            html, tween = self._cut_to_html(i, cut, width, height, strict=mapping is not None)
            clip_html.append(html)
            if tween:
                entrance_tweens.append(tween)

        caption_html: list[str] = []
        caption_tweens: list[str] = []
        if isinstance(caption_contract, dict) and caption_contract.get("mode") == "overlay":
            safe_pixels = ((caption_contract.get("safe_area") or {}).get("pixels") or {})
            wrapping = caption_contract.get("wrapping") or {}
            safe_bottom = int(safe_pixels.get("bottom", round(height * 0.12)))
            safe_left = int(safe_pixels.get("left", round(width * 0.05)))
            safe_right = int(safe_pixels.get("right", round(width * 0.05)))
            font_size = int(wrapping.get("font_size", 42))
            for index, cue in enumerate(caption_contract.get("cues") or []):
                cue_id = f"om-caption-{index}"
                start = float(cue["start"])
                end = float(cue["end"])
                caption_html.append(
                    f'<div id="{cue_id}" class="om-caption" '
                    f'data-caption-start="{self._f(start)}" data-caption-end="{self._f(end)}">'
                    f'<span>{self._escape_text(str(cue["text"]))}</span></div>'
                )
                caption_tweens.append(
                    f'tl.set("#{cue_id}", {{ opacity: 1 }}, {self._f(start)}); '
                    f'tl.set("#{cue_id}", {{ opacity: 0 }}, {self._f(end)});'
                )
        caption_style = ""
        if isinstance(caption_contract, dict):
            safe_pixels = ((caption_contract.get("safe_area") or {}).get("pixels") or {})
            wrapping = caption_contract.get("wrapping") or {}
            safe_bottom = int(safe_pixels.get("bottom", round(height * 0.12)))
            safe_left = int(safe_pixels.get("left", round(width * 0.05)))
            safe_right = int(safe_pixels.get("right", round(width * 0.05)))
            font_size = int(wrapping.get("font_size", 42))
            caption_style = (
                f"bottom: {safe_bottom}px; left: {safe_left}px; right: {safe_right}px; "
                f"font-size: {font_size}px; max-width: calc(100% - {safe_left + safe_right}px);"
            )

        audio_html: list[str] = []
        audio_tweens: list[str] = []
        for j, nar in enumerate(audio_refs.get("narration") or []):
            src = self._rel_from_workspace(nar["src"])
            start = nar.get("start_seconds", 0)
            end = nar.get("end_seconds")
            duration = (end - start) if end and end > start else (total_duration - start)
            audio_html.append(
                f'<audio id="nar-{j}" '
                f'data-start="{self._f(start)}" data-duration="{self._f(duration)}" '
                f'data-media-start="{self._f(nar.get("offset_seconds", nar.get("source_start_seconds", 0)))}" '
                f'data-track-index="10" data-role="speech" src="{self._escape_attr(src)}" '
                f'data-volume="{self._f(nar.get("volume", 1))}"></audio>'
            )

        music = audio_refs.get("music")
        if music:
            src = self._rel_from_workspace(music["src"])
            music_volume = float(music.get("volume", 0.15) or 0.15)
            music_start = float(music.get("start_seconds", 0) or 0)
            fade_in = float(music.get("fade_in_seconds", 0) or 0)
            fade_out = float(music.get("fade_out_seconds", 0) or 0)
            ducking = music.get("ducking") if isinstance(music.get("ducking"), dict) else {}
            has_volume_timeline = bool(fade_in > 0 or fade_out > 0 or ducking.get("enabled"))
            # HyperFrames treats `data-volume` as the element's base gain and
            # GSAP volume tweens as absolute values.  Keep a plain clip's
            # authored gain on the element; for a tweened clip use a neutral
            # base and express every gain transition explicitly below.
            declared_volume = 1.0 if has_volume_timeline else music_volume
            audio_html.append(
                f'<audio id="music" '
                f'data-start="{self._f(music_start)}" data-duration="{self._f(total_duration - music_start)}" '
                f'data-media-start="{self._f(music.get("offset_seconds", music.get("source_start_seconds", 0)))}" '
                f'data-track-index="20" data-role="music" src="{self._escape_attr(src)}" '
                f'data-volume="{self._f(declared_volume)}" '
                f'data-loop="{"true" if music.get("loop") else "false"}"></audio>'
            )
            if has_volume_timeline:
                audio_tweens.append(
                    f'tl.set("#music", {{ volume: {self._f(music_volume)} }}, {self._f(music_start)});'
                )
            if fade_in > 0:
                audio_tweens.append(
                    f'tl.set("#music", {{ volume: 0 }}, {self._f(music_start)}); '
                    f'tl.to("#music", {{ volume: {self._f(music_volume)}, duration: {self._f(fade_in)} }}, {self._f(music_start)});'
                )
            if fade_out > 0:
                fade_start = max(music_start, total_duration - fade_out)
                audio_tweens.append(
                    f'tl.to("#music", {{ volume: 0, duration: {self._f(fade_out)} }}, {self._f(fade_start)});'
                )
            if ducking.get("enabled"):
                reduction_db = float(ducking.get("reduction_db", -12) or -12)
                duck_volume = music_volume * (10 ** (reduction_db / 20.0))
                for nar in audio_refs.get("narration") or []:
                    n_start = float(nar.get("start_seconds", 0) or 0)
                    n_end = nar.get("end_seconds")
                    n_end = total_duration if n_end is None else float(n_end)
                    attack = max(0.0, float(ducking.get("attack_ms", 200) or 200) / 1000.0)
                    release = max(0.0, float(ducking.get("release_ms", 500) or 500) / 1000.0)
                    audio_tweens.append(
                        f'tl.to("#music", {{ volume: {self._f(duck_volume)}, duration: {self._f(attack)} }}, {self._f(n_start)});'
                    )
                    restore_at = max(n_start, n_end - release)
                    audio_tweens.append(
                        f'tl.to("#music", {{ volume: {self._f(music_volume)}, duration: {self._f(release)} }}, {self._f(restore_at)});'
                    )

        for j, sfx in enumerate(audio_refs.get("sfx") or []):
            src = self._rel_from_workspace(sfx["src"])
            start = float(sfx.get("start_seconds", 0) or 0)
            end = sfx.get("end_seconds")
            duration = float(sfx.get("duration_seconds") or (float(end) - start if end else total_duration - start))
            audio_html.append(
                f'<audio id="sfx-{j}" data-start="{self._f(start)}" data-duration="{self._f(duration)}" '
                f'data-media-start="{self._f(sfx.get("offset_seconds", 0))}" data-track-index="30" '
                f'data-role="sfx" src="{self._escape_attr(src)}" data-volume="{self._f(sfx.get("volume", 1))}"></audio>'
            )

        tween_lines = entrance_tweens + audio_tweens + caption_tweens
        tween_block = "\n      ".join(tween_lines) if tween_lines else "// no tweens"
        runtime_script = "vendor/gsap.min.js"
        mapping_attr = " data-mapping-version=\"1.0\"" if mapping is not None else ""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{self._escape_text(title)}</title>
  <style>
    :root {{
      {vars_css}
    }}
    body {{ margin: 0; background: var(--color-bg); color: var(--color-fg); font-family: var(--font-body); }}
    [data-composition-id="root"] {{
      position: relative;
      width: {width}px;
      height: {height}px;
      overflow: hidden;
    }}
    .clip {{ position: absolute; inset: 0; }}
    .clip.video-clip, .clip.image-clip {{ object-fit: cover; width: 100%; height: 100%; }}
    .clip.text-card {{ display: flex; align-items: center; justify-content: center; padding: 120px 160px; box-sizing: border-box; text-align: center; }}
    .clip.text-card h1 {{ font-family: var(--font-heading); font-weight: 700; font-size: 96px; line-height: 1.1; margin: 0; color: var(--color-fg); }}
    .clip.text-card .subtitle {{ font-size: 36px; margin-top: 24px; color: var(--color-accent); }}
    .om-caption {{
      position: absolute;
      z-index: 100;
      opacity: 0;
      visibility: visible;
      box-sizing: border-box;
      {caption_style}
      padding: 14px 28px;
      border-radius: 12px;
      color: #fff;
      background: rgba(0, 0, 0, 0.72);
      font-family: var(--font-body);
      font-weight: 700;
      line-height: 1.35;
      text-align: center;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      pointer-events: none;
    }}
  </style>
  <script src="{runtime_script}"></script>
</head>
<body>
  <div data-composition-id="root" data-start="0" data-duration="{self._f(total_duration)}" data-width="{width}" data-height="{height}" data-fps="{int(fps)}"{mapping_attr}>
    {"".join(clip_html)}
    {"".join(caption_html)}
    {"".join(audio_html)}
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      {tween_block}
      window.__timelines["root"] = tl;
    </script>
  </div>
</body>
</html>
"""

    def _cut_to_html(
        self, index: int, cut: dict, width: int, height: int, *, strict: bool = False
    ) -> tuple[str, Optional[str]]:
        """Render one cut + its entrance tween. Returns (html, tween or None)."""
        cut_id = f"cut-{index}"
        in_s = float(cut.get("in_seconds", 0) or 0)
        out_s = float(cut.get("out_seconds", 0) or 0)
        duration = max(0.1, out_s - in_s)

        source = cut.get("source") or ""
        cut_type = (cut.get("type") or "").lower()
        text = cut.get("text") or cut.get("title") or ""
        layer = str(cut.get("layer") or "primary").lower()
        track_index = cut.get("track_index", cut.get("track"))
        if track_index is None:
            track_index = {"background": 0, "primary": 1, "overlay": 2}.get(layer, 1)
        media_start = cut.get("media_start_seconds", cut.get("source_in_seconds", 0)) or 0
        animation = cut.get("animation")
        if animation is None and isinstance(cut.get("transform"), dict):
            animation = cut["transform"].get("animation")
        animation = str(animation or "static").strip().lower()
        motion_keyframes = int(cut.get("keyframes", 3 if animation not in {"static", "none", "hold", "still"} else 0) or 0)
        transition_in = str(cut.get("transition_in") or "cut").lower()
        transition_out = str(cut.get("transition_out") or "cut").lower()
        transition_duration = float(cut.get("transition_duration", 0) or 0)
        common = (
            f'data-start="{self._f(in_s)}" data-duration="{self._f(duration)}" '
            f'data-track-index="{int(track_index)}" '
            f'data-media-start="{self._f(media_start)}" data-speed="{self._f(float(cut.get("speed", 1) or 1))}" '
            f'data-motion-keyframes="{motion_keyframes}"'
        )

        src_path = Path(source) if source else None
        ext = src_path.suffix.lower() if src_path else ""

        # Decide scene shape
        if cut_type in {"text_card", "hero_title", "callout"} or (not source and text):
            inner = f'<h1>{self._escape_text(text or f"Scene {index + 1}")}</h1>'
            subtitle = cut.get("subtitle") or cut.get("caption")
            if subtitle:
                inner += f'<div class="subtitle">{self._escape_text(subtitle)}</div>'
            html = (
                f'<div id="{cut_id}" class="clip text-card" '
                f'{common}>{inner}</div>'
            )
            # Mild entrance — fade + lift.
            entrance = min(0.6, max(0.05, duration / 3))
            tween = f'tl.from("#{cut_id} h1", {{ y: 40, opacity: 0, duration: {self._f(entrance)}, ease: "power3.out" }}, {self._f(in_s + min(0.1, duration / 4))});'
            if transition_out in {"fade", "dissolve", "crossfade"} and transition_duration > 0:
                tween += f' tl.to("#{cut_id}", {{ opacity: 0, duration: {self._f(transition_duration)} }}, {self._f(out_s - transition_duration)});'
            return html, tween

        if ext in _IMAGE_EXTENSIONS and src_path:
            rel = self._rel_from_workspace(str(src_path))
            html = (
                f'<img id="{cut_id}" class="clip image-clip" '
                f'src="{self._escape_attr(rel)}" '
                f'{common} alt="">'
            )
            tween = None
            if animation not in {"static", "none", "hold", "still"}:
                tween = (
                    f'tl.from("#{cut_id}", {{ scale: 1.05, opacity: 0, duration: {self._f(min(0.5, duration / 3))}, ease: "power2.out" }}, {self._f(in_s)}); '
                    f'tl.to("#{cut_id}", {{ scale: 1.02, duration: {self._f(max(0.1, duration / 2))}, ease: "none" }}, {self._f(in_s + duration / 2)});'
                )
            elif transition_in in {"fade", "dissolve", "crossfade"} and transition_duration > 0:
                tween = f'tl.from("#{cut_id}", {{ opacity: 0, duration: {self._f(transition_duration)} }}, {self._f(in_s)});'
            if transition_out in {"fade", "dissolve", "crossfade"} and transition_duration > 0:
                tween = (tween + " " if tween else "") + f'tl.to("#{cut_id}", {{ opacity: 0, duration: {self._f(transition_duration)} }}, {self._f(out_s - transition_duration)});'
            return html, tween

        if ext in _VIDEO_EXTENSIONS and src_path:
            rel = self._rel_from_workspace(str(src_path))
            html = (
                f'<video id="{cut_id}" class="clip video-clip" '
                f'src="{self._escape_attr(rel)}" '
                f'{common} muted playsinline preload="auto"></video>'
            )
            tween = None
            if transition_in in {"fade", "dissolve", "crossfade"} and transition_duration > 0:
                tween = f'tl.from("#{cut_id}", {{ opacity: 0, duration: {self._f(transition_duration)} }}, {self._f(in_s)});'
            if transition_out in {"fade", "dissolve", "crossfade"} and transition_duration > 0:
                tween = (tween + " " if tween else "") + f'tl.to("#{cut_id}", {{ opacity: 0, duration: {self._f(transition_duration)} }}, {self._f(out_s - transition_duration)});'
            return html, tween

        # Unknown cut shape — render a placeholder text card so the render
        # still succeeds; lint/validate will surface the issue.
        if cut_type in {"composition", "html"} or ext in {".html", ".htm"}:
            if not src_path:
                if strict:
                    raise ValueError(f"cut {cut_id} composition requires an HTML source")
                return "", None
            rel = self._rel_from_workspace(str(src_path))
            composition_id = Path(rel).stem
            html = (
                f'<div id="{cut_id}" class="clip composition-clip" '
                f'data-composition-id="{self._escape_attr(composition_id)}" '
                f'data-composition-src="{self._escape_attr(rel)}" '
                f'{common} '
                f'data-width="{width}" data-height="{height}" '
                f'></div>'
            )
            return html, None

        if cut_type in {"diagram", "teacher_slide", "code_snippet", "subtitle"}:
            slide = cut.get("teacher_slide") if isinstance(cut.get("teacher_slide"), dict) else {}
            heading = slide.get("title") or text or f"Scene {index + 1}"
            bullets = slide.get("bullets") if isinstance(slide.get("bullets"), list) else []
            bullet_html = "".join(f"<li>{self._escape_text(str(item))}</li>" for item in bullets[:3])
            inner = f'<h1>{self._escape_text(str(heading))}</h1>'
            if bullet_html:
                inner += f'<ul class="diagram-bullets">{bullet_html}</ul>'
            html = f'<div id="{cut_id}" class="clip text-card diagram-card" {common}>{inner}</div>'
            tween = f'tl.from("#{cut_id}", {{ opacity: 0, y: 20, duration: {self._f(min(0.5, duration / 3))} }}, {self._f(in_s)});'
            return html, tween

        if strict:
            raise ValueError(
                f"cut {cut_id} type {cut_type or 'unknown'!r} has no HyperFrames renderer mapping"
            )
        placeholder = self._escape_text(text or cut.get("reason") or f"Scene {index + 1}")
        html = (
            f'<div id="{cut_id}" class="clip text-card" '
            f'{common}><h1>{placeholder}</h1></div>'
        )
        return html, None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _run_hf(
        self,
        args: list[str],
        *,
        cwd: Optional[Path],
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess:
        """Invoke `npx hyperframes <args>` with the right Windows quirks.

        We intentionally bypass `self.run_command` here because we do NOT
        want to raise CalledProcessError on non-zero exits — the caller
        parses lint/validate/render exit codes itself.
        """
        local = None
        if getattr(self, "_offline_mode", False):
            local = shutil.which("hyperframes")
            if not local and cwd is not None:
                for candidate in (
                    cwd / "node_modules" / ".bin" / "hyperframes",
                    cwd / "node_modules" / ".bin" / "hyperframes.cmd",
                ):
                    if candidate.is_file():
                        local = str(candidate)
                        break
        if getattr(self, "_offline_mode", False):
            if local:
                cmd = [local, *args]
            else:
                # npm's content-addressed cache may contain the complete
                # package even when `npx --offline hyperframes` cannot resolve
                # registry metadata. Invoke the cached bin directly so an
                # offline production render is genuinely offline and
                # repeatable.
                cached = self._offline_package_check()
                source = Path(str(cached.get("source", ""))) if cached.get("source") else None
                entry = source / "bin" / "hyperframes.mjs" if source else None
                node = shutil.which("node")
                if entry is not None and entry.is_file() and node:
                    cmd = [node, str(entry), *args]
                else:
                    cmd = ["npx", "--offline", "--yes", "hyperframes", *args]
        else:
            cmd = ["npx", "--yes", "hyperframes", *args]
        # On Windows, resolve the .cmd wrapper so subprocess can find it
        # without shell=True.
        if os.name == "nt":
            resolved = shutil.which(cmd[0])
            if resolved:
                cmd[0] = resolved
        return self._run_bounded_process(cmd, cwd=cwd, timeout=timeout)

    @staticmethod
    def _parse_json_output(stdout: str) -> Optional[Any]:
        """Parse a `--json` report, tolerating surrounding banner lines."""
        if not stdout:
            return None
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(stdout[start : end + 1])
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _f(v: float) -> str:
        return f"{float(v):.3f}".rstrip("0").rstrip(".")

    @staticmethod
    def _escape_text(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @staticmethod
    def _escape_attr(s: str) -> str:
        return HyperFramesCompose._escape_text(s).replace('"', "&quot;")

    @staticmethod
    def _rel_from_workspace(path: str) -> str:
        """HyperFrames resolves src= relative to index.html. Our asset files
        live under workspace/assets/, so when we stage a copy we know the
        relative path is `assets/<name>`. For files already in the workspace
        tree, fall back to the file name.
        """
        p = Path(path)
        # If it's already a relative path starting with assets/, keep as-is.
        if not p.is_absolute():
            return str(p).replace("\\", "/")
        parts = p.parts
        for anchor in ("assets", "compositions"):
            if anchor in parts:
                index = len(parts) - 1 - list(reversed(parts)).index(anchor)
                return "/".join(parts[index:])
        # Otherwise emit just the basename under assets/.
        return f"assets/{p.name}"
