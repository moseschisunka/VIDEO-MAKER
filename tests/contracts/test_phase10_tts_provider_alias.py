"""PR-10G narration-provider identity and execution-boundary regressions."""

from __future__ import annotations

import asyncio

from lib import project_pipeline
from lib.voice_contracts import canonical_voice_provider


def test_voice_provider_aliases_are_canonical_without_fallback() -> None:
    assert canonical_voice_provider("edge") == "edge_tts"
    assert canonical_voice_provider("edge_tts") == "edge_tts"
    assert canonical_voice_provider("microsoft_edge") == "edge_tts"
    assert canonical_voice_provider("open-ai") == "openai"
    assert canonical_voice_provider("piper_tts") == "piper_tts"


def test_legacy_edge_tts_identifier_reaches_narration_boundary(monkeypatch, tmp_path) -> None:
    """The persisted Backlot default must not fail before provider execution."""

    monkeypatch.setattr(project_pipeline, "assemble_audio_segments", lambda segments, output: None)

    result = asyncio.run(
        project_pipeline._generate_narration(
            [],
            "en-US-ChristopherNeural",
            tmp_path,
            [],
            tts_provider="edge_tts",
        )
    )

    assert result == []
