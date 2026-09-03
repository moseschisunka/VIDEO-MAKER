"""Deterministic Phase 5 voice identity, approval, and resume contracts."""

from pathlib import Path

import pytest

from lib.voice_contracts import (
    VoiceContractError,
    VoiceSampleApprovalError,
    VoiceSegmentCache,
    compare_transcript_to_script,
    execute_narration_segments,
    normalize_voice_identity,
    plan_narration_segments,
    require_voice_sample_approval,
    strict_bool,
    validate_narration_manifest,
    validate_voice_propagation,
    verify_transcript,
)


def _voice(**overrides):
    payload = {
        "provider": "openai",
        "model": "gpt-4o-mini-tts",
        "voice_id": "coral",
        "locale": "en-US",
        "settings": {"speed": 0.9},
    }
    payload.update(overrides)
    return payload


def test_voice_identity_is_canonical_and_provider_alias_does_not_fallback():
    first = normalize_voice_identity(_voice())
    alias = normalize_voice_identity(_voice(provider="open-ai"))
    changed = normalize_voice_identity(_voice(voice_id="alloy"))

    assert first.contract()["identity_key"] == first.identity_key
    assert alias.identity_key == first.identity_key
    assert changed.identity_key != first.identity_key
    assert first.provider == "openai"


def test_voice_propagation_detects_provider_or_voice_drift():
    voice = normalize_voice_identity(_voice()).contract()
    drifted = normalize_voice_identity(_voice(provider="edge_tts", model="default", voice_id="en-US-AriaNeural")).contract()
    report = validate_voice_propagation(
        {"proposal_packet": {"voice_identity": voice}, "render_report": {"voice_identity": drifted}}
    )
    assert report["valid"] is False
    assert "voice identity drift" in report["errors"][0]


def test_sample_gate_blocks_batch_until_explicit_approval():
    with pytest.raises(VoiceSampleApprovalError):
        require_voice_sample_approval({"sample_approval_required": True}, batch=True)
    allowed = require_voice_sample_approval(
        {"sample_approval_required": True},
        sample={"status": "approved", "approved": True},
        batch=True,
    )
    assert allowed["allowed"] is True
    # Samples themselves are allowed before approval; only batch narration is gated.
    assert require_voice_sample_approval({"sample_approval_required": True}, batch=False)["allowed"] is True


def test_sample_gate_rejects_truthy_non_boolean_approval_flags():
    with pytest.raises(VoiceContractError, match="sample_approval.approved must be boolean"):
        require_voice_sample_approval(
            {"sample_approval_required": True},
            sample={"approved": "false"},
            batch=True,
        )
    with pytest.raises(VoiceContractError, match="voice_selection.sample_approved must be boolean"):
        require_voice_sample_approval(
            {"sample_approval_required": True, "sample_approved": "false"},
            sample={"status": "approved"},
            batch=True,
        )
    with pytest.raises(VoiceContractError, match="voice_selection.sample_approval_required must be boolean"):
        require_voice_sample_approval(
            {"sample_approval_required": "true"},
            sample={"approved": True},
            batch=True,
        )


def test_strict_bool_does_not_coerce_untrusted_values():
    assert strict_bool(None, "flag") is False
    assert strict_bool(True, "flag") is True
    with pytest.raises(VoiceContractError, match="flag must be boolean"):
        strict_bool("false", "flag")


def test_narration_segment_ids_and_timeline_are_stable():
    voice = normalize_voice_identity(_voice())
    sections = [{"id": "intro", "text": "  Hello   world.  "}, {"id": "close", "text": "Keep learning."}]
    first = plan_narration_segments(sections, voice)
    second = plan_narration_segments(sections, voice)
    changed_voice = plan_narration_segments(sections, normalize_voice_identity(_voice(voice_id="alloy")))

    assert [item["segment_id"] for item in first] == [item["segment_id"] for item in second]
    assert first[0]["segment_id"] != changed_voice[0]["segment_id"]
    assert validate_narration_manifest(first)["valid"] is True
    assert first[1]["start_seconds"] == first[0]["end_seconds"]


def test_segment_cache_resumes_completed_work_and_reruns_only_failed_segment(tmp_path):
    voice = normalize_voice_identity(_voice())
    segments = plan_narration_segments(["First segment.", "Second segment.", "Third segment."], voice)
    cache = VoiceSegmentCache(tmp_path / "cache")
    calls = []

    def generate(segment):
        calls.append(segment["segment_id"])
        audio = tmp_path / f"{segment['segment_id']}.mp3"
        audio.write_bytes(f"audio-{segment['segment_id']}".encode())
        return {"audio_path": str(audio), "measured_duration_seconds": 1.0}

    first = execute_narration_segments(segments[:1], generate, cache=cache)
    assert first["valid"] is True
    calls.clear()
    second = execute_narration_segments(segments[:1], generate, cache=cache)
    assert second["valid"] is True
    assert second["segments"][0]["cache_hit"] is True
    assert calls == []

    def fail_second(segment):
        calls.append(segment["segment_id"])
        raise RuntimeError("provider timeout")

    failed = execute_narration_segments(segments, fail_second, cache=cache)
    assert failed["valid"] is False
    assert failed["failed_segment_id"] == segments[1]["segment_id"]
    assert calls == [segments[1]["segment_id"]]


def test_transcript_verifier_catches_punctuation_leak_and_accepts_clean_text():
    clean = compare_transcript_to_script("A short lesson.", "A short lesson.")
    leak = compare_transcript_to_script("Wait... now continue.", "Wait dot dot dot now continue.")
    assert clean["valid"] is True
    assert leak["valid"] is False
    assert leak["punctuation_leaks"]


def test_verify_transcript_accepts_word_timestamps_and_flags_missing_segments(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        '{"word_timestamps": [{"word": "Hello"}, {"word": "world"}], "segments": [{"id": "s1"}]}',
        encoding="utf-8",
    )
    report = verify_transcript(transcript, "Hello world.", expected_segment_ids=["s1"])
    assert report["valid"] is True
    missing = verify_transcript(transcript, "Hello world.", expected_segment_ids=["s1", "s2"])
    assert missing["valid"] is False
    assert "s2" in missing["missing_segment_ids"]


@pytest.mark.parametrize("provider", ["piper", "cloud_fake"])
def test_provider_failure_matrix_timeout_and_partial_output_are_retryable(tmp_path, provider):
    voice = normalize_voice_identity(_voice(provider="openai" if provider == "cloud_fake" else "piper", model="fake-v1"))
    segment = plan_narration_segments(["A recoverable segment."], voice)[0]
    cache = VoiceSegmentCache(tmp_path / provider)

    def timeout(_segment):
        raise TimeoutError("provider timeout")

    failed = execute_narration_segments([segment], timeout, cache=cache)
    assert failed["valid"] is False
    assert failed["failed_segment_id"] == segment["segment_id"]
    assert cache.load(segment) is None

    def partial(_segment):
        path = tmp_path / f"{provider}-partial.mp3"
        path.write_bytes(b"")
        return {"audio_path": str(path)}

    incomplete = execute_narration_segments([segment], partial, cache=cache)
    assert incomplete["valid"] is False
    assert cache.load(segment) is None
