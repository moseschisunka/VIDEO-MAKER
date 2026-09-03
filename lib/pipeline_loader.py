"""Pipeline manifest loader.

Loads and validates pipeline YAML manifests from pipeline_defs/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml
import jsonschema

PIPELINE_DEFS_DIR = Path(__file__).resolve().parent.parent / "pipeline_defs"
SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "pipelines"
    / "pipeline_manifest.schema.json"
)


from functools import lru_cache


@lru_cache(maxsize=1)
def _load_manifest_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=64)
def _load_pipeline_cached(name: str, defs_dir_key: str) -> dict[str, Any]:
    """Cached manifest load. Treat the returned dict as READ-ONLY."""
    return load_pipeline(name, Path(defs_dir_key) if defs_dir_key else None)


def load_pipeline_readonly(name: str, defs_dir: Optional[Path] = None) -> dict[str, Any]:
    """Load a manifest through a cache. The result MUST NOT be mutated.

    Manifests are immutable within a run; hot paths (gate checks on every
    checkpoint write, board state derivation) should use this instead of
    re-parsing YAML + re-validating the schema each call.
    """
    return _load_pipeline_cached(name, str(defs_dir) if defs_dir else "")


def load_pipeline(name: str, defs_dir: Optional[Path] = None) -> dict[str, Any]:
    """Load and validate a pipeline manifest by name.

    Args:
        name: Pipeline name (without .yaml extension).
        defs_dir: Override directory for pipeline definitions.

    Returns:
        Validated pipeline manifest dict.
    """
    defs_dir = defs_dir or PIPELINE_DEFS_DIR
    path = defs_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Pipeline manifest not found: {path}")

    with open(path, encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    schema = _load_manifest_schema()
    jsonschema.validate(instance=manifest, schema=schema)

    return manifest


def list_pipelines(defs_dir: Optional[Path] = None) -> list[str]:
    """List all available pipeline manifest names."""
    defs_dir = defs_dir or PIPELINE_DEFS_DIR
    return [p.stem for p in defs_dir.glob("*.yaml")]


MANIFEST_MATURITIES = {"test", "experimental", "beta", "production"}


def get_manifest_release_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return release metadata with conservative, non-mutating defaults.

    JSON Schema ``default`` annotations do not modify a loaded YAML object.
    Callers therefore use this helper when they need effective values.  A
    missing/invalid maturity is *experimental* and a missing visibility flag
    is hidden; neither default can accidentally advertise production support.
    """
    raw_maturity = manifest.get("maturity")
    maturity = raw_maturity if raw_maturity in MANIFEST_MATURITIES else "experimental"
    return {
        "ui_visible": bool(manifest.get("ui_visible", False)),
        "maturity": maturity,
        "supported_runtimes": list(manifest.get("supported_runtimes") or []),
        "supported_profiles": list(manifest.get("supported_profiles") or []),
        "required_capabilities": list(manifest.get("required_capabilities") or []),
        "required_artifacts": list(manifest.get("required_artifacts") or []),
        "deprecated": bool(manifest.get("deprecated", False)),
        "replacement_pipeline": manifest.get("replacement_pipeline"),
        "deprecation_reason": manifest.get("deprecation_reason"),
    }


def _condition_is_active(condition: Optional[str], context: Optional[dict[str, Any]]) -> bool:
    """Evaluate a simple manifest condition against runtime context."""
    if not condition:
        return True
    if not context:
        return False
    return bool(context.get(condition))


def get_reference_input_config(manifest: dict) -> dict[str, Any]:
    """Return reference-input configuration, defaulting to disabled."""
    return manifest.get("reference_input", {}) or {}


def pipeline_supports_reference_input(manifest: dict) -> bool:
    """Whether the manifest declares support for reference-video input."""
    return bool(get_reference_input_config(manifest).get("supported", False))


def get_stage_sub_stages(
    manifest: dict,
    stage_name: str,
    *,
    context: Optional[dict[str, Any]] = None,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    """Return sub-stage definitions for a stage.

    By default this returns all declared sub-stages so agents can inspect the
    full workflow shape. Pass ``include_inactive=False`` with context to filter
    to active sub-stages only.
    """
    for stage in manifest["stages"]:
        if stage["name"] != stage_name:
            continue
        sub_stages = list(stage.get("sub_stages", []))
        if include_inactive:
            return sub_stages
        return [
            sub_stage
            for sub_stage in sub_stages
            if _condition_is_active(sub_stage.get("condition"), context)
        ]
    return []


def get_stage_order(
    manifest: dict,
    *,
    include_sub_stages: bool = False,
    context: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Extract the ordered list of stage names from a manifest.

    ``include_sub_stages=True`` exposes declarative sample/preview units to the
    agent without turning them into mandatory checkpoint stages. Sub-stages are
    emitted as ``<stage>.<sub_stage>``.
    """
    order: list[str] = []
    for stage in manifest["stages"]:
        order.append(stage["name"])
        if not include_sub_stages:
            continue
        for sub_stage in get_stage_sub_stages(
            manifest,
            stage["name"],
            context=context,
            include_inactive=context is None,
        ):
            order.append(f"{stage['name']}.{sub_stage['name']}")
    return order


def get_required_tools(manifest: dict) -> set[str]:
    """Collect tools across stages, sub-stages, and reference-input analysis."""
    tools: set[str] = set()
    for stage in manifest["stages"]:
        tools.update(stage.get("preferred_tools", []))
        tools.update(stage.get("fallback_tools", []))
        tools.update(stage.get("tools_available", []))
        for sub_stage in stage.get("sub_stages", []):
            tools.update(sub_stage.get("tools_available", []))
    tools.update(get_reference_input_config(manifest).get("analysis_tools", []))
    return tools


def get_stage_skill(manifest: dict, stage_name: str) -> Optional[str]:
    """Get the skill path for an instruction-driven stage."""
    for stage in manifest["stages"]:
        if stage["name"] == stage_name:
            return stage.get("skill")
    return None


def get_stage_human_approval_default(manifest: dict, stage_name: str) -> Optional[bool]:
    """Whether a stage gates on human approval. None if the stage isn't declared.

    This is the single lookup used by gate enforcement (lib/checkpoint.py)
    and the Backlot board — keep them reading the same field the same way.
    """
    for stage in manifest["stages"]:
        if stage["name"] == stage_name:
            return bool(stage.get("human_approval_default", False))
    return None


def get_stage_review_focus(manifest: dict, stage_name: str) -> list[str]:
    """Get the review focus items for a stage."""
    for stage in manifest["stages"]:
        if stage["name"] == stage_name:
            return stage.get("review_focus", [])
    return []


# ---------------------------------------------------------------------------
# Manifest → agent-contract validation
# ---------------------------------------------------------------------------

class ManifestAgentContractError(ValueError):
    """Raised when a pipeline cannot be represented by its agent contract."""


def _contract_skill_path(skill_ref: str, repo_root: Path) -> Optional[Path]:
    """Resolve a manifest skill reference without allowing path traversal."""
    if not isinstance(skill_ref, str) or not skill_ref.strip():
        return None
    raw = skill_ref.strip().replace("\\", "/")
    parts = Path(raw).parts
    if raw.startswith("/") or ".." in parts:
        return None
    candidates = [
        repo_root / "skills" / f"{raw}.md",
        repo_root / "skills" / raw / "SKILL.md",
        repo_root / ".agents" / "skills" / raw / "SKILL.md",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _contract_string_list(
    value: Any,
    *,
    field: str,
    issues: list[dict[str, Any]],
    allow_empty: bool = True,
) -> list[str]:
    if value is None:
        if allow_empty:
            return []
        issues.append({"code": "missing_list", "field": field, "message": f"{field} must be declared"})
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        issues.append({"code": "invalid_list", "field": field, "message": f"{field} must be a list of non-empty strings"})
        return []
    values = [item.strip() for item in value]
    if len(set(values)) != len(values):
        issues.append({"code": "duplicate_list_item", "field": field, "message": f"{field} contains duplicates"})
    if not allow_empty and not values:
        issues.append({"code": "empty_list", "field": field, "message": f"{field} must not be empty"})
    return values


def validate_manifest_agent_contract(
    manifest: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Return a deterministic report for a manifest's stage/agent contract.

    The validator is read-only.  It checks skill readability, artifact schema
    coverage, tool-policy shape, checkpoint/gate declarations, review focus,
    and release support metadata.  It never imports or calls a provider tool.
    """
    root = Path(repo_root) if repo_root else PIPELINE_DEFS_DIR.parent
    issues: list[dict[str, Any]] = []
    pipeline_name = str(manifest.get("name") or "unknown") if isinstance(manifest, Mapping) else "unknown"
    if not isinstance(manifest, Mapping):
        return {
            "valid": False,
            "pipeline_type": pipeline_name,
            "manifest_version": None,
            "visible": False,
            "production_support": {"status": "invalid_manifest", "production_ready": False, "production_gate": "PR-11G"},
            "stages": [],
            "issues": [{"code": "invalid_manifest", "field": "manifest", "message": "manifest must be an object"}],
        }

    try:
        jsonschema.validate(
            instance=dict(manifest),
            schema=_load_manifest_schema(),
        )
    except jsonschema.ValidationError as exc:
        issues.append({
            "code": "manifest_schema_invalid",
            "field": str(exc.json_path or "manifest"),
            "message": exc.message,
        })

    release = get_manifest_release_metadata(dict(manifest))
    from lib.pipeline_release import pipeline_release_metadata
    scope_release = pipeline_release_metadata(
        pipeline_name,
        manifest=dict(manifest),
        schema_valid=True,
    )
    # The reviewed release scope is the effective user-facing visibility. A
    # manifest may predate the metadata fields and therefore have the
    # conservative manifest default ``ui_visible: false`` while the scope
    # explicitly keeps it discoverable as a held beta/experimental lane.
    visible = scope_release["ui_visible"]
    maturity = release["maturity"]
    if visible and maturity == "test":
        issues.append({"code": "visible_test_manifest", "field": "maturity", "message": "a user-visible manifest cannot have test maturity"})
    if visible and not release["supported_runtimes"]:
        issues.append({"code": "missing_supported_runtimes", "field": "supported_runtimes", "message": "visible manifests must declare supported runtimes"})
    if visible and not release["supported_profiles"]:
        issues.append({"code": "missing_supported_profiles", "field": "supported_profiles", "message": "visible manifests must declare supported output profiles"})

    from schemas.artifacts import ARTIFACT_NAMES, load_schema

    def artifact_contract(
        name: str,
        field: str,
        target: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        target = target if target is not None else issues
        if name not in ARTIFACT_NAMES:
            target.append({"code": "unknown_artifact", "field": field, "artifact": name, "message": f"unknown canonical artifact {name!r}"})
            return {"name": name, "schema": None, "valid": False}
        try:
            load_schema(name)
        except Exception as exc:
            target.append({"code": "missing_artifact_schema", "field": field, "artifact": name, "message": str(exc)})
            return {"name": name, "schema": None, "valid": False}
        return {"name": name, "schema": f"schemas/artifacts/{name}.schema.json", "valid": True}

    top_required = _contract_string_list(
        manifest.get("required_artifacts"), field="required_artifacts", issues=issues
    )
    top_artifacts = [artifact_contract(name, "required_artifacts") for name in top_required]
    stages = manifest.get("stages")
    if not isinstance(stages, list) or not stages:
        issues.append({"code": "missing_stages", "field": "stages", "message": "manifest must declare at least one stage"})
        stages = []

    stage_reports: list[dict[str, Any]] = []
    produced_names: list[str] = []
    for index, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            issues.append({"code": "invalid_stage", "field": f"stages[{index}]", "message": "stage must be an object"})
            continue
        stage_name = str(stage.get("name") or "")
        prefix = f"stages[{index}]"
        stage_issues: list[dict[str, Any]] = []
        if not stage_name:
            stage_issues.append({"code": "missing_stage_name", "field": f"{prefix}.name", "message": "stage name is required"})

        skill_ref = stage.get("skill")
        skill_path = _contract_skill_path(skill_ref, root) if isinstance(skill_ref, str) else None
        if not isinstance(skill_ref, str) or not skill_ref.strip():
            stage_issues.append({"code": "missing_director_skill", "field": f"{prefix}.skill", "message": "stage must declare a director skill"})
        elif skill_path is None:
            stage_issues.append({"code": "missing_director_skill", "field": f"{prefix}.skill", "message": f"skill file not found for {skill_ref!r}"})

        produces = _contract_string_list(stage.get("produces"), field=f"{prefix}.produces", issues=stage_issues, allow_empty=False)
        produced_names.extend(produces)
        produced_contracts = [artifact_contract(name, f"{prefix}.produces", stage_issues) for name in produces]
        required_in = _contract_string_list(stage.get("required_artifacts_in"), field=f"{prefix}.required_artifacts_in", issues=stage_issues)
        required_in_contracts = [artifact_contract(name, f"{prefix}.required_artifacts_in", stage_issues) for name in required_in]

        tools_available = _contract_string_list(stage.get("tools_available"), field=f"{prefix}.tools_available", issues=stage_issues)
        preferred = _contract_string_list(stage.get("preferred_tools"), field=f"{prefix}.preferred_tools", issues=stage_issues)
        fallback = _contract_string_list(stage.get("fallback_tools"), field=f"{prefix}.fallback_tools", issues=stage_issues)
        required_tools = _contract_string_list(stage.get("required_tools"), field=f"{prefix}.required_tools", issues=stage_issues)
        optional_tools = _contract_string_list(stage.get("optional_tools"), field=f"{prefix}.optional_tools", issues=stage_issues)
        permitted_tools = sorted(set(tools_available) | set(preferred) | set(fallback) | set(required_tools) | set(optional_tools))
        declared_tool_pool = set(tools_available) | set(preferred) | set(fallback)
        if required_tools and not set(required_tools) <= declared_tool_pool:
            stage_issues.append({"code": "required_tool_not_permitted", "field": f"{prefix}.required_tools", "message": "required tools must be present in tools_available, preferred_tools, or fallback_tools"})

        checkpoint_required = stage.get("checkpoint_required")
        if not isinstance(checkpoint_required, bool):
            stage_issues.append({"code": "missing_checkpoint_policy", "field": f"{prefix}.checkpoint_required", "message": "checkpoint_required must be an explicit boolean"})
        human_approval = stage.get("human_approval_default")
        if not isinstance(human_approval, bool):
            stage_issues.append({"code": "missing_gate_policy", "field": f"{prefix}.human_approval_default", "message": "human_approval_default must be an explicit boolean"})
        review_focus = _contract_string_list(stage.get("review_focus"), field=f"{prefix}.review_focus", issues=stage_issues, allow_empty=False)
        success_criteria = _contract_string_list(stage.get("success_criteria"), field=f"{prefix}.success_criteria", issues=stage_issues)

        issues.extend({**item, "stage": stage_name} for item in stage_issues)
        stage_reports.append({
            "name": stage_name,
            "order": index,
            "skill": skill_ref,
            "skill_path": str(skill_path.relative_to(root)).replace("\\", "/") if skill_path else None,
            "artifacts": {
                "produces": produced_contracts,
                "required_in": required_in_contracts,
            },
            "tools": {
                "permitted": permitted_tools,
                "required": required_tools,
                "available": tools_available,
                "preferred": preferred,
                "fallback": fallback,
                "optional": optional_tools,
            },
            "checkpoint": {
                "required": checkpoint_required,
                "human_approval_default": human_approval,
            },
            "review_focus": review_focus,
            "success_criteria": success_criteria,
            "valid": not stage_issues,
        })

    if len({report["name"] for report in stage_reports}) != len(stage_reports):
        issues.append({"code": "duplicate_stage_name", "field": "stages", "message": "stage names must be unique"})
    produced_set = set(produced_names)
    for name in top_required:
        if name not in produced_set:
            issues.append({"code": "required_artifact_not_produced", "field": "required_artifacts", "artifact": name, "message": f"manifest-required artifact {name!r} is not produced by a declared stage"})

    valid = not issues
    production_status = "supported" if valid and visible else ("not_user_facing" if not visible else "blocked")
    return {
        "valid": valid,
        "pipeline_type": pipeline_name,
        "manifest_version": manifest.get("version"),
        "visible": visible,
        "production_support": {
            "status": production_status,
            "maturity": maturity,
            "release_lane": scope_release["release_lane"],
            "creation_enabled": scope_release["creation_enabled"],
            "production_ready": False,
            "production_gate": "PR-11G",
            "release_status": scope_release["release_status"],
            "required_capabilities": release["required_capabilities"],
            "required_artifacts": top_artifacts,
        },
        "stages": stage_reports,
        "issues": issues,
    }


def assert_manifest_agent_contract(
    manifest: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Validate a manifest and raise a concise error when it is not runnable."""
    report = validate_manifest_agent_contract(manifest, repo_root=repo_root)
    if not report["valid"]:
        raise ManifestAgentContractError(
            f"manifest {report['pipeline_type']!r} agent contract invalid: "
            + "; ".join(str(item.get("message")) for item in report["issues"][:8])
        )
    return report


# ---------------------------------------------------------------------------
# Capability-Extension Enforcement
# ---------------------------------------------------------------------------

class ExtensionNotPermitted(PermissionError):
    """Raised when a capability extension is used but not permitted by the pipeline."""


def check_extension_permitted(
    manifest: dict,
    extension_type: str,
) -> None:
    """Enforce that a capability extension is permitted by the pipeline manifest.

    Args:
        manifest: Loaded pipeline manifest dict.
        extension_type: One of 'custom_scripts', 'custom_playbooks',
                        'custom_skills', 'custom_tools'.

    Raises:
        ExtensionNotPermitted: If the extension is not allowed.
    """
    valid_extensions = {"custom_scripts", "custom_playbooks", "custom_skills", "custom_tools"}
    if extension_type not in valid_extensions:
        raise ValueError(
            f"Unknown extension type {extension_type!r}. "
            f"Valid types: {sorted(valid_extensions)}"
        )

    extensions = manifest.get("extensions", {})
    if not extensions.get(extension_type, False):
        raise ExtensionNotPermitted(
            f"Pipeline {manifest.get('name', 'unknown')!r} does not permit "
            f"{extension_type}. Set extensions.{extension_type}: true in the "
            f"pipeline manifest to allow this."
        )


def get_permitted_extensions(manifest: dict) -> dict[str, bool]:
    """Return the extension permission flags for a pipeline."""
    defaults = {
        "custom_scripts": False,
        "custom_playbooks": False,
        "custom_skills": False,
        "custom_tools": False,
    }
    extensions = manifest.get("extensions", {})
    return {k: extensions.get(k, v) for k, v in defaults.items()}


def _list_pipeline_catalog_uncached(
    defs_dir: Optional[Path] = None,
    *,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """Build the one release-aware catalog consumed by Backlot and agents.

    Discovery intentionally uses the same validated loader as execution.  The
    optional ``include_hidden`` view is for audits/diagnostics only; ordinary
    callers receive only entries permitted by the reviewed release scope.
    Malformed manifests are retained only in the hidden audit view and are
    never marked creatable.
    """
    defs_dir = defs_dir or PIPELINE_DEFS_DIR
    if not defs_dir.is_dir():
        return []

    from lib.pipeline_release import pipeline_release_metadata, pipeline_sort_key

    records: list[dict[str, Any]] = []
    for path in sorted(defs_dir.glob("*.yaml")):
        name = path.stem
        manifest: dict[str, Any] | None = None
        schema_valid = False
        validation_error: str | None = None
        try:
            manifest = load_pipeline_readonly(name, defs_dir=defs_dir)
            schema_valid = True
        except Exception as exc:
            validation_error = str(exc)
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                manifest = raw if isinstance(raw, dict) else None
            except Exception as raw_exc:
                validation_error = f"{validation_error}; raw load failed: {raw_exc}"

        release = pipeline_release_metadata(
            name,
            manifest=manifest,
            schema_valid=schema_valid,
            validation_error=validation_error,
        )
        agent_contract = (
            validate_manifest_agent_contract(manifest, repo_root=defs_dir.parent)
            if schema_valid and manifest is not None
            else {
                "valid": False,
                "pipeline_type": name,
                "manifest_version": manifest.get("version") if isinstance(manifest, dict) else None,
                "visible": release["ui_visible"],
                "production_support": {
                    "status": "invalid_manifest",
                    "production_ready": False,
                    "production_gate": release["production_gate"],
                },
                "stages": [],
                "issues": [{"code": "invalid_manifest", "field": "manifest", "message": validation_error or "manifest could not be validated"}],
            }
        )
        if not include_hidden and not release["ui_visible"]:
            continue

        manifest = manifest or {}
        stages = manifest.get("stages") if isinstance(manifest.get("stages"), list) else []
        records.append({
            "id": name,
            "name": manifest.get("name", name.replace("-", " ").title()),
            "type": manifest.get("type", name),
            "version": manifest.get("version"),
            "description": manifest.get("description", ""),
            "default_playbook": manifest.get("default_playbook", "premium-minimalist"),
            "category": manifest.get("category"),
            "manifest_stability": manifest.get("stability"),
            "maturity": release["manifest_maturity"],
            "stages": stages,
            "stage_order": [
                str(stage.get("name")) for stage in stages
                if isinstance(stage, dict) and stage.get("name")
            ],
            "required_tools": sorted(get_required_tools(manifest)) if schema_valid else [],
            "agent_contract_valid": bool(agent_contract["valid"]),
            "agent_contract_issues": agent_contract["issues"],
            "agent_contract": agent_contract,
            **release,
        })
    records.sort(key=pipeline_sort_key)
    return records


@lru_cache(maxsize=8)
def _pipeline_catalog_cached(
    defs_dir_key: str,
    include_hidden: bool,
    file_fingerprint: tuple[tuple[str, int, int], ...],
) -> tuple[dict[str, Any], ...]:
    """Cache the expensive release-aware catalog calculation.

    Catalog construction validates every manifest, director skill, and
    artifact schema.  Backlot create validation calls it on every request, so
    recomputing the same 13-manifest report made a warm request approach a
    second on modest machines.  The fingerprint keeps development edits and
    deploy-time package replacement safe while returning an immutable tuple;
    callers receive a deep copy from :func:`list_pipeline_catalog` below.
    """

    del file_fingerprint  # the key is used for invalidation, not computation
    return tuple(
        _list_pipeline_catalog_uncached(
            Path(defs_dir_key), include_hidden=include_hidden
        )
    )


def list_pipeline_catalog(
    defs_dir: Optional[Path] = None,
    *,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """Return a copy of the cached release-aware pipeline catalog."""

    import copy

    root = (defs_dir or PIPELINE_DEFS_DIR).expanduser().resolve()
    fingerprint: list[tuple[str, int, int]] = []
    try:
        for path in sorted(root.glob("*.yaml")):
            stat = path.stat()
            fingerprint.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
    except OSError:
        fingerprint = []
    report = _pipeline_catalog_cached(
        str(root), bool(include_hidden), tuple(fingerprint)
    )
    return copy.deepcopy(list(report))


# Short alias for callers that describe the result as a catalog.
pipeline_catalog = list_pipeline_catalog


if __name__ == "__main__":
    # Read-only diagnostic surface used by operators and release evidence.
    import sys

    print(json.dumps(
        list_pipeline_catalog(include_hidden="--include-hidden" in sys.argv[1:]),
        indent=2,
    ))
