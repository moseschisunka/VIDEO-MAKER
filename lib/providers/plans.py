"""Provider selector dry-run plan serialization."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


def build_ranked_plan(
    *,
    capability: str,
    operation: str,
    inputs: Mapping[str, Any],
    rankings: Iterable[Mapping[str, Any]],
    providers: Iterable[Any],
) -> dict[str, Any]:
    """Build an observable plan without invoking a provider."""
    ranking_items = [dict(item) for item in rankings]
    provider_by_tool = {str(getattr(tool, "name", "")): tool for tool in providers}
    candidates: list[dict[str, Any]] = []
    for rank, item in enumerate(ranking_items, start=1):
        tool = provider_by_tool.get(str(item.get("tool_name") or item.get("name") or ""))
        candidate = {
            **item,
            "rank": rank,
            "tool": getattr(tool, "name", item.get("tool_name")),
            "provider": getattr(tool, "provider", item.get("provider")),
            "status": (
                getattr(tool.get_status(), "value", str(tool.get_status()))
                if tool is not None else item.get("status", "untested")
            ),
            "estimated_cost_usd": (
                round(float(tool.estimate_cost(dict(inputs)) or 0), 8)
                if tool is not None else None
            ),
        }
        candidate["available_for_execution"] = candidate["status"] == "available"
        candidates.append(candidate)

    selected = next((item for item in candidates if item.get("available_for_execution")), None)
    alternatives = [item for item in candidates if not selected or item.get("tool") != selected.get("tool")]
    selected_cost = float((selected or {}).get("estimated_cost_usd") or 0)
    approval_required = bool(selected_cost > 0 and not inputs.get("approved", False))
    if selected is None:
        execution = "blocked_unavailable"
    elif approval_required:
        execution = "awaiting_approval"
    else:
        execution = "ready_to_execute"
    try:
        plan_key = hashlib.sha256(
            json.dumps(
                {"capability": capability, "operation": operation, "inputs": dict(inputs), "candidates": candidates},
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError):
        plan_key = ""
    return {
        "version": "1.0",
        "plan_id": plan_key,
        "capability": capability,
        "operation": operation,
        "dry_run": True,
        "would_execute": False,
        "selected_candidate": selected,
        "alternatives": alternatives,
        "candidates": candidates,
        "approval_required": approval_required,
        "execution": execution,
    }


__all__ = ["build_ranked_plan"]
