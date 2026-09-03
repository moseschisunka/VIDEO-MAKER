"""PR-102 canonical catalog contracts."""

from __future__ import annotations

from backlot.server import _load_pipelines_data
from lib.pipeline_loader import list_pipeline_catalog


def test_api_and_loader_share_the_same_filtered_catalog() -> None:
    loader_records = list_pipeline_catalog()
    api_records = _load_pipelines_data()
    assert [record["id"] for record in api_records] == [record["id"] for record in loader_records]
    assert "framework-smoke" not in {record["id"] for record in loader_records}
    assert all(record["ui_visible"] for record in loader_records)
    assert all(record["schema_valid"] for record in loader_records)
    for api, loader in zip(api_records, loader_records):
        for key in (
            "release_lane", "release_status", "production_ready", "production_gate",
            "creation_enabled", "maturity", "schema_valid", "stage_order",
        ):
            assert api[key] == loader[key], (api["id"], key)


def test_catalog_is_release_ordered_and_marks_held_lanes() -> None:
    records = list_pipeline_catalog()
    assert [record["id"] for record in records[:2]] == ["screen-demo", "talking-head"]
    assert all(record["production_ready"] is False for record in records)
    assert all(record["production_gate"] == "PR-11G" for record in records)
    assert all(record["release_status"] == "not_certified" for record in records)
    assert any(record["release_lane"] == "beta" and not record["creation_enabled"] for record in records)
    assert any(record["release_lane"] == "experimental" and not record["creation_enabled"] for record in records)


def test_hidden_audit_catalog_keeps_test_manifest_for_accounting() -> None:
    records = list_pipeline_catalog(include_hidden=True)
    smoke = next(record for record in records if record["id"] == "framework-smoke")
    assert smoke["ui_visible"] is False
    assert smoke["creation_enabled"] is False
    assert smoke["availability"] == "test_only"
