"""PR-3G integration gate checks for the provider kernel."""

from __future__ import annotations

import uuid
from pathlib import Path

from lib.providers.contracts import ProviderRequest, ProviderResultStatus, stable_idempotency_key
from lib.providers.executor import ProviderExecutor
from lib.providers.preflight import PreflightStatus, fast_preflight
from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolStatus


def test_all_capability_selectors_route_provider_calls_through_kernel() -> None:
    selector_sources = {
        "tools/audio/tts_selector.py": "tts",
        "tools/graphics/image_selector.py": "image",
        "tools/video/video_selector.py": "video",
    }
    for relative, _capability in selector_sources.items():
        source = Path(relative).read_text(encoding="utf-8")
        assert "execute_with_provider_executor" in source
        assert "result = tool.execute" not in source


def test_executor_emits_structured_attempt_events() -> None:
    events: list[dict] = []
    payload = {"prompt": "event fixture"}
    request = ProviderRequest(
        capability="image_generation",
        operation="generate",
        provider="fixture",
        payload=payload,
        idempotency_key=stable_idempotency_key(
            provider="fixture", model=None, capability="image_generation", operation="generate", payload=payload
        ),
        approved=True,
    )
    result = ProviderExecutor(event_sink=events.append).execute(request, lambda _request: {"ok": True})
    assert result.status is ProviderResultStatus.SUCCESS
    assert [event["event"] for event in events] == ["start", "attempt_start", "success"]
    assert all(event["idempotency_key"] == request.idempotency_key for event in events)


class _ProductionFixture(BaseTool):
    name = "production_fixture"
    provider = "fixture"
    capability = "tts"
    runtime = ToolRuntime.API
    dependencies = []
    idempotency_key_fields = ["text"]
    calls = 0

    def get_status(self):
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs):
        return 0.01

    def execute(self, inputs):
        type(self).calls += 1
        return ToolResult(success=True, data={"ok": True})


def test_identity_bearing_paid_call_cannot_bypass_approval() -> None:
    from lib.providers.bridge import execute_with_provider_executor

    _ProductionFixture.calls = 0
    result = execute_with_provider_executor(
        _ProductionFixture(),
        {
            "text": "production fixture",
            "project_dir": "projects/gate-fixture",
            "project_id": "gate-fixture",
            "pipeline_type": "screen-demo",
            "run_id": str(uuid.uuid4()),
            "attempt": 1,
            "provider_kernel": True,
            "provider_approved": False,
        },
    )
    assert not result.success
    assert result.data["provider_result"]["status"] == ProviderResultStatus.BLOCKED.value
    assert _ProductionFixture.calls == 0


def test_direct_identity_bearing_provider_is_auto_routed_by_base_tool() -> None:
    _ProductionFixture.calls = 0
    result = _ProductionFixture().execute(
        {
            "text": "direct production fixture",
            "project_dir": "projects/gate-fixture",
            "project_id": "gate-fixture",
            "pipeline_type": "screen-demo",
            "run_id": str(uuid.uuid4()),
            "attempt": 1,
        }
    )
    assert not result.success
    assert "approved" in (result.error or "").lower()
    assert _ProductionFixture.calls == 0


def test_fast_preflight_status_vocabulary_is_complete(tmp_path: Path) -> None:
    report = fast_preflight([], cache_path=tmp_path / "preflight.json")
    assert set(PreflightStatus) == {
        PreflightStatus.CONFIGURED,
        PreflightStatus.AVAILABLE_LOCAL,
        PreflightStatus.DEGRADED,
        PreflightStatus.UNAVAILABLE,
        PreflightStatus.UNTESTED,
        PreflightStatus.REQUIRES_LIVE_PROBE,
    }
    assert report["mode"] == "fast"
