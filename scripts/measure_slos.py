"""Measure the offline release-candidate SLO baseline.

Usage from the repository root::

    python scripts/measure_slos.py

The command is intentionally local-only. It creates a disposable temporary
project, uses fake/local providers, and prints JSON suitable for attaching to a
release evidence record. It never loads a provider health probe or spends API
credits.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# A direct ``python scripts/measure_slos.py`` invocation puts scripts/ (rather
# than the checkout root) first on sys.path. Add the root explicitly so the
# same command behaves consistently in shells and CI.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backlot import server as server_mod  # noqa: E402
from lib.checkpoint import init_project  # noqa: E402
from lib.pipeline_loader import load_pipeline_readonly  # noqa: E402
from lib.providers.preflight import fast_preflight  # noqa: E402
from lib.slo import evaluate_threshold, load_slo_config, measure_callable  # noqa: E402
from lib.work_order import (  # noqa: E402
    build_work_order,
    cancel_work_order,
    claim_work_order,
    restart_work_order,
    write_work_order,
)
from tools.base_tool import ToolRuntime  # noqa: E402
from tools.tool_registry import registry  # noqa: E402
from tools.video.video_compose import VideoCompose  # noqa: E402


class _FakeLocalTool:
    name = "slo_fake_local"
    provider = "fake"
    capability = "video_post"
    runtime = ToolRuntime.LOCAL
    dependencies = ("cmd:python",)

    def get_status(self):  # pragma: no cover - fast_preflight must not call it
        raise AssertionError("fast preflight must not call get_status")


def _gate(config: dict, gate_id: str) -> dict:
    return next(item for item in config["performance_gates"] if item["id"] == gate_id)


def _result(config: dict, gate_id: str, observations: list[float]) -> dict:
    gate = _gate(config, gate_id)
    target = gate["target"]
    return {
        "gate": gate_id,
        "indicator": gate["indicator"],
        "target": target,
        **evaluate_threshold(observations, target=float(target["value"]), operator=str(target["operator"])),
    }


def _restart_fixture(root: Path) -> Path:
    project = init_project(
        "slo-restart",
        title="SLO restart fixture",
        pipeline_type="screen-demo",
        run_id="12345678-1234-4234-8234-123456789abc",
        pipeline_dir=root,
    )
    manifest_path = REPO_ROOT / "pipeline_defs" / "screen-demo.yaml"
    manifest = load_pipeline_readonly("screen-demo", defs_dir=manifest_path.parent)
    order = build_work_order(
        project_id="slo-restart",
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


def _render_fixture(root: Path, sample_count: int) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return {
            "gate": "PERF-06",
            "status": "SKIPPED",
            "reason": "ffmpeg and ffprobe are required",
        }
    source = root / "source.mp4"
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
    output_index = iter(range(sample_count))

    def render_once() -> None:
        output = root / f"render-{next(output_index)}.mp4"
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
        if not result.success or not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError(result.error or "local render did not produce an output")
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(probe.stdout)
        if float(payload["format"]["duration"]) < 0.7:
            raise RuntimeError("rendered fixture duration is shorter than the acceptance floor")

    measured = measure_callable(render_once, samples=sample_count, warmups=0)
    return _result(load_slo_config(), "PERF-06", measured["samples_seconds"])


def _observed_environment() -> dict[str, str | None]:
    """Capture versions beside the SLO result without exposing credentials."""

    def version(command: list[str]) -> str | None:
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        line = (completed.stdout or completed.stderr).splitlines()
        return line[0].strip()[:160] if line else None

    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "node": version(["node", "--version"]),
        "ffmpeg": version(["ffmpeg", "-version"]),
        "ffprobe": version(["ffprobe", "-version"]),
    }


def run() -> dict:
    config = load_slo_config()
    sample_count = int(config["reference_environment"]["measurement"]["samples"])
    warmups = int(config["reference_environment"]["measurement"]["warmups"])
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="openmontage-slo-") as raw_root:
        root = Path(raw_root)
        results: list[dict] = []

        registry.ensure_discovered()
        menu = measure_callable(registry.provider_menu_summary, samples=sample_count, warmups=warmups)
        results.append(_result(config, "PERF-01", menu["samples_seconds"]))

        preflight = measure_callable(
            lambda: fast_preflight([_FakeLocalTool()]), samples=sample_count, warmups=warmups
        )
        results.append(_result(config, "PERF-02", preflight["samples_seconds"]))

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
            lambda: server_mod._validate_create_request(request), samples=sample_count, warmups=warmups
        )
        results.append(_result(config, "PERF-03", validation["samples_seconds"]))

        projects = root / "projects"
        projects.mkdir()
        old_projects = server_mod.PROJECTS_DIR
        old_root_str = server_mod._PROJECTS_ROOT_STR
        server_mod.PROJECTS_DIR = projects
        server_mod._PROJECTS_ROOT_STR = str(projects.resolve()).lower()
        try:
            async def no_watch() -> None:
                return None

            old_watch = server_mod._watch_projects
            server_mod._watch_projects = no_watch
            try:
                with TestClient(server_mod.create_app()) as client:
                    created = client.post(
                        "/api/project/create",
                        json={"title": "SLO Backlot fixture", "pipeline_type": "screen-demo", "target_duration_seconds": 30},
                    )
                    created.raise_for_status()
                    project_id = created.json()["project_id"]
                    state = measure_callable(
                        lambda: client.get(f"/api/project/{project_id}/state").raise_for_status(),
                        samples=sample_count,
                        warmups=warmups,
                    )
                    results.append(_result(config, "PERF-05", state["samples_seconds"]))

                    first = client.post(f"/api/project/{project_id}/run?agent_id=slo-agent")
                    first.raise_for_status()
                    duplicate = measure_callable(
                        lambda: client.post(f"/api/project/{project_id}/run?agent_id=slo-agent").raise_for_status(),
                        samples=sample_count,
                        warmups=warmups,
                    )
                    results.append(_result(config, "PERF-04", duplicate["samples_seconds"]))
            finally:
                server_mod._watch_projects = old_watch
        finally:
            server_mod.PROJECTS_DIR = old_projects
            server_mod._PROJECTS_ROOT_STR = old_root_str

        results.append(_render_fixture(root, sample_count))

        project = _restart_fixture(root)
        claim_work_order(project, "slo-agent", lease_seconds=60)
        observations: list[float] = []
        for index in range(sample_count):
            cancel_work_order(project, "slo-agent", reason=f"baseline-{index}")
            tick = time.perf_counter()
            restarted = restart_work_order(project, "slo-agent", reason=f"baseline-{index}")
            observations.append(time.perf_counter() - tick)
            if restarted["status"] != "queued":
                raise RuntimeError("restart did not return a queued resumable work order")
            if index + 1 < sample_count:
                claim_work_order(project, "slo-agent", lease_seconds=60)
        results.append(_result(config, "PERF-07", observations))

    return {
        "schema_version": config["version"],
        "reference_environment": config["reference_environment"],
        "observed_environment": _observed_environment(),
        "measurement_scope": "offline_local_baseline",
        "results": results,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure the offline OpenMontage SLO baseline")
    parser.add_argument("--output", type=Path, help="write the JSON evidence to this path")
    args = parser.parse_args()
    encoded = json.dumps(run(), indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
