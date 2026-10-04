"""Tests for pipeline manifest validity, schema conformance, and skill file existence."""

from __future__ import annotations

from pathlib import Path
import pytest
import yaml

from lib.pipeline_loader import (
    PIPELINE_DEFS_DIR,
    get_stage_order,
    load_pipeline,
    load_pipeline_readonly,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_all_manifests_load_and_validate():
    """Ensure every YAML manifest in pipeline_defs/ validates against the pipeline schema."""
    manifest_paths = list(PIPELINE_DEFS_DIR.glob("*.yaml"))
    assert len(manifest_paths) >= 12, f"Expected at least 12 pipelines, found {len(manifest_paths)}"

    for path in manifest_paths:
        name = path.stem
        manifest = load_pipeline(name)
        assert manifest is not None, f"Failed to load {name}"
        assert manifest.get("name") == name, f"Manifest name mismatch in {path}"


def test_all_stage_director_skills_exist():
    """Ensure every skill referenced in each manifest stage exists on disk."""
    manifest_paths = list(PIPELINE_DEFS_DIR.glob("*.yaml"))

    missing_skills = []
    for path in manifest_paths:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        stages = data.get("stages", [])
        for stage in stages:
            skill_ref = stage.get("skill")
            if not skill_ref:
                continue

            # Resolves relative to skills/
            skill_path = REPO_ROOT / "skills" / f"{skill_ref}.md"
            if not skill_path.is_file():
                # Some references might already include .md or point directly
                alt_path = REPO_ROOT / "skills" / skill_ref
                if not alt_path.is_file():
                    missing_skills.append(f"{data.get('name')}.stages[{stage.get('name')}]: {skill_ref} -> {skill_path}")

    assert not missing_skills, f"Missing stage director skills:\n" + "\n".join(missing_skills)


def test_get_stage_order_deterministic():
    """Verify get_stage_order returns a coherent ordered sequence of stages."""
    manifest = load_pipeline("animated-explainer")
    order = get_stage_order(manifest)
    assert isinstance(order, list)
    assert len(order) > 0
    assert "research" in order or "idea" in order
    assert "compose" in order


def test_cached_pipeline_load():
    """Verify load_pipeline_readonly caches manifests properly."""
    m1 = load_pipeline_readonly("animated-explainer")
    m2 = load_pipeline_readonly("animated-explainer")
    assert m1 is m2
