"""PR-1010 operator and incident runbook documentation contracts."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "operations" / "RUNBOOKS.md"


def test_runbooks_cover_every_phase10_operational_failure_class() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    required_sections = (
        "## 1. Severity, ownership, and universal stop rules",
        "## 2. Pre-deploy and deploy",
        "## 3. Rollback and bad deploy",
        "## 4. Provider outage, quota, or throttling",
        "## 5. Stuck, abandoned, or duplicate job",
        "## 6. Corrupt, partial, or wrong artifact",
        "## 7. Backup, restore, and state migration",
        "## 8. Secret rotation and privacy incident",
        "## 9. Incident evidence and closure",
        "## 10. Runbook verification commands",
    )
    for section in required_sections:
        assert section in text
    for command in (
        "docker compose up -d --no-build openmontage-backlot",
        "docker compose stop openmontage-backlot",
        "python scripts/measure_slos.py",
        "python scripts/measure_load_soak.py",
        "python scripts/state_backup.py backup",
        "python scripts/state_backup.py restore",
        "python scripts/state_backup.py migrate",
        "/api/project/<PROJECT_ID>/resume",
        "/api/project/<PROJECT_ID>/cancel",
        "/api/metrics",
    ):
        assert command in text


def test_runbooks_are_fail_closed_about_identity_secrets_and_production_gate() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    for phrase in (
        "PR-11G",
        "silently changing the selected provider",
        "Never restore a project archive over a live project",
        "Never include `.env`",
        "Unknown versions fail closed",
        "A failed or missing sample is a stop condition",
        "do not edit or delete",
        "wrong/stale output",
    ):
        assert phrase in text
    # The document may contain placeholders, but never a token-shaped secret
    # assignment or an actual bearer value.
    assert "BACKLOT_AUTH_TOKEN=" not in text
    assert "sk-" not in text


def test_runbook_references_existing_operator_entry_points() -> None:
    assert (REPO_ROOT / "scripts" / "state_backup.py").is_file()
    assert (REPO_ROOT / "scripts" / "measure_slos.py").is_file()
    assert (REPO_ROOT / "scripts" / "measure_load_soak.py").is_file()
    assert (REPO_ROOT / "docs" / "operations" / "BACKUP_RESTORE_MIGRATION.md").is_file()
    assert (REPO_ROOT / "docs" / "operations" / "OBSERVABILITY.md").is_file()
    assert (REPO_ROOT / "config" / "alerts.yaml").is_file()
    assert (REPO_ROOT / "docs" / "operations" / "SECRETS_PRIVACY_RETENTION.md").is_file()


def test_ci_exposes_linux_phase10_measurement_and_raw_artifact_job() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for phrase in (
        "phase10-operational-evidence:",
        "needs: [release-blockers, clean-install-smoke]",
        "python scripts/measure_slos.py > slo-baseline.json",
        "python scripts/measure_load_soak.py --output pr1009-load-soak.json",
        "python scripts/run_operations_drill.py --output pr10g-operations-drill.json",
        "actions/upload-artifact@v4",
        "openmontage-phase10-evidence",
        "npx --no-install remotion browser ensure",
        "npx --no-install tsc --noEmit -p tsconfig.json",
        "npx --no-install remotion bundle src/index.tsx --out-dir ../remotion-bundle",
        "npx --no-install remotion compositions src/index.tsx",
        "openmontage-remotion-compositions",
        "remotion-clean-build-evidence.json",
    ):
        assert phrase in workflow
