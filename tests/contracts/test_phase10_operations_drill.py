"""PR-10G offline execution drills for operator runbooks."""

from __future__ import annotations

from scripts.run_operations_drill import run_operations_drill


def test_pr10g_operations_drill_passes_without_network_or_spend() -> None:
    evidence = run_operations_drill()

    assert evidence["status"] == "PASS", evidence
    assert evidence["mode"] == "offline_fake_no_network_no_spend"
    assert evidence["drills"]["provider_outage"]["first_attempts"] == 2
    assert evidence["drills"]["provider_outage"]["second_error_kind"] == "circuit_open"
    assert evidence["drills"]["stuck_and_corrupt_job"]["duplicate_claim_http_status"] == 409
    assert evidence["drills"]["stuck_and_corrupt_job"]["restart_next_stage"] == "idea"
    assert evidence["drills"]["stuck_and_corrupt_job"]["corrupt_artifact"]["final_promoted"] is False
    assert evidence["drills"]["secret_rotation"]["old_token_after_rotation"] == 401
    assert evidence["drills"]["secret_rotation"]["new_token_after_rotation"] is True
    assert evidence["drills"]["alerting"]["delivered_count"] == 3
    assert "provider-circuit-open" in evidence["drills"]["alerting"]["rule_ids"]
