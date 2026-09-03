"""PR-1009 bounded offline load, queue, provider, and SSE soak contracts."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lib.load_harness import (
    SSE_QUEUE_LIMIT,
    run_concurrent_isolation_probe,
    run_provider_throttle_probe,
    run_same_project_queue_probe,
    run_sse_stability_probe,
    run_temporary_cleanup_probe,
)
from lib.hyperframes_contracts import select_worker_policy
from tools.video.video_compose import VideoCompose


def test_perf_08_four_concurrent_local_runs_are_isolated(tmp_path: Path) -> None:
    result = run_concurrent_isolation_probe(tmp_path / "projects", project_count=4, workers=4)

    assert result["status"] == "PASS", result
    assert result["completed"] == 4
    assert len({item["project_id"] for item in result["results"]}) == 4
    assert len({item["run_id"] for item in result["results"]}) == 4
    assert result["contamination"] == []
    # This is a bounded fixture, not a production-memory capacity claim.  It
    # catches accidental unbounded fixture growth while keeping the gate
    # deterministic on the supported developer/CI machines.
    assert result["peak_python_memory_bytes"] < 64 * 1024 * 1024


def test_queue_allows_one_live_owner_and_reports_other_claims_as_conflicts(tmp_path: Path) -> None:
    result = run_same_project_queue_probe(tmp_path / "queue", contenders=8)

    assert result["status"] == "PASS", result
    assert result["winners"] == 1
    assert result["conflicts"] == 7


def test_provider_throttle_spaces_fake_calls_without_network_or_spend() -> None:
    result = run_provider_throttle_probe(calls=6, rate_per_second=4)

    assert result["status"] == "PASS", result
    assert result["minimum_spacing_seconds"] >= 0.25 - 1e-9


def test_perf_09_ten_run_disposable_soak_leaves_no_orphans(tmp_path: Path) -> None:
    result = run_temporary_cleanup_probe(tmp_path / "temporary", iterations=10)

    assert result["status"] == "PASS", result
    assert result["added_orphans"] == []
    assert result["after_orphans"] == result["before_orphans"]


def test_perf_10_sse_connect_disconnect_soak_is_bounded_and_coalescible() -> None:
    result = run_sse_stability_probe(subscriptions=100, burst=200)

    assert result["status"] == "PASS", result
    assert result["max_queue"] <= SSE_QUEUE_LIMIT
    assert result["final_subscribers"] == result["baseline_subscribers"]
    assert result["dropped_burst_events"] > 0


def test_remotion_worker_policy_replaces_fixed_eight_worker_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A long video on the reference profile must use the conservative cap."""
    tool = VideoCompose()
    monkeypatch.setattr(shutil, "which", lambda _name: "npx")
    captured: dict[str, object] = {}

    def fake_run_command(cmd, *args, **kwargs):
        captured["cmd"] = [str(item) for item in cmd]
        captured["timeout"] = kwargs.get("timeout")
        return None

    monkeypatch.setattr(tool, "run_command", fake_run_command)
    result = tool._remotion_render(
        {
            "composition_data": {
                "renderer_family": "explainer-data",
                "cuts": [{"source": "clip.mp4", "type": "video", "in_seconds": 0, "out_seconds": 45}],
            },
            "output_path": str(tmp_path / "out.mp4"),
            "render_concurrency": 8,
        }
    )

    # The fake command intentionally does not create media; this assertion is
    # about the generated command and policy metadata, not a runtime render.
    assert result.success is False
    command = captured["cmd"]
    assert "--concurrency=1" in command
    assert "--concurrency=8" not in command


def test_worker_policy_caps_arbitrary_operator_request() -> None:
    policy = select_worker_policy(
        {"cuts": [{"type": "image", "in_seconds": 0, "out_seconds": 2}], "duration_seconds": 2},
        requested_workers=128,
        cpu_count=64,
    )

    assert policy["workers"] == 4
    assert policy["capped"] is True


def test_high_level_render_forwards_bounded_worker_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = VideoCompose()
    monkeypatch.setattr(tool, "_pre_compose_validation", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool, "_needs_remotion", lambda *args, **kwargs: True)
    captured: dict[str, object] = {}

    def fake_remotion(inputs):
        captured.update(inputs)
        from tools.base_tool import ToolResult

        return ToolResult(success=False, error="probe")

    monkeypatch.setattr(tool, "_remotion_render", fake_remotion)
    tool._render(
        {
            "edit_decisions": {
                "render_runtime": "remotion",
                "renderer_family": "explainer-data",
                "cuts": [{"source": "asset-1", "in_seconds": 0, "out_seconds": 2}],
            },
            "asset_manifest": {"assets": [{"id": "asset-1", "path": "clip.mp4"}]},
            "output_path": str(tmp_path / "out.mp4"),
            "render_concurrency": 2,
        }
    )

    assert captured["render_concurrency"] == 2
