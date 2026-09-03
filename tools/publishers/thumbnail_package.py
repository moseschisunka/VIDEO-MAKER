"""Build a validated title and thumbnail package for a creator profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.content_templates import load_content_template
from lib.creator_profile import load_creator_profile
from schemas.artifacts import validate_artifact
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)


class ThumbnailPackageBuilder(BaseTool):
    """Create the packaging artifact that must precede script production."""

    name = "thumbnail_package"
    version = "0.1.0"
    tier = ToolTier.PUBLISH
    capability = "packaging"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []
    install_instructions = "No setup required — uses local YAML and JSON schemas."
    capabilities = ["package_title_thumbnail", "validate_thumbnail_variants"]
    supports = {"local_offline": True, "free": True, "uploads": False}
    best_for = [
        "locking an honest title-thumbnail promise before scripting",
        "creating review-ready iLearnZed packaging variants",
        "preserving template, CTA, logo, and quality-gate context",
    ]

    input_schema = {
        "type": "object",
        "required": [
            "video_id",
            "template",
            "viewer_promise",
            "title_options",
            "thumbnail_variants",
            "recommended_pair",
        ],
        "properties": {
            "video_id": {"type": "string"},
            "template": {"type": "string"},
            "viewer_promise": {"type": "string"},
            "central_question": {"type": "string"},
            "misconception": {"type": "string"},
            "story_lens": {"type": "string"},
            "language": {"type": "string", "default": "en"},
            "title_options": {"type": "array", "items": {"type": "string"}},
            "thumbnail_variants": {"type": "array", "items": {"type": "object"}},
            "recommended_pair": {"type": "object"},
            "experiment": {"type": "object"},
            "output_path": {"type": "string"},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "thumbnail_package": {"type": "object"},
            "output_path": {"type": "string"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=1)
    side_effects = ["optionally writes a JSON packaging artifact to output_path"]
    user_visible_verification = [
        "Review five title options, three thumbnail variants, and the recommended pair before scripting."
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            profile = load_creator_profile()
            template = load_content_template(inputs["template"])
        except (FileNotFoundError, ValueError, TypeError) as exc:
            return ToolResult(success=False, error=str(exc))

        title_options = list(inputs.get("title_options") or [])
        variants = list(inputs.get("thumbnail_variants") or [])
        recommended = dict(inputs.get("recommended_pair") or {})

        required_variants = int(profile["thumbnail"].get("required_variants", 3))
        if len(title_options) < 5:
            return ToolResult(success=False, error="At least five title options are required.")
        if len(variants) != required_variants:
            return ToolResult(
                success=False,
                error=f"iLearnZed requires exactly {required_variants} thumbnail variants.",
            )
        if recommended.get("title") not in title_options:
            return ToolResult(
                success=False,
                error="recommended_pair.title must match one of title_options.",
            )
        variant_ids = {variant.get("id") for variant in variants}
        if recommended.get("thumbnail_variant_id") not in variant_ids:
            return ToolResult(
                success=False,
                error="recommended_pair.thumbnail_variant_id must match a thumbnail variant id.",
            )

        artifact: dict[str, Any] = {
            "version": "1.0",
            "video_id": inputs["video_id"],
            "target_audience": template["audience"]["primary"],
            "viewer_promise": inputs["viewer_promise"],
            "central_question": inputs.get("central_question", ""),
            "misconception": inputs.get("misconception", ""),
            "story_lens": inputs.get("story_lens", ""),
            "language": inputs.get("language", profile["language"]["primary"]),
            "title_options": title_options,
            "thumbnail_variants": variants,
            "recommended_pair": recommended,
            "quality_gates": list(dict.fromkeys(
                template["quality_gates"] +
                profile["thumbnail"]["required_checks"]
            )),
            "metadata": {
                "profile_id": profile["id"],
                "template_id": template["id"],
                "default_pipeline": template["production"]["default_pipeline"],
                "cta_text": profile["cta"]["default_text"],
                "cta_destination": profile["cta"]["temporary_destination"],
                "logo_asset": profile["thumbnail"]["logo_asset"],
            },
        }
        if inputs.get("experiment"):
            artifact["experiment"] = inputs["experiment"]

        try:
            validate_artifact("thumbnail_package", artifact)
        except Exception as exc:
            return ToolResult(success=False, error=f"thumbnail_package failed schema validation: {exc}")

        output_path = inputs.get("output_path")
        if output_path:
            destination = Path(output_path).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

        data: dict[str, Any] = {"thumbnail_package": artifact}
        if output_path:
            data["output_path"] = str(Path(output_path).expanduser())
        return ToolResult(success=True, data=data, artifacts=[str(output_path)] if output_path else [])
