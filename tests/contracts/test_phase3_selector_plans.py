"""PR-306 dry-run ranked provider-plan contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.providers.plans import build_ranked_plan


class _Provider:
    def __init__(self, name: str, provider: str, status: str, cost: float) -> None:
        self.name = name
        self.provider = provider
        self._status = status
        self._cost = cost

    def get_status(self):
        return SimpleNamespace(value=self._status)

    def estimate_cost(self, _inputs):
        return self._cost


def test_ranked_plan_exposes_selection_alternatives_and_no_spend() -> None:
    providers = [
        _Provider("fast-image", "provider-a", "available", 0.03),
        _Provider("backup-image", "provider-b", "available", 0.05),
        _Provider("offline-image", "provider-c", "unavailable", 0.0),
    ]
    rankings = [
        {"tool_name": "fast-image", "provider": "provider-a", "weighted_score": 0.91},
        {"tool_name": "backup-image", "provider": "provider-b", "weighted_score": 0.72},
        {"tool_name": "offline-image", "provider": "provider-c", "weighted_score": 0.10},
    ]

    plan = build_ranked_plan(
        capability="image_generation",
        operation="generate",
        inputs={"prompt": "a clean diagram"},
        rankings=rankings,
        providers=providers,
    )

    assert plan["dry_run"] is True
    assert plan["would_execute"] is False
    assert plan["selected_candidate"]["tool"] == "fast-image"
    assert [item["tool"] for item in plan["alternatives"]] == ["backup-image", "offline-image"]
    assert plan["approval_required"] is True
    assert plan["execution"] == "awaiting_approval"


@pytest.mark.parametrize("field,value", [("approved", "false"), ("approved", 1), ("provider_approved", "no")])
def test_ranked_plan_blocks_malformed_approval_without_spend(field: str, value: object) -> None:
    provider = _Provider("paid-image", "provider-a", "available", 0.03)
    plan = build_ranked_plan(
        capability="image_generation",
        operation="generate",
        inputs={"prompt": "a clean diagram", field: value},
        rankings=[{"tool_name": "paid-image", "provider": "provider-a", "weighted_score": 0.91}],
        providers=[provider],
    )

    assert plan["would_execute"] is False
    assert plan["approval_required"] is True
    assert plan["execution"] == "blocked_invalid_approval"
    assert "boolean" in plan["approval_error"]
