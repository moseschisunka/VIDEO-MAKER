"""Tests for the iLearnZed slide-and-voice production runner."""

from lib.project_pipeline import (
    _build_edit_decisions,
    _estimate_beat_durations,
    _build_lesson_beats,
    _build_scene_plan,
    _build_script,
    _build_teaching_plan,
    _build_tts_decision_log,
    _materialize_teaching_events,
    MIN_SLIDE_SECONDS,
    _write_lesson_slides,
)
from schemas.artifacts import validate_artifact


def test_epidemiology_runner_builds_distinct_teaching_beats():
    script = _build_script("WHAT IS EPIDEMIOLOGY", "BASED ON FACTS", 30)
    beats = _build_lesson_beats(script["title"], "BASED ON FACTS", 15)
    scene_plan = _build_scene_plan(script, "premium-minimalist", "demo", beats)
    edits = _build_edit_decisions(
        scene_plan,
        30,
        "premium-minimalist",
        [f"asset_{i}" for i in range(15)],
        beats,
    )

    assert len(beats) == 15
    assert len({beat["title"] for beat in beats}) == 15
    assert len({beat["diagram"] for beat in beats}) >= 10
    assert beats[0]["narration"].startswith("Let")
    assert beats[0]["objective"]
    assert beats[0]["complexity"] == 0.35
    assert beats[0]["importance"] == 0.95
    assert beats[0]["key_takeaway"] == "Epidemiology studies patterns of health in groups of people."
    assert [event["type"] for event in beats[0]["event_templates"]] == [
        "orientation", "read", "explanation", "break_down_visual",
        "connect", "emphasis", "transition",
    ]
    assert beats[-1]["title"] == "From facts to action"
    assert len(scene_plan["scenes"]) == 15
    assert len(edits["cuts"]) == 15
    assert all(scene["information_role"] for scene in scene_plan["scenes"])
    assert all(cut["type"] == "teacher_slide" for cut in edits["cuts"])
    assert all(cut["teacher_slide"]["diagram"] for cut in edits["cuts"])
    assert all(cut["backgroundColor"] == "#061F18" for cut in edits["cuts"])
    assert {cut["transition_in"] for cut in edits["cuts"]} <= {"cut", "dissolve", "slide-left"}

    validate_artifact("script", script)
    validate_artifact("scene_plan", scene_plan)
    validate_artifact("edit_decisions", edits)
    assert scene_plan["metadata"]["teaching_skill"] == "video-reader-ai-teacher"
    assert edits["metadata"]["teaching_plan_artifact"] == "artifacts/teaching_plan.json"


def test_teacher_framework_materializes_frame_addressable_cues():
    beats = _build_lesson_beats("WHAT IS EPIDEMIOLOGY", "BASED ON FACTS", 15)
    timeline = []
    cursor = 0.0
    for index, beat in enumerate(beats):
        end = cursor + max(4.0, len(beat["narration"].split()) / 1.8)
        timeline.append({
            "id": f"beat_narration_{index + 1}",
            "start_seconds": round(cursor, 3),
            "end_seconds": round(end, 3),
            "text": beat["narration"],
        })
        cursor = end

    _materialize_teaching_events(beats, timeline)
    plan = _build_teaching_plan("WHAT IS EPIDEMIOLOGY", beats, timeline)

    assert plan["skill"] == "video-reader-ai-teacher"
    assert plan["teacher_loop"][0] == "orientation"
    assert len(plan["slides"]) == 15
    for index, slide in enumerate(plan["slides"]):
        events = slide["events"]
        assert events[0]["type"] == "orientation"
        assert events[-1]["type"] == "transition"
        assert events[0]["start_frame"] == round(timeline[index]["start_seconds"] * 30)
        assert events[-1]["end_frame"] == round(timeline[index]["end_seconds"] * 30)
        assert all(event["end_frame"] > event["start_frame"] for event in events)
        assert all(event["visualTarget"] for event in events)


def test_lesson_slides_keep_brand_frame_but_change_content(tmp_path):
    beats = _build_lesson_beats("WHAT IS EPIDEMIOLOGY", "BASED ON FACTS", 3)
    paths = _write_lesson_slides(tmp_path, "WHAT IS EPIDEMIOLOGY", beats)

    assert len(paths) == 3
    contents = [path.read_text(encoding="utf-8") for path in paths]
    assert all("iLearnZed · VISUAL LESSON" in content for content in contents)
    assert "What is epidemiology?" in contents[0]
    assert "The first questions" in contents[1]
    assert "Person · place · time" in contents[2]
    assert len(set(contents)) == 3


def test_content_led_timing_holds_each_slide_for_calm_explanation():
    beats = _build_lesson_beats("WHAT IS EPIDEMIOLOGY", "BASED ON FACTS", 15)
    planned = _estimate_beat_durations(beats)
    timeline = []
    cursor = 0.0
    for index, seconds in enumerate(planned):
        timeline.append({
            "id": f"beat_narration_{index + 1}",
            "start_seconds": round(cursor, 3),
            "end_seconds": round(cursor + seconds, 3),
            "text": beats[index]["narration"],
        })
        cursor += seconds

    script = _build_script(beats[0]["title"], "WHAT IS EPIDEMIOLOGY", cursor)
    scene_plan = _build_scene_plan(
        script,
        "premium-minimalist",
        "demo",
        beats,
        timeline,
    )
    edits = _build_edit_decisions(
        scene_plan,
        cursor,
        "premium-minimalist",
        [f"asset_{i}" for i in range(15)],
        beats,
        timeline,
        30,
    )

    assert cursor > 30
    assert all(seconds >= MIN_SLIDE_SECONDS for seconds in planned)
    assert all(
        scene["end_seconds"] > scene["start_seconds"]
        for scene in scene_plan["scenes"]
    )
    assert scene_plan["scenes"][-1]["end_seconds"] == round(cursor, 3)
    assert edits["metadata"]["timing_mode"] == "content_led"
    assert len(edits["audio"]["narration"]["segments"]) == 15
    assert edits["audio"]["narration"]["segments"][1]["offset_seconds"] == planned[0]


def test_openai_tts_selection_is_explicit_and_auditable():
    decision_log = _build_tts_decision_log(
        "unit-test-openai-tts",
        "openai",
        "gpt-4o-mini-tts",
        "coral",
    )

    validate_artifact("decision_log", decision_log)
    decision = decision_log["decisions"][-1]
    assert decision["category"] == "voice_selection"
    assert decision["subject"] == "Narration TTS provider"
    assert decision["selected"] == "openai_gpt_4o_mini_tts_coral"
    assert decision["user_approved"] is True
    assert any(option["option_id"] == "edge_en_US_ChristopherNeural" for option in decision["options_considered"])
