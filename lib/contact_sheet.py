"""Scene-linked candidate contact sheets and immutable sample approval records."""

from __future__ import annotations

import hashlib
import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


class ContactSheetError(ValueError):
    """Raised when candidates cannot be shown or approved safely."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_contact_sheet_manifest(
    candidates: Iterable[Mapping[str, Any]],
    *,
    batch_id: str,
    required_approval: bool = True,
) -> dict[str, Any]:
    """Normalize candidates for Backlot before any batch generation."""
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ContactSheetError("batch_id is required")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        if not isinstance(raw, Mapping):
            raise ContactSheetError(f"candidate {index} must be an object")
        item = dict(raw)
        candidate_id = str(item.get("asset_id") or item.get("candidate_id") or item.get("id") or "").strip()
        scene_id = str(item.get("scene_id") or item.get("slot_id") or "").strip()
        provider = str(item.get("provider") or item.get("source") or "").strip()
        if not candidate_id or not scene_id or not provider:
            raise ContactSheetError(f"candidate {index} requires asset_id, scene_id, and provider")
        strategy = str(item.get("strategy") or item.get("source_strategy") or "").lower()
        if strategy in {"stock", "source"}:
            missing = [key for key in ("source_url", "license", "creator") if not str(item.get(key) or "").strip()]
            if missing:
                raise ContactSheetError(f"candidate {candidate_id} is missing stock provenance: {', '.join(missing)}")
        if strategy in {"ai", "generated"} and not (str(item.get("model") or "").strip() or str(item.get("prompt") or "").strip()):
            raise ContactSheetError(f"generated candidate {candidate_id} requires model or prompt provenance")
        cost = item.get("cost_usd", item.get("estimated_cost_usd", 0.0))
        try:
            cost = float(cost or 0.0)
        except (TypeError, ValueError) as exc:
            raise ContactSheetError(f"candidate {candidate_id} cost_usd must be numeric") from exc
        if cost < 0:
            raise ContactSheetError(f"candidate {candidate_id} cost_usd cannot be negative")
        normalized.append({
            "candidate_id": candidate_id,
            "scene_id": scene_id,
            "provider": provider,
            "strategy": strategy or "unknown",
            "path": str(item.get("path") or item.get("output_path") or ""),
            "thumbnail_path": str(item.get("thumbnail_path") or item.get("thumbnail") or ""),
            "source_url": str(item.get("source_url") or item.get("original_url") or ""),
            "creator": str(item.get("creator") or ""),
            "license": str(item.get("license") or ""),
            "license_url": str(item.get("license_url") or ""),
            "attribution_required": bool(item.get("attribution_required", False)),
            "restrictions": [str(value) for value in (item.get("restrictions") or [])],
            "model": str(item.get("model") or ""),
            "prompt": str(item.get("prompt") or ""),
            "cost_usd": round(cost, 6),
            "rejected": bool(item.get("rejected", False)),
            "rejection_reasons": [str(value) for value in (item.get("reasons") or item.get("rejection_reasons") or [])],
            "metadata": dict(item.get("metadata") or {}),
        })
    if not normalized:
        raise ContactSheetError("at least one candidate is required")
    body = {
        "version": "1.0",
        "batch_id": batch_id.strip(),
        "required_approval": bool(required_approval),
        "approval_status": "pending" if required_approval else "not_required",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidates": normalized,
    }
    stable_body = dict(body)
    stable_body.pop("created_at", None)
    body["manifest_hash"] = hashlib.sha256(_canonical(stable_body).encode("utf-8")).hexdigest()
    return body


def approve_contact_sheet(
    manifest: Mapping[str, Any],
    approval: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate a reviewer decision against the exact candidate manifest."""
    raw = dict(approval or {})
    expected = str(manifest.get("manifest_hash") or "")
    if raw.get("approved") is not True:
        raise ContactSheetError("contact-sheet approval is required before batch generation")
    if str(raw.get("manifest_hash") or "") != expected:
        raise ContactSheetError("approval manifest_hash does not match current candidates")
    approval_id = str(raw.get("approval_id") or "").strip()
    if not approval_id:
        raise ContactSheetError("approval_id is required")
    return {
        "approved": True,
        "approval_id": approval_id,
        "manifest_hash": expected,
        "approved_at": str(raw.get("approved_at") or datetime.now(timezone.utc).isoformat()),
        "reviewer": str(raw.get("reviewer") or ""),
        "review_notes": str(raw.get("review_notes") or ""),
    }


def write_contact_sheet(
    manifest: Mapping[str, Any],
    output_path: Path | str,
    *,
    tile_width: int = 320,
    tile_height: int = 220,
) -> dict[str, Any]:
    """Render a lightweight contact sheet; missing media stays an explicit tile."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ContactSheetError("Pillow is required to render contact sheets") from exc
    candidates = list(manifest.get("candidates") or [])
    cols = min(4, max(1, len(candidates)))
    rows = (len(candidates) + cols - 1) // cols
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (cols * tile_width, rows * tile_height), "#151722")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
        small = ImageFont.truetype("arial.ttf", 11)
    except (OSError, IOError):
        font = small = ImageFont.load_default()
    for index, candidate in enumerate(candidates):
        x, y = (index % cols) * tile_width, (index // cols) * tile_height
        image_path = Path(str(candidate.get("thumbnail_path") or candidate.get("path") or ""))
        image = None
        if image_path.is_file():
            try:
                image = Image.open(image_path).convert("RGB")
                image.thumbnail((tile_width - 16, tile_height - 78))
                canvas.paste(image, (x + (tile_width - image.width) // 2, y + 8))
            except Exception:
                image = None
        if image is None:
            draw.rectangle((x + 8, y + 8, x + tile_width - 8, y + tile_height - 78), fill="#24283a", outline="#69708c")
            draw.text((x + 16, y + 38), "preview unavailable", fill="#d7d9e8", font=font)
        label = f"{candidate.get('scene_id')} · {candidate.get('candidate_id')}"
        detail = f"{candidate.get('provider')} · {candidate.get('strategy')} · ${float(candidate.get('cost_usd') or 0):.2f}"
        draw.text((x + 8, y + tile_height - 62), label[:48], fill="#ffffff", font=font)
        draw.text((x + 8, y + tile_height - 42), detail[:52], fill="#aeb5d0", font=small)
        provenance = candidate.get("license") or candidate.get("model") or "provenance missing"
        draw.text((x + 8, y + tile_height - 24), str(provenance)[:52], fill="#8dd6ae" if provenance != "provenance missing" else "#ff8c8c", font=small)
    canvas.save(output)
    return {"path": str(output), "candidate_count": len(candidates), "manifest_hash": manifest.get("manifest_hash"), "approval_status": manifest.get("approval_status")}


__all__ = ["ContactSheetError", "build_contact_sheet_manifest", "approve_contact_sheet", "write_contact_sheet"]
