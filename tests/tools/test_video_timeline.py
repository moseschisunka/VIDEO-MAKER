"""Tests for editorial beat cadence and narration alignment."""

from __future__ import annotations

from lib.video_timeline import (
    minimum_visual_beats,
    validate_narration_timeline,
    validate_visual_timeline,
)


def _cuts(duration: int, beat: int = 2) -> list[dict]:
    return [
        {
            "id": f"cut_{index + 1}",
            "in_seconds": index * beat,
            "out_seconds": min(duration, (index + 1) * beat),
            "layer": "primary",
            "transition_in": "fade" if index else "cut",
            "transition_out": "fade",
            "transition_duration": 0.3,
        }
        for index in range(duration // beat)
    ]


def test_minimum_visual_beats_scales_with_duration():
    assert minimum_visual_beats(30) == 15
    assert minimum_visual_beats(60) == 30
    assert minimum_visual_beats(600) == 300


def test_30_second_timeline_requires_and_accepts_15_beats():
    report = validate_visual_timeline(_cuts(30), duration_seconds=30)
    assert report["valid"]
    assert report["cut_count"] == 15
    assert report["minimum_beats"] == 15


def test_sparse_timeline_is_rejected():
    report = validate_visual_timeline(
        [{"id": "cut_1", "in_seconds": 0, "out_seconds": 30}],
        duration_seconds=30,
    )
    assert not report["valid"]
    assert any("at least 15" in error for error in report["errors"])


def test_gaps_and_overlaps_are_rejected():
    gap_report = validate_visual_timeline(
        [
            {"id": "a", "in_seconds": 0, "out_seconds": 2},
            {"id": "b", "in_seconds": 2.2, "out_seconds": 4},
        ],
        duration_seconds=4,
    )
    assert not gap_report["valid"]
    assert any("Gap" in error for error in gap_report["errors"])

    overlap_report = validate_visual_timeline(
        [
            {"id": "a", "in_seconds": 0, "out_seconds": 2.2},
            {"id": "b", "in_seconds": 2, "out_seconds": 4},
        ],
        duration_seconds=4,
    )
    assert not overlap_report["valid"]
    assert any("Overlap" in error for error in overlap_report["errors"])


def test_transition_cannot_consume_an_entire_beat():
    report = validate_visual_timeline(
        [{"id": "a", "in_seconds": 0, "out_seconds": 2, "transition_duration": 1.2}],
        duration_seconds=2,
    )
    assert not report["valid"]
    assert any("safely use at most" in error for error in report["errors"])


def test_narration_segments_must_cover_without_overlap():
    valid = validate_narration_timeline(
        {
            "segments": [
                {"asset_id": "voice_a", "start_seconds": 0, "end_seconds": 4},
                {"asset_id": "voice_b", "start_seconds": 4, "end_seconds": 8},
            ]
        },
        duration_seconds=8,
    )
    assert valid["valid"]
    assert valid["coverage_verified"]

    invalid = validate_narration_timeline(
        {
            "segments": [
                {"asset_id": "voice_a", "start_seconds": 0, "end_seconds": 5},
                {"asset_id": "voice_b", "start_seconds": 4, "end_seconds": 8},
            ]
        },
        duration_seconds=8,
    )
    assert not invalid["valid"]
    assert any("overlap" in error.lower() for error in invalid["errors"])
