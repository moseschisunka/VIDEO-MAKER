"""PR-101 manifest inventory and repair contracts."""

from __future__ import annotations

from pathlib import Path

from lib.pipeline_loader import (
    get_manifest_release_metadata,
    get_stage_skill,
    list_pipelines,
    load_pipeline,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = PROJECT_ROOT / "pipeline_defs"
SKILLS_DIR = PROJECT_ROOT / "skills"


def test_every_pipeline_manifest_now_validates_against_the_strict_schema() -> None:
    names = sorted(list_pipelines())
    assert len(names) == 13
    for name in names:
        manifest = load_pipeline(name)
        assert manifest["name"] == name
        release = get_manifest_release_metadata(manifest)
        assert release["maturity"] in {"test", "experimental", "beta", "production"}
        assert isinstance(release["supported_runtimes"], list)
        assert isinstance(release["supported_profiles"], list)
        assert isinstance(release["required_capabilities"], list)
        assert isinstance(release["required_artifacts"], list)


def test_documentary_manifest_preserves_its_domain_category_after_schema_repair() -> None:
    manifest = load_pipeline("documentary-montage")
    assert manifest["category"] == "documentary"
    assert manifest["stability"] == "beta"


def test_non_test_manifests_have_readable_director_skills() -> None:
    for name in list_pipelines():
        if name == "framework-smoke":
            continue
        manifest = load_pipeline(name)
        for stage in manifest["stages"]:
            skill = get_stage_skill(manifest, stage["name"])
            assert skill, f"{name}:{stage['name']} has no director skill"
            assert (SKILLS_DIR / f"{skill}.md").is_file(), f"missing skill {skill}"
