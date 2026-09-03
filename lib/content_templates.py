"""Load and validate reusable content production templates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import jsonschema
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "content_templates"
SCHEMA_PATH = REPO_ROOT / "schemas" / "content_templates" / "content_template.schema.json"


def _load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_content_template(
    name: str, templates_dir: Optional[Path] = None
) -> dict[str, Any]:
    """Load and validate a content template by name."""
    root = templates_dir or TEMPLATES_DIR
    path = root / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Content template not found: {path}")

    with path.open(encoding="utf-8") as handle:
        template = yaml.safe_load(handle) or {}
    jsonschema.validate(instance=template, schema=_load_schema())
    return template


def list_content_templates(
    templates_dir: Optional[Path] = None,
) -> list[str]:
    """Return available content template names."""
    root = templates_dir or TEMPLATES_DIR
    return sorted(path.stem for path in root.glob("*.yaml"))
