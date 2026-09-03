"""PR-1007 release SLO definitions and offline baseline measurements."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from lib.checkpoint import init_project
from lib.pipeline_loader import load_pipeline_readonly
from lib.providers.preflight import fast_preflight
from lib.slo import (
    evaluate_ratio,
    evaluate_threshold,
    load_slo_config,
    measure_callable,
    percentile,
    summarize_samples,
)
from lib.work_order import (
    build_work_order,
    cancel_work_order,
    claim_work_order,
    restart_work_order,
    write_work_order,
)
from tools.base_tool import ToolRuntime
from tools.tool_registry import registry
from tools.video.video_compose import VideoCompose


REPO_ROOT = Path(__file__).resolve().parents[2]


def _gate(config: dict, gate_id: str) -> dict:
    return next(item for item in config["performance_gates"] if item["id"] == gate_id)


def _assert_gate(result: dict, gate: dict) -> None:
    target = gate["target"]
    assert result["status"] == "PASS", f"{gate['id']} baseline failed: {result}"
    assert result["count"] >= 1
    assert result["p95"] <= float(target["value"])


def test_slo_contract_covers_required_performance_and_operational_dimensions():
    config = load_slo_config()
    gate_ids = {item["id"] for item in config["performance_gates"]}
    assert gate_ids == {f"PERF-{index:02d}" for index in range(1, 11)}
    assert {
        "preflight",
        "queue_start",
        "availability",
        "render",
        "failure_recovery",
        "scalability",
    } <= {item["category"] for item in config["performance_gates"]}
    assert {item["id"] for item in config["service_level_objectives"]} >= {
        "SLO-AVAILABILITY",
        "SLO-QUEUE-START",
        "SLO-FAILURE-RECOVERY",
        "SLO-QUALITY-ESCAPE",
    }
    assert config["reference_environment"]["measurement"]["samples"] >= 11
    assert config["reference_environment"]["measurement"]["percentile"] == "p95"

    docs = (REPO_ROOT / "docs" / "operations" / "SLOs.md").read_text(encoding="utf-8")
    for gate_id in gate_ids:
        assert gate_id in docs
    for phrase in ("Reference measurement", "Error budget", "missing sample", "PR-11G"):
        assert phrase in docs
    observability = (REPO_ROOT / "config" / "observability.yaml").read_text(encoding="utf-8")
    assert "slo_contract: config/slo.yaml" in observability
    assert (REPO_ROOT / "scripts" / "measure_slos.py").is_file()


def test_slo_math_matches_bounded_metrics_convention():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0.95) == 4.0
    summary = summarize_samples(values)
    assert summary["count"] == 4
    assert summary["p95"] == 4.0
    assert evaluate_threshold(values, target=4.0) ["status"] == "PASS"
    assert evaluate_threshold(values, target=3.0)["status"] == "FAIL"
    assert evaluate_ratio(199, 200, target=0.995)["status"] == "PASS"
    assert evaluate_ratio(198, 200, target=0.995)["status"] == "FAIL"


class _FakeLocalTool:
    name = "slo_fake_local"
    provider = "fake"
    capability = "video_post"
    runtime = ToolRuntime.LOCAL
    dependencies = ("cmd:python",)

    def get_status(self):  # pragma: no cover - must never run in fast mode
        raise AssertionError("fast preflight must not call get_status")


def test_perf_01_warm_provider_menu_and_perf_02_cold_preflight():
    config = load_slo_config()

    registry.ensure_discovered()
    warm_menu = measure_callable(
        registry.provider_menu_summary,
        samples=int(config["reference_environment"]["measurement"]["samples"]),
        warmups=int(config["reference_environment"]["measurement"]["warmups"]),
    )
    _assert_gate(evaluate_threshold(warm_menu["samples_seconds"], target=_gate(config, "PERF-01")["target"]["value"]), _gate(config, "PERF-01"))
    assert warm_menu["summary"]["count"] == config["reference_environment"]["measurement"]["samples"]

    preflight = measure_callable(
        lambda: fast_preflight([_FakeLocalTool()]),
        samples=int(config["reference_environment"]["measurement"]["samples"]),
        warmups=int(config["reference_environment"]["measurement"]["warmups"]),
    )
    _assert_gate(evaluate_threshold(preflight["samples_seconds"], target=_gate(config, "PERF-02")["target"]["value"]), _gate(config, "PERF-02"))


def test_perf_03_create_validation_and_perf_05_state_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = load_slo_config()
    request = server_mod.CreateProjectRequest(
        title="SLO validation fixture",
        topic_prompt="A deterministic offline baseline",
        pipeline_type="screen-demo",
        voice="en-US-ChristopherNeural",
        voice_provider="edge_tts",
        render_runtime="remotion",
        output_profile="youtube_landscape",
        target_duration_seconds=30,
    )
    validation = measure_callable(
        lambda: server_mod._validate_create_request(request),
        samples=int(config["reference_environment"]["measurement"]["samples"]),
        warmups=int(config["reference_environment"]["measurement"]["warmups"]),
    )
    _assert_gate(evaluate_threshold(validation["samples_seconds"], target=_gate(config, "PERF-03")["target"]["value"]), _gate(config, "PERF-03"))

    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", str(projects.resolve()).lower())

    async def no_watch() -> None:
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as client:
        created = client.post(
            "/api/project/create",
            json={
                "title": "SLO state fixture",
                "pipeline_type": "screen-demo",
                "target_duration_seconds": 30,
            },
        )
        assert created.status_code == 200, created.text
        project_id = created.json()["project_id"]
        state = measure_callable(
            lambda: client.get(f"/api/project/{project_id}/state").raise_for_status(),
            samples=int(config["reference_environment"]["measurement"]["samples"]),
            warmups=int(config["reference_environment"]["measurement"]["warmups"]),
        )
    _assert_gate(evaluate_threshold(state["samples_seconds"], target=_gate(config, "PERF-05")["target"]["value"]), _gate(config, "PERF-05"))


def test_perf_04_duplicate_run_is_fast_and_does_not_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = load_slo_config()
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", str(projects.resolve()).lower())

    async def no_watch() -> None:
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as client:
        created = client.post(
            "/api/project/create",
            json={
                "title": "SLO duplicate run fixture",
                "pipeline_type": "screen-demo",
                "target_duration_seconds": 30,
            },
        )
        assert created.status_code == 200, created.text
        project_id = created.json()["project_id"]
        first = client.post(f"/api/project/{project_id}/run?agent_id=slo-agent")
        assert first.status_code == 200, first.text
        run_id = first.json()["work_order"]["run_id"]
        duplicate = measure_callable(
            lambda: client.post(f"/api/project/{project_id}/run?agent_id=slo-agent").raise_for_status(),
            samples=int(config["reference_environment"]["measurement"]["samples"]),
            warmups=int(config["reference_environment"]["measurement"]["warmups"]),
        )
        assert duplicate["summary"]["count"] == config["reference_environment"]["measurement"]["samples"]
        replay = client.post(f"/api/project/{project_id}/run?agent_id=slo-agent")
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["work_order"]["run_id"] == run_id
    _assert_gate(evaluate_threshold(duplicate["samples_seconds"], target=_gate(config, "PERF-04")["target"]["value"]), _gate(config, "PERF-04"))


def _restart_fixture(root: Path, project_id: str) -> Path:
    project = init_project(
        project_id,
        title="SLO restart fixture",
        pipeline_type="screen-demo",
        run_id="12345678-1234-4234-8234-123456789abc",
        pipeline_dir=root,
    )
    manifest_path = REPO_ROOT / "pipeline_defs" / "screen-demo.yaml"
    manifest = load_pipeline_readonly("screen-demo", defs_dir=manifest_path.parent)
    order = build_work_order(
        project_id=project_id,
        title="SLO restart fixture",
        topic_prompt="offline restart baseline",
        target_duration_seconds=10,
        pipeline_type="screen-demo",
        manifest=manifest,
        manifest_path=manifest_path,
        selections={
            "playbook": "premium-minimalist",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "remotion",
            "output_profile": "youtube_landscape",
            "aspect_ratio": "16:9",
            "source_mode": "synthetic_terminal",
        },
        run_id="12345678-1234-4234-8234-123456789abc",
    )
    write_work_order(project, order)
    return project


def test_perf_06_local_render_and_perf_07_restart_resume(tmp_path: Path):
    config = load_slo_config()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and ffprobe are required for the local render SLO")

    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x180:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    outputs = iter(tmp_path / f"render-{index}.mp4" for index in range(11))

    def render_once() -> None:
        output = next(outputs)
        result = VideoCompose().execute(
            {
                "operation": "compose",
                "edit_decisions": {
                    "render_runtime": "ffmpeg",
                    "cuts": [{"source": str(source), "in_seconds": 0, "out_seconds": 1.0, "speed": 1.0}],
                    "subtitles": {"enabled": False},
                },
                "output_path": str(output),
                "expected_duration_seconds": 1.0,
                "profile": "youtube_landscape",
            }
        )
        assert result.success, result.error
        assert output.is_file() and output.stat().st_size > 0

    # Render uses a one-second fixture, therefore wall seconds equal the
    # wall-seconds-per-output-second indicator.
    render = measure_callable(
        render_once,
        samples=int(config["reference_environment"]["measurement"]["samples"]),
        warmups=0,
    )
    _assert_gate(evaluate_threshold(render["samples_seconds"], target=_gate(config, "PERF-06")["target"]["value"]), _gate(config, "PERF-06"))
    for path in tmp_path.glob("render-*.mp4"):
        path.unlink(missing_ok=True)

    project = _restart_fixture(tmp_path, "slo-restart")
    claim_work_order(project, "slo-agent", lease_seconds=60)
    restart_samples: list[float] = []
    for index in range(int(config["reference_environment"]["measurement"]["samples"])):
        cancel_work_order(project, "slo-agent", reason=f"baseline-{index}")
        started = time.perf_counter()
        restarted = restart_work_order(project, "slo-agent", reason=f"baseline-{index}")
        restart_samples.append(time.perf_counter() - started)
        assert restarted["status"] == "queued"
        assert restarted["resume"]["next_action"].startswith("start_stage:")
        if index + 1 < int(config["reference_environment"]["measurement"]["samples"]):
            claim_work_order(project, "slo-agent", lease_seconds=60)
    _assert_gate(evaluate_threshold(restart_samples, target=_gate(config, "PERF-07")["target"]["value"]), _gate(config, "PERF-07"))
