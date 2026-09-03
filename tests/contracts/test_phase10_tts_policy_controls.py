"""PR-1031 TTS selector batch/sample policy boundary regressions."""

from __future__ import annotations

import pytest

from tools.audio.tts_selector import TTSSelector


@pytest.mark.parametrize(
    "field,value",
    [
        ("batch", "false"),
        ("batch", 1),
        ("sample_mode", "false"),
        ("sample_mode", 0),
        ("timestamps", "false"),
        ("timestamps", 1),
    ],
)
def test_tts_selector_rejects_malformed_batch_controls_before_provider_discovery(
    monkeypatch, field, value
):
    selector = TTSSelector()
    called = False

    def provider_discovery_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider discovery must not run with malformed batch controls")

    monkeypatch.setattr(selector, "_providers", provider_discovery_must_not_run)
    result = selector.execute({"text": "A precise narration", field: value})

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not called


def test_tts_selector_sample_mode_cannot_bypass_batch_approval(monkeypatch):
    selector = TTSSelector()
    monkeypatch.setattr(selector, "_providers", lambda: [])
    result = selector.execute(
        {
            "text": "A batch narration",
            "batch": True,
            "sample_mode": "false",
            "voice_selection": {"sample_approval_required": True},
        }
    )

    assert not result.success
    assert "boolean" in (result.error or "").lower()


def test_tts_selector_rejects_non_array_segments_before_provider_discovery(monkeypatch):
    selector = TTSSelector()
    called = False

    def provider_discovery_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider discovery must not run with malformed segments")

    monkeypatch.setattr(selector, "_providers", provider_discovery_must_not_run)
    result = selector.execute({"text": "Narration", "segments": {"id": "not-an-array"}})

    assert not result.success
    assert "segments" in (result.error or "").lower()
    assert not called


def test_tts_selector_rejects_unknown_operation_before_provider_discovery(monkeypatch):
    selector = TTSSelector()
    called = False

    def provider_discovery_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider discovery must not run for unknown operation")

    monkeypatch.setattr(selector, "_providers", provider_discovery_must_not_run)
    result = selector.execute({"text": "Narration", "operation": "not-supported"})

    assert not result.success
    assert "unknown operation" in (result.error or "").lower()
    assert not called
