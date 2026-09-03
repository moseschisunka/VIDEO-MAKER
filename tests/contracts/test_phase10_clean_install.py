"""PR-1002 disposable-environment smoke checks.

The test intentionally uses only local/fake inputs.  It proves that a clean
Python install can load every manifest, discover the tool registry, answer the
Backlot health endpoint, and produce a small real video through the local
FFmpeg composition path without credentials or provider spend.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from lib.pipeline_loader import load_pipeline
from tools.tool_registry import registry
from tools.video.video_compose import VideoCompose


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.release_blocker
def test_clean_install_manifest_registry_health_and_local_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg, "FFmpeg is required by the supported local render path"
    assert ffprobe, "ffprobe is required to verify the local render output"

    defs_dir = REPO_ROOT / "pipeline_defs"
    manifests = sorted(defs_dir.glob("*.yaml"))
    assert len(manifests) >= 2
    for manifest_path in manifests:
        manifest = load_pipeline(manifest_path.stem, defs_dir=defs_dir)
        assert manifest["name"] == manifest_path.stem

    registry.discover()
    assert "video_compose" in registry._tools
    assert registry._tools["video_compose"].capability == "video_post"
    assert any(tool.capability == "tts" for tool in registry._tools.values())
    assert any(tool.capability == "image_generation" for tool in registry._tools.values())

    async def no_watch() -> None:
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "app": "backlot"}

    source = tmp_path / "source.mp4"
    output = tmp_path / "render.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x180:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = VideoCompose().execute(
        {
            "operation": "compose",
            "edit_decisions": {
                "render_runtime": "ffmpeg",
                "cuts": [{"source": str(source), "in_seconds": 0, "out_seconds": 1.0, "speed": 1.0}],
                "subtitles": {"enabled": False},
            },
            "output_path": str(output),
            "expected_duration_seconds": 1.0,
            "profile": "youtube_landscape",
        }
    )
    assert result.success, result.error
    assert output.is_file() and output.stat().st_size > 0

    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(probe.stdout)
    stream_types = {stream.get("codec_type") for stream in payload.get("streams", [])}
    assert {"video", "audio"} <= stream_types
    assert float(payload["format"]["duration"]) > 0.7
