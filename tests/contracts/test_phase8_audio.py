"""Phase 8 audio finishing contracts (PR-802 onward)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lib.media_profiles import profile_contract
from lib.caption_contracts import validate_verified_transcript
from tools.analysis.audio_quality import AudioQualityProbe, probe_audio_quality
from tools.audio.audio_mixer import AudioMixer
from tools.subtitle.subtitle_gen import SubtitleGen


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


def _sine(path: Path, frequency: int, duration: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def test_full_mix_preserves_independently_addressable_stems(tmp_path: Path):
    speech = tmp_path / "speech.wav"
    music = tmp_path / "music.wav"
    sfx = tmp_path / "sfx.wav"
    _sine(speech, 440, 1.5)
    _sine(music, 220, 2.0)
    _sine(sfx, 880, 0.25)
    output = tmp_path / "mix.wav"
    result = AudioMixer().execute(
        {
            "operation": "full_mix",
            "tracks": [
                {"path": str(speech), "role": "speech"},
                {"path": str(music), "role": "music", "volume": 0.2},
                {"path": str(sfx), "role": "sfx", "start_seconds": 0.75},
            ],
            "preserve_stems": True,
            "output_path": str(output),
            "stem_output_dir": str(tmp_path / "stems"),
        }
    )
    assert result.success, result.error
    assert output.exists()
    data = result.data
    assert data["stems_preserved"] is True
    assert {record["role"] for record in data["stems"]} == {"speech", "music", "sfx"}
    assert all(Path(record["path"]).exists() for record in data["stems"])
    assert set(result.artifacts) == {str(output), *(record["path"] for record in data["stems"])}


def test_full_mix_legacy_mode_reports_stems_not_preserved(tmp_path: Path):
    speech = tmp_path / "speech.wav"
    music = tmp_path / "music.wav"
    _sine(speech, 440, 0.5)
    _sine(music, 220, 0.5)
    result = AudioMixer().execute(
        {
            "operation": "full_mix",
            "tracks": [
                {"path": str(speech), "role": "speech"},
                {"path": str(music), "role": "music"},
            ],
            "preserve_stems": False,
            "output_path": str(tmp_path / "mix.wav"),
        }
    )
    assert result.success, result.error
    assert result.data["stems_preserved"] is False
    assert result.data["stems"] == []


def test_profile_audio_contracts_are_explicit():
    youtube = profile_contract("youtube_landscape")
    assert youtube["audio_loudness_lufs"] == -14.0
    assert youtube["audio_true_peak_db"] == -1.0
    assert AudioQualityProbe().get_info(include_status=False)["name"] == "audio_quality_probe"


def test_quality_probe_measures_clean_normalized_mix(tmp_path: Path):
    source = tmp_path / "source.wav"
    output = tmp_path / "normal.wav"
    _sine(source, 440, 1.0)
    result = AudioMixer().execute(
        {
            "operation": "mix",
            "tracks": [{"path": str(source), "role": "speech"}],
            "normalize": True,
            "output_path": str(output),
        }
    )
    assert result.success, result.error
    report = probe_audio_quality(output, profile="generic_hd")
    assert report["valid"] is True, report["errors"]
    assert abs(report["integrated_lufs"] + 16.0) <= 1.5
    assert report["true_peak_db"] <= -1.0
    assert report["clipping_samples"] == 0


def test_ducking_rejects_invalid_level(tmp_path: Path):
    speech = tmp_path / "speech.wav"
    music = tmp_path / "music.wav"
    _sine(speech, 440, 0.3)
    _sine(music, 220, 0.3)
    result = AudioMixer().execute(
        {
            "operation": "duck",
            "primary_audio": str(speech),
            "secondary_audio": str(music),
            "ducking": {"music_volume_during_speech": 1.5},
            "output_path": str(tmp_path / "bad.wav"),
        }
    )
    assert result.success is False
    assert "between 0 and 1" in (result.error or "")


@pytest.mark.parametrize("field", ["normalize", "preserve_stems", "quality_check"])
@pytest.mark.parametrize("malformed", ["false", "true", 0, 1, None, []])
def test_full_mix_rejects_malformed_boolean_controls(field, malformed, tmp_path: Path):
    result = AudioMixer().execute(
        {
            "operation": "full_mix",
            "tracks": [{"path": str(tmp_path / "missing.wav"), "role": "speech"}],
            field: malformed,
            "output_path": str(tmp_path / "mix.wav"),
        }
    )

    assert result.success is False
    assert f"{field} must be boolean" in (result.error or "")


@pytest.mark.parametrize("malformed", ["false", "true", 0, 1, None, []])
def test_full_mix_rejects_malformed_ducking_enabled(malformed, tmp_path: Path):
    result = AudioMixer().execute(
        {
            "operation": "full_mix",
            "tracks": [{"path": str(tmp_path / "missing.wav"), "role": "speech"}],
            "ducking": {"enabled": malformed},
            "output_path": str(tmp_path / "mix.wav"),
        }
    )

    assert result.success is False
    assert result.error == "ducking.enabled must be boolean"


def _verified_transcript() -> dict:
    return {
        "verified": True,
        "verification_status": "verified",
        "language": "en-US",
        "source_audio_sha256": "a" * 64,
        "segments": [
            {
                "text": "Hello world",
                "start": 0.0,
                "end": 1.2,
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.5},
                    {"word": "world", "start": 0.6, "end": 1.2},
                ],
            }
        ],
    }


def test_captions_require_verified_transcript_and_preserve_digest(tmp_path: Path):
    transcript = _verified_transcript()
    report = validate_verified_transcript(transcript, expected_language="en", expected_text="Hello world")
    assert report["valid"] is True, report["errors"]
    out = tmp_path / "captions.srt"
    result = SubtitleGen().execute(
        {
            "segments": transcript["segments"],
            "transcript": transcript,
            "require_verified_transcript": True,
            "expected_language": "en-US",
            "expected_text": "Hello world",
            "output_path": str(out),
        }
    )
    assert result.success, result.error
    assert result.data["transcript_digest"] == report["transcript_digest"]
    assert "Hello world" in out.read_text(encoding="utf-8")


def test_unverified_wrong_language_and_overlapping_transcript_fail_closed(tmp_path: Path):
    transcript = _verified_transcript()
    transcript["verified"] = False
    result = SubtitleGen().execute(
        {
            "segments": transcript["segments"],
            "transcript": transcript,
            "require_verified_transcript": True,
            "output_path": str(tmp_path / "bad.srt"),
        }
    )
    assert result.success is False
    assert "verified" in (result.error or "")

    wrong_language = _verified_transcript()
    wrong_language["language"] = "fr-FR"
    report = validate_verified_transcript(wrong_language, expected_language="en-US")
    assert report["valid"] is False
    assert any("language" in error for error in report["errors"])

    overlap = _verified_transcript()
    overlap["segments"][0]["words"][1]["start"] = 0.2
    report = validate_verified_transcript(overlap)
    assert report["valid"] is False
    assert any("overlaps" in error for error in report["errors"])
