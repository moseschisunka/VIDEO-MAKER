"""PR-1001 installed-package data checks."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_release_assets_are_present_in_built_wheel(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
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
    wheel_path = next(wheel_dir.glob("openmontage-*.whl"))
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
