"""PR-1001 installed-package data checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_wheel(wheel_dir: Path) -> Path:
    wheel_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return next(wheel_dir.glob("openmontage-*.whl"))


def _install_conventional_wheel(wheel_path: Path, venv_dir: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    venv_python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            str(wheel_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return venv_python


def _probe_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("OPENMONTAGE_PROJECTS_DIR", None)
    env.pop("OPENMONTAGE_RESOURCE_ROOT", None)
    return env


def _dependency_paths() -> list[str]:
    return [
        entry
        for entry in sys.path
        if Path(entry).name.lower() in {"site-packages", "dist-packages"}
    ]


@pytest.fixture(scope="module")
def release_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_wheel(tmp_path_factory.mktemp("release-wheel"))


def test_release_assets_are_present_in_built_wheel(
    tmp_path: Path, release_wheel: Path
) -> None:
    wheel_path = release_wheel
    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())
    data_root = next(name.split("/", 1)[0] for name in names if ".data/data/config.yaml" in name)
    data_prefix = f"{data_root}/data/"

    expected = {
        "pipeline_defs/screen-demo.yaml",
        "skills/core/remotion.md",
        "styles/ilearnzed-education.yaml",
        "content_templates/ilearnzed-concept-explainer.yaml",
        "profiles/ilearnzed.yaml",
        "schemas/artifacts/brief.schema.json",
        "backlot/ui/index.html",
        "config/pipeline_release_scope.json",
        f"{data_prefix}config.yaml",
        f"{data_prefix}remotion-composer/package.json",
        f"{data_prefix}remotion-composer/package-lock.json",
        f"{data_prefix}remotion-composer/src/Root.tsx",
        f"{data_prefix}remotion-composer/src/components/CaptionOverlay.tsx",
        f"{data_prefix}remotion-composer/public/demo-props/code-to-screen.json",
    }
    assert expected <= names
    # Talking-head footage, downloaded stock, and generated project media are
    # user/runtime inputs.  They are intentionally ignored and must not become
    # accidental release assets merely because a developer has them locally.
    assert not any(
        name.endswith("/remotion-composer/public/talking-head/in.mp4")
        or "/remotion-composer/public/projects/" in name
        or "/remotion-composer/public/demo-props/caption-burn-" in name
        for name in names
    )

    install_dir = tmp_path / "install"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_dir),
            str(wheel_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    installed_expected = (
        "config.yaml",
        "pipeline_defs/screen-demo.yaml",
        "skills/core/remotion.md",
        "styles/ilearnzed-education.yaml",
        "content_templates/ilearnzed-concept-explainer.yaml",
        "profiles/ilearnzed.yaml",
        "schemas/artifacts/brief.schema.json",
        "backlot/ui/index.html",
        "remotion-composer/package.json",
        "remotion-composer/src/Root.tsx",
    )
    assert all((install_dir / relative).is_file() for relative in installed_expected)

    # ``--target`` flattens ``.data/data`` and therefore masked the real bug:
    # a conventional virtualenv keeps setuptools data-files under its prefix.
    # Exercise the install shape users get from ``pip install openmontage`` and
    # verify the runtime resolver finds both config and Remotion resources.
    venv_dir = tmp_path / "venv"
    venv_python = _install_conventional_wheel(wheel_path, venv_dir)
    # Earlier browser/staging tests intentionally set these process-level
    # overrides while importing their fixture module.  The installed-package
    # contract must exercise the default conventional layout, so do not let a
    # test-order leak change the child process under test.
    probe_env = _probe_env()
    # The nested venv must stay isolated so its ``lib`` package comes from the
    # wheel under test.  Reuse only the outer test environment's dependency
    # directories because the wheel install above deliberately uses --no-deps.
    probe_dependency_paths = _dependency_paths()
    probe = subprocess.run(
        [
            str(venv_python),
            "-I",
            "-c",
            (
                "import json, sys; from pathlib import Path; "
                f"sys.path.extend({json.dumps(probe_dependency_paths)}); "
                "from lib.config_model import OpenMontageConfig; "
                "from lib.paths import PROJECTS_DIR, resource_path; "
                "cfg=OpenMontageConfig.load(); "
                "print(json.dumps({"
                "'config': resource_path('config.yaml').is_file(), "
                "'composer': resource_path('remotion-composer/package.json').is_file(), "
                "'pipeline': resource_path('pipeline_defs/screen-demo.yaml').is_file(), "
                "'projects': str(PROJECTS_DIR), "
                "'cwd_projects': str(Path.cwd().resolve() / 'projects'), "
                "'output': str(cfg.resolve_path('output_dir')), "
                "'cwd_output': str(Path.cwd().resolve() / 'output'), "
                "'default_fps': cfg.output.default_fps}))"
            ),
        ],
        cwd=tmp_path,
        env=probe_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, (
        f"installed-wheel probe failed ({probe.returncode}):\n"
        f"stdout:\n{probe.stdout}\nstderr:\n{probe.stderr}"
    )
    probe_payload = json.loads(probe.stdout)
    assert probe_payload["config"] is True
    assert probe_payload["composer"] is True
    assert probe_payload["pipeline"] is True
    assert probe_payload["projects"] == probe_payload["cwd_projects"]
    assert probe_payload["output"] == probe_payload["cwd_output"]
    assert probe_payload["default_fps"] == 30


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)
def test_installed_wheel_video_compose_renders_from_outside_checkout(
    tmp_path: Path, release_wheel: Path
) -> None:
    """Exercise the actual FFmpeg tool from its conventional wheel install."""
    venv_dir = tmp_path / "venv"
    venv_python = _install_conventional_wheel(release_wheel, venv_dir)
    probe_env = _probe_env()
    source_path = tmp_path / "fixture.mp4"
    output_path = tmp_path / "wheel-render.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=teal:s=320x240:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    probe_dependency_paths = _dependency_paths()
    probe = subprocess.run(
        [
            str(venv_python),
            "-I",
            "-c",
            (
                "import json, sys; from pathlib import Path; "
                f"sys.path.extend({json.dumps(probe_dependency_paths)}); "
                "import lib.paths as package_paths; "
                "from lib.config_model import OpenMontageConfig; "
                "from tools.video.video_compose import VideoCompose; "
                "assert Path(package_paths.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
                "cfg=OpenMontageConfig.load(); "
                "result=VideoCompose().execute({"
                "'operation':'encode', 'input_path':sys.argv[1], 'output_path':sys.argv[2], "
                "'codec':'libx264', 'crf':28, 'preset':'ultrafast'}); "
                "assert result.success, result.error; "
                "print(json.dumps({'default_fps':cfg.output.default_fps, "
                "'module':str(Path(package_paths.__file__).resolve()), "
                "'output':result.data['output']}))"
            ),
            str(source_path),
            str(output_path),
        ],
        cwd=tmp_path,
        env=probe_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, (
        f"installed-wheel render failed ({probe.returncode}):\n"
        f"stdout:\n{probe.stdout}\nstderr:\n{probe.stderr}"
    )
    render = json.loads(probe.stdout)
    assert render["default_fps"] == 30
    assert Path(render["module"]).is_relative_to(venv_dir.resolve())
    assert Path(render["output"]) == output_path
    assert output_path.is_file() and output_path.stat().st_size > 0

    media_probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(media_probe.stdout)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 320, 240)
    assert audio["codec_name"] == "aac"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(output_path), "-f", "null", "-"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_source_and_installed_resource_roots_have_required_contract_files() -> None:
    expected = (
        "config.yaml",
        "pipeline_defs/screen-demo.yaml",
        "skills/core/remotion.md",
        "styles/ilearnzed-education.yaml",
        "content_templates/ilearnzed-concept-explainer.yaml",
        "profiles/ilearnzed.yaml",
        "schemas/artifacts/brief.schema.json",
        "backlot/ui/index.html",
        "remotion-composer/package.json",
        "remotion-composer/src/Root.tsx",
    )
    assert all((REPO_ROOT / relative).is_file() for relative in expected)
