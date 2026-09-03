"""Review-gated contact-sheet builder for mixed-media candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.contact_sheet import build_contact_sheet_manifest, write_contact_sheet
from schemas.artifacts import validate_artifact
from tools.base_tool import BaseTool, Determinism, ExecutionMode, ResourceProfile, ToolResult, ToolRuntime, ToolStability, ToolTier


class ContactSheetBuilder(BaseTool):
    name = "contact_sheet"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "asset_review"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL
    dependencies = ["python:PIL"]
    install_instructions = "pip install Pillow"
    capabilities = ["contact_sheet", "asset_sample_review", "batch_approval"]
    supports = {"local_offline": True, "mixed_media": True, "approval_gate": True}
    best_for = ["scene-linked candidate review before paid generation or batch compose"]
    input_schema = {
        "type": "object",
        "required": ["batch_id", "candidates"],
        "properties": {
            "batch_id": {"type": "string"},
            "candidates": {"type": "array", "items": {"type": "object"}, "minItems": 1},
            "required_approval": {"type": "boolean", "default": True},
            "output_path": {"type": "string"},
            "manifest_path": {"type": "string"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=100)
    side_effects = ["writes a review contact-sheet image and candidate manifest"]
    user_visible_verification = ["review each scene-linked tile, provider/license/model, and estimated cost before approving the batch"]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            manifest = build_contact_sheet_manifest(
                inputs.get("candidates") or [],
                batch_id=str(inputs.get("batch_id") or ""),
                required_approval=(
                    inputs["required_approval"] if "required_approval" in inputs else True
                ),
            )
            validate_artifact("contact_sheet", manifest)
            output = inputs.get("output_path")
            rendered = write_contact_sheet(manifest, output) if output else None
            if rendered:
                manifest["contact_sheet_path"] = rendered["path"]
            manifest_path = inputs.get("manifest_path")
            if manifest_path:
                destination = Path(manifest_path).expanduser().resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            validate_artifact("contact_sheet", manifest)
            data = {"contact_sheet": manifest}
            if rendered:
                data["output_path"] = rendered["path"]
            if manifest_path:
                data["manifest_path"] = str(Path(manifest_path).expanduser().resolve())
            artifacts = [rendered["path"]] if rendered else []
            if manifest_path:
                artifacts.append(str(Path(manifest_path).expanduser().resolve()))
            return ToolResult(success=True, data=data, artifacts=artifacts)
        except Exception as exc:
            return ToolResult(success=False, error=f"contact_sheet failed: {exc}")


__all__ = ["ContactSheetBuilder"]
