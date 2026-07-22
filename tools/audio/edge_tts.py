"""Microsoft Edge Neural Text-to-Speech provider tool.

Provides zero-key high-quality neural voices (e.g. en-US-ChristopherNeural, en-US-AriaNeural, en-US-GuyNeural)
free of charge for fast and natural voice generation.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class EdgeTTS(BaseTool):
    name = "edge_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "edge_tts"
    stability = ToolStability.PRODUCTION
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.HYBRID

    dependencies = ["python:edge_tts"]
    install_instructions = "Install edge-tts:\n  pip install edge-tts"
    agent_skills = ["text-to-speech"]

    capabilities = [
        "text_to_speech",
        "voice_selection",
        "multilingual",
    ]
    supports = {
        "voice_cloning": False,
        "multilingual": True,
        "offline": False,
        "native_audio": True,
    }
    best_for = [
        "zero-key free production TTS with natural neural voices",
        "fast narration generation without rate limits or credits",
        "multilingual speech synthesis",
    ]
    not_good_for = [
        "voice cloning",
        "offline generation without internet access",
    ]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "Text to convert to speech"},
            "voice": {
                "type": "string",
                "default": "en-US-ChristopherNeural",
                "description": "Neural voice name. Options: en-US-ChristopherNeural, en-US-AriaNeural, en-US-GuyNeural, en-US-JennyNeural, etc.",
            },
            "output_path": {"type": "string", "description": "Output path for generated MP3 audio file"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["network", "timeout"])
    idempotency_key_fields = ["text", "voice"]
    side_effects = ["writes audio file to output_path"]
    user_visible_verification = ["Listen to generated audio for natural neural speech quality"]

    def get_status(self) -> ToolStatus:
        try:
            import edge_tts  # noqa: F401
            return ToolStatus.AVAILABLE
        except ImportError:
            return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(
                success=False,
                error="edge-tts Python package is not installed. " + self.install_instructions,
            )

        start = time.time()
        try:
            result = self._generate(inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"EdgeTTS generation failed: {exc}")

        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = 0.0
        return result

    def _generate(self, inputs: dict[str, Any]) -> ToolResult:
        import edge_tts

        text = inputs["text"]
        voice = inputs.get("voice") or inputs.get("voice_id") or "en-US-ChristopherNeural"
        output_path = Path(inputs.get("output_path", "tts_output.mp3"))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))

        asyncio.run(_run())

        if not output_path.exists() or output_path.stat().st_size == 0:
            return ToolResult(success=False, error=f"EdgeTTS output file missing or empty: {output_path}")

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "voice": voice,
                "text_length": len(text),
                "output": str(output_path),
                "audio_path": str(output_path),
                "format": "mp3",
                "file_size_bytes": output_path.stat().st_size,
            },
            artifacts=[str(output_path)],
            model=f"edge-tts/{voice}",
        )
