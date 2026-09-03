"""PR-1032 cost-estimate motion-policy precision regressions."""

from __future__ import annotations

import pytest

from tools.cost_tracker import CostTracker


def _brief(motion_required):
    return {
        "source": {"type": "shorts", "duration_seconds": 60},
        "structure_analysis": {
            "total_scenes": 12,
            "pacing_profile": {"pacing_style": "steady_educational"},
            "scenes": [{"visual_type": "other"} for _ in range(12)],
        },
        "narration_transcript": {"word_count": 180},
        "replication_guidance": {"motion_required": motion_required},
    }


def _plan():
    return {
        "video_generation": {"tool": "video", "cost_per_unit": 0.3, "clip_duration_seconds": 5},
        "image_generation": {"tool": "image", "cost_per_unit": 0.05},
        "tts": {"tool": "tts", "cost_per_word": 0.00003},
    }


@pytest.mark.parametrize("value", ["false", "true", 0, 1, [], {}])
def test_cost_estimate_rejects_malformed_motion_policy(value):
    with pytest.raises(ValueError, match="motion_required must be boolean"):
        CostTracker(mode="observe").estimate_from_reference(_brief(value), 60, _plan())


def test_cost_estimate_defaults_missing_motion_policy_to_false():
    brief = _brief(False)
    brief["source"]["type"] = "lesson"
    brief["replication_guidance"].pop("motion_required")
    result = CostTracker(mode="observe").estimate_from_reference(brief, 60, _plan())
    assert result["motion_ratio"] < 0.6
