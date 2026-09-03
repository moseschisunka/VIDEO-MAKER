"""PR-5G narrated golden-project integration gate."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lib.audio_assembly import assemble_audio_segments
from lib.voice_contracts import (
    VoiceSegmentCache,
    compare_transcript_to_script,
    execute_narration_segments,
    normalize_voice_identity,
    plan_narration_segments,
    require_voice_sample_approval,
    validate_voice_propagation,
)


ROOT = Path(__file__).resolve().parents[2]


def test_phase5_gate_identity_approval_segments_resume_assembly_and_transcript(tmp_path: Path):
    voice = normalize_voice_identity({
        "provider": "edge_tts",
        "model": "neural-v1",
        "voice_id": "en-US-ChristopherNeural",
        "locale": "en-US",
        "settings": {"rate": "-12%"},
    })
    selection = {**voice.contract(), "sample_approval_required": True}
    assert require_voice_sample_approval(
        selection,
        sample={"approved": True, "status": "approved"},
        batch=True,
    )["allowed"] is True
    segments = plan_narration_segments(
        [{"id": "intro", "text": "Open Montage keeps the selected voice stable."},
         {"id": "close", "text": "Every completed segment can resume safely."}],
        voice,
        voice_rate_wpm=150,
    )
    cache = VoiceSegmentCache(tmp_path / "cache")
    calls: list[str] = []

    def fake_tts(segment):
        calls.append(segment["segment_id"])
        wav = tmp_path / f"{segment['segment_id']}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.25", str(wav)],
            check=True,
            capture_output=True,
        )
        return {"audio_path": str(wav), "measured_duration_seconds": 0.25}

    first = execute_narration_segments(segments, fake_tts, cache=cache)
    assert first["valid"] is True
    assert first["completed_count"] == 2
    calls.clear()
    resumed = execute_narration_segments(segments, fake_tts, cache=cache)
    assert resumed["valid"] is True
    assert all(item["cache_hit"] for item in resumed["segments"])
    assert calls == []

    output = tmp_path / "narration.mp3"
    assembly = assemble_audio_segments(
        [item["audio_path"] for item in resumed["segments"]], output
    )
    assert assembly["segment_count"] == 2
    assert output.is_file() and output.stat().st_size > 0

    transcript = compare_transcript_to_script(
        "Open Montage keeps the selected voice stable. Every completed segment can resume safely.",
        "Open Montage keeps the selected voice stable. Every completed segment can resume safely.",
    )
    assert transcript["valid"] is True
    propagation = validate_voice_propagation(
        {
            "proposal_packet": {"production_plan": {"voice_selection": selection}},
            "script": {"voice_identity": voice.contract()},
            "asset_manifest": {"voice_identity": voice.contract()},
            "render_report": {"voice_identity": voice.contract()},
        },
        expected=voice,
    )
    assert propagation["valid"] is True


def test_phase5_gate_keeps_global_production_lock():
    tracker = (ROOT / "docs" / "production-readiness" / "PROGRESS_TRACKER.md").read_text(encoding="utf-8")
    assert "Production decision | Not eligible" in tracker
    playbook = (ROOT / "docs" / "production-readiness" / "EXECUTION_PLAYBOOK.md").read_text(encoding="utf-8")
    assert "PR-11G" in playbook


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_phase5_gate_rejects_unapproved_batch_without_provider_call():
    from tools.audio.tts_selector import TTSSelector

    result = TTSSelector().execute({
        "text": "This must not start before approval.",
        "batch": True,
        "voice_selection": {
            "provider": "openai",
            "model": "gpt-4o-mini-tts",
            "voice_id": "coral",
            "locale": "en-US",
            "sample_approval_required": True,
        },
    })
    assert result.success is False
    assert "sample" in (result.error or "").lower()
