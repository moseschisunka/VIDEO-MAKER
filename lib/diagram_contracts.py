"""Semantic and legibility checks for structured visual diagrams."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping


class DiagramContractError(ValueError):
    """Raised when a diagram spec or rendered fallback is ambiguous."""


def validate_diagram_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, Mapping):
        raise DiagramContractError("diagram specification must be an object")
    kind = str(spec.get("diagram_type") or "").lower()
    if kind not in {"mermaid", "flowchart", "boxes"}:
        raise DiagramContractError("diagram_type must be mermaid, flowchart, or boxes")
    labels: list[str] = []
    if kind == "mermaid":
        definition = str(spec.get("definition") or "").strip()
        if not definition:
            raise DiagramContractError("Mermaid definition required")
        # A Mermaid graph without any node/edge declaration is a semantic
        # placeholder, not a usable structured visual.
        if not re.search(r"(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|mindmap)\b", definition, re.I):
            raise DiagramContractError("Mermaid definition has no recognized diagram declaration")
        labels = [line.strip() for line in definition.splitlines() if line.strip()][:50]
    else:
        boxes = spec.get("boxes") or []
        if not isinstance(boxes, list) or not boxes:
            raise DiagramContractError("structured diagrams require at least one labelled box")
        for index, box in enumerate(boxes):
            if not isinstance(box, Mapping) or not str(box.get("label") or "").strip():
                raise DiagramContractError(f"box {index} requires a non-empty label")
            labels.append(str(box["label"]).strip())
        connections = spec.get("connections") or []
        if not isinstance(connections, list):
            raise DiagramContractError("connections must be an array")
        for index, connection in enumerate(connections):
            if not isinstance(connection, Mapping):
                raise DiagramContractError(f"connection {index} must be an object")
            try:
                source, target = int(connection.get("from")), int(connection.get("to"))
            except (TypeError, ValueError) as exc:
                raise DiagramContractError(f"connection {index} requires integer from/to indexes") from exc
            if not (0 <= source < len(boxes) and 0 <= target < len(boxes)):
                raise DiagramContractError(f"connection {index} points outside the box list")
            if connection.get("label") is not None and not str(connection.get("label") or "").strip():
                raise DiagramContractError(f"connection {index} label cannot be blank")
    width = int(spec.get("width", 1200) or 1200)
    height = int(spec.get("height", 800) or 800)
    if width < 320 or height < 180:
        raise DiagramContractError("diagram dimensions are too small for legible labels")
    return {"valid": True, "diagram_type": kind, "label_count": len(labels), "labels": labels, "width": width, "height": height}


def validate_diagram_render(path: Path | str, *, min_width: int = 320, min_height: int = 180) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    if not target.is_file() or target.stat().st_size <= 0:
        raise DiagramContractError(f"diagram render is missing or empty: {target}")
    try:
        from PIL import Image

        with Image.open(target) as image:
            image.verify()
        with Image.open(target) as image:
            width, height = int(image.width), int(image.height)
            mode = image.mode
    except Exception as exc:
        raise DiagramContractError(f"diagram render is not a decodable image: {exc}") from exc
    if width < min_width or height < min_height:
        raise DiagramContractError("diagram render is below the minimum legible dimensions")
    return {"valid": True, "path": str(target), "width": width, "height": height, "mode": mode, "size_bytes": target.stat().st_size}


__all__ = ["DiagramContractError", "validate_diagram_spec", "validate_diagram_render"]
