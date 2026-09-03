"""Capability-level text-to-speech selector that chooses among provider tools.

Provider discovery is automatic — any BaseTool with capability="tts"
is picked up from the registry.  Adding a new TTS provider requires only creating
the tool file in tools/audio/; no changes to this selector are needed.
"""

from __future__ import annotations

from typing import Any

from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolStability, ToolTier, ToolStatus


class TTSSelector(BaseTool):
    name = "tts_selector"
    version = "0.2.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "selector"
    stability = ToolStability.BETA
    runtime = ToolRuntime.HYBRID
    agent_skills = ["text-to-speech", "elevenlabs", "openai-docs"]

    capabilities = [
        "text_to_speech",
        "provider_selection",
    ]
    supports = {
        "user_preference_routing": True,
        "offline_fallback": True,
        "multilingual": True,
    }
    best_for = [
        "preflight tool selection",
        "user-facing recommendation flows",
    ]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "voice_id": {
                "type": "string",
                "description": "Provider-specific voice ID. Passed through to the selected TTS provider.",
            },
            "voice": {
                "type": "string",
                "description": "Provider-specific voice name or ID. fal.ai ElevenLabs accepts names such as Rachel.",
            },
            "voice_language": {
                "type": "string",
                "enum": ["zh", "en"],
                "description": "Kling official voice language. Passed through when selected provider supports it.",
            },
            "voice_speed": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 2.0,
                "description": "Kling official voice speed. Use speed for OpenAI/ElevenLabs-style controls.",
            },
            "model_id": {
                "type": "string",
                "description": "TTS model to use (e.g. eleven-v3 or eleven_multilingual_v2). Passed through to provider.",
            },
            "stability": {
                "type": "number", "minimum": 0, "maximum": 1,
                "description": "Voice stability (ElevenLabs). Lower = more expressive.",
            },
            "similarity_boost": {
                "type": "number", "minimum": 0, "maximum": 1,
                "description": "Voice similarity boost (ElevenLabs).",
            },
            "style": {
                "type": "number", "minimum": 0, "maximum": 1,
                "description": "Style exaggeration (ElevenLabs). Higher = more expressive.",
            },
            "instructions": {
                "type": "string",
                "description": "Provider-level delivery instructions for expressive narration when supported.",
            },
            "speaking_rate": {
                "type": "number",
                "minimum": 0.25,
                "maximum": 2.0,
                "description": "Google-style speakingRate control. Use speed for OpenAI/ElevenLabs-style controls.",
            },
            "speed": {
                "type": "number",
                "minimum": 0.25,
                "maximum": 4.0,
                "description": "Alias for speaking speed used by some providers.",
            },
            "pitch": {
                "type": "number",
                "minimum": -50,
                "maximum": 50,
                "description": "Provider-specific pitch control. Google TTS accepts -20..20; HeyGen-style providers may accept wider ranges.",
            },
            "input_type": {
                "type": "string",
                "enum": ["text", "ssml"],
                "default": "text",
                "description": "Use 'ssml' only when the selected provider supports tags such as <break>.",
            },
            "language_code": {
                "type": "string",
                "description": "Provider-specific language code, such as en-US for Google or en for fal.ai ElevenLabs.",
            },
            "timestamps": {
                "type": "boolean",
                "default": False,
                "description": "Request word timestamps when the selected provider supports them.",
            },
            "apply_text_normalization": {
                "type": "string",
                "enum": ["auto", "on", "off"],
                "description": "Text normalization mode for providers that support it.",
            },
            "seed": {
                "type": "integer",
                "description": "Optional generation seed for providers that support reproducible speech.",
            },
            "voice_performance": {
                "type": "object",
                "description": "Structured voice-performance plan or section delivery cues from the script artifact.",
            },
            "voice_identity": {
                "type": "object",
                "description": "Immutable provider/model/voice/locale selection for production narration.",
            },
            "voice_selection": {
                "type": "object",
                "description": "Proposal-stage voice selection; used to enforce provider identity and sample approval.",
            },
            "sample_approval": {
                "type": "object",
                "description": "Explicit approval record for the selected voice sample.",
            },
            "batch": {
                "type": "boolean",
                "default": False,
                "description": "True when this call is part of batch narration and sample approval is required.",
            },
            "segments": {
                "type": "array",
                "description": "Deterministic narration segments for a resumable batch run.",
            },
            "sample_mode": {
                "type": "boolean",
                "default": False,
                "description": "True when generating an approval sample before batch narration.",
            },
            "output_format": {
                "type": "string",
                "description": "Audio output format (e.g. mp3_44100_128). Passed through to provider.",
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
            "output_path": {"type": "string"},
        },
    }

    def _providers(self) -> list[BaseTool]:
        """Auto-discover TTS providers from the registry."""
        from tools.tool_registry import registry
        registry.ensure_discovered()
        return [t for t in registry.get_by_capability("tts")
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
        from lib.scoring import rank_providers
        from lib.media_contracts import MediaContractError, strict_bool

        # Batch/sample controls are approval gates.  Parse them before voice
        # identity normalization, provider discovery, or ranking so malformed
        # values cannot bypass the sample gate or trigger provider work.
        try:
            requested_batch = (
                strict_bool(inputs["batch"], "batch")
                if "batch" in inputs else False
            )
            sample_mode = (
                strict_bool(inputs["sample_mode"], "sample_mode")
                if "sample_mode" in inputs else False
            )
            requested_timestamps = (
                strict_bool(inputs["timestamps"], "timestamps")
                if "timestamps" in inputs else None
            )
        except MediaContractError as exc:
            return ToolResult(success=False, error=f"Invalid voice batch control: {exc}")

        operation = inputs.get("operation", "generate")
        if operation not in {"generate", "rank", "batch_generate"}:
            return ToolResult(success=False, error=f"Unknown operation: {operation}")
        segments = inputs.get("segments")
        if segments is not None and not isinstance(segments, list):
            return ToolResult(success=False, error="segments must be an array")
        if requested_timestamps is not None:
            inputs = dict(inputs)
            inputs["timestamps"] = requested_timestamps

        # A selected voice is a hard production contract.  A provider may not
        # silently substitute a nominally similar default, and a batch run may
        # not start before its required sample is explicitly approved.
        from lib.voice_contracts import (
            VoiceContractError,
            normalize_voice_identity,
            require_voice_sample_approval,
        )

        selection = inputs.get("voice_selection")
        if not isinstance(selection, dict):
            selection = {}
        identity_payload = inputs.get("voice_identity")
        if identity_payload is None and any(
            selection.get(field) not in (None, "")
            for field in ("provider", "model", "model_id", "voice_id", "voice", "locale", "language_code")
        ):
            identity_payload = selection
        selected_identity = None
        if identity_payload is not None:
            try:
                selected_identity = normalize_voice_identity(identity_payload)
            except VoiceContractError as exc:
                return ToolResult(success=False, error=f"voice identity contract failed: {exc}")
            # Identity-bearing calls are pinned to the declared provider.  The
            # selector may rank alternatives for display, but cannot execute a
            # different provider without a new approved decision.
            inputs = dict(inputs)
            requested_provider = str(inputs.get("preferred_provider") or "auto").strip().lower()
            requested_provider = {"edge": "edge_tts", "microsoft_edge": "edge_tts", "open-ai": "openai"}.get(requested_provider, requested_provider)
            if requested_provider not in {"auto", selected_identity.provider}:
                return ToolResult(
                    success=False,
                    error=(
                        f"preferred_provider {requested_provider!r} conflicts with locked voice provider "
                        f"{selected_identity.provider!r}"
                    ),
                )
            requested_allowed = inputs.get("allowed_providers")
            if requested_allowed:
                allowed = {
                    {"edge": "edge_tts", "microsoft_edge": "edge_tts", "open-ai": "openai"}.get(str(item).strip().lower(), str(item).strip().lower())
                    for item in requested_allowed
                }
                if selected_identity.provider not in allowed:
                    return ToolResult(
                        success=False,
                        error=(
                            f"allowed_providers {sorted(allowed)!r} exclude locked voice provider "
                            f"{selected_identity.provider!r}"
                        ),
                    )
            inputs["preferred_provider"] = selected_identity.provider
            inputs["allowed_providers"] = [selected_identity.provider]
            inputs.setdefault("model", selected_identity.model)
            inputs.setdefault("model_id", selected_identity.model)
            inputs.setdefault("voice", selected_identity.voice_id)
            inputs.setdefault("voice_id", selected_identity.voice_id)
            inputs.setdefault("language_code", selected_identity.locale)
        batch = requested_batch or bool(segments) or operation == "batch_generate"
        try:
            require_voice_sample_approval(
                selection,
                sample=inputs.get("sample_approval") if isinstance(inputs.get("sample_approval"), dict) else None,
                batch=batch and not sample_mode,
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

        task_context = self._prepare_task_context(inputs)
        candidates = self._providers()

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
            return ToolResult(success=False, error="No TTS provider available.")

        if selected_identity is not None:
            provider = str(getattr(tool, "provider", "")).strip().lower()
            canonical_provider = {"edge": "edge_tts", "microsoft_edge": "edge_tts", "open-ai": "openai"}.get(provider, provider)
            if canonical_provider != selected_identity.provider:
                return ToolResult(
                    success=False,
                    error=(
                        f"voice identity provider {selected_identity.provider!r} is unavailable; "
                        f"selected provider {canonical_provider!r} would change the approved voice"
                    ),
                )

        adapted_inputs = self._adapt_inputs(tool, inputs)
        from lib.providers.bridge import execute_with_provider_executor

        # All selector executions use the common kernel.  Legacy direct
        # selector callers remain compatible through the bridge's explicit
        # non-production context; project/run identity callers must provide
        # approval before a paid operation can proceed.
        result = execute_with_provider_executor(tool, adapted_inputs)
        if result.success:
            result.data.setdefault("selected_tool", tool.name)
            result.data["selected_provider"] = tool.provider
            if selected_identity is not None:
                result.data["voice_identity"] = selected_identity.contract()
            result.data["selection_reason"] = score.explain() if score else f"Selected {tool.provider} ({tool.name})"
            if score:
                result.data["provider_score"] = score.to_dict()
            result.data.update(self._tool_context_payload(tool))
            result.data["alternatives_considered"] = [
                t.name for t in candidates
                if t.name != tool.name and t.get_status().value == "available"
            ]
        return result

    @staticmethod
    def _adapt_inputs(tool: BaseTool, inputs: dict[str, Any]) -> dict[str, Any]:
        """Translate capability-level controls to provider-native inputs."""
        adapted = dict(inputs)
        if tool.name != "azure_tts":
            return adapted

        if inputs.get("voice_id") and not inputs.get("voice"):
            adapted["voice"] = inputs["voice_id"]

        speed = inputs.get("speaking_rate", inputs.get("speed"))
        if speed is not None and "rate" not in inputs:
            percent = round((float(speed) - 1.0) * 100)
            adapted["rate"] = f"{percent:+d}%" if percent else "0%"

        pitch = inputs.get("pitch")
        if isinstance(pitch, (int, float)):
            adapted["pitch"] = f"{pitch:+g}st" if pitch else "0%"

        # The selector's numeric style is ElevenLabs-specific. Azure's style
        # is a named express-as value such as "calm" or "newscast".
        if not isinstance(inputs.get("style"), str):
            adapted.pop("style", None)

        output_format = str(inputs.get("output_format", ""))
        if output_format.startswith("mp3"):
            adapted["output_format"] = "mp3"
        elif output_format.startswith(("wav", "riff", "pcm")):
            adapted["output_format"] = "wav"
        return adapted

    def _select_best_tool(
        self,
        inputs: dict[str, Any],
        candidates: list[BaseTool],
        task_context: dict[str, Any],
    ) -> tuple[BaseTool | None, object]:
        """Select the best TTS provider using scored ranking."""
        from lib.scoring import rank_providers

        preferred = inputs.get("preferred_provider", "auto")
        allowed = set(inputs.get("allowed_providers") or [])
        if allowed:
            candidates = [tool for tool in candidates if tool.provider in allowed]

        rankings = rank_providers(candidates, task_context)

        tool_by_provider: dict[str, BaseTool] = {}
        for tool in candidates:
            if tool.provider not in tool_by_provider and tool.get_status() == ToolStatus.AVAILABLE:
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
            prompt=inputs.get("text", ""),
            capability=self.capability,
            operation=inputs.get("operation", "generate"),
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
                item["status"] = str(tool.get_status())
            serialized.append(item)
        return serialized
