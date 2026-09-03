"""PR-400/PR-401 source and claim-grounding contracts."""

from __future__ import annotations

from lib.grounding import ClaimStatus, GroundingDecision, validate_grounding
from lib.project_pipeline import (
    ContentTemplateRequiredError,
    _build_lesson_beats,
    _build_script,
)
from schemas.artifacts import validate_artifact


def _brief(*, topic: str = "A neutral topic", complete: bool = True) -> dict:
    source = {
        "id": "source_primary",
        "url": "https://example.test/primary",
        "canonical_locator": "https://example.test/primary",
        "title": "Primary source",
        "used_for": "claim c_supported",
        "accessed_at": "2026-09-02",
        "excerpt_or_note": "Synthetic source note for the contract test.",
        "license": "synthetic-test",
        "usage_constraints": "Use only as a synthetic fixture.",
        "claim_ids": ["c_supported"],
        "reliability": "primary",
    }
    if not complete:
        source.pop("accessed_at")
        source.pop("license")
        source.pop("usage_constraints")
    return {
        "topic": topic,
        "grounding_contract": {"required": True, "source_record_version": "1.0"},
        "sources": [source],
        "data_points": [
            {
                "id": "data_point_1",
                "claim": "A synthetic supported fact.",
                "source_url": "https://example.test/primary",
                "credibility": "primary_source",
            }
        ],
    }


def test_supported_factual_claim_is_traceable_to_canonical_source():
    report = validate_grounding(
        _brief(),
        {
            "grounding_contract": {"required": True},
            "claims": [
                {
                    "id": "c_supported",
                    "text": "A synthetic supported fact.",
                    "claim_type": "factual",
                    "source_refs": ["source_primary"],
                }
            ],
        },
    )

    assert report["valid"] is True
    assert report["decision"] == GroundingDecision.PASS.value
    assert report["claims"][0]["status"] == ClaimStatus.SUPPORTED.value
    assert report["claims"][0]["resolved_source_ids"] == ["source_primary"]


def test_unknown_source_blocks_high_risk_claim():
    report = validate_grounding(
        _brief(topic="Medical treatment safety"),
        {
            "grounding_contract": {"required": True},
            "claims": [
                {
                    "id": "c_danger",
                    "text": "This treatment is safe for everyone.",
                    "claim_type": "factual",
                    "source_refs": ["source_does_not_exist"],
                    "risk_level": "high",
                }
            ],
        },
    )

    assert report["valid"] is False
    assert report["decision"] == GroundingDecision.BLOCK.value
    assert report["claims"][0]["status"] == ClaimStatus.MISSING_SOURCE.value
    assert any("unknown source" in error for error in report["errors"])


def test_explicit_contradiction_and_uncertainty_are_not_silently_supported():
    report = validate_grounding(
        _brief(),
        {
            "claims": [
                {
                    "id": "c_contradicted",
                    "text": "The source says the opposite.",
                    "claim_type": "factual",
                    "source_refs": ["source_primary"],
                    "status": "contradicted",
                },
                {
                    "id": "c_uncertain",
                    "text": "The evidence is not settled.",
                    "claim_type": "factual",
                    "source_refs": ["source_primary"],
                    "status": "uncertain",
                },
            ]
        },
    )

    assert report["valid"] is False
    assert report["decision"] == GroundingDecision.REVISE.value
    assert [claim["status"] for claim in report["claims"]] == [
        ClaimStatus.CONTRADICTED.value,
        ClaimStatus.UNCERTAIN.value,
    ]


def test_creative_and_opinion_text_are_classified_separately():
    report = validate_grounding(
        _brief(),
        {
            "claims": [
                {"id": "hook", "text": "Imagine a world without friction.", "claim_type": "creative"},
                {"id": "opinion", "text": "This is the clearest approach.", "claim_type": "opinion"},
            ]
        },
    )

    assert report["valid"] is True
    assert report["decision"] == GroundingDecision.PASS.value
    assert [claim["status"] for claim in report["claims"]] == [
        ClaimStatus.CREATIVE.value,
        ClaimStatus.OPINION.value,
    ]


def test_legacy_data_point_reference_resolves_when_source_metadata_is_complete():
    report = validate_grounding(
        _brief(),
        {
            "sections": [
                {
                    "id": "section_1",
                    "text": "A synthetic supported fact.",
                    "source_ref": "data_point_1",
                }
            ]
        },
    )

    assert report["valid"] is True
    assert report["claims"][0]["resolved_source_ids"] == ["source_primary"]


def test_grounding_report_and_extended_artifacts_are_schema_valid():
    report = validate_grounding(
        _brief(),
        {
            "grounding_contract": {"required": True},
            "claims": [
                {
                    "id": "c_supported",
                    "text": "A synthetic supported fact.",
                    "claim_type": "factual",
                    "source_refs": ["source_primary"],
                }
            ],
        },
    )
    validate_artifact("grounding_report", report)
    validate_artifact(
        "script",
        {
            "version": "1.0",
            "title": "Grounded fixture",
            "total_duration_seconds": 5,
            "grounding_contract": {"required": True, "source_artifact": "research_brief"},
            "claims": [
                {
                    "id": "c_supported",
                    "text": "A synthetic supported fact.",
                    "claim_type": "factual",
                    "source_refs": ["source_primary"],
                }
            ],
            "sections": [
                {
                    "id": "s1",
                    "text": "A synthetic supported fact.",
                    "start_seconds": 0,
                    "end_seconds": 5,
                    "claim_id": "c_supported",
                    "claim_type": "factual",
                    "source_refs": ["source_primary"],
                }
            ],
        },
    )


def test_non_template_subject_cannot_receive_generic_teacher_filler():
    for builder in (
        lambda: _build_lesson_beats("A new subject", "not epidemiology", 3),
        lambda: _build_script("A new subject", "not epidemiology", 30),
    ):
        try:
            builder()
        except ContentTemplateRequiredError as exc:
            assert "template" in str(exc).lower()
        else:  # pragma: no cover - makes the failure message explicit
            raise AssertionError("non-template subject unexpectedly received generated filler")


def test_explicit_subject_template_is_the_only_non_domain_fallback():
    beats = _build_lesson_beats(
        "A new subject",
        "not epidemiology",
        2,
        template_beats=[
            {
                "title": "Verified concept",
                "section": "CORE",
                "bullets": ["A", "B"],
                "narration": "A subject-specific explanation.",
                "diagram": "verified-diagram",
            },
            {
                "title": "Applied example",
                "section": "APPLICATION",
                "bullets": ["C", "D"],
                "narration": "A subject-specific example.",
                "diagram": "example-diagram",
            },
        ],
    )
    assert [beat["title"] for beat in beats] == ["Verified concept", "Applied example"]
    assert all("epidemiology" not in beat["narration"].lower() for beat in beats)
