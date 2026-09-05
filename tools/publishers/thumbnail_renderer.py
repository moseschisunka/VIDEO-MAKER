"""Render deterministic, branded thumbnail previews for a packaging artifact."""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from lib.creator_profile import load_creator_profile
from lib.paths import resource_path
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


def _safe_child(root: Path, raw: str, label: str) -> Path:
    """Resolve a project-relative path and reject path traversal."""
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside project_dir") from exc
    return resolved


def _slug(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_").lower()
    return text or fallback


def _wrap(text: str, max_chars: int = 19, max_lines: int = 3) -> list[str]:
    words = str(text or "").upper().split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max_chars - 1].rstrip() + "…"
    return lines or ["THUMBNAIL PREVIEW"]


def _xml(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _logo_data_uri(profile: dict[str, Any]) -> str | None:
    raw = profile.get("thumbnail", {}).get("logo_asset")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = resource_path(path)
    if not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _svg_for_variant(
    variant: dict[str, Any],
    package: dict[str, Any],
    logo_uri: str | None,
) -> str:
    on_image = _wrap(variant.get("on_image_text", ""))
    focal = _xml(variant.get("focal_subject", ""))
    proof = _xml(variant.get("visual_proof", ""))
    variant_id = _xml(variant.get("id", "variant"))
    language = _xml(package.get("language", "en"))
    title_y = 340 - (len(on_image) - 1) * 38
    title_nodes = "\n".join(
        f'  <text x="72" y="{title_y + index * 76}" class="title">{_xml(line)}</text>'
        for index, line in enumerate(on_image)
    )
    logo_node = (
        f'  <image href="{logo_uri}" x="1050" y="575" width="160" height="105" '
        'preserveAspectRatio="xMidYMid meet"/>'
        if logo_uri else
        '  <text x="1056" y="638" class="brand">iLearnZed</text>'
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" role="img" aria-labelledby="title desc">
  <title id="title">iLearnZed thumbnail preview {variant_id}</title>
  <desc id="desc">{_xml(variant.get("on_image_text", ""))} — {focal}</desc>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#062d22"/>
      <stop offset="0.56" stop-color="#168044"/>
      <stop offset="0.57" stop-color="#081714"/>
      <stop offset="1" stop-color="#020706"/>
    </linearGradient>
    <pattern id="grid" width="44" height="44" patternUnits="userSpaceOnUse">
      <path d="M 44 0 L 0 0 0 44" fill="none" stroke="#b8f0c9" stroke-opacity=".18" stroke-width="2"/>
    </pattern>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#000" flood-opacity=".65"/>
    </filter>
  </defs>
  <rect width="1280" height="720" fill="url(#bg)"/>
  <rect width="1280" height="720" fill="url(#grid)"/>
  <path d="M740 0 L1280 0 L1280 720 L560 720 Z" fill="#020706" fill-opacity=".3"/>
  <rect x="54" y="54" width="330" height="42" rx="7" fill="#06150f" fill-opacity=".76" stroke="#b8f0c9" stroke-opacity=".45"/>
  <text x="76" y="82" class="eyebrow">iLearnZed · {language} · CONCEPT {variant_id}</text>
{title_nodes}
  <text x="76" y="520" class="focal">{focal}</text>
  <rect x="72" y="550" width="650" height="2" fill="#f4d03f"/>
  <text x="76" y="582" class="proof">PROOF: {proof}</text>
  <text x="76" y="665" class="preview">PACKAGING PREVIEW · CHECK AT MOBILE SIZE</text>
{logo_node}
  <style>
    .title {{ fill: #fff; font: 800 74px/1.05 Arial, sans-serif; letter-spacing: 1px; filter: url(#shadow); }}
    .eyebrow {{ fill: #d9ffe8; font: 600 20px Arial, sans-serif; letter-spacing: 2px; }}
    .focal {{ fill: #d9ffe8; font: 500 28px Arial, sans-serif; }}
    .proof {{ fill: #f4d03f; font: 600 22px Arial, sans-serif; }}
    .preview {{ fill: #b8d5c1; font: 500 16px Arial, sans-serif; letter-spacing: 2px; }}
    .brand {{ fill: #f4d03f; font: 800 28px Arial, sans-serif; }}
  </style>
</svg>'''


class ThumbnailPreviewRenderer(BaseTool):
    """Turn a thumbnail package into project-local SVG preview assets."""

    name = "thumbnail_preview_renderer"
    version = "0.1.0"
    tier = ToolTier.PUBLISH
    capability = "packaging"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []
    install_instructions = "No setup required — renders SVG previews locally."
    capabilities = ["render_thumbnail_previews", "attach_preview_paths"]
    supports = {"local_offline": True, "free": True, "uploads": False}
    best_for = [
        "creating reviewable thumbnail assets before image-generation spend",
        "attaching truthful iLearnZed packaging previews to a Backlot project",
        "checking title hierarchy and mobile readability locally",
    ]

    input_schema = {
        "type": "object",
        "required": ["project_dir"],
        "properties": {
            "project_dir": {"type": "string"},
            "thumbnail_package": {"type": "object"},
            "package_path": {"type": "string"},
            "output_dir": {"type": "string", "default": "assets/images/thumbnails"},
            "output_package_path": {"type": "string"},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "thumbnail_package": {"type": "object"},
            "preview_paths": {"type": "array", "items": {"type": "string"}},
            "output_path": {"type": "string"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=3)
    side_effects = [
        "writes SVG preview assets inside project_dir",
        "updates thumbnail_package.json with preview_path references",
    ]
    user_visible_verification = [
        "Open the Backlot project and inspect each attached preview at mobile size before approval."
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            project_dir = Path(inputs["project_dir"]).expanduser().resolve()
            if not project_dir.is_dir():
                raise ValueError(f"project_dir does not exist: {project_dir}")
            package = inputs.get("thumbnail_package")
            package_path = _safe_child(
                project_dir,
                str(inputs.get("package_path") or "artifacts/thumbnail_package.json"),
                "package_path",
            )
            if package is None:
                if not package_path.is_file():
                    raise FileNotFoundError(f"Thumbnail package not found: {package_path}")
                package = json.loads(package_path.read_text(encoding="utf-8"))
            if not isinstance(package, dict):
                raise ValueError("thumbnail_package must be an object")
            validate_artifact("thumbnail_package", package)

            variants = package.get("thumbnail_variants") or []
            ids = [str(item.get("id") or "") for item in variants]
            if not all(ids) or len(set(ids)) != len(ids):
                raise ValueError("thumbnail variants must have unique non-empty ids")
            output_dir = _safe_child(
                project_dir,
                str(inputs.get("output_dir") or "assets/images/thumbnails"),
                "output_dir",
            )
            output_dir.mkdir(parents=True, exist_ok=True)

            profile = load_creator_profile()
            logo_uri = _logo_data_uri(profile)
            rendered: list[str] = []
            updated_variants: list[dict[str, Any]] = []
            for index, variant in enumerate(variants, start=1):
                variant_copy = dict(variant)
                filename = f"{_slug(ids[index - 1], f'variant-{index}')}.svg"
                destination = _safe_child(output_dir, filename, "preview asset")
                destination.write_text(
                    _svg_for_variant(variant_copy, package, logo_uri), encoding="utf-8"
                )
                variant_copy["preview_path"] = destination.relative_to(project_dir).as_posix()
                updated_variants.append(variant_copy)
                rendered.append(str(destination))

            updated = dict(package)
            updated["thumbnail_variants"] = updated_variants
            metadata = dict(updated.get("metadata") or {})
            metadata.update({
                "preview_renderer": self.name,
                "preview_format": "svg",
                "preview_asset_dir": output_dir.relative_to(project_dir).as_posix(),
            })
            updated["metadata"] = metadata
            validate_artifact("thumbnail_package", updated)

            output_package = _safe_child(
                project_dir,
                str(inputs.get("output_package_path") or package_path.relative_to(project_dir)),
                "output_package_path",
            )
            output_package.parent.mkdir(parents=True, exist_ok=True)
            output_package.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
            return ToolResult(
                success=True,
                data={
                    "thumbnail_package": updated,
                    "preview_paths": rendered,
                    "output_path": str(output_package),
                },
                artifacts=[*rendered, str(output_package)],
            )
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ToolResult(success=False, error=str(exc))
