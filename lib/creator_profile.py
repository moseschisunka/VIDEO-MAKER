"""Load and validate creator/channel profiles used by production workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import jsonschema
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"
SCHEMA_PATH = REPO_ROOT / "schemas" / "profiles" / "creator_profile.schema.json"


def load_creator_profile(
    name: str = "ilearnzed", profiles_dir: Optional[Path] = None
) -> dict[str, Any]:
    """Load and validate a creator profile by name or YAML path."""
    root = profiles_dir or PROFILES_DIR
    path = Path(name)
    if path.suffix.lower() not in {".yaml", ".yml"}:
        path = root / f"{name}.yaml"
    elif not path.is_absolute():
        path = REPO_ROOT / path

    if not path.exists():
        raise FileNotFoundError(f"Creator profile not found: {path}")

    with path.open(encoding="utf-8") as handle:
        profile = yaml.safe_load(handle) or {}

    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    jsonschema.validate(instance=profile, schema=schema)
    return profile


def list_creator_profiles(profiles_dir: Optional[Path] = None) -> list[str]:
    """Return available creator profile names."""
    root = profiles_dir or PROFILES_DIR
    return sorted(path.stem for path in root.glob("*.yaml"))
