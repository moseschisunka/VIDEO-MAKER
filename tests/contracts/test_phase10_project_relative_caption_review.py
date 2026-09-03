"""PR-1015 project-relative caption evidence contract.

Backlot invokes the composer from the repository process, while artifact paths
are intentionally relative to the individual project.  A captioned
source-footage render must therefore be reviewed against the canonical project
root, not the caller's working directory.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.video.video_compose import VideoCompose


PROJECT_ID = "talking-head-caption-root"


@pytest.mark.release_blocker
def test_project_relative_subtitle_source_survives_root_caller_final_review(
    tmp_path: Path,
) -> None:
    project = tmp_path / PROJECT_ID
    source_dir = project / "assets" / "video"
    source_dir.mkdir(parents=True)
    (project / "assets" / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello from the talking head.\n",
        encoding="utf-8",
    )
    (project / "renders").mkdir()
    (project / "project.json").write_text(
        json.dumps(
            {
                "project_id": PROJECT_ID,
                "pipeline_type": "talking-head",
                "title": "Caption root review",
            }
        ),
        encoding="utf-8",
    )

    source = source_dir / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    edit_decisions = {
        "version": "1.0",
        "project_id": PROJECT_ID,
        "pipeline_type": "talking-head",
        "render_runtime": "ffmpeg",
        "renderer_family": "presenter",
        "composition_mode": "templated",
        "target_duration_seconds": 2,
        "cuts": [
            {
                "id": "source-cut",
                "source": "source-video",
                "in_seconds": 0,
                "out_seconds": 2,
                "speed": 1,
                "layer": "primary",
                "reason": "Preserve the supplied talking-head footage.",
            }
        ],
        "subtitles": {
            "enabled": True,
            "source": "assets/subtitles.srt",
            "position": "bottom-center",
        },
        "metadata": {"target_duration_seconds": 2},
    }
    asset_manifest = {
        "version": "1.0",
        "assets": [
            {
                "id": "source-video",
                "type": "video",
                "path": "assets/video/source.mp4",
                "source_tool": "deterministic_source_footage_fixture",
                "scene_id": "scene-1",
                "duration_seconds": 2,
            }
        ],
        "total_cost_usd": 0,
    }

    # Deliberately call from the repository-root process with a project-relative
    # output and subtitle source.  Before PR-1015, burn-in succeeded but the
    # subsequent final review checked assets/subtitles.srt against cwd and
    # returned a false "subtitles expected" revision.
    result = VideoCompose().execute(
        {
            "operation": "render",
            "project_dir": str(project),
            "output_path": "renders/final.mp4",
            "edit_decisions": edit_decisions,
            "asset_manifest": asset_manifest,
        }
    )

    assert result.success, result.error
    review = result.data["final_review"]
    assert review["status"] == "pass", review
    assert review["checks"]["subtitle_check"]["subtitles_expected"] is True
    assert review["checks"]["subtitle_check"]["subtitles_present"] is True
    assert review["checks"]["subtitle_check"]["issues"] == []
    assert (project / "renders" / "final.mp4").is_file()
