"""PR-309 capability-family migration through the common provider bridge."""

from __future__ import annotations

from pathlib import Path

from lib.providers.bridge import execute_with_provider_executor
from lib.providers.contracts import ProviderResultStatus
from lib.providers.executor import ProviderExecutor
from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolStatus


class _MigratedProvider(BaseTool):
    name = "migrated_fixture"
    provider = "fixture-provider"
    capability = "tts"
    runtime = ToolRuntime.API
    dependencies = []
    idempotency_key_fields = ["text", "output_path"]

    def get_status(self):
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs):
        return 0.02

    def execute(self, inputs):
        if inputs.get("_provider_executor_bypass") is not True:
            raise AssertionError("bridge must call the implementation with its bypass marker")
        output = Path(inputs["output_path"])
        output.write_bytes(b"fixture-audio")
        return ToolResult(
            success=True,
            data={"provider_payload_seen": True},
            artifacts=[str(output)],
            cost_usd=0.015,
            model="fixture-v1",
        )


def test_migrated_provider_returns_common_result_and_persists_cache(tmp_path: Path) -> None:
    provider = _MigratedProvider()
    output = tmp_path / "voice.mp3"
    executor = ProviderExecutor(cache_dir=tmp_path / "cache")
    inputs = {
        "text": "hello",
        "output_path": str(output),
        "provider_kernel": True,
        "provider_approved": True,
        "provider_require_artifacts": True,
        "provider_executor": executor,
    }
    first = execute_with_provider_executor(provider, inputs, executor=executor)
    second = execute_with_provider_executor(provider, inputs, executor=executor)
    assert first.success
    assert first.data["provider_kernel"] == "ProviderExecutor"
    assert first.artifacts == [str(output)]
    assert first.cost_usd == 0.015
    assert second.success
    assert second.data["provider_result"]["status"] == ProviderResultStatus.CACHED.value


def test_migrated_paid_provider_is_blocked_without_explicit_approval(tmp_path: Path) -> None:
    provider = _MigratedProvider()
    called = False

    def implementation(_inputs):
        nonlocal called
        called = True
        return ToolResult(success=True)

    result = execute_with_provider_executor(
        provider,
        {
            "text": "no spend",
            "output_path": str(tmp_path / "blocked.mp3"),
            "provider_kernel": True,
            "provider_approved": False,
        },
        implementation=implementation,
    )
    assert not result.success
    assert "approved" in (result.error or "").lower()
    assert called is False


def test_music_selector_uses_the_same_kernel_boundary(tmp_path: Path, monkeypatch) -> None:
    from tools.audio.music_selector import MusicSelector

    provider = _MigratedProvider()
    provider.capability = "music_generation"
    provider.idempotency_key_fields = ["prompt", "output_path"]
    monkeypatch.setattr(MusicSelector, "_providers", lambda self: [provider])
    output = tmp_path / "music.mp3"
    result = MusicSelector().execute({
        "prompt": "calm instrumental",
        "duration_seconds": 4,
        "output_path": str(output),
        "provider_approved": True,
    })
    assert result.success
    assert result.data["provider_kernel"] == "ProviderExecutor"
    assert result.data["selected_provider"] == provider.provider
