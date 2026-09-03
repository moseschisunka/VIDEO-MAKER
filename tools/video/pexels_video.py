"""Stock video acquisition from Pexels API (free)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class PexelsVideo(BaseTool):
    name = "pexels_video"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "video_generation"
    provider = "pexels"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set PEXELS_API_KEY to your Pexels API key.\n"
        "  Get one free at https://www.pexels.com/api/"
    )
    agent_skills = []

    capabilities = ["search_video", "download_video", "stock_video", "multi_result_search"]
    supports = {
        "orientation_filter": True,
        "size_filter": True,
        "free_commercial_use": True,
    }
    best_for = [
        "real-world B-roll footage (cities, nature, people, offices)",
        "establishing shots and transitions",
        "free stock video — no cost, no attribution required",
    ]
    not_good_for = [
        "custom/specific scenes",
        "animated or stylized content",
        "offline use",
    ]
    fallback_tools = ["pixabay_video"]

    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search term"},
            "orientation": {
                "type": "string",
                "enum": ["landscape", "portrait", "square"],
            },
            "size": {
                "type": "string",
                "enum": ["large", "medium", "small"],
                "description": "large=4K, medium=Full HD, small=HD",
            },
            "min_duration": {
                "type": "integer",
                "description": "Minimum duration in seconds",
            },
            "max_duration": {
                "type": "integer",
                "description": "Maximum duration in seconds",
            },
            "per_page": {"type": "integer", "default": 5, "minimum": 1, "maximum": 80},
            "page": {"type": "integer", "default": 1},
            "select_index": {
                "type": "integer", "minimum": 0, "default": 0,
                "description": "Zero-based result to download after reviewing returned options.",
            },
            "download": {
                "type": "boolean", "default": True,
                "description": "When false, return search options without downloading a file.",
            },
            "preferred_quality": {
                "type": "string",
                "enum": ["hd", "sd"],
                "default": "hd",
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["query", "orientation", "size", "page", "select_index", "download"]
    side_effects = ["optionally writes the selected video to output_path", "calls Pexels API"]
    user_visible_verification = ["Watch downloaded clip to verify it matches the intended scene"]

    def get_status(self) -> ToolStatus:
        if os.environ.get("PEXELS_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("PEXELS_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="PEXELS_API_KEY not set. " + self.install_instructions,
            )

        import requests

        start = time.time()
        query = inputs["query"]

        params: dict[str, Any] = {
            "query": query,
            "per_page": inputs.get("per_page", 5),
            "page": inputs.get("page", 1),
        }
        if inputs.get("orientation"):
            params["orientation"] = inputs["orientation"]
        if inputs.get("size"):
            params["size"] = inputs["size"]

        try:
            search_response = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": api_key},
                params=params,
                timeout=30,
            )
            search_response.raise_for_status()
            data = search_response.json()

            videos = data.get("videos", [])

            # Filter by duration if specified
            min_dur = inputs.get("min_duration")
            max_dur = inputs.get("max_duration")
            if min_dur or max_dur:
                filtered = []
                for v in videos:
                    dur = v.get("duration", 0)
                    if min_dur and dur < min_dur:
                        continue
                    if max_dur and dur > max_dur:
                        continue
                    filtered.append(v)
                videos = filtered

            if not videos:
                return ToolResult(
                    success=False,
                    error=f"No videos found for query: {query}",
                    data={"total_results": data.get("total_results", 0)},
                )

            preferred_quality = inputs.get("preferred_quality", "hd")
            options = []
            for index, candidate in enumerate(videos):
                video_files = candidate.get("video_files", [])
                selected = next(
                    (vf for vf in sorted(video_files, key=lambda x: x.get("width", 0), reverse=True)
                     if vf.get("quality") == preferred_quality and vf.get("link")),
                    None,
                )
                if not selected:
                    selected = next(
                        (vf for vf in sorted(video_files, key=lambda x: x.get("width", 0), reverse=True) if vf.get("link")),
                        None,
                    )
                options.append({
                    "index": index,
                    "video_id": candidate.get("id"),
                    "user": (candidate.get("user") or {}).get("name", "Unknown"),
                    "duration_seconds": candidate.get("duration"),
                    "width": selected.get("width") if selected else candidate.get("width"),
                    "height": selected.get("height") if selected else candidate.get("height"),
                    "quality": selected.get("quality") if selected else None,
                    "fps": selected.get("fps") if selected else None,
                    "preview_url": candidate.get("image", ""),
                    "download_url": selected.get("link") if selected else None,
                    "pexels_url": candidate.get("url", ""),
                })
            select_index = int(inputs.get("select_index", 0))
            if select_index < 0 or select_index >= len(videos):
                return ToolResult(
                    success=False,
                    error=f"select_index must be between 0 and {len(videos) - 1}",
                    data={"options": options, "total_results": data.get("total_results", 0)},
                )

            video = videos[select_index]
            selected_file = next(
                (vf for vf in sorted(video.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True)
                 if vf.get("quality") == preferred_quality and vf.get("link")),
                None,
            )
            if not selected_file:
                selected_file = next(
                    (vf for vf in sorted(video.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True) if vf.get("link")),
                    None,
                )
            if not selected_file:
                return ToolResult(success=False, error="Selected Pexels video has no downloadable video file.", data={"options": options})

            output_path = None
            if inputs.get("download", True):
                video_response = requests.get(selected_file["link"], timeout=120)
                video_response.raise_for_status()

                output_path = Path(inputs.get("output_path", f"pexels_video_{video['id']}.mp4"))
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(video_response.content)

        except Exception as e:
            return ToolResult(success=False, error=f"Pexels video search failed: {e}")

        return ToolResult(
            success=True,
            data={
                "provider": "pexels",
                "video_id": video["id"],
                "user": video.get("user", {}).get("name", "Unknown"),
                "duration_seconds": video.get("duration"),
                "width": selected_file.get("width"),
                "height": selected_file.get("height"),
                "fps": selected_file.get("fps"),
                "quality": selected_file.get("quality"),
                "query": query,
                "output": str(output_path) if output_path else None,
                "total_results": data.get("total_results", 0),
                "results_returned": len(videos),
                "selected_index": select_index,
                "options": options,
                "license": "Pexels License (free, no attribution required)",
                "pexels_url": video.get("url", ""),
            },
            artifacts=[str(output_path)] if output_path else [],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
        )
