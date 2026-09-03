"""PR-1008 backup/restore integrity and durable-state migration contracts."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from lib.checkpoint import init_project, write_checkpoint
from lib.pipeline_loader import load_pipeline_readonly
from lib.run_record import read_run_record, run_record_path
from lib.state_backup import StateBackupError, create_backup, read_backup_manifest, restore_backup
from lib.state_migrations import migrate_project_state
from lib.work_order import build_work_order, write_work_order


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "12345678-1234-4234-8234-123456789abc"


def _project(root: Path, project_id: str = "recovery-fixture") -> Path:
    project = init_project(
        project_id,
        title="Recovery fixture",
        pipeline_type="screen-demo",
        run_id=RUN_ID,
        pipeline_dir=root,
    )
    manifest_path = REPO_ROOT / "pipeline_defs" / "screen-demo.yaml"
    manifest = load_pipeline_readonly("screen-demo", defs_dir=manifest_path.parent)
    order = build_work_order(
        project_id=project_id,
        title="Recovery fixture",
        topic_prompt="An offline recovery drill",
        target_duration_seconds=10,
        pipeline_type="screen-demo",
        manifest=manifest,
        manifest_path=manifest_path,
        selections={
            "playbook": "premium-minimalist",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "remotion",
            "output_profile": "youtube_landscape",
            "aspect_ratio": "16:9",
            "source_mode": "synthetic_terminal",
        },
        run_id=RUN_ID,
    )
    write_work_order(project, order)
    write_checkpoint(
        root,
        project_id,
        "idea",
        "awaiting_human",
        {
            "brief": {
                "version": "1.0",
                "title": "Recovery fixture",
                "hook": "State survives a restore.",
                "key_points": ["Backup", "Restore"],
                "tone": "clear",
                "style": "premium-minimalist",
                "target_platform": "youtube",
                "target_duration_seconds": 10,
            }
        },
        pipeline_type="screen-demo",
        run_id=RUN_ID,
        attempt=1,
        human_approval_required=True,
    )
    (project / "events.jsonl").write_text(
        json.dumps(
            {
                "version": "1.0",
                "project_id": project_id,
                "pipeline_type": "screen-demo",
                "run_id": RUN_ID,
                "event": "recovery_fixture",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (project / "artifacts" / "notes.txt").write_text("private recovery note\n", encoding="utf-8")
    (project / "assets" / "images" / "fixture.txt").write_bytes(b"media bytes\n")
    (project / ".env").write_text("OPENAI_API_KEY=must-not-enter-backup\n", encoding="utf-8")
    (project / "artifacts" / "stale.lock").write_text("lock", encoding="utf-8")
    return project


def test_backup_manifest_excludes_secrets_and_restore_round_trips_state(tmp_path: Path):
    project = _project(tmp_path / "source")
    archive = tmp_path / "backups" / "recovery.zip"
    manifest = create_backup(project, archive)

    assert archive.is_file()
    assert manifest["project_id"] == project.name
    paths = {item["path"] for item in manifest["files"]}
    assert "project.json" in paths
    assert "work_order.json" in paths
    assert "checkpoint_idea.json" in paths
    assert "assets/images/fixture.txt" in paths
    assert ".env" not in paths
    assert all(not path.endswith(".lock") for path in paths)
    assert any(item.startswith(".env:") for item in manifest["excluded"])
    assert read_backup_manifest(archive)["backup_id"] == manifest["backup_id"]

    restored_root = tmp_path / "restored"
    result = restore_backup(archive, restored_root)
    restored = Path(result["restored_path"])
    assert result["status"] == "RESTORED"
    assert restored.name == project.name
    assert (restored / "work_order.json").read_bytes() == (project / "work_order.json").read_bytes()
    assert (restored / "checkpoint_idea.json").read_bytes() == (project / "checkpoint_idea.json").read_bytes()
    assert (restored / "assets" / "images" / "fixture.txt").read_bytes() == b"media bytes\n"
    assert not (restored / ".env").exists()
    assert read_run_record(restored, RUN_ID)["project_id"] == project.name


def test_state_only_backup_and_existing_target_safeguard(tmp_path: Path):
    project = _project(tmp_path / "source")
    archive = tmp_path / "backups" / "state-only.zip"
    manifest = create_backup(project, archive, include_media=False)
    paths = {item["path"] for item in manifest["files"]}
    assert not any(path.startswith("assets/") or path.startswith("renders/") for path in paths)
    assert any("media_excluded_by_request" in item for item in manifest["excluded"])

    destination = tmp_path / "restored"
    restore_backup(archive, destination)
    with pytest.raises(StateBackupError, match="already exists"):
        restore_backup(archive, destination)


def test_restore_rejects_tampered_member_before_promotion(tmp_path: Path):
    project = _project(tmp_path / "source")
    source_archive = tmp_path / "backups" / "source.zip"
    create_backup(project, source_archive)
    tampered_archive = tmp_path / "backups" / "tampered.zip"
    with zipfile.ZipFile(source_archive, "r") as source, zipfile.ZipFile(tampered_archive, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "artifacts/notes.txt":
                data = b"tampered\n"
            target.writestr(info, data)
    with pytest.raises(StateBackupError, match="integrity check failed"):
        restore_backup(tampered_archive, tmp_path / "restored")
    assert not (tmp_path / "restored" / project.name).exists()


def test_legacy_state_migration_is_dry_run_safe_idempotent_and_audited(tmp_path: Path):
    project = _project(tmp_path / "source", "migration-fixture")
    control_paths = [
        project / "project.json",
        project / "work_order.json",
        project / "checkpoint_idea.json",
        run_record_path(project, RUN_ID),
    ]
    originals: dict[Path, bytes] = {}
    for path in control_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = "0.9"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        originals[path] = path.read_bytes()

    dry = migrate_project_state(project, dry_run=True)
    assert dry["status"] == "DRY_RUN"
    assert set(dry["migrated_files"]) == {path.relative_to(project).as_posix() for path in control_paths}
    assert all(path.read_bytes() == originals[path] for path in control_paths)

    report = migrate_project_state(project)
    assert report["status"] == "MIGRATED"
    history = project / report["history_ref"]
    assert history.is_dir()
    assert all((history / path.relative_to(project)).is_file() for path in control_paths)
    assert all(json.loads(path.read_text(encoding="utf-8"))["version"] == "1.0" for path in control_paths)

    noop = migrate_project_state(project)
    assert noop["status"] == "NOOP"
    assert noop["migrated_files"] == []


def test_unknown_state_version_fails_closed(tmp_path: Path):
    project = _project(tmp_path / "source", "unknown-version")
    path = project / "work_order.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = "9.9"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported work_order state version"):
        migrate_project_state(project)
