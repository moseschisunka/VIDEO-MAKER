"""PR-1000 dependency and lock-file contract tests.

These tests deliberately inspect the source metadata rather than importing
the current developer environment.  A passing result therefore means that a
fresh checkout has one authoritative Python dependency declaration and a
coherent Remotion lock file, instead of silently relying on a stale venv.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _metadata() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _dependency_name(value: str) -> str:
    """Return a PEP 508 dependency's normalized-ish distribution name."""

    return re.split(r"[<>=!~;\s\[]", value, maxsplit=1)[0].lower().replace("_", "-")


def test_pyproject_is_the_only_python_dependency_source() -> None:
    metadata = _metadata()
    project = metadata["project"]
    deps = {_dependency_name(item) for item in project["dependencies"]}

    expected_runtime = {
        "pyyaml",
        "pydantic",
        "jsonschema",
        "python-dotenv",
        "pillow",
        "numpy",
        "requests",
        "filelock",
        "google-auth",
        "google-genai",
        "openai",
        "fastapi",
        "uvicorn",
        "watchfiles",
        "edge-tts",
    }
    assert expected_runtime <= deps

    dev = metadata["project"]["optional-dependencies"]["dev"]
    assert {_dependency_name(item) for item in dev} >= {
        "pytest",
        "pytest-asyncio",
        "httpx",
    }

    setup_text = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
    assert "install_requires" not in setup_text
    assert "find_packages" not in setup_text

    assert (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").strip().endswith("-e .")
    assert (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8").strip().endswith("-e .[dev]")
    assert (REPO_ROOT / "requirements-gpu.txt").read_text(encoding="utf-8").strip().endswith("-e .[gpu]")


def test_gpu_extra_contains_every_gpu_dependency_used_by_makefile() -> None:
    gpu = _metadata()["project"]["optional-dependencies"]["gpu"]
    names = {_dependency_name(item) for item in gpu}
    assert {"torch", "torchaudio", "torchvision", "diffusers", "transformers", "accelerate"} <= names


def test_remotion_package_lock_matches_declared_dependencies() -> None:
    package_path = REPO_ROOT / "remotion-composer" / "package.json"
    lock_path = REPO_ROOT / "remotion-composer" / "package-lock.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))

    assert lock["lockfileVersion"] == 3
    root = lock["packages"][""]
    assert root["name"] == package["name"]
    assert root["version"] == package["version"]
    assert root["dependencies"] == package["dependencies"]
    assert root["devDependencies"] == package["devDependencies"]

    expected_overrides = {
        "nanoid": "3.3.18",
        "browserslist": "4.28.8",
        "fast-uri": "4.1.4",
        "postcss": "8.5.26",
    }
    assert package.get("overrides", {}) == expected_overrides
    for name, version in expected_overrides.items():
        assert lock["packages"][f"node_modules/{name}"]["version"] == version


def test_ci_audits_the_locked_remotion_dependency_graph() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert workflow.count("npm audit --audit-level=high") >= 2


def test_legacy_setuptools_entry_point_uses_pyproject_metadata() -> None:
    result = subprocess.run(
        [sys.executable, "setup.py", "--name"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "openmontage"
