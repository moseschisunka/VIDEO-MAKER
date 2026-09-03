"""PR-404 through PR-407 canonical duration and profile contracts."""

from __future__ import annotations

import pytest

from lib.format_contracts import propagate_profile, validate_profile_propagation
from lib.media_profiles import duration_tolerance_seconds
from lib.video_timeline import (
    solve_duration_plan,
    validate_duration_tolerance,
    validate_script_duration,
)


@pytest.mark.parametrize(
    ("target", "expected"),
    [(15, 1.0), (30, 1.5), (60, 3.0), (300, 9.0)],
)
def test_canonical_duration_tolerance_scales_by_product_length(target: float, expected: float):
    assert duration_tolerance_seconds(target) == expected


def test_target_led_solver_allocates_breathing_room_without_speed_abuse():
    plan = solve_duration_plan(
        15,
        word_count=20,
        voice_rate_wpm=120,
        scene_count=3,
        transition_duration_seconds=0.25,
        intro_seconds=0.25,
        outro_seconds=0.25,
        silence_seconds=0.25,
        profile="youtube_shorts",
    )

    assert plan["valid"] is True
    assert plan["planned_duration_seconds"] == 15
    assert plan["narration_seconds"] == 10
    assert plan["visual_hold_seconds"] > 0
    assert len(plan["scene_durations"]) == 3
    assert sum(plan["scene_durations"]) == pytest.approx(15, abs=0.001)


def test_overlong_short_script_is_rejected_instead_of_visual_padding_or_speedup():
    plan = solve_duration_plan(
        15,
        word_count=100,
        voice_rate_wpm=150,
        scene_count=3,
        profile="youtube_shorts",
    )
    assert plan["valid"] is False
    assert any("shorten the script" in error for error in plan["errors"])


def test_content_led_plan_is_explicit_when_narration_requires_more_time():
    plan = solve_duration_plan(
        30,
        word_count=100,
        voice_rate_wpm=100,
        scene_count=4,
        duration_policy="content_led",
        profile="youtube_landscape",
    )
    assert plan["valid"] is True
    assert plan["planned_duration_seconds"] == pytest.approx(60, abs=0.001)
    assert any("content-led plan grows" in warning for warning in plan["warnings"])


def test_measured_output_uses_short_and_long_form_tolerances():
    assert validate_duration_tolerance(16, 15)["valid"]
    assert not validate_duration_tolerance(17, 15)["valid"]
    assert validate_duration_tolerance(308, 300)["valid"]
    assert not validate_duration_tolerance(310, 300)["valid"]


def test_script_duration_uses_words_and_declared_duration():
    script = {
        "total_duration_seconds": 30,
        "sections": [
            {"id": "s1", "text": "One two three four five six seven eight nine ten", "start_seconds": 0, "end_seconds": 30}
        ],
    }
    report = validate_script_duration(script, voice_rate_wpm=120)
    assert report["valid"] is True
    assert report["word_count"] == 10
    assert report["plan"]["narration_seconds"] == 5


def test_profile_propagation_survives_brief_to_render_and_rejects_drift():
    source = {
        "brief": {"version": "1.0", "title": "x", "target_duration_seconds": 30},
        "proposal_packet": {"version": "1.0", "production_plan": {}},
        "script": {"version": "1.0", "total_duration_seconds": 30},
        "edit_decisions": {"version": "1.0", "cuts": [], "render_runtime": "remotion"},
        "render_report": {"version": "1.0", "outputs": [{"path": "x.mp4", "format": "mp4", "resolution": "1920x1080", "duration_seconds": 30}]},
    }
    propagated = propagate_profile(source, "youtube_landscape", target_duration_seconds=30)
    report = validate_profile_propagation(
        propagated,
        expected_profile="youtube_landscape",
        expected_duration_seconds=30,
    )
    assert report["valid"] is True
    assert propagated["proposal_packet"]["production_plan"]["aspect_ratio"] == "16:9"
    assert propagated["render_report"]["outputs"][0]["profile"] == "youtube_landscape"

    propagated["edit_decisions"]["profile"] = "tiktok"
    drift = validate_profile_propagation(propagated, expected_profile="youtube_landscape")
    assert drift["valid"] is False
    assert any("profile" in error.lower() for error in drift["errors"])
