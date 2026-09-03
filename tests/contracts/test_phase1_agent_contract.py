"""PR-106 manifest-to-agent-contract validation."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from lib.pipeline_loader import (
    ManifestAgentContractError,
    assert_manifest_agent_contract,
    list_pipeline_catalog,
    load_pipeline_readonly,
    validate_manifest_agent_contract,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_every_user_visible_manifest_has_a_complete_agent_contract() -> None:
    records = list_pipeline_catalog()
    assert records
    for record in records:
        report = record["agent_contract"]
        if record["release_lane"] == "launch":
            assert record["agent_contract_valid"] is True
            assert report["valid"] is True
            assert report["production_support"]["status"] == "supported"
        else:
            # Held lanes remain discoverable for roadmap transparency, but
            # missing runtime/profile declarations block their agent contract.
            assert record["agent_contract_valid"] is False
            assert report["valid"] is False
            assert report["production_support"]["status"] == "blocked"
        assert report["visible"] is True
        assert report["production_support"]["production_ready"] is False
        assert report["production_support"]["production_gate"] == "PR-11G"
        assert report["stages"]
        for stage in report["stages"]:
            assert stage["skill_path"]
            assert stage["artifacts"]["produces"]
            assert isinstance(stage["tools"]["permitted"], list)
            assert isinstance(stage["checkpoint"]["required"], bool)
            assert isinstance(stage["checkpoint"]["human_approval_default"], bool)
            assert stage["review_focus"]


def test_catalog_embeds_the_same_contract_report_as_direct_validation() -> None:
    record = next(item for item in list_pipeline_catalog() if item["id"] == "screen-demo")
    manifest = load_pipeline_readonly("screen-demo")
    direct = validate_manifest_agent_contract(manifest, repo_root=PROJECT_ROOT)

    assert record["agent_contract"] == direct
    assert record["agent_contract_issues"] == []
    assert direct["production_support"]["release_lane"] == "launch"
    assert direct["production_support"]["creation_enabled"] is True


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda m: m["stages"][0].pop("skill"), "missing_director_skill"),
        (lambda m: m["stages"][0]["produces"].append("not-an-artifact"), "unknown_artifact"),
        (lambda m: m["stages"][0].update({"required_tools": ["secret_tool"], "tools_available": []}), "required_tool_not_permitted"),
        (lambda m: m["stages"][0].pop("checkpoint_required"), "missing_checkpoint_policy"),
        (lambda m: m["stages"][0].update({"review_focus": []}), "empty_list"),
    ],
)
def test_contract_validator_catches_missing_stage_guards(mutation, code) -> None:
    manifest = copy.deepcopy(load_pipeline_readonly("screen-demo"))
    mutation(manifest)

    report = validate_manifest_agent_contract(manifest, repo_root=PROJECT_ROOT)

    assert report["valid"] is False
    assert any(item["code"] == code for item in report["issues"])
    with pytest.raises(ManifestAgentContractError):
        assert_manifest_agent_contract(manifest, repo_root=PROJECT_ROOT)


def test_hidden_framework_fixture_is_reported_but_not_user_catalogued() -> None:
    hidden = next(item for item in list_pipeline_catalog(include_hidden=True) if item["id"] == "framework-smoke")
    assert hidden["ui_visible"] is False
    assert hidden["agent_contract_valid"] is False
    assert any(item["code"] == "missing_director_skill" for item in hidden["agent_contract_issues"])
    assert hidden["agent_contract"]["production_support"]["status"] == "not_user_facing"
