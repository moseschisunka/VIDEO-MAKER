"""Capability-level image selector that routes between generation and stock providers.

Provider discovery is automatic — any BaseTool with capability="image_generation"
is picked up from the registry.  Adding a new image provider requires only creating
the tool file in tools/graphics/; no changes to this selector are needed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolStability, ToolStatus, ToolTier


class ImageSelector(BaseTool):
    name = "image_selector"
    version = "0.2.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "selector"
    stability = ToolStability.BETA
    runtime = ToolRuntime.HYBRID
    agent_skills = ["flux-best-practices", "bfl-api"]

    capabilities = [
        "generate_image", "search_image", "download_image",
        "provider_selection", "text_to_image", "stock_image",
    ]
    supports = {
        "user_preference_routing": True,
        "offline_fallback": True,
        "stock_fallback": True,
    }
    best_for = [
        "preflight routing — pick the best image provider for the task",
        "switching between generated and stock images",
        "automatic fallback when preferred provider is unavailable",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Image description (used as prompt for generation or query for stock)",
            },
            "negative_prompt": {
                "type": "string",
                "description": "What to avoid in the generated image. Passed to providers that support it.",
            },
            "width": {"type": "integer", "description": "Image width in pixels"},
            "height": {"type": "integer", "description": "Image height in pixels"},
            "seed": {"type": "integer", "description": "Random seed for reproducibility (generation providers only)"},
            "n": {"type": "integer", "description": "Number of image variations to request when supported."},
            "aspect_ratio": {
                "type": "string",
                "description": "Aspect ratio hint for providers that support ratio-based generation.",
            },
            "resolution": {
                "type": "string",
                "description": "Resolution tier for providers that support named resolutions.",
            },
            "api_family": {
                "type": "string",
                "description": "Provider-specific API family hint passed through when supported.",
            },
            "model_name": {
                "type": "string",
                "description": "Provider-specific model name passed through when supported.",
            },
            "generation_mode": {
                "type": "string",
                "enum": ["generate", "edit"],
                "default": "generate",
                "description": "Use 'edit' when providing one or more source images.",
            },
            "image_url": {"type": "string", "description": "Single source image URL for edit-capable providers."},
            "image_path": {"type": "string", "description": "Single local source image path for edit-capable providers."},
            "image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple source image URLs for compositing edits.",
            },
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple local source image paths for compositing edits.",
            },
            "image_list": {
                "type": "array",
                "description": "Provider-specific image reference list, e.g. Kling Official Image Omni.",
            },
            "element_list": {
                "type": "array",
                "description": "Provider-specific element references, e.g. Kling Official element_id objects.",
            },
            "image_reference": {
                "type": "string",
                "description": "Provider-specific reference type, e.g. subject or face.",
            },
            "image_fidelity": {
                "type": "number",
                "description": "Provider-specific reference image fidelity hint.",
            },
            "human_fidelity": {
                "type": "number",
                "description": "Provider-specific human or face fidelity hint.",
            },
            "result_type": {
                "type": "string",
                "description": "Provider-specific result type, e.g. single or series.",
            },
            "series_amount": {
                "type": "string",
                "description": "Provider-specific series amount for image series generation.",
            },
            "watermark": {
                "type": "boolean",
                "description": "Provider-specific watermark toggle passed through when supported.",
            },
            "callback_url": {
                "type": "string",
                "description": "Provider-specific callback URL. Current OpenMontage providers still poll by default.",
            },
            "external_task_id": {
                "type": "string",
                "description": "Provider-specific idempotency/provenance task id.",
            },
            "preferred_provider": {
                "type": "string",
                "description": "Provider name or 'auto'. Valid values are discovered at runtime from the registry.",
                "default": "auto",
            },
            "allowed_providers": {
                "type": "array",
                "items": {"type": "string"},
            },
            "operation": {
                "type": "string",
                "enum": ["generate", "rank"],
                "default": "generate",
                "description": "Operation mode. 'rank' returns scored provider rankings without generating.",
            },
            "workflow_json": {
                "type": "string",
                "description": (
                    "Optional full ComfyUI workflow JSON. Routes to a custom-workflow-capable "
                    "provider (e.g. comfyui_image) based on server availability, not bundled "
                    "model readiness. Requires output_node."
                ),
            },
            "workflow_path": {
                "type": "string",
                "description": (
                    "Optional path to a ComfyUI workflow JSON file. Routes to a custom-workflow-"
                    "capable provider based on server availability. Requires output_node."
                ),
            },
            "output_node": {
                "type": "string",
                "description": "ComfyUI output node ID for a custom workflow_json/workflow_path.",
            },
            "workflow_name": {
                "type": "string",
                "description": "Optional human-readable provenance label for a custom workflow.",
            },
            "workflow_model": {
                "type": "string",
                "description": "Optional model/provenance label for a custom workflow.",
            },
            "workflow_model_stack": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Optional provenance metadata for custom workflow dependencies.",
            },
            "output_path": {"type": "string"},
            "asset_request": {
                "type": "object",
                "description": "Canonical scene asset request. Required for production mixed-media runs.",
            },
            "sample_required": {
                "type": "boolean",
                "default": False,
                "description": "Require a reviewed sample before any batch generation.",
            },
            "sample_approval": {
                "type": "object",
                "description": "Immutable sample approval record for the selected generation plan.",
            },
            "batch": {"type": "boolean", "default": False},
            "strict_media_validation": {
                "type": "boolean",
                "default": False,
                "description": "Production mode: require local decoded output and requested dimensions.",
            },
            "production_mode": {"type": "boolean", "default": False},
            "asset_cache_dir": {
                "type": "string",
                "description": "Optional run-local content-addressed cache for validated generated assets.",
            },
        },
    }

    def _providers(self) -> list[BaseTool]:
        """Auto-discover image generation providers from the registry."""
        from tools.tool_registry import registry
        registry.ensure_discovered()
        return [t for t in registry.get_by_capability("image_generation")
                if t.name != self.name]

    @property
    def fallback_tools(self) -> list[str]:
        """Dynamically built from discovered providers."""
        return [t.name for t in self._providers()]

    @property
    def provider_matrix(self) -> dict[str, dict[str, str]]:
        """Built at runtime from each provider's best_for field."""
        matrix = {}
        for tool in self._providers():
            strength = ", ".join(tool.best_for) if tool.best_for else tool.name
            matrix[tool.provider] = {"tool": tool.name, "strength": strength}
        return matrix

    def get_status(self) -> ToolStatus:
        if any(tool.get_status() == ToolStatus.AVAILABLE for tool in self._providers()):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        candidates = self._providers()
        if not candidates:
            return 0.0
        tool, _ = self._select_best_tool(inputs, candidates, self._prepare_task_context(inputs))
        return tool.estimate_cost(inputs) if tool else 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        import logging
        from lib.scoring import rank_providers
        from lib.media_contracts import MediaContractError, AssetRequest, build_asset_request, strict_bool

        logger = logging.getLogger(__name__)

        # Parse all release/spend controls before provider discovery, ranking,
        # or any other provider-facing work.  Python truthiness is unsafe here:
        # values such as ``"false"`` and ``1`` must never silently enable or
        # disable a production gate.
        try:
            requested_sample = (
                strict_bool(inputs["sample_required"], "sample_required")
                if "sample_required" in inputs else False
            )
            requested_batch = (
                strict_bool(inputs["batch"], "batch")
                if "batch" in inputs else False
            )
            requested_strict = (
                strict_bool(inputs["strict_media_validation"], "strict_media_validation")
                if "strict_media_validation" in inputs else False
            )
            requested_production = (
                strict_bool(inputs["production_mode"], "production_mode")
                if "production_mode" in inputs else False
            )
            requested_watermark = (
                strict_bool(inputs["watermark"], "watermark")
                if "watermark" in inputs else None
            )
        except MediaContractError as exc:
            return ToolResult(success=False, error=f"Invalid media gate control: {exc}")

        if requested_watermark is not None:
            inputs = dict(inputs)
            inputs["watermark"] = requested_watermark

        task_context = self._prepare_task_context(inputs)
        candidates = self._filter_candidates(inputs, self._providers())

        # Rank mode — return scored provider rankings without generating
        if inputs.get("operation") == "rank":
            rankings = rank_providers(candidates, task_context)
            serialized = self._serialize_rankings(candidates, rankings)
            from lib.providers.plans import build_ranked_plan

            plan = build_ranked_plan(
                capability=self.capability,
                operation="generate",
                inputs=inputs,
                rankings=serialized,
                providers=candidates,
            )
            return ToolResult(
                success=True,
                data={
                    "rankings": serialized,
                    "dry_run_plan": plan,
                    "explanation": "\n".join(r.explain() for r in rankings[:5]),
                    "normalized_task_context": task_context,
                },
            )

        # Normal generation — use scored selection
        tool, score = self._select_best_tool(inputs, candidates, task_context)
        if tool is None:
            return ToolResult(success=False, error="No image provider available.")

        from lib.media_generation import (
            build_generation_plan,
            collect_output_paths,
            require_sample_approval,
            validate_generation_output,
        )

        raw_request = inputs.get("asset_request")
        if raw_request:
            try:
                asset_request = build_asset_request(raw_request)
                if requested_sample or requested_batch:
                    asset_request = replace(asset_request, sample_required=True)
            except Exception as exc:
                return ToolResult(success=False, error=f"Invalid asset_request: {exc}")
        else:
            asset_request = AssetRequest(
                request_id=str(inputs.get("request_id") or f"image:{inputs.get('prompt', '')}"),
                scene_id=str(inputs.get("scene_id") or "unspecified"),
                intent=str(inputs.get("prompt") or "image"),
                media_type="image",
                strategy="ai",
                constraints={
                    key: inputs[key]
                    for key in ("width", "height", "aspect_ratio")
                    if inputs.get(key) is not None
                },
                sample_required=requested_sample or requested_batch,
            )
        try:
            plan = build_generation_plan(
                asset_request,
                provider=str(getattr(tool, "provider", "")),
                model=str(inputs.get("model_name") or inputs.get("model") or getattr(tool, "version", "")),
                inputs=inputs,
            )
            approval = require_sample_approval(
                asset_request,
                inputs.get("sample_approval") if isinstance(inputs.get("sample_approval"), dict) else None,
                plan_id=plan["plan_id"],
            )
        except Exception as exc:
            # Keep selector failures structured and prevent any provider call.
            return ToolResult(success=False, error=f"Image generation plan blocked: {exc}")

        cache = None
        if inputs.get("asset_cache_dir"):
            try:
                from lib.asset_cache import AssetCache

                cache = AssetCache(str(inputs["asset_cache_dir"]))
                cached = cache.get(asset_request, destination=inputs.get("output_path"), validate=True)
                if cached:
                    return ToolResult(success=True, data={
                        "output": cached["path"],
                        "output_path": cached["path"],
                        "selected_tool": tool.name,
                        "selected_provider": tool.provider,
                        "generation_plan": plan,
                        "sample_approval": approval,
                        "asset_request": asset_request.to_dict(),
                        "asset_validation": cached.get("validation") or {},
                        "cache_hit": True,
                    }, artifacts=[cached["path"]])
            except Exception as exc:
                logger.warning("image_selector asset cache lookup skipped: %s", exc)

        # Adapt input keys: stock tools use 'query' while generators use 'prompt'
        adapted = dict(inputs)
        if hasattr(tool, 'input_schema'):
            props = tool.input_schema.get("properties", {})
            if "query" in props and "query" not in adapted:
                adapted["query"] = adapted.get("prompt", "")
            # The selector exposes a provider-neutral ``model_name`` field,
            # while several providers call the same input ``model``.
            if (
                "model_name" in adapted
                and "model" in props
                and "model" not in adapted
            ):
                adapted["model"] = adapted["model_name"]

        # Strip selector-only keys that downstream tools don't understand
        adapted.pop("preferred_provider", None)
        adapted.pop("allowed_providers", None)
        for control_key in (
            "asset_request", "sample_required", "sample_approval", "batch",
            "strict_media_validation", "production_mode", "request_id", "scene_id",
            "asset_cache_dir",
        ):
            adapted.pop(control_key, None)

        # Pass through generation params only to tools that accept them.
        if hasattr(tool, 'input_schema'):
            props = tool.input_schema.get("properties", {})
            stripped = []
            for passthrough_key in (
                "negative_prompt",
                "width",
                "height",
                "seed",
                "n",
                "aspect_ratio",
                "resolution",
                "generation_mode",
                "image_url",
                "image_path",
                "image_urls",
                "image_paths",
                "image_list",
                "element_list",
                "api_family",
                "model_name",
                "image_reference",
                "image_fidelity",
                "human_fidelity",
                "result_type",
                "series_amount",
                "watermark",
                "callback_url",
                "external_task_id",
                "workflow_json",
                "workflow_path",
                "output_node",
                "workflow_name",
                "workflow_model",
                "workflow_model_stack",
            ):
                if passthrough_key in adapted and passthrough_key not in props:
                    stripped.append(f"{passthrough_key}={adapted.pop(passthrough_key)}")
            if stripped:
                logger.warning(
                    "image_selector: stripped unsupported params for %s: %s",
                    tool.name, ", ".join(stripped),
                )

        from lib.providers.bridge import execute_with_provider_executor

        # All selector executions are kernel-governed; the bridge preserves
        # direct-test compatibility while production identity calls stay
        # approval-aware.
        result = execute_with_provider_executor(tool, adapted)
        if result.success:
            strict = requested_strict or requested_production
            if strict:
                try:
                    validation = validate_generation_output(
                        result,
                        media_type="image",
                        constraints=asset_request.constraints,
                        strict=True,
                    )
                except Exception as exc:
                    return ToolResult(success=False, data={"generation_plan": plan, "sample_approval": approval}, error=f"Generated image failed validation: {exc}")
                result.data["asset_validation"] = validation
                if cache:
                    try:
                        paths = [p for p in collect_output_paths(result) if p.is_file()]
                        if paths:
                            entry = cache.put(
                                asset_request,
                                paths[0],
                                asset_id=f"asset_{validation['outputs'][0].get('sha256', '')[:16]}",
                                media_type="image",
                                validation=validation["outputs"][0],
                                metadata={"generation_plan": plan},
                            )
                            result.data["asset_cache"] = {"request_key": entry.request_key, "sha256": entry.sha256, "path": entry.path}
                    except Exception as exc:
                        logger.warning("image_selector asset cache write skipped: %s", exc)
            result.data.setdefault("selected_tool", tool.name)
            result.data["selected_provider"] = tool.provider
            result.data["selection_reason"] = score.explain() if score else f"Selected {tool.provider} ({tool.name})"
            if score:
                result.data["provider_score"] = score.to_dict()
            result.data.update(self._tool_context_payload(tool))
            result.data["alternatives_considered"] = [
                t.name for t in candidates
                if t.name != tool.name and t.get_status().value == "available"
            ]
            result.data["generation_plan"] = plan
            result.data["sample_approval"] = approval
            result.data["asset_request"] = asset_request.to_dict()
        return result

    def _select_best_tool(
        self,
        inputs: dict[str, Any],
        candidates: list[BaseTool],
        task_context: dict[str, Any],
    ) -> tuple[BaseTool | None, object]:
        """Select the best provider using scored ranking."""
        from lib.scoring import rank_providers

        preferred = inputs.get("preferred_provider", "auto")
        allowed = set(inputs.get("allowed_providers") or [])
        if allowed:
            candidates = [tool for tool in candidates if tool.provider in allowed]
        candidates = self._filter_candidates(inputs, candidates)

        rankings = rank_providers(candidates, task_context)

        tool_by_provider: dict[str, BaseTool] = {}
        for tool in candidates:
            if tool.provider not in tool_by_provider and self._tool_selectable(tool, inputs):
                tool_by_provider[tool.provider] = tool

        if preferred != "auto":
            for score_item in rankings:
                if score_item.provider == preferred and score_item.provider in tool_by_provider:
                    return tool_by_provider[score_item.provider], score_item

        for score_item in rankings:
            if score_item.provider in tool_by_provider:
                return tool_by_provider[score_item.provider], score_item

        return None, None

    def _prepare_task_context(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from lib.scoring import normalize_task_context

        return normalize_task_context(
            inputs.get("task_context", {}),
            prompt=inputs.get("prompt", ""),
            capability=self.capability,
            operation=inputs.get("generation_mode", inputs.get("operation", "generate")),
        )

    @staticmethod
    def _tool_context_payload(tool: BaseTool) -> dict[str, Any]:
        info = tool.get_info()
        return {
            "selected_tool_agent_skills": info.get("agent_skills", []),
            "required_agent_skills": info.get("agent_skills", []),
            "selected_tool_usage_location": info.get("usage_location"),
            "selected_tool_best_for": info.get("best_for", []),
        }

    def _serialize_rankings(self, candidates: list[BaseTool], rankings: list[object]) -> list[dict[str, Any]]:
        tool_by_name = {tool.name: tool for tool in candidates}
        serialized: list[dict[str, Any]] = []
        for score in rankings:
            item = score.to_dict()
            tool = tool_by_name.get(score.tool_name)
            if tool:
                info = tool.get_info()
                item["agent_skills"] = info.get("agent_skills", [])
                item["usage_location"] = info.get("usage_location")
                item["best_for"] = info.get("best_for", [])
                item["supports"] = info.get("supports", {})
                item["status"] = str(tool.get_status())
            serialized.append(item)
        return serialized

    def _filter_candidates(self, inputs: dict[str, Any], candidates: list[BaseTool]) -> list[BaseTool]:
        # A caller-supplied custom workflow is provider-specific (ComfyUI graph
        # JSON). Route it only to custom-workflow-capable providers whose server
        # is reachable — bundled-model readiness is irrelevant in that case.
        if self._has_custom_workflow(inputs):
            return [t for t in candidates if self._custom_workflow_eligible(t, inputs)]

        wants_edit = (
            inputs.get("generation_mode") == "edit"
            or inputs.get("image_url")
            or inputs.get("image_path")
            or inputs.get("image_urls")
            or inputs.get("image_paths")
        )
        if not wants_edit:
            return candidates

        filtered: list[BaseTool] = []
        for tool in candidates:
            props = getattr(tool, "input_schema", {}).get("properties", {})
            supports = getattr(tool, "supports", {})
            if supports.get("image_edit") or any(
                key in props for key in ("image", "images", "image_url", "image_path", "image_urls", "image_paths")
            ):
                filtered.append(tool)
        return filtered or candidates

    @staticmethod
    def _has_custom_workflow(inputs: dict[str, Any]) -> bool:
        return bool(inputs.get("workflow_json") or inputs.get("workflow_path"))

    def _custom_workflow_eligible(self, tool: BaseTool, inputs: dict[str, Any]) -> bool:
        """Whether a tool can run the caller-supplied custom workflow.

        Eligibility is based on server availability, not bundled-model readiness:
        a provider qualifies when it advertises ``custom_workflow`` support, an
        ``output_node`` is supplied, and its backend is reachable (status is not
        UNAVAILABLE).
        """
        if not self._has_custom_workflow(inputs):
            return False
        if not inputs.get("output_node"):
            return False
        supports = getattr(tool, "supports", {})
        if not supports.get("custom_workflow"):
            return False
        return tool.get_status() != ToolStatus.UNAVAILABLE

    def _tool_selectable(self, tool: BaseTool, inputs: dict[str, Any]) -> bool:
        """A provider is selectable if it is AVAILABLE, or if it can serve a
        caller-supplied custom workflow even while bundled models report DEGRADED."""
        if tool.get_status() == ToolStatus.AVAILABLE:
            return True
        return self._custom_workflow_eligible(tool, inputs)
