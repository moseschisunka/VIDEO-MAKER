# Backup, restore, and state migration runbook

Status: `PR-1008` implementation and local drill. Production use still
requires the Phase 10 gate and `PR-11G` certification.

## What is backed up

`lib.state_backup.create_backup()` creates an atomic ZIP containing the project
marker, work order, run records, checkpoints/history, events, artifacts, media,
and other project-local files. Each member is recorded with its byte size and
SHA-256 digest in `backup_manifest.json`. Environment files, credentials,
lock/temp/partial files, symlinks, and Python caches are never copied. A
state-only archive can omit `assets/` and `renders/` when the media is stored
separately.

The archive is integrity checked, not encrypted. Treat it as private user data:
write it only to an approved encrypted backup store, restrict access, and apply
the 180-day retention default from `config/security_policy.yaml`.

## Create and inspect a backup

From the repository root:

```text
python scripts/state_backup.py backup projects/<project-id> backups/<project-id>.zip
python scripts/state_backup.py inspect backups/<project-id>.zip
```

The archive must be outside the project being walked. Keep the JSON output with
the incident or release record; it contains no credentials or full prompt
payloads.

## Restore drill

Restore into a new root first. The command validates every manifest member,
rejects traversal/absolute paths and symlink members, validates work-order,
checkpoint, run-record, and cross-artifact identity, then atomically promotes
the staged project directory:

```text
python scripts/state_backup.py restore backups/<project-id>.zip projects-restore-drill
python scripts/state_backup.py inspect backups/<project-id>.zip
```

An existing target is refused. `--overwrite` is an explicit operator action;
the previous directory is renamed to `.pre-restore-<id>-<nonce>` and is not
deleted, so a rollback remains possible. Never restore a project archive over a
live project without stopping its worker and recording the incident/change
approval.

## State migration

Current control state is version `1.0`. Known legacy versions (`0.8`, `0.9`,
and versionless legacy fixtures) may be migrated only through the explicit
migrator. It covers `project.json`, `work_order.json`, current checkpoints,
and `runs/*/run.json`; creative artifact payloads are validated by their own
schemas and are not rewritten.

Always run a dry-run first, then create a backup, then migrate:

```text
python scripts/state_backup.py migrate projects/<project-id> --dry-run
python scripts/state_backup.py backup projects/<project-id> backups/<project-id>-pre-migration.zip
python scripts/state_backup.py migrate projects/<project-id>
```

Every changed file is copied to
`history/migrations/<migration-id>/` before an atomic replacement. If a write
fails, already-written files are restored from that history copy and the
migration fails. A successful report lists source versions, migrated files,
validated files, and the history reference. Re-running on version `1.0` is a
validated `NOOP`.

## Recovery acceptance checks

The release-blocking contract is:

```text
python -m pytest tests/contracts/test_phase10_backup_restore.py -q
```

It must prove backup/restore byte integrity, secret exclusion, traversal and
symlink rejection, refusal to overwrite by default, migration dry-run and
promotion, migration idempotency, and rollback-safe behavior. The test uses a
disposable temporary project and no provider or network call.
