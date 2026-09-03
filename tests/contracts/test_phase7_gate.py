"""Phase 7 HyperFrames production-hardening contracts.

The live HyperFrames npm package is intentionally not required for this gate;
these tests exercise the deterministic OpenMontage adapter boundary and use a
fake CLI only for ordering/argument assertions.  The opt-in QA suite remains
responsible for the real browser/runtime render once the environment blocker
is cleared.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lib.hyperframes_contracts import (
    HyperFramesContractError,
    build_edit_mapping,
    select_worker_policy,
    workspace_digest,
)
from tools.base_tool import ToolResult
from tools.video.hyperframes_compose import HyperFramesCompose


def _edit_with_audio() -> dict:
    return {
        "version": "1.0",
        "render_runtime": "hyperframes",
        "renderer_family": "animation-first",
        "cuts": [
            {
                "id": "hero",
                "source": "hero.png",
                "type": "image",
                "in_seconds": 0,
                "out_seconds": 4,
                "animation": "ken-burns",
                "keyframes": 3,
                "transition_in": "fade",
                "transition_out": "dissolve",
                "transition_duration": 0.4,
            },
            {
                "id": "title",
                "source": "",
                "type": "text_card",
                "text": "Phase 7",
                "in_seconds": 4,
                "out_seconds": 8,
            },
        ],
        "audio": {
            "narration": {
                "segments": [
                    {
                        "asset_id": "voice",
                        "start_seconds": 1.25,
                        "end_seconds": 3.5,
                        "offset_seconds": 0.4,
                    }
                ]
            },
            "music": {
                "asset_id": "music",
                "offset_seconds": 2.0,
                "volume": 0.25,
                "fade_in_seconds": 0.5,
                "fade_out_seconds": 0.75,
                "loop": True,
                "ducking": {
                    "enabled": True,
                    "reduction_db": -12,
                    "attack_ms": 100,
                    "release_ms": 300,
                },
            },
            "sfx": [{"asset_id": "sfx", "start_seconds": 4.0, "volume": 0.4}],
        },
    }


def test_phase7_mapping_preserves_timing_audio_and_stems():
    mapping = build_edit_mapping(
        _edit_with_audio(),
        {
            "assets": [
                {"id": "hero.png", "path": "hero.png"},
                {"id": "voice", "path": "voice.wav"},
                {"id": "music", "path": "music.mp3"},
                {"id": "sfx", "path": "click.wav"},
            ]
        },
    )
    assert mapping["duration_seconds"] == pytest.approx(8)
    assert mapping["cuts"][0]["start_seconds"] == pytest.approx(0)
    assert mapping["cuts"][0]["duration_seconds"] == pytest.approx(4)
    audio = mapping["audio"]
    assert audio["narration"][0]["offset_seconds"] == pytest.approx(0.4)
    assert audio["music"]["offset_seconds"] == pytest.approx(2)
    assert audio["music"]["fade_in_seconds"] == pytest.approx(0.5)
    assert audio["music"]["fade_out_seconds"] == pytest.approx(0.75)
    assert audio["music"]["ducking"]["reduction_db"] == pytest.approx(-12)
    assert audio["stems"] == {"narration": ["narration-0"], "music": ["music"], "sfx": ["sfx-0"]}


def test_phase7_unsupported_cut_fails_closed():
    edit = _edit_with_audio()
    edit["cuts"][0]["type"] = "magic_unknown_renderer"
    with pytest.raises(HyperFramesContractError, match="unsupported shape/type"):
        build_edit_mapping(edit, {"assets": []})


def test_phase7_worker_policy_is_conservative_for_video():
    mapping = build_edit_mapping(
        {
            "version": "1.0",
            "render_runtime": "hyperframes",
            "cuts": [{"id": "v", "source": "clip.mp4", "type": "video", "in_seconds": 0, "out_seconds": 45}],
        },
        {"assets": []},
    )
    policy = select_worker_policy(mapping, requested_workers=4, cpu_count=16)
    assert policy["video_heavy"] is True
    assert policy["workers"] == 1
    assert policy["capped"] is True


def test_phase7_production_scaffold_is_local_hashed_and_inspectable(tmp_path: Path):
    source = tmp_path / "hero.png"
    try:
        from PIL import Image

        Image.new("RGB", (32, 32), (20, 40, 60)).save(source)
    except ImportError:  # pragma: no cover - bundled test runtime has Pillow
        source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 256)
    workspace = tmp_path / "hyperframes"
    result = HyperFramesCompose().execute(
        {
            "operation": "scaffold_workspace",
            "workspace_path": str(workspace),
            "production_mode": True,
            "edit_decisions": {
                "version": "1.0",
                "render_runtime": "hyperframes",
                "renderer_family": "animation-first",
                "cuts": [
                    {
                        "id": "hero",
                        "source": "hero_asset",
                        "type": "image",
                        "in_seconds": 0,
                        "out_seconds": 3,
                        "animation": "ken-burns",
                        "keyframes": 3,
                    },
                    {"id": "title", "source": "", "type": "text_card", "text": "Ready", "in_seconds": 3, "out_seconds": 5},
                ],
            },
            "asset_manifest": {"assets": [{"id": "hero_asset", "path": str(source)}]},
        }
    )
    assert result.success, result.error
    html = (workspace / "index.html").read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in html
    assert 'src="vendor/gsap.min.js"' in html
    assert 'data-fps="30"' in html
    assert (workspace / "vendor" / "gsap.min.js").is_file()
    assert (workspace / "index.motion.json").is_file()
    assert (workspace / "audio_plan.json").is_file()
    assert (workspace / "EDIT_MAPPING.json").is_file()
    staged_names = [path.name for path in (workspace / "assets").iterdir()]
    assert staged_names and staged_names[0] != "hero.png"
    mapping = json.loads((workspace / "EDIT_MAPPING.json").read_text(encoding="utf-8"))
    assert mapping["cuts"][0]["keyframe_count"] == 3


def test_phase7_production_render_orders_inspect_and_caps_workers(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "hf"
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"placeholder-video")
    calls: list[str] = []
    tool = HyperFramesCompose()
    monkeypatch.setattr(
        tool,
        "_runtime_check",
        lambda: {
            "runtime_available": True,
            "npm_package": "hyperframes",
            "npm_package_version": "0.4.5",
            "reasons": [],
        },
    )

    def fake_cli(args, *, cwd, timeout, check):
        calls.append(args[0])
        if args[0] == "render":
            output = Path(args[args.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"rendered")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{\"issues\": []}", stderr="")

    monkeypatch.setattr(tool, "_run_hf", fake_cli)
    result = tool.execute(
        {
            "operation": "render",
            "workspace_path": str(workspace),
            "output_path": str(tmp_path / "out.mp4"),
            "production_mode": True,
            "workers": 4,
            "edit_decisions": {
                "version": "1.0",
                "render_runtime": "hyperframes",
                "renderer_family": "animation-first",
                "cuts": [{"id": "v", "source": "clip.mp4", "type": "video", "in_seconds": 0, "out_seconds": 45}],
            },
            "asset_manifest": {"assets": [{"id": "clip.mp4", "path": str(video)}]},
        }
    )
    assert result.success, result.error
    assert calls[:4] == ["lint", "validate", "inspect", "render"]
    assert result.data["worker_policy"]["workers"] == 1
    assert result.data["render_report"]["steps"] == ["scaffold", "lint", "validate", "inspect", "render"]


def test_phase7_run_workspace_rejects_shared_path(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    run_id = "6f34a1e2-cf7d-4c02-bd1a-6ec07eac13fa"
    with pytest.raises(ValueError, match="inside the run work envelope"):
        HyperFramesCompose()._resolve_workspace(
            {
                "project_dir": str(project),
                "run_id": run_id,
                "workspace_path": str(tmp_path / "shared-hyperframes"),
                "production_mode": True,
            }
        )


def test_phase7_scaffold_digest_is_repeatable_for_golden_inputs(tmp_path: Path):
    """The same canonical edit must produce the same workspace digest."""
    source = tmp_path / "hero.png"
    source.write_bytes(b"golden-image-bytes")
    edit = {
        "version": "1.0",
        "render_runtime": "hyperframes",
        "renderer_family": "animation-first",
        "cuts": [{"id": "hero", "source": "hero", "type": "image", "in_seconds": 0, "out_seconds": 2}],
    }
    manifest = {"assets": [{"id": "hero", "path": str(source)}]}
    digests = []
    for name in ("golden-a", "golden-b"):
        result = HyperFramesCompose().execute(
            {
                "operation": "scaffold_workspace",
                "workspace_path": str(tmp_path / name),
                "production_mode": True,
                "edit_decisions": edit,
                "asset_manifest": manifest,
            }
        )
        assert result.success, result.error
        digests.append(workspace_digest(tmp_path / name))
    assert digests[0] == digests[1]


def test_phase7_offline_mode_never_queries_npm(monkeypatch):
    tool = HyperFramesCompose()
    tool._offline_mode = True
    monkeypatch.setattr(tool, "_node_major_version", lambda: 22)
    monkeypatch.setattr("shutil.which", lambda name: "C:/bin/" + name)
    monkeypatch.setattr(
        HyperFramesCompose,
        "_offline_package_check",
        classmethod(lambda cls: {"version": "local-cache"}),
    )
    monkeypatch.setattr(
        HyperFramesCompose,
        "_resolve_npm_package",
        classmethod(lambda cls: (_ for _ in ()).throw(AssertionError("npm queried in offline mode"))),
    )
    check = tool._runtime_check()
    assert check["runtime_available"] is True
    assert check["offline"] is True
    assert check["npm_package_version"] == "local-cache"


def test_phase7_doctor_uses_required_checks_not_process_exit(monkeypatch):
    """The upstream doctor exits zero even when optional integrations are absent."""
    tool = HyperFramesCompose()
    monkeypatch.setattr(
        tool,
        "_runtime_check",
        lambda: {"runtime_available": True, "reasons": []},
    )
    payload = {
        "ok": False,
        "checks": [
            {"name": "Node.js", "ok": True},
            {"name": "Chrome", "ok": True},
            {"name": "TTS (Kokoro)", "ok": False, "detail": "Not installed (optional)"},
            {"name": "Docker", "ok": False, "detail": "Not found"},
        ],
        "_meta": {"version": "0.8.25"},
    }
    monkeypatch.setattr(
        tool,
        "_run_hf",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    result = tool.execute({"operation": "doctor"})
    assert result.success, result.error
    doctor = result.data["cli_doctor"]
    assert doctor["json"] is True
    assert doctor["package_version"] == "0.8.25"
    assert [item["name"] for item in doctor["optional_failures"]] == ["TTS (Kokoro)", "Docker"]
    assert doctor["required_failures"] == []


def test_phase7_doctor_fails_required_check(monkeypatch):
    tool = HyperFramesCompose()
    monkeypatch.setattr(
        tool,
        "_runtime_check",
        lambda: {"runtime_available": True, "reasons": []},
    )
    payload = {
        "ok": False,
        "checks": [{"name": "Chrome", "ok": False, "detail": "Not found"}],
    }
    monkeypatch.setattr(
        tool,
        "_run_hf",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    result = tool.execute({"operation": "doctor"})
    assert not result.success
    assert "Chrome" in (result.error or "")
