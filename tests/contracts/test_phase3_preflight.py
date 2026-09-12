"""PR-307 fast preflight and bounded deep diagnostics contracts."""

from __future__ import annotations

import time
from pathlib import Path

from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolStatus
from lib.providers.preflight import PreflightStatus, deep_preflight, fast_preflight


class _LocalTool(BaseTool):
    name = "local_fixture"
    provider = "fixture"
    capability = "image_generation"
    runtime = ToolRuntime.LOCAL
    dependencies = []
    status_calls = 0

    def get_status(self):
        type(self).status_calls += 1
        return ToolStatus.AVAILABLE

    def execute(self, inputs):
        return ToolResult(success=True, data=dict(inputs))


class _ApiTool(BaseTool):
    name = "api_fixture"
    provider = "fixture-api"
    capability = "tts"
    runtime = ToolRuntime.API
    dependencies = ["env:PHASE3_MISSING_KEY"]

    def get_status(self):
        raise AssertionError("fast preflight must never call get_status")

    def execute(self, inputs):
        return ToolResult(success=True, data=dict(inputs))


class _UndeclaredGpuTool(BaseTool):
    name = "undeclared_gpu_fixture"
    provider = "fixture-gpu"
    capability = "video_generation"
    runtime = ToolRuntime.LOCAL_GPU
    dependencies = []

    def execute(self, inputs):
        return ToolResult(success=True, data=dict(inputs))


class _EnabledGpuTool(BaseTool):
    name = "enabled_gpu_fixture"
    provider = "fixture-enabled-gpu"
    capability = "video_generation"
    runtime = ToolRuntime.LOCAL_GPU
    dependencies = ["env-enabled:PHASE3_GPU_ENABLED", "python:json"]

    def execute(self, inputs):
        return ToolResult(success=True, data=dict(inputs))


class _HangingTool(BaseTool):
    name = "hanging_fixture"
    provider = "fixture-api"
    capability = "video_generation"
    runtime = ToolRuntime.API
    dependencies = []

    def get_status(self):
        time.sleep(0.3)
        return ToolStatus.AVAILABLE

    def execute(self, inputs):
        return ToolResult(success=True, data=dict(inputs))


def test_fast_preflight_is_local_only_and_cacheable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PHASE3_MISSING_KEY", raising=False)
    local = _LocalTool()
    api = _ApiTool()
    cache = tmp_path / "preflight.json"

    first = fast_preflight([local, api], cache_path=cache, now=100.0)
    assert first["mode"] == "fast"
    assert first["cached"] is False
    by_name = {item["tool"]: item for item in first["records"]}
    assert by_name["local_fixture"]["status"] == PreflightStatus.AVAILABLE_LOCAL.value
    assert by_name["api_fixture"]["status"] == PreflightStatus.UNAVAILABLE.value
    assert _LocalTool.status_calls == 0

    second = fast_preflight([local, api], cache_path=cache, now=101.0)
    assert second["cached"] is True
    assert all(item["cached"] for item in second["records"])
    assert _LocalTool.status_calls == 0


def test_fast_preflight_invalidates_when_configuration_changes(tmp_path: Path, monkeypatch) -> None:
    api = _ApiTool()
    cache = tmp_path / "preflight.json"
    monkeypatch.delenv("PHASE3_MISSING_KEY", raising=False)
    first = fast_preflight([api], cache_path=cache, now=100.0)
    monkeypatch.setenv("PHASE3_MISSING_KEY", "configured")
    second = fast_preflight([api], cache_path=cache, now=101.0)
    assert first["cached"] is False
    assert second["cached"] is False
    assert second["records"][0]["status"] == PreflightStatus.REQUIRES_LIVE_PROBE.value


def test_gpu_preflight_requires_declared_dependencies(tmp_path: Path) -> None:
    report = fast_preflight(
        [_UndeclaredGpuTool()],
        cache_path=tmp_path / "preflight.json",
    )

    record = report["records"][0]
    assert record["status"] == PreflightStatus.UNTESTED.value
    assert "readiness has no static dependency declaration" in record["reasons"][0]


def test_enabled_local_gpu_preflight_matches_execution_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tool = _EnabledGpuTool()
    cache = tmp_path / "preflight.json"

    monkeypatch.setenv("PHASE3_GPU_ENABLED", "false")
    disabled = fast_preflight([tool], cache_path=cache)
    assert disabled["records"][0]["status"] == PreflightStatus.UNAVAILABLE.value
    assert tool.get_status() == ToolStatus.UNAVAILABLE

    monkeypatch.setenv("PHASE3_GPU_ENABLED", "YeS")
    enabled = fast_preflight([tool], cache_path=cache)
    assert enabled["records"][0]["status"] == PreflightStatus.AVAILABLE_LOCAL.value
    assert tool.get_status() == ToolStatus.AVAILABLE


def test_deep_preflight_timeout_is_bounded_and_explicit() -> None:
    started = time.monotonic()
    report = deep_preflight([_HangingTool()], timeout_seconds=0.02)
    elapsed = time.monotonic() - started
    assert elapsed < 0.15
    record = report["records"][0]
    assert record["live_probe"] is True
    assert record["status"] == PreflightStatus.UNAVAILABLE.value
    assert "timed out" in record["reasons"][0]
