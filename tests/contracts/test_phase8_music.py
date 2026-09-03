"""Phase 8 proposal-stage music contract tests (PR-800)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from lib.music_contracts import (
    MusicContractError,
    append_music_decision,
    music_provenance_from_output,
    normalize_music_provenance,
    normalize_music_source,
    validate_music_asset_provenance,
)


ROOT = Path(__file__).resolve().parents[2]


def _proposal(music_source: dict) -> dict:
    return {
        "version": "1.0",
        "concept_options": [
            {
                "id": f"c{i}",
                "title": f"Concept {i}",
                "hook": "A precise hook.",
                "narrative_structure": "tutorial",
                "visual_approach": "Clear instructional diagrams.",
                "target_duration_seconds": 60,
                "why_this_works": "It makes the claim visible.",
            }
            for i in range(1, 4)
        ],
        "selected_concept": {"concept_id": "c1", "rationale": "Best fit."},
        "production_plan": {
            "pipeline": "animated-explainer",
            "stages": [{"stage": "proposal", "tools": [], "approach": "Plan."}],
            "render_runtime": "remotion",
            "music_source": music_source,
        },
        "cost_estimate": {
            "total_estimated_usd": 0,
            "line_items": [],
            "budget_verdict": "no_budget_set",
        },
        "approval": {"status": "pending"},
    }


def test_proposal_schema_requires_explicit_music_decision():
    schema = json.loads(
        (ROOT / "schemas" / "artifacts" / "proposal_packet.schema.json").read_text(
            encoding="utf-8"
        )
    )
    proposal = _proposal({"source_type": "none", "reason": "No music is appropriate."})
    jsonschema.validate(proposal, schema)
    missing = _proposal({})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)


def test_none_music_requires_reason_and_has_no_track():
    assert normalize_music_source(
        {"source_type": "none", "reason": "Narration-only lesson."}
    ) == {"source_type": "none", "reason": "Narration-only lesson."}
    with pytest.raises(MusicContractError, match="reason"):
        normalize_music_source({"source_type": "none"})
    with pytest.raises(MusicContractError, match="track_path"):
        normalize_music_source(
            {"source_type": "none", "reason": "No music", "track_path": "music.wav"}
        )


def test_other_music_sources_require_auditable_selection_details():
    assert normalize_music_source(
        {"source_type": "user_library", "track_path": "assets/music/bed.wav"}
    )["source_type"] == "user_library"
    assert normalize_music_source(
        {
            "source_type": "ai_generated",
            "provider": "local-musicgen",
            "model": "musicgen-small",
            "prompt": "Warm, restrained educational ambient bed",
        }
    )["provider"] == "local-musicgen"
    with pytest.raises(MusicContractError, match="provider"):
        normalize_music_source(
            {"source_type": "ai_generated", "mood_direction": "calm"}
        )
    with pytest.raises(MusicContractError, match="track_path"):
        normalize_music_source({"source_type": "user_library"})


def test_music_decision_log_is_idempotent_and_auditable():
    source = {"source_type": "none", "reason": "No music for this lesson."}
    first = append_music_decision(
        {"version": "1.0", "project_id": "p", "decisions": []}, source
    )
    second = append_music_decision(first, source)
    assert len(first["decisions"]) == len(second["decisions"]) == 1
    decision = second["decisions"][0]
    assert decision["category"] == "music_source"
    assert decision["selected"] == "none"
    assert {item["option_id"] for item in decision["options_considered"]} == {
        "user_library",
        "ai_generated",
        "bring_your_own",
        "none",
    }


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_music_decision_rejects_non_boolean_user_approval(value: object):
    with pytest.raises(MusicContractError, match="user_approved must be boolean"):
        append_music_decision(
            {"version": "1.0", "project_id": "p", "decisions": []},
            {"source_type": "none", "reason": "No music for this lesson."},
            user_approved=value,  # type: ignore[arg-type]
        )


def test_music_asset_provenance_is_complete_and_rights_honest():
    provenance = normalize_music_provenance(
        {
            "source_type": "ai_generated",
            "source_tool": "music_gen",
            "provider": "elevenlabs",
            "license": "provider_terms",
            "prompt": "restrained ambient bed",
            "model": "music-v1",
            "duration_seconds": 30,
            "loop_allowed": True,
            "edit_rights": "allowed",
        }
    )
    assert provenance["rights_status"] == "verified"
    assert provenance["duration_seconds"] == 30.0
    assert validate_music_asset_provenance(
        {
            "type": "music",
            "source_tool": "music_gen",
            "music_provenance": provenance,
        }
    ) == []
    assert validate_music_asset_provenance(
        {"type": "music", "source_tool": "music_gen"}
    )


def test_asset_schema_requires_music_provenance_for_music_rows():
    schema = json.loads(
        (ROOT / "schemas" / "artifacts" / "asset_manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    base = {
        "version": "1.0",
        "assets": [
            {
                "id": "music-1",
                "type": "music",
                "path": "assets/music/bed.mp3",
                "source_tool": "music_gen",
                "scene_id": "scene-1",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(base, schema)
    base["assets"][0]["music_provenance"] = {
        "source_type": "ai_generated",
        "source_tool": "music_gen",
        "provider": "elevenlabs",
        "license": "provider_terms",
        "prompt": "ambient",
        "model": "music-v1",
        "duration_seconds": 10,
        "loop_allowed": True,
        "edit_rights": "allowed",
        "rights_status": "verified",
    }
    jsonschema.validate(base, schema)


def test_provider_result_can_be_promoted_to_manifest_provenance():
    provenance = music_provenance_from_output(
        {
            "provider": "elevenlabs",
            "model": "music-v1",
            "prompt": "warm ambient bed",
            "duration_seconds": 12,
            "license": "provider_terms",
        },
        {},
        source_tool="music_gen",
    )
    assert provenance["source_type"] == "ai_generated"
    assert provenance["source_tool"] == "music_gen"
    assert provenance["model"] == "music-v1"
