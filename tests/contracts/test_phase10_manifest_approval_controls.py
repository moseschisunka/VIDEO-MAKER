"""PR-1036 strict approval controls at every manifest persistence boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backlot.server import SubmitManifestStageRequest
from lib.checkpoint import CheckpointValidationError, write_checkpoint
from lib.manifest_executor import ManifestExecutionError, submit_manifest_stage


@pytest.mark.release_blocker
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("human_approval_required", "false"),
        ("human_approval_required", 1),
        ("human_approval_required", None),
        ("human_approved", "false"),
        ("human_approved", 1),
        ("human_approved", None),
    ],
)
def test_checkpoint_writer_rejects_malformed_approval_flags_before_write(
    tmp_path, field, value
):
    checkpoint = tmp_path / "approval-flags" / "checkpoint_research.json"

    with pytest.raises(CheckpointValidationError, match=rf"{field} must be boolean"):
        write_checkpoint(
            tmp_path,
            "approval-flags",
            "research",
            "in_progress",
            {},
            **{field: value},
        )

    assert not checkpoint.exists()


@pytest.mark.release_blocker
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_manifest_handoff_rejects_malformed_approval_before_loading_state(
    monkeypatch, tmp_path, value
):
    def load_must_not_run(*_args, **_kwargs):
        raise AssertionError("manifest state must not load for malformed approval")

    monkeypatch.setattr(
        "lib.manifest_executor.load_manifest_stage_context", load_must_not_run
    )

    with pytest.raises(ManifestExecutionError, match="human_approved must be boolean"):
        submit_manifest_stage(
            tmp_path,
            "agent-a",
            "research",
            {},
            status="in_progress",
            human_approved=value,
        )


@pytest.mark.release_blocker
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_backlot_request_rejects_coercible_approval_values(value):
    with pytest.raises(ValidationError):
        SubmitManifestStageRequest(
            agent_id="agent-a",
            stage="research",
            human_approved=value,
        )


def test_backlot_request_accepts_real_boolean_approval_values():
    request = SubmitManifestStageRequest(
        agent_id="agent-a",
        stage="research",
        human_approved=True,
    )
    assert request.human_approved is True
