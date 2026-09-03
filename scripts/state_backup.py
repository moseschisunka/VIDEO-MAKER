"""Operator CLI for project backup, restore, and state migration.

Examples::

    python scripts/state_backup.py backup projects/lesson backups/lesson.zip
    python scripts/state_backup.py inspect backups/lesson.zip
    python scripts/state_backup.py restore backups/lesson.zip projects-restored
    python scripts/state_backup.py migrate projects/lesson --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.state_backup import create_backup, read_backup_manifest, restore_backup  # noqa: E402
from lib.state_migrations import migrate_project_state  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenMontage project state recovery tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="create an integrity-checked ZIP backup")
    backup.add_argument("project_dir", type=Path)
    backup.add_argument("archive_path", type=Path)
    backup.add_argument("--state-only", action="store_true", help="exclude assets/ and renders/")

    inspect = subparsers.add_parser("inspect", help="inspect a backup manifest")
    inspect.add_argument("archive_path", type=Path)

    restore = subparsers.add_parser("restore", help="verify and atomically restore a backup")
    restore.add_argument("archive_path", type=Path)
    restore.add_argument("target_root", type=Path)
    restore.add_argument("--project-id")
    restore.add_argument("--overwrite", action="store_true", help="preserve existing project under .pre-restore-* before replacement")

    migrate = subparsers.add_parser("migrate", help="migrate durable state to version 1.0")
    migrate.add_argument("project_dir", type=Path)
    migrate.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "backup":
        result = create_backup(args.project_dir, args.archive_path, include_media=not args.state_only)
    elif args.command == "inspect":
        result = read_backup_manifest(args.archive_path)
    elif args.command == "restore":
        result = restore_backup(
            args.archive_path,
            args.target_root,
            project_id=args.project_id,
            overwrite=args.overwrite,
        )
    else:
        result = migrate_project_state(args.project_dir, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
