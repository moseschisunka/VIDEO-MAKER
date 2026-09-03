"""Manifest-faithful stage handoff for the agent control plane.

The execution model in OpenMontage is intentionally agent-first: the agent
reads the manifest and director skill, makes the creative decisions, and
produces the stage artifact.  Python owns only the deterministic boundary
around that work.  This module validates the selected manifest, project
identity, artifact schemas, required inputs, and local media references before
recording a checkpoint and advancing the durable work order.

It is therefore not a creative orchestrator and does not call providers,
choose a visual treatment, or fabricate missing artifacts.  A caller that has
not produced the declared artifact receives a fail-closed error.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from lib.checkpoint import (
    write_checkpoint,
)
from lib.pipeline_loader import (
    assert_manifest_agent_contract,
    get_stage_human_approval_default,
)
from lib.project_identity import assert_project_identity
from lib.work_order import (
    WorkOrderStateError,
    WorkOrderValidationError,
    _claim_owner,
    _read_execution_order,
    advance_work_order,
    next_stage_from_work_order,
    read_work_order,
)
from schemas.artifacts import validate_artifact


class ManifestExecutionError(ValueError):
    """Raised when an agent stage handoff violates the manifest contract."""


# A pipeline enters this allow-list only after a golden, manifest-faithful
# thin vertical has been verified.  Keeping the list explicit prevents a
# newly discovered manifest from being treated as runnable merely because its
# schema happens to validate.
CERTIFIED_EXECUTOR_PIPELINES = frozenset({"screen-demo", "talking-head"})


@dataclass(frozen=True)
class ManifestStageContext:
    """Read-only execution context for the manifest-derived next stage."""

    project_dir: Path
    order: dict[str, Any]
    manifest: dict[str, Any]
    contract: dict[str, Any]
    stage: str | None
    stage_definition: dict[str, Any] | None

    @property
    def required_artifacts_in(self) -> tuple[str, ...]:
        if not self.stage_definition:
            return ()
        return tuple(str(item) for item in self.stage_definition.get("required_artifacts_in", []) or [])

    @property
    def produced_artifacts(self) -> tuple[str, ...]:
        if not self.stage_definition:
            return ()
        return tuple(str(item) for item in self.stage_definition.get("produces", []) or [])

    @property
    def director_skill(self) -> str | None:
        if not self.stage_definition:
            return None
        value = self.stage_definition.get("skill")
        return str(value) if value else None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe inspection payload for API/agent callers."""
        return {
            "project_id": self.order.get("project_id"),
            "pipeline_type": self.order.get("pipeline_type"),
            "run_id": self.order.get("run_id"),
            "attempt": self.order.get("attempt"),
            "status": self.order.get("status"),
            "current_stage": self.order.get("current_stage"),
            "next_stage": self.stage,
            "director_skill": self.director_skill,
            "required_artifacts_in": list(self.required_artifacts_in),
            "produces": list(self.produced_artifacts),
            "checkpoint_required": bool(
                self.stage_definition.get("checkpoint_required", False)
                if self.stage_definition
                else False
            ),
            "human_approval_default": bool(
                self.stage_definition.get("human_approval_default", False)
                if self.stage_definition
                else False
            ),
            "review_focus": list(self.stage_definition.get("review_focus", []) or [])
            if self.stage_definition
            else [],
            "success_criteria": list(self.stage_definition.get("success_criteria", []) or [])
            if self.stage_definition
            else [],
            "manifest_version": self.order.get("manifest_version"),
            "manifest_hash": self.order.get("manifest_hash"),
            "contract_valid": bool(self.contract.get("valid")),
        }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a JSON artifact atomically within its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass


def _artifact_path(project_dir: Path, artifact_name: str) -> Path:
    if not isinstance(artifact_name, str) or not artifact_name.strip():
        raise ManifestExecutionError("artifact names must be non-empty strings")
    name = artifact_name.strip()
    if Path(name).name != name or name in {".", ".."}:
        raise ManifestExecutionError(f"artifact name {name!r} is not a canonical filename")
    return project_dir / "artifacts" / f"{name}.json"


def _load_artifact(project_dir: Path, artifact_name: str) -> dict[str, Any] | None:
    path = _artifact_path(project_dir, artifact_name)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestExecutionError(f"artifact {artifact_name!r} cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestExecutionError(f"artifact {artifact_name!r} must be a JSON object")
    try:
        validate_artifact(artifact_name, payload)
    except Exception as exc:
        raise ManifestExecutionError(
            f"artifact {artifact_name!r} failed schema validation: {exc}"
        ) from exc
    return payload


def _validate_local_references(
    project_dir: Path,
    artifact_name: str,
    payload: Mapping[str, Any],
) -> None:
    """Reject missing/escaping local media references in critical artifacts."""
    if artifact_name == "asset_manifest":
        references = [
            (str(asset.get("id") or "asset"), asset.get("path"))
            for asset in payload.get("assets", [])
            if isinstance(asset, Mapping)
        ]
    elif artifact_name == "render_report":
        references = [
            (f"output[{index}]", output.get("path"))
            for index, output in enumerate(payload.get("outputs", []))
            if isinstance(output, Mapping)
        ]
    elif artifact_name == "final_review":
        references = [("output_path", payload.get("output_path"))]
    else:
        return

    root = project_dir.resolve()
    for label, raw_path in references:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ManifestExecutionError(
                f"{artifact_name}.{label} must declare a path"
            )
        value = raw_path.strip()
        # Remote stock/provider URLs are provenance, not local files.  They
        # remain allowed here; a downloader/provider stage is responsible for
        # materialising them before a compose-stage asset manifest is submitted.
        if value.startswith(("http://", "https://", "s3://", "gs://")):
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = project_dir / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ManifestExecutionError(
                f"{artifact_name}.{label} path escapes the project directory"
            ) from exc
        if not resolved.is_file():
            raise ManifestExecutionError(
                f"{artifact_name}.{label} references a missing local file: {value}"
            )

    if artifact_name == "render_report":
        _validate_render_report_media(project_dir, payload)


def _validate_render_report_media(
    project_dir: Path,
    payload: Mapping[str, Any],
) -> None:
    """Compare declared render facts with a fresh ffprobe of each output."""
    outputs = payload.get("outputs") or []
    for index, output in enumerate(outputs):
        if not isinstance(output, Mapping):
            continue
        raw_path = output.get("path")
        if not isinstance(raw_path, str) or raw_path.startswith(("http://", "https://", "s3://", "gs://")):
            continue
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = project_dir / candidate
        try:
            from lib.output_promotion import (
                artifact_fingerprint,
                probe_media,
                validate_media_contract,
            )

            probe = probe_media(candidate)
            expected_duration = output.get("duration_seconds")
            declared_profile = (
                output.get("output_profile")
                or output.get("profile")
                or payload.get("output_profile")
                or payload.get("profile")
            )
            # Use the same duration tolerance as atomic promotion.  A report
            # may omit a duration only for a non-video sidecar output.
            validate_media_contract(
                probe,
                profile=(str(declared_profile).strip().lower() if declared_profile else None),
                expected_duration_seconds=(
                    float(payload.get("target_duration_seconds"))
                    if payload.get("target_duration_seconds") is not None
                    else (float(expected_duration) if expected_duration is not None else None)
                ),
            )
        except Exception as exc:
            raise ManifestExecutionError(
                f"render_report.outputs[{index}] failed ffprobe validation: {exc}"
            ) from exc

        comparisons = {
            "resolution": f"{probe['width']}x{probe['height']}",
            "fps": probe.get("fps"),
            "codec": probe.get("video_codec"),
            "audio_codec": probe.get("audio_codec"),
            "file_size_bytes": probe.get("file_size_bytes"),
        }
        try:
            fingerprint = artifact_fingerprint(candidate)
            comparisons["sha256"] = fingerprint["sha256"]
        except Exception:
            comparisons["sha256"] = None
        for field, actual in comparisons.items():
            declared = output.get(field)
            if declared is None or actual is None:
                continue
            if field == "fps":
                try:
                    mismatch = abs(float(declared) - float(actual)) > 0.5
                except (TypeError, ValueError):
                    mismatch = True
            elif field == "file_size_bytes":
                mismatch = int(declared) != int(actual)
            else:
                mismatch = str(declared) != str(actual)
            if mismatch:
                raise ManifestExecutionError(
                    f"render_report.outputs[{index}].{field}={declared!r} does not match "
                    f"ffprobe value {actual!r}"
                )


def _stage_definition(manifest: Mapping[str, Any], stage: str | None) -> dict[str, Any] | None:
    if stage is None:
        return None
    for item in manifest.get("stages", []) or []:
        if isinstance(item, Mapping) and item.get("name") == stage:
            return dict(item)
    raise ManifestExecutionError(f"manifest does not declare stage {stage!r}")


def load_manifest_stage_context(project_dir: Path | str) -> ManifestStageContext:
    """Load the immutable manifest/work-order context for the next stage.

    This is a read-only operation.  It proves that the selected manifest's
    agent contract and all cross-artifact identity sources are valid before an
    agent is given a stage handoff.
    """
    root = Path(project_dir)
    if not root.is_dir():
        raise ManifestExecutionError(f"project directory does not exist: {root}")
    try:
        order, manifest, _manifest_path = _read_execution_order(root)
    except (WorkOrderValidationError, OSError) as exc:
        raise ManifestExecutionError(f"work order is not executable: {exc}") from exc
    try:
        identity = assert_project_identity(root, strict=True)
    except Exception as exc:
        raise ManifestExecutionError(f"project identity is not executable: {exc}") from exc

    pipeline_type = str(order.get("pipeline_type") or "")
    try:
        contract = assert_manifest_agent_contract(
            manifest, repo_root=Path(__file__).resolve().parent.parent
        )
    except Exception as exc:
        raise ManifestExecutionError(
            f"manifest {pipeline_type!r} is not executable: {exc}"
        ) from exc

    next_stage = next_stage_from_work_order(order)
    stage_definition = _stage_definition(manifest, next_stage)
    # Keep this explicit even though assert_project_identity already checked
    # the envelope.  It makes the returned context self-documenting and avoids
    # accidentally discarding the identity report in future refactors.
    if identity["expected"].get("project_id") != order.get("project_id"):
        raise ManifestExecutionError("work-order project_id does not match the project directory")
    return ManifestStageContext(
        project_dir=root,
        order=order,
        manifest=dict(manifest),
        contract=contract,
        stage=next_stage,
        stage_definition=stage_definition,
    )


def _validate_required_inputs(context: ManifestStageContext) -> None:
    missing: list[str] = []
    for artifact_name in context.required_artifacts_in:
        if _load_artifact(context.project_dir, artifact_name) is None:
            missing.append(artifact_name)
    if missing:
        raise ManifestExecutionError(
            f"stage {context.stage!r} is missing required input artifacts: {', '.join(missing)}"
        )


def _validate_submission_artifacts(
    context: ManifestStageContext,
    artifacts: Mapping[str, Any],
    *,
    status: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(artifacts, Mapping):
        raise ManifestExecutionError("artifacts must be an object keyed by canonical artifact name")
    supplied = {str(key): value for key, value in artifacts.items()}
    declared = set(context.produced_artifacts)
    unknown = sorted(set(supplied) - declared)
    if unknown:
        raise ManifestExecutionError(
            f"stage {context.stage!r} supplied undeclared artifacts: {', '.join(unknown)}"
        )
    required = set(context.produced_artifacts) if status in {"completed", "awaiting_human"} else set()
    missing = sorted(required - set(supplied))
    if missing:
        raise ManifestExecutionError(
            f"stage {context.stage!r} must produce: {', '.join(missing)}"
        )

    validated: dict[str, dict[str, Any]] = {}
    for artifact_name, payload in supplied.items():
        if not isinstance(payload, Mapping):
            raise ManifestExecutionError(f"artifact {artifact_name!r} must be a JSON object")
        value = dict(payload)
        try:
            validate_artifact(artifact_name, value)
        except Exception as exc:
            raise ManifestExecutionError(
                f"artifact {artifact_name!r} failed schema validation: {exc}"
            ) from exc
        _validate_local_references(context.project_dir, artifact_name, value)
        validated[artifact_name] = value

    # Compose is not complete when it only reports a render.  The persisted
    # final_review must point at the same output bytes and the render report
    # must carry an explicit durable link to that review.
    if context.stage == "compose" and status in {"completed", "awaiting_human"}:
        render_report = validated.get("render_report") or _load_artifact(
            context.project_dir, "render_report"
        )
        final_review = validated.get("final_review") or _load_artifact(
            context.project_dir, "final_review"
        )
        if not isinstance(render_report, Mapping) or not isinstance(final_review, Mapping):
            raise ManifestExecutionError(
                "compose requires both render_report and final_review evidence"
            )
        review_status = str(final_review.get("status") or "").strip().lower()
        if status == "completed" and review_status != "pass":
            raise ManifestExecutionError(
                f"compose cannot complete with final_review.status={review_status!r}; revise or fail must block advancement"
            )
        review_ref = str(render_report.get("final_review_ref") or "").strip()
        if review_ref not in {"artifacts/final_review.json", "final_review.json"}:
            raise ManifestExecutionError(
                "render_report.final_review_ref must link to artifacts/final_review.json"
            )
        output_rows = render_report.get("outputs") or []
        output_paths = {
            str(row.get("path"))
            for row in output_rows
            if isinstance(row, Mapping) and row.get("path")
        }
        review_output = str(final_review.get("output_path") or "").strip()
        if not review_output:
            raise ManifestExecutionError("final_review.output_path is required")

        def _resolved(raw: str) -> Path:
            candidate = Path(raw)
            return (candidate if candidate.is_absolute() else context.project_dir / candidate).resolve()

        try:
            review_path = _resolved(review_output)
            if not any(_resolved(path) == review_path for path in output_paths):
                raise ManifestExecutionError(
                    "final_review.output_path does not match any render_report output"
                )
            if not review_path.is_file():
                raise ManifestExecutionError(
                    f"final_review.output_path references a missing file: {review_output}"
                )
            declared_digest = final_review.get("output_sha256")
            if declared_digest:
                import hashlib

                digest = hashlib.sha256()
                with review_path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                if str(declared_digest).lower() != digest.hexdigest():
                    raise ManifestExecutionError(
                        "final_review.output_sha256 does not match the reviewed output"
                    )
        except ManifestExecutionError:
            raise
        except OSError as exc:
            raise ManifestExecutionError(f"final_review output linkage could not be checked: {exc}") from exc

    # Every durable handoff is checked against the already-persisted artifact
    # chain.  This catches identity/profile/runtime/duration drift before the
    # work-order pointer can move, while leaving exploratory artifacts that do
    # not opt into those fields readable outside the manifest executor.
    chain: dict[str, Mapping[str, Any]] = {}
    for name in (
        "brief", "proposal_packet", "script", "scene_plan",
        "asset_manifest", "edit_decisions", "render_report", "final_review",
    ):
        value = validated.get(name) or _load_artifact(context.project_dir, name)
        if isinstance(value, Mapping):
            chain[name] = value
    if any(name in chain for name in ("render_report", "final_review")):
        from lib.cross_artifact import validate_cross_artifact_consistency

        selections = context.order.get("selections") or {}
        consistency = validate_cross_artifact_consistency(
            chain,
            expected_identity={
                "project_id": context.order.get("project_id"),
                "pipeline_type": context.order.get("pipeline_type"),
                "run_id": context.order.get("run_id"),
            },
            expected_profile=str(selections.get("output_profile") or "").strip().lower() or None,
            expected_runtime=str(selections.get("render_runtime") or "").strip().lower() or None,
        )
        if not consistency["valid"]:
            raise ManifestExecutionError(
                "cross-artifact consistency failed: "
                + "; ".join(consistency["errors"][:8])
            )

    if context.stage == "publish" and status == "completed":
        final_review = _load_artifact(context.project_dir, "final_review")
        if not isinstance(final_review, Mapping):
            raise ManifestExecutionError("publish is blocked until final_review evidence exists")
        if str(final_review.get("status") or "").strip().lower() != "pass":
            raise ManifestExecutionError(
                f"publish is blocked by final_review.status={final_review.get('status')!r}"
            )

    # Grounding is intentionally opt-in at the artifact boundary so legacy
    # exploratory checkpoints remain readable.  Production research/script
    # artifacts opt in with ``grounding_contract.required=true`` (the director
    # skills now make that field mandatory for new work), or by supplying the
    # explicit claim ledger.  The validator is deterministic and performs no
    # network access.
    brief = validated.get("research_brief") or _load_artifact(
        context.project_dir, "research_brief"
    ) or {}
    script = validated.get("script") or _load_artifact(
        context.project_dir, "script"
    ) or {}
    brief_contract = brief.get("grounding_contract") if isinstance(brief, Mapping) else {}
    script_contract = script.get("grounding_contract") if isinstance(script, Mapping) else {}
    grounding_required = bool(
        (isinstance(brief_contract, Mapping) and brief_contract.get("required") is True)
        or (isinstance(script_contract, Mapping) and script_contract.get("required") is True)
        or (isinstance(script, Mapping) and "claims" in script)
    )
    if grounding_required and (brief or script):
        from lib.grounding import validate_grounding

        grounding = validate_grounding(brief, script, strict=True)
        if not grounding["valid"]:
            detail = "; ".join(grounding["errors"][:5])
            if grounding.get("decision") == "block":
                raise ManifestExecutionError(
                    f"grounding gate blocked stage {context.stage!r}: {detail}"
                )
            raise ManifestExecutionError(
                f"grounding gate requires revision for stage {context.stage!r}: {detail}"
            )

    if isinstance(script, Mapping) and (
        "duration_contract" in script
        or "target_duration_seconds" in script
        or any(key in script for key in ("output_profile", "profile"))
    ):
        from lib.video_timeline import validate_script_duration

        duration_contract = script.get("duration_contract")
        contract = duration_contract if isinstance(duration_contract, Mapping) else {}
        duration_report = validate_script_duration(
            script,
            voice_rate_wpm=float(contract.get("voice_rate_wpm", 150.0)),
            profile=str(script.get("output_profile") or script.get("profile") or "").strip().lower() or None,
            duration_policy=str(contract.get("duration_policy") or "target_led"),
            tolerance_seconds=(
                float(contract["tolerance_seconds"])
                if contract.get("tolerance_seconds") is not None
                else None
            ),
        )
        if not duration_report["valid"]:
            raise ManifestExecutionError(
                "script duration contract failed: "
                + "; ".join(duration_report["errors"][:6])
            )

    # Profile identity is another cross-stage immutable contract.  Keep this
    # conditional for compatibility with exploratory legacy artifacts; once a
    # project opts into profile fields, every supplied/prior handoff is checked
    # against the work-order selection before it can advance.
    profile_artifacts: dict[str, Mapping[str, Any]] = {}
    for name in ("brief", "proposal_packet", "script", "scene_plan", "edit_decisions", "render_report"):
        value = validated.get(name) or _load_artifact(context.project_dir, name)
        if isinstance(value, Mapping):
            profile_artifacts[name] = value
    has_profile_contract = any(
        isinstance(value, Mapping)
        and any(str(value.get(key) or "").strip() for key in ("output_profile", "profile", "aspect_ratio"))
        for value in profile_artifacts.values()
    )
    if has_profile_contract:
        from lib.format_contracts import validate_profile_propagation

        selections = context.order.get("selections") or {}
        expected_profile = str(selections.get("output_profile") or "").strip().lower() or None
        expected_duration = context.order.get("target_duration_seconds")
        profile_report = validate_profile_propagation(
            profile_artifacts,
            expected_profile=expected_profile,
            expected_duration_seconds=(
                float(expected_duration) if expected_duration is not None else None
            ),
        )
        if not profile_report["valid"]:
            raise ManifestExecutionError(
                "profile propagation failed: " + "; ".join(profile_report["errors"][:6])
            )

    # Voice identity is immutable once a production run selects a provider.
    # Check every artifact that declares it, including artifacts written in a
    # previous stage, so a resumed run cannot silently switch provider/model or
    # voice between proposal, narration assets, and final render.
    voice_artifacts: dict[str, Mapping[str, Any]] = {}
    for name in ("proposal_packet", "script", "asset_manifest", "render_report", "final_review", "decision_log"):
        value = validated.get(name) or _load_artifact(context.project_dir, name)
        if isinstance(value, Mapping):
            voice_artifacts[name] = value
    selections = context.order.get("selections") or {}
    expected_voice_candidate = (
        selections.get("voice_identity")
        or selections.get("voice_selection")
        or selections.get("voice")
    ) if isinstance(selections, Mapping) else None
    expected_voice = expected_voice_candidate if isinstance(expected_voice_candidate, Mapping) else None
    declared_voice = any(
        isinstance(value, Mapping)
        and (
            any(key in value for key in ("voice_identity", "voice_selection", "voice_contract", "voice_performance"))
            or isinstance(value.get("production_plan"), Mapping)
            and any(key in value["production_plan"] for key in ("voice_identity", "voice_selection", "voice_contract"))
        )
        for value in voice_artifacts.values()
    ) or expected_voice is not None
    if declared_voice:
        from lib.voice_contracts import validate_voice_propagation

        voice_report = validate_voice_propagation(voice_artifacts, expected=expected_voice)
        if not voice_report["valid"]:
            raise ManifestExecutionError(
                "voice identity propagation failed: " + "; ".join(voice_report["errors"][:6])
            )

    # Mixed-media requests opt into the canonical asset contract by declaring
    # a strategy/hash on their manifest rows.  When both asset and edit
    # artifacts are available, reject any approved asset that is not actually
    # referenced by the edit plan or lacks source/generation/user provenance.
    asset_manifest = validated.get("asset_manifest") or _load_artifact(context.project_dir, "asset_manifest")
    edit_decisions = validated.get("edit_decisions") or _load_artifact(context.project_dir, "edit_decisions")
    contact_sheet = validated.get("contact_sheet") or _load_artifact(context.project_dir, "contact_sheet")
    if isinstance(asset_manifest, Mapping) and isinstance(edit_decisions, Mapping):
        asset_rows = asset_manifest.get("assets") or []
        if any(isinstance(row, Mapping) and (row.get("strategy") or row.get("sha256")) for row in asset_rows):
            from lib.media_contracts import validate_mixed_media_coverage

            coverage = validate_mixed_media_coverage(
                asset_manifest,
                edit_decisions,
                contact_sheet=contact_sheet if isinstance(contact_sheet, Mapping) else None,
            )
            if not coverage["valid"]:
                raise ManifestExecutionError(
                    "mixed-media asset coverage failed: " + "; ".join(coverage["errors"][:8])
                )
    return validated


def submit_manifest_stage(
    project_dir: Path | str,
    agent_id: str,
    stage: str,
    artifacts: Mapping[str, Any],
    *,
    status: str = "completed",
    human_approved: bool = False,
    approval_record: Mapping[str, Any] | None = None,
    checkpoint_policy: str = "guided",
    review: Mapping[str, Any] | None = None,
    cost_snapshot: Mapping[str, Any] | None = None,
    error: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    producing_tool: str = "stage-director",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist an agent-produced stage result and advance one manifest stage.

    ``status`` may be ``in_progress``, ``awaiting_human``, ``completed`` or
    ``failed``.  In-progress submissions only refresh the checkpoint and do
    not move the work-order pointer.  All other statuses pass through
    :func:`lib.work_order.advance_work_order`, which enforces lease ownership,
    manifest order, checkpoint identity, and human gates.
    """
    root = Path(project_dir)
    context = load_manifest_stage_context(root)
    if context.stage is None:
        raise ManifestExecutionError("work order has no stage left to submit")
    requested_stage = str(stage).strip()
    if requested_stage != context.stage:
        raise ManifestExecutionError(
            f"cannot submit stage {requested_stage!r}; manifest-derived next stage is {context.stage!r}"
        )
    if status not in {"in_progress", "awaiting_human", "completed", "failed"}:
        raise ManifestExecutionError(
            "stage status must be one of in_progress, awaiting_human, completed, or failed"
        )

    # A manifest-declared human gate is a pause boundary.  Agent submissions
    # cannot complete it by passing ``human_approved=True`` (or by smuggling a
    # fabricated approval object through the stage endpoint); only the human
    # decision transition can produce a completed checkpoint.
    gate_required = bool(get_stage_human_approval_default(context.manifest, requested_stage))
    if gate_required and status == "completed":
        status = "awaiting_human"
        human_approved = False
        approval_record = None
    if gate_required and status == "awaiting_human" and (human_approved or approval_record is not None):
        raise ManifestExecutionError(
            f"stage {requested_stage!r} is human-gated; submit awaiting_human and use the approval endpoint"
        )

    # Validate the lease before writing any stage material.  Importing this
    # private helper is deliberate: the public advance operation performs the
    # same check, while the in-progress path has no advance call to reuse.
    from lib.work_order import _normalise_now

    _claim_owner(context.order, str(agent_id).strip(), _normalise_now(now))
    _validate_required_inputs(context)
    validated = _validate_submission_artifacts(context, artifacts, status=status)

    normalized_tool = str(producing_tool or "stage-director").strip()
    if not normalized_tool:
        raise ManifestExecutionError("producing_tool must be a non-empty string")

    try:
        for artifact_name, payload in validated.items():
            _atomic_json(_artifact_path(root, artifact_name), payload)

        checkpoint_path = write_checkpoint(
            root.parent,
            root.name,
            requested_stage,
            status,
            validated,
            pipeline_type=context.order["pipeline_type"],
            run_id=context.order["run_id"],
            attempt=context.order.get("attempt"),
            producer_stage=requested_stage,
            producer_tool=normalized_tool,
            checkpoint_policy=checkpoint_policy,
            human_approval_required=bool(
                get_stage_human_approval_default(context.manifest, requested_stage)
            ),
            human_approved=bool(human_approved),
            approval_record=dict(approval_record) if isinstance(approval_record, Mapping) else None,
            review=dict(review) if isinstance(review, Mapping) else None,
            cost_snapshot=dict(cost_snapshot) if isinstance(cost_snapshot, Mapping) else None,
            error=error,
            metadata=dict(metadata) if isinstance(metadata, Mapping) else None,
        )
    except ManifestExecutionError:
        raise
    except Exception as exc:
        raise ManifestExecutionError(
            f"stage {requested_stage!r} could not persist its checkpoint: {exc}"
        ) from exc

    if status == "in_progress":
        # The checkpoint is deliberately not advanced.  Return the validated
        # order plus the durable checkpoint path so the agent can continue.
        order = read_work_order(root)
        from lib.run_record import record_artifacts

        provenance_paths = {
            f"artifact:{name}": _artifact_path(root, name)
            for name in validated
        }
        provenance_paths[f"checkpoint:{requested_stage}"] = checkpoint_path
        try:
            record_artifacts(
                root,
                order,
                stage=requested_stage,
                tool=normalized_tool,
                artifacts=provenance_paths,
                agent_id=str(agent_id).strip(),
                now=now,
            )
        except Exception as exc:
            raise ManifestExecutionError(
                f"stage {requested_stage!r} checkpoint provenance could not be recorded: {exc}"
            ) from exc
        return {
            "project_id": order["project_id"],
            "stage": requested_stage,
            "status": status,
            "checkpoint_path": str(checkpoint_path),
            "artifact_paths": {
                name: str(_artifact_path(root, name)) for name in validated
            },
            "work_order": order,
            "context": context.as_dict(),
        }

    try:
        order = advance_work_order(
            root,
            str(agent_id).strip(),
            requested_stage,
            checkpoint_ref=checkpoint_path.name,
            now=now,
        )
    except (WorkOrderStateError, WorkOrderValidationError) as exc:
        raise ManifestExecutionError(f"stage {requested_stage!r} could not advance: {exc}") from exc

    from lib.run_record import record_artifacts

    provenance_paths = {
        f"artifact:{name}": _artifact_path(root, name)
        for name in validated
    }
    provenance_paths[f"checkpoint:{requested_stage}"] = checkpoint_path
    try:
        record_artifacts(
            root,
            order,
            stage=requested_stage,
            tool=normalized_tool,
            artifacts=provenance_paths,
            agent_id=str(agent_id).strip(),
            now=now,
        )
    except Exception as exc:
        raise ManifestExecutionError(
            f"stage {requested_stage!r} checkpoint provenance could not be recorded: {exc}"
        ) from exc

    return {
        "project_id": order["project_id"],
        "stage": requested_stage,
        "status": status,
        "checkpoint_path": str(checkpoint_path),
        "artifact_paths": {
            name: str(_artifact_path(root, name)) for name in validated
        },
        "work_order": order,
        "context": context.as_dict(),
    }


__all__ = [
    "CERTIFIED_EXECUTOR_PIPELINES",
    "ManifestExecutionError",
    "ManifestStageContext",
    "load_manifest_stage_context",
    "submit_manifest_stage",
]
