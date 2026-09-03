"""Seeded offline fault corpus for the Phase 9 final-review gate.

Every case uses local FFmpeg/media fixtures or local JSON evidence.  The
corpus is intentionally deterministic: it proves that the final review
detects a defect and routes the artifact to the correct next action instead of
returning an optimistic render success.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.video.video_compose import VideoCompose


CORPUS_PATH = Path(__file__).parents[1] / "eval" / "qa_fault_corpus.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=90,
    )


def _media(path: Path, *, video: str = "testsrc=size=320x240:rate=25", audio: str = "sine=frequency=440:sample_rate=44100") -> Path:
    audio_expr = audio if ("d=" in audio or "duration=" in audio) else f"{audio}:d=3"
    _ffmpeg(
        "-f", "lavfi", "-i", f"{video}:d=3",
        "-f", "lavfi", "-i", audio_expr,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )
    return path


def _frozen_tail(path: Path) -> Path:
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:d=3",
        "-filter_complex", "[0:v]tpad=stop_mode=clone:stop_duration=2,trim=duration=3,setpts=PTS-STARTPTS[v]",
        "-map", "[v]", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    )
    return path


def _duplicate_shots(path: Path) -> Path:
    # A → B → A gives the sampled-frame detector a non-adjacent repeat.
    _ffmpeg(
        "-filter_complex",
        "color=c=red:s=320x240:r=25:d=1[a];"
        "color=c=blue:s=320x240:r=25:d=1[b];"
        "color=c=red:s=320x240:r=25:d=1[c];"
        "[a][b][c]concat=n=3:v=1:a=0[v]",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:d=3",
        "-map", "[v]", "-map", "0:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    )
    return path


def _transcript(path: Path, *, language: str | None = None, voice: dict | None = None, words: list[str]) -> Path:
    payload: dict[str, object] = {
        "word_timestamps": [{"word": word} for word in words],
    }
    if language:
        payload["language"] = language
    if voice:
        payload["voice_identity"] = voice
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _approved_voice(locale: str = "en-US", voice_id: str = "en-US-ChristopherNeural") -> dict[str, object]:
    return {
        "provider": "edge_tts",
        "model": "neural",
        "voice_id": voice_id,
        "locale": locale,
    }


def _review(case_id: str, root: Path) -> dict:
    output = root / f"{case_id}.mp4"
    compose = VideoCompose()
    if case_id == "missing_media":
        return compose._run_final_review(output, {"version": "1.0", "cuts": []})
    if case_id == "black_video":
        _media(output, video="color=c=black:s=320x240")
        return compose._run_final_review(output, {"version": "1.0", "cuts": [{"id": "black", "in_seconds": 0, "out_seconds": 3}]})
    if case_id == "frozen_video":
        _frozen_tail(output)
        return compose._run_final_review(output, {"version": "1.0", "cuts": [{"id": "tail", "in_seconds": 0, "out_seconds": 3}]})
    if case_id == "duplicate_shots":
        _duplicate_shots(output)
        return compose._run_final_review(output, {"version": "1.0", "cuts": [
            {"id": "a", "in_seconds": 0, "out_seconds": 1},
            {"id": "b", "in_seconds": 1, "out_seconds": 2},
            {"id": "c", "in_seconds": 2, "out_seconds": 3},
        ]})
    if case_id == "wrong_profile":
        _media(output)
        return compose._run_final_review(output, {"version": "1.0", "output_profile": "youtube_landscape", "cuts": [{"id": "c", "in_seconds": 0, "out_seconds": 3}]})
    if case_id == "silent_audio":
        _media(output, audio="anullsrc=r=44100:cl=stereo")
        return compose._run_final_review(output, {"version": "1.0", "output_profile": "generic_hd", "audio": {"narration": {"required": True}}, "cuts": [{"id": "c", "in_seconds": 0, "out_seconds": 3}]})
    if case_id == "clipped_audio":
        _media(output, audio="sine=frequency=440:sample_rate=44100:d=3,volume=10")
        return compose._run_final_review(output, {"version": "1.0", "output_profile": "generic_hd", "audio": {"narration": {"required": True}}, "cuts": [{"id": "c", "in_seconds": 0, "out_seconds": 3}]})
    if case_id == "transcript_drift":
        _media(output)
        transcript = _transcript(root / "transcript-drift.json", words=["wrong", "words", "only"])
        return compose._run_final_review(
            output,
            {"version": "1.0", "audio": {"narration": {"required": True}}, "cuts": [{"id": "c", "in_seconds": 0, "out_seconds": 3}]},
            narration_transcript_path=transcript,
            script_text="approved words here",
        )
    if case_id == "language_voice_mismatch":
        _media(output)
        approved = _approved_voice()
        drifted = _approved_voice(locale="fr-FR", voice_id="fr-FR-DeniseNeural")
        transcript = _transcript(root / "transcript-language-voice-drift.json", language="fr-FR", voice=drifted, words=["approved", "words", "here"])
        edit = {
            "version": "1.0",
            "voice_identity": approved,
            "audio": {"narration": {"required": True}},
            "cuts": [{"id": "c", "in_seconds": 0, "out_seconds": 3}],
        }
        return compose._run_final_review(output, edit, asset_manifest={"version": "1.0", "voice_identity": approved, "assets": []}, narration_transcript_path=transcript, script_text="approved words here")
    if case_id == "broken_overlay_bounds":
        _media(output)
        edit = {
            "version": "1.0",
            "cuts": [{"id": "c", "in_seconds": 0, "out_seconds": 3}],
            "overlays": [{"position": {"x": 300, "y": 0, "width": 40, "height": 40}, "start_seconds": 0, "end_seconds": 3}],
        }
        return compose._run_final_review(output, edit)
    if case_id == "missing_visual_source":
        _media(output)
        edit = {"version": "1.0", "cuts": [{"id": "c", "source": "missing.mp4", "in_seconds": 0, "out_seconds": 3}]}
        return compose._run_final_review(output, edit, asset_manifest={"version": "1.0", "assets": []})
    if case_id == "placeholder_copy":
        _media(output)
        edit = {"version": "1.0", "cuts": [{"id": "c", "in_seconds": 0, "out_seconds": 3}], "metadata": {"title": "TODO: replace this title"}}
        return compose._run_final_review(output, edit)
    raise AssertionError(f"unhandled corpus case: {case_id}")


def test_fault_corpus_is_offline_and_complete() -> None:
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert payload["version"] == "1.0"
    assert payload["provider_access"] == "offline_only"
    assert {case["id"] for case in payload["cases"]} == {
        "missing_media", "black_video", "frozen_video", "duplicate_shots",
        "wrong_profile", "silent_audio", "clipped_audio", "transcript_drift",
        "language_voice_mismatch", "broken_overlay_bounds", "missing_visual_source",
        "placeholder_copy",
    }
    assert shutil.which("ffmpeg") and shutil.which("ffprobe")


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_fault_corpus_routes_each_defect_to_the_declared_action(case: dict, tmp_path: Path) -> None:
    review = _review(case["id"], tmp_path)
    issues = "\n".join(str(item) for item in review.get("issues_found") or [])
    assert review["status"] == case["expected_status"]
    assert review["recommended_action"] == case["expected_action"]
    assert case["required_signal"].lower() in issues.lower(), (case["id"], review)
