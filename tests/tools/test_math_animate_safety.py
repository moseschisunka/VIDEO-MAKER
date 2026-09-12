"""MathAnimate must be disabled unless the operator explicitly trusts it."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.base_tool import ToolStatus  # noqa: E402
from tools.graphics.math_animate import MathAnimate  # noqa: E402

TRUST_ENV = "OPENMONTAGE_TRUST_UNSANDBOXED_MANIM"


def test_math_animate_is_unavailable_without_operator_opt_in(monkeypatch):
    monkeypatch.delenv(TRUST_ENV, raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/manim")

    assert MathAnimate().get_status() == ToolStatus.UNAVAILABLE


def test_execute_refuses_host_code_before_starting_manim(monkeypatch):
    monkeypatch.delenv(TRUST_ENV, raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/manim")

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("Manim must not run unless the operator enabled trusted execution")

    monkeypatch.setattr("subprocess.run", fail_if_called)

    result = MathAnimate().execute(
        {"scene_code": "import pkgutil\npkgutil.resolve_name('os:system')('echo unsafe')"}
    )

    assert result.success is False
    assert TRUST_ENV in (result.error or "")
    assert "does not provide a sandbox" in (result.error or "")


def test_opt_in_is_operator_controlled_and_allows_trusted_scene_to_reach_manim(monkeypatch):
    monkeypatch.setenv(TRUST_ENV, "1")
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/manim")
    observed = {}

    def fake_manim(command, **_kwargs):
        scene_file = Path(command[-2])
        observed["scene"] = scene_file.read_text(encoding="utf-8")
        return SimpleNamespace(returncode=1, stderr="test stop", stdout="")

    monkeypatch.setattr("subprocess.run", fake_manim)

    # This resembles the reviewed scanner bypass, but the mocked subprocess
    # never executes it. The test checks only that an operator-enabled tool
    # delegates the trusted scene to Manim.
    scene = (
        "class Demo(Scene):\n"
        "    def construct(self):\n"
        "        import pkgutil\n"
        "        pkgutil.resolve_name('os:system')('echo test')\n"
    )
    result = MathAnimate().execute({"scene_code": scene, "scene_name": "Demo"})

    assert result.success is False
    assert "Manim render failed" in result.error
    assert "pkgutil.resolve_name" in observed["scene"]


def test_schema_has_no_caller_controlled_execution_bypass():
    properties = MathAnimate.input_schema["properties"]

    assert "allow_unsafe_code" not in properties
    assert "OPENMONTAGE_TRUST_UNSANDBOXED_MANIM=1" in properties["scene_code"]["description"]
