"""PR-1028 VideoSelector policy and operation-boundary regressions."""

from __future__ import annotations

from typing import Any

import pytest

from tools.base_tool import ToolResult, ToolStatus
from tools.video.video_selector import VideoSelector


class _Provider:
    name = "stub_video"
    provider = "stub"
    version = "1"
    capability = "video_generation"
    supports = {"text_to_video": True, "image_to_video": True, "reference_to_video": True}
    input_schema = {"properties": {"prompt": {}}}
    best_for = ["tests"]

    def get_status(self):
        return ToolStatus.AVAILABLE

    def get_info(self, *args, **kwargs):
        return {
            "name": self.name,
            "provider": self.provider,
            "agent_skills": [],
            "best_for": self.best_for,
            "supports": self.supports,
        }

    def is_operation_available(self, operation: str) -> bool:
        return bool(self.supports.get(operation))


@pytest.fixture()
def selector(monkeypatch):
    provider = _Provider()
    sel = VideoSelector()
    monkeypatch.setattr(sel, "_providers", lambda: [provider])
    monkeypatch.setattr(sel, "_select_best_tool", lambda *args, **kwargs: (provider, None))
    return sel


@pytest.mark.parametrize(
    "field,value",
    [
        ("motion_required", "false"),
        ("motion_required", 1),
        ("strict_media_validation", "false"),
        ("strict_media_validation", 1),
        ("production_mode", "false"),
        ("production_mode", 1),
    ],
)
def test_malformed_video_policy_controls_fail_before_provider_call(selector, monkeypatch, field, value):
    called = False

    def provider_call(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not run with malformed policy controls")

    monkeypatch.setattr("lib.providers.bridge.execute_with_provider_executor", provider_call)

    result = selector.execute({"prompt": "moving subject", field: value})

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not called


def test_motion_requirement_blocks_still_downgrade_even_for_text_to_video(selector, monkeypatch):
    monkeypatch.setattr(
        "lib.providers.bridge.execute_with_provider_executor",
        lambda *args, **kwargs: ToolResult(
            success=True,
            data={"media_type": "image"},
            artifacts=["still.png"],
        ),
    )

    result = selector.execute(
        {"prompt": "moving subject", "operation": "text_to_video", "motion_required": True}
    )

    assert not result.success
    assert "motion-required" in (result.error or "").lower()


def test_motion_requirement_reaches_strict_output_validation(selector, monkeypatch):
    seen: dict[str, Any] = {}

    monkeypatch.setattr(
        "lib.providers.bridge.execute_with_provider_executor",
        lambda *args, **kwargs: ToolResult(success=True, data={}, artifacts=["video.mp4"]),
    )

    def validate(result, *, media_type, constraints, motion_required, strict):
        seen.update(
            media_type=media_type,
            motion_required=motion_required,
            strict=strict,
        )
        return {"valid": True, "outputs": []}

    monkeypatch.setattr("lib.media_generation.validate_generation_output", validate)

    result = selector.execute(
        {
            "prompt": "moving subject",
            "operation": "text_to_video",
            "motion_required": True,
            "strict_media_validation": True,
        }
    )

    assert result.success
    assert seen == {"media_type": "video", "motion_required": True, "strict": True}


def test_motion_required_fallback_excludes_image_selector_for_text_to_video(monkeypatch):
    sel = VideoSelector()
    monkeypatch.setattr(sel, "_providers", lambda: [])

    assert "image_selector" not in sel.fallback_tools_for(
        {"operation": "text_to_video", "motion_required": True}
    )
    assert "image_selector" not in sel.fallback_tools_for(
        {"operation": "text_to_video", "motion_required": "false"}
    )


def test_unknown_video_operation_fails_before_provider_selection(monkeypatch):
    sel = VideoSelector()
    called = False

    def select(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider selection must not run for an unknown operation")

    monkeypatch.setattr(sel, "_select_best_tool", select)
    result = sel.execute({"prompt": "x", "operation": "mystery"})

    assert not result.success
    assert "unsupported video operation" in (result.error or "").lower()
    assert not called
