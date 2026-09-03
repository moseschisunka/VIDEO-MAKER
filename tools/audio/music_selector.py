"""Provider-neutral music selector with the common execution kernel."""

from __future__ import annotations

from typing import Any

from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolStability, ToolStatus, ToolTier


class MusicSelector(BaseTool):
    """Rank and execute music providers without hiding a provider switch."""

    name = "music_selector"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"
    provider = "selector"
    runtime = ToolRuntime.HYBRID
    stability = ToolStability.BETA
    capabilities = ["generate_background_music", "provider_selection", "offline_fallback"]
    supports = {"instrumental": True, "duration_control": True, "provider_selection": True}
    best_for = ["background music selection", "soundtrack generation", "music provider preflight"]
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "duration_seconds": {"type": "number", "minimum": 3, "maximum": 600},
            "output_path": {"type": "string"},
            "force_instrumental": {"type": "boolean", "default": True},
            "preferred_provider": {"type": "string", "default": "auto"},
            "allowed_providers": {"type": "array", "items": {"type": "string"}},
            "operation": {"type": "string", "enum": ["generate", "rank"], "default": "generate"},
        },
    }

    def _providers(self) -> list[BaseTool]:
        from tools.tool_registry import registry

        registry.ensure_discovered()
        return [tool for tool in registry.get_by_capability(self.capability) if tool.name != self.name and tool.provider != "selector"]

    @property
    def fallback_tools(self) -> list[str]:
        return [tool.name for tool in self._providers()]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if any(tool.get_status() == ToolStatus.AVAILABLE for tool in self._providers()) else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        candidates = self._filter(inputs, self._providers())
        tool, _ = self._select(inputs, candidates)
        return float(tool.estimate_cost(inputs) or 0.0) if tool else 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from lib.providers.plans import build_ranked_plan
        from lib.scoring import rank_providers
        from lib.media_contracts import MediaContractError, strict_bool

        operation = inputs.get("operation", "generate")
        if operation not in {"generate", "rank"}:
            return ToolResult(success=False, error=f"Unknown operation: {operation}")
        try:
            requested_instrumental = (
                strict_bool(inputs["force_instrumental"], "force_instrumental")
                if "force_instrumental" in inputs else None
            )
        except MediaContractError as exc:
            return ToolResult(success=False, error=f"Invalid music control: {exc}")
        if requested_instrumental is not None:
            inputs = dict(inputs)
            inputs["force_instrumental"] = requested_instrumental

        candidates = self._filter(inputs, self._providers())
        task_context = {
            **dict(inputs.get("task_context") or {}),
            "asset_type": "music",
        }
        rankings = rank_providers(candidates, task_context)
        serialized = [score.to_dict() for score in rankings]
        if operation == "rank":
            plan = build_ranked_plan(
                capability=self.capability,
                operation="generate",
                inputs=inputs,
                rankings=serialized,
                providers=candidates,
            )
            return ToolResult(success=True, data={"rankings": serialized, "dry_run_plan": plan})

        tool, score = self._select(inputs, candidates, rankings=rankings)
        if tool is None:
            return ToolResult(success=False, error="No music provider available.")
        from lib.providers.bridge import execute_with_provider_executor

        result = execute_with_provider_executor(tool, dict(inputs))
        if result.success:
            from lib.music_contracts import music_provenance_from_output

            result.data = dict(result.data or {})
            result.data["music_provenance"] = music_provenance_from_output(
                result.data,
                inputs,
                source_tool=tool.name,
                source_type=str(inputs.get("source_type") or "ai_generated"),
            )
            result.data.setdefault("selected_tool", tool.name)
            result.data["selected_provider"] = tool.provider
            result.data["selection_reason"] = score.explain() if score else f"Selected {tool.provider} ({tool.name})"
            if score:
                result.data["provider_score"] = score.to_dict()
            result.data["alternatives_considered"] = [candidate.name for candidate in candidates if candidate.name != tool.name]
        return result

    @staticmethod
    def _filter(inputs: dict[str, Any], candidates: list[BaseTool]) -> list[BaseTool]:
        allowed = set(inputs.get("allowed_providers") or [])
        return [tool for tool in candidates if not allowed or tool.provider in allowed]

    def _select(self, inputs: dict[str, Any], candidates: list[BaseTool], rankings: list[Any] | None = None):
        from lib.scoring import rank_providers

        scores = rankings or rank_providers(candidates, {"asset_type": "music", **dict(inputs.get("task_context") or {})})
        preferred = inputs.get("preferred_provider", "auto")
        available = {tool.provider: tool for tool in candidates if tool.get_status() == ToolStatus.AVAILABLE}
        if preferred != "auto" and preferred in available:
            score = next((item for item in scores if item.provider == preferred), None)
            return available[preferred], score
        for score in scores:
            if score.provider in available:
                return available[score.provider], score
        return None, None


__all__ = ["MusicSelector"]
