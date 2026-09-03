"""PR-1030 HyperFrames policy and offline-boundary regressions."""

from __future__ import annotations

import pytest

from tools.video.hyperframes_compose import HyperFramesCompose


@pytest.mark.parametrize(
    "field,value",
    [
        ("strict", "false"),
        ("strict", 1),
        ("production_mode", "false"),
        ("production_mode", 1),
        ("offline", "false"),
        ("offline", 1),
    ],
)
def test_execute_rejects_malformed_policy_before_runtime_call(monkeypatch, field, value):
    tool = HyperFramesCompose()
    called = False

    def runtime_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("runtime must not run with malformed policy controls")

    monkeypatch.setattr(tool, "_runtime_check", runtime_must_not_run)

    result = tool.execute({"operation": "doctor", field: value})

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not called


@pytest.mark.parametrize("method", ["_scaffold", "_lint", "_validate", "_inspect"])
def test_direct_operations_reject_malformed_policy_before_side_effects(
    monkeypatch, tmp_path, method
):
    tool = HyperFramesCompose()
    called = False

    def cli_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("HyperFrames CLI must not run with malformed policy")

    monkeypatch.setattr(tool, "_run_hf", cli_must_not_run)
    workspace = tmp_path / "workspace"
    inputs = {"workspace_path": str(workspace), "production_mode": "false"}
    if method == "_scaffold":
        inputs.update(
            {
                "edit_decisions": {"cuts": [{"source": "x", "in_seconds": 0, "out_seconds": 1}]},
                "asset_manifest": {"assets": []},
            }
        )

    result = getattr(tool, method)(inputs)

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not called
    assert not workspace.exists()


def test_offline_override_is_preserved_when_key_is_omitted(monkeypatch):
    tool = HyperFramesCompose()
    tool._offline_mode = True

    def runtime_check():
        assert tool._offline_mode is True
        return {"runtime_available": False, "reasons": ["offline fixture"]}

    monkeypatch.setattr(tool, "_runtime_check", runtime_check)

    result = tool.execute({"operation": "doctor"})

    # The direct operation must preserve the cached-QA override when offline is
    # omitted from the request.
    assert tool._offline_mode is True
    assert result.success is False


def test_validate_malformed_policy_does_not_invoke_hyperframes(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "index.html").write_text("<!doctype html>", encoding="utf-8")
    called = False

    def cli_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("validate CLI must not run with malformed policy")

    monkeypatch.setattr(HyperFramesCompose, "_run_hf", cli_must_not_run)
    result = HyperFramesCompose()._validate(
        {"workspace_path": str(workspace), "production_mode": 0}
    )

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not called


def test_execute_rejects_unknown_operation_without_runtime_probe(monkeypatch):
    tool = HyperFramesCompose()
    called = False

    def runtime_must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("runtime probe must not run for unknown operation")

    monkeypatch.setattr(tool, "_runtime_check", runtime_must_not_run)
    result = tool.execute({"operation": "not-a-hyperframes-operation"})

    assert not result.success
    assert "unknown operation" in (result.error or "").lower()
    assert not called
