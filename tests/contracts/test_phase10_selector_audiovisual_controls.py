"""PR-1033 selector-level audiovisual control boundaries."""

from __future__ import annotations

import pytest

from tools.audio.music_selector import MusicSelector
from tools.graphics.image_selector import ImageSelector
from tools.video.video_selector import VideoSelector


@pytest.mark.parametrize(
    "selector,field,value",
    [
        (ImageSelector, "watermark", "false"),
        (ImageSelector, "watermark", 1),
        (VideoSelector, "multi_shot", "false"),
        (VideoSelector, "multi_shot", 1),
        (VideoSelector, "watermark", "false"),
        (VideoSelector, "watermark", 1),
        (MusicSelector, "force_instrumental", "false"),
        (MusicSelector, "force_instrumental", 0),
    ],
)
def test_selector_rejects_malformed_audiovisual_controls_before_provider_discovery(
    monkeypatch, selector, field, value
):
    instance = selector()
    called = False

    def discovery_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider discovery must not run with malformed controls")

    monkeypatch.setattr(instance, "_providers", discovery_must_not_run)
    prompt_key = "text" if selector is MusicSelector else "prompt"
    result = instance.execute({prompt_key: "a precise request", field: value})

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not called


@pytest.mark.parametrize("operation", ["not-supported", None])
def test_video_selector_rejects_unknown_operation_before_provider_discovery(monkeypatch, operation):
    selector = VideoSelector()
    called = False

    def discovery_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider discovery must not run for unknown operation")

    monkeypatch.setattr(selector, "_providers", discovery_must_not_run)
    result = selector.execute({"prompt": "a video", "operation": operation})

    assert not result.success
    assert "unsupported video operation" in (result.error or "").lower()
    assert not called


def test_video_selector_rejects_unknown_rank_target_before_provider_discovery(monkeypatch):
    selector = VideoSelector()
    monkeypatch.setattr(selector, "_providers", lambda: pytest.fail("provider discovery must not run"))
    result = selector.execute({"prompt": "a video", "operation": "rank", "target_operation": "unknown"})
    assert not result.success
    assert "unsupported video operation" in (result.error or "").lower()
