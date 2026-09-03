"""PR-1034 execution-boundary schema boolean regressions."""

from __future__ import annotations

import pytest

from tools.audio.doubao_tts import DoubaoTTS
from tools.audio.music_gen import MusicGen
from tools.audio.tts_selector import TTSSelector
from tools.base_tool import BaseTool, ToolResult
from tools.graphics.dashscope_image import DashscopeImage
from tools.video.seedance_ark import SeedanceArkVideo
from tools.video.veo_video import VeoVideo
from tools.character.character_animation import CharacterAnimationReviewer


class _BooleanTool(BaseTool):
    name = "boolean_schema_fixture"
    input_schema = {
        "type": "object",
        "properties": {"enabled": {"type": "boolean"}},
    }

    def execute(self, inputs):
        return ToolResult(success=True, data={"enabled": inputs.get("enabled")})


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_base_tool_rejects_malformed_declared_boolean_before_execute(value):
    result = _BooleanTool().execute({"enabled": value})
    assert not result.success
    assert "enabled must be boolean" in (result.error or "")


@pytest.mark.parametrize(
    "tool,field",
    [
        (DoubaoTTS, "return_usage"),
        (DoubaoTTS, "enable_timestamp"),
        (DoubaoTTS, "disable_markdown_filter"),
        (MusicGen, "force_instrumental"),
        (TTSSelector, "timestamps"),
        (DashscopeImage, "prompt_extend"),
        (DashscopeImage, "watermark"),
        (SeedanceArkVideo, "generate_audio"),
        (SeedanceArkVideo, "watermark"),
        (SeedanceArkVideo, "return_last_frame"),
        (SeedanceArkVideo, "web_search"),
        (VeoVideo, "generate_audio"),
        (VeoVideo, "auto_fix"),
    ],
)
def test_provider_declared_boolean_is_rejected_before_external_work(monkeypatch, tool, field):
    instance = tool()
    monkeypatch.setattr(instance, "check_dependencies", lambda: pytest.fail("dependency check must not run"), raising=False)
    result = instance.execute({"prompt": "a precise request", field: "false"})
    assert not result.success
    assert f"{field} must be boolean" in (result.error or "")


def test_structured_character_reviewer_retains_its_strict_qa_report_contract():
    result = CharacterAnimationReviewer().execute(
        {
            "review_level": "final",
            "browser_preview_checked": "false",
            "frame_samples_checked": True,
        }
    )
    assert result.success is True
    report = result.data["character_qa_report"]
    assert report["status"] == "revise"
    assert "browser_preview_checked must be boolean" in report["issues"]
