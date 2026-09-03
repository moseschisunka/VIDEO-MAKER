"""PR-4G integration gate for grounding, timing, and format contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lib.grounding import validate_grounding
from lib.media_profiles import get_profile, validate_profile_output
from lib.output_promotion import probe_media
from lib.video_timeline import (
    solve_duration_plan,
    validate_duration_tolerance,
    validate_timeline_contract,
)


ROOT = Path(__file__).resolve().parents[2]


def _cuts(duration: float, count: int) -> list[dict]:
    step = duration / count
    return [
        {
            "id": f"cut-{index + 1}",
            "in_seconds": round(index * step, 3),
            "out_seconds": round((index + 1) * step, 3),
            "layer": "primary",
            "transition_in": "cut" if index == 0 else "dissolve",
            "transition_out": "dissolve",
            "transition_duration": min(0.25, step / 4),
        }
        for index in range(count)
    ]


def test_phase4_grounded_format_and_duration_gate():
    cases = json.loads((ROOT / "tests" / "eval" / "grounding_cases.json").read_text(encoding="utf-8"))
    supported = next(case for case in cases if case["expected_decision"] == "pass")
    grounding = validate_grounding(
        supported["research_brief"], supported["script"], strict=True
    )
    assert grounding["valid"] is True
    assert grounding["decision"] == "pass"

    format_cases = (
        (15.0, "youtube_shorts", 3),
        (30.0, "youtube_landscape", 5),
        (60.0, "youtube_landscape", 10),
        (300.0, "ilearnzed_long_form", 15),
    )
    for target, profile, scene_count in format_cases:
        plan = solve_duration_plan(
            target,
            word_count=int(target * 1.5),
            voice_rate_wpm=120,
            scene_count=scene_count,
            transition_duration_seconds=0.2,
            profile=profile,
        )
        assert plan["valid"] is True, plan["errors"]
        assert plan["planned_duration_seconds"] == target
        measured = validate_duration_tolerance(target, target, profile=profile)
        assert measured["valid"] is True
        media = get_profile(profile)
        output = validate_profile_output(
            profile,
            width=media.width,
            height=media.height,
            aspect_ratio=media.aspect_ratio.value,
            fps=media.fps,
            duration_seconds=target,
        )
        assert output["valid"] is True

        timeline = validate_timeline_contract(
            _cuts(target, scene_count),
            {
                "segments": [
                    {"asset_id": "voice", "start_seconds": 0, "end_seconds": target}
                ]
            },
            target_duration_seconds=target,
            measured_duration_seconds=target,
            duration_plan=plan,
            profile=profile,
        )
        assert timeline["valid"] is True, timeline["errors"]


def test_phase4_gate_keeps_production_lock_explicit():
    roadmap = (ROOT / "docs" / "production-readiness" / "PROGRESS_TRACKER.md").read_text(encoding="utf-8")
    assert "Production decision | Not eligible" in roadmap
    playbook = (ROOT / "docs" / "production-readiness" / "EXECUTION_PLAYBOOK.md").read_text(encoding="utf-8")
    assert "PR-11G" in playbook


def test_phase4_short_outputs_are_profile_and_duration_verified(tmp_path: Path):
    """Use fresh local media, not a stale final, for the short-format gate."""

    for duration, profile_name in ((15, "youtube_shorts"), (30, "youtube_landscape"), (60, "youtube_landscape")):
        profile = get_profile(profile_name)
        output = tmp_path / f"render-{duration}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c=0x123B31:s={profile.width}x{profile.height}:r={profile.fps}",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", profile.pixel_format, "-c:a", "aac", "-ar", "48000", "-ac", "2",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        facts = probe_media(output)
        assert facts["valid_container"] is True
        assert validate_profile_output(
            profile_name,
            width=facts["width"],
            height=facts["height"],
            aspect_ratio=profile.aspect_ratio.value,
            fps=facts["fps"],
            duration_seconds=facts["duration_seconds"],
        )["valid"] is True
        assert validate_duration_tolerance(
            facts["duration_seconds"], duration, profile=profile_name
        )["valid"] is True
