"""Explicit operator CLI for inert backup and scratch-restore tooling."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .core import BackupError, create_backup, restore_backup
from .store import S3Settings, download_bundle, upload_bundle


def add_backup_subparser(subparsers) -> None:
    backup = subparsers.add_parser(
        "backup",
        help="Create, upload, download, or scratch-verify an operator backup.",
    )
    commands = backup.add_subparsers(dest="backup_command", required=True)

    create = commands.add_parser(
        "create",
        help="Create a WAL-safe local bundle; never contacts object storage.",
    )
    create.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path("/data/sessions"),
        help="Sessions directory containing swinglab.db.",
    )
    create.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New directory to receive the completed bundle.",
    )

    upload = commands.add_parser(
        "upload",
        help="Upload a verified bundle; COMPLETE.json is uploaded last.",
    )
    upload.add_argument("--bundle", type=Path, required=True)
    upload.add_argument(
        "--confirm-private-bucket",
        action="store_true",
        help="Confirm bucket privacy, encryption, and least-privilege access.",
    )

    download = commands.add_parser(
        "download",
        help="Download one completed backup into a new local directory.",
    )
    download.add_argument("--backup-id", required=True)
    download.add_argument("--output-dir", type=Path, required=True)

    restore = commands.add_parser(
        "restore-drill",
        help="Restore a bundle to a unique scratch child and verify it.",
    )
    restore.add_argument("--bundle", type=Path, required=True)
    restore.add_argument(
        "--scratch-root",
        type=Path,
        required=True,
        help="Existing non-/data directory that will receive a new restore child.",
    )


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


def run_backup_command(args: argparse.Namespace) -> int:
    command = args.backup_command
    gate = (
        "CADDIE_RESTORE_ENABLED"
        if command in {"download", "restore-drill"}
        else "CADDIE_BACKUP_ENABLED"
    )
    if not _enabled(gate):
        print(
            f"{gate}=true is required for this explicit operator command.",
            file=sys.stderr,
        )
        return 2
    try:
        if command == "create":
            manifest = create_backup(args.sessions_dir, args.output_dir)
            print(
                f"Backup {manifest['backup_id']} created with "
                f"{manifest['artifacts']['count']} verified artifact(s)."
            )
        elif command == "upload":
            if not args.confirm_private_bucket:
                raise BackupError(
                    "--confirm-private-bucket is required before any upload."
                )
            backup_id = upload_bundle(
                args.bundle, S3Settings.from_env(role="backup")
            )
            print(f"Backup {backup_id} uploaded and marked complete.")
        elif command == "download":
            manifest = download_bundle(
                args.backup_id,
                args.output_dir,
                S3Settings.from_env(role="restore"),
            )
            print(f"Backup {manifest['backup_id']} downloaded and verified.")
        elif command == "restore-drill":
            result = restore_backup(args.bundle, args.scratch_root)
            print(
                f"Restore drill passed for backup "
                f"{result['report']['backup_id']}."
            )
        else:  # pragma: no cover - argparse enforces the command set
            raise BackupError("Unknown backup command.")
        return 0
    except BackupError as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return 1
