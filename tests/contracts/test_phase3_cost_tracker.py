"""PR-308 cost reservation/reconciliation idempotency contracts."""

from __future__ import annotations

import pytest

from lib.config_model import BudgetMode
from tools.cost_tracker import CostTracker, EntryStatus


def test_cost_estimate_reuses_idempotency_key_after_replay(tmp_path) -> None:
    tracker = CostTracker(mode=BudgetMode.OBSERVE, cost_log_path=tmp_path / "cost.json")
    first = tracker.estimate("provider-a", "generate", 0.12, idempotency_key="attempt-1")
    replay = tracker.estimate("provider-a", "generate", 0.12, idempotency_key="attempt-1")
    assert replay == first
    assert len(tracker.entries) == 1

    tracker.reserve(first)
    tracker.reserve(first)
    assert tracker.budget_reserved_usd == pytest.approx(0.12)
    tracker.reconcile(first, 0.10, success=True)
    tracker.reconcile(first, 0.10, success=True)
    assert tracker.budget_spent_usd == pytest.approx(0.10)
    assert tracker.entries[0]["status"] == EntryStatus.COMPLETED.value


def test_cost_tracker_rejects_conflicting_or_terminal_replays() -> None:
    tracker = CostTracker(mode=BudgetMode.OBSERVE)
    entry = tracker.estimate("provider-a", "generate", 0.12, idempotency_key="attempt-1")
    tracker.reserve(entry)
    tracker.reconcile(entry, 0.10, success=True)
    with pytest.raises(ValueError, match="already reconciled"):
        tracker.reconcile(entry, 0.11, success=True)
    with pytest.raises(ValueError, match="terminal"):
        tracker.reserve(entry)


def test_cost_tracker_refund_is_idempotent_but_only_for_reservations() -> None:
    tracker = CostTracker(mode=BudgetMode.OBSERVE)
    entry = tracker.estimate("provider-a", "generate", 0.12)
    tracker.reserve(entry)
    tracker.refund(entry)
    tracker.refund(entry)
    assert tracker.entries[0]["status"] == EntryStatus.REFUNDED.value
    with pytest.raises(ValueError, match="refunded"):
        tracker.reconcile(entry, 0.0, success=False)
