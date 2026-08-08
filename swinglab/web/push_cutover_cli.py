"""Operator CLI for mobile push environment cutover (status/close/purge)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .push_cutover import (
    PushCutoverConflictError,
    PushCutoverNotReadyError,
    PushFenceClosedError,
    PushFenceMismatchError,
    close_fence,
    cutover_request_hash,
    fence_status,
    purge_fence,
)
from .push_store import load_mobile_push_settings
from .review_auth import resolve_mobile_deployment_environment


def add_mobile_push_cutover_subparser(subparsers) -> None:
    """Register ``swinglab mobile-push-cutover`` on the main CLI."""

    cutover = subparsers.add_parser(
        "mobile-push-cutover",
        help="Inspect or apply mobile push environment fence cutover.",
    )
    cutover.add_argument(
        "--sessions-dir",
        type=Path,
        required=True,
        help="The web app's sessions directory (contains swinglab.db)",
    )
    cutover.add_argument(
        "--environment",
        required=True,
        choices=("development", "staging", "production"),
        help="Target deployment environment (must match server config)",
    )
    cutover.add_argument(
        "--expo-project-id",
        required=True,
        help="Target Expo project UUID (must match server config)",
    )
    cutover.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (defaults to package discovery)",
    )
    actions = cutover.add_subparsers(dest="cutover_action", required=True)

    status = actions.add_parser("status", help="Print aggregate-only fence status.")
    status.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        required=True,
        help="Print aggregate status as JSON (required)",
    )

    close = actions.add_parser(
        "close",
        help="Close the fence and terminalize unsent outbox rows.",
    )
    close.add_argument(
        "--operation-id",
        required=True,
        help="Opaque 128-bit operation id (UUID or 32-hex)",
    )
    close.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the planned close without writing (default when --apply is omitted)",
    )
    close.add_argument(
        "--apply",
        action="store_true",
        help="Apply the close mutation",
    )

    purge = actions.add_parser(
        "purge",
        help="After provider_safe_after, delete registrations and outbox rows.",
    )
    purge.add_argument(
        "--operation-id",
        required=True,
        help="Opaque 128-bit operation id (UUID or 32-hex)",
    )
    purge.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the planned purge without writing (default when --apply is omitted)",
    )
    purge.add_argument(
        "--apply",
        action="store_true",
        help="Apply the purge mutation",
    )


def _validate_target_matches_server(
    *,
    environment: str,
    expo_project_id: str,
    config_path: Path | None,
) -> tuple[str, str, object]:
    from ..config import Config

    cfg = Config.load(config_path)
    configured_environment = resolve_mobile_deployment_environment()
    settings = load_mobile_push_settings(cfg.web)
    if environment != configured_environment:
        raise PushFenceMismatchError(
            "The target environment does not match server configuration."
        )
    if expo_project_id != settings.expo_project_id:
        raise PushFenceMismatchError(
            "The target Expo project ID does not match server configuration."
        )
    return configured_environment, settings.expo_project_id, settings


def run_mobile_push_cutover_command(args: argparse.Namespace) -> int:
    """Run one cutover operator action against an existing sessions database."""

    from .users import UserStore

    db_path = args.sessions_dir / "swinglab.db"
    if not db_path.is_file():
        print(
            "mobile-push-cutover: database not found; pass the existing "
            "sessions directory with --sessions-dir",
            file=sys.stderr,
        )
        return 2

    try:
        _environment, project_id, settings = _validate_target_matches_server(
            environment=args.environment,
            expo_project_id=args.expo_project_id,
            config_path=getattr(args, "config", None),
        )
    except (PushFenceMismatchError, ValueError) as exc:
        print(f"mobile-push-cutover: {exc}", file=sys.stderr)
        return 2

    users = UserStore(db_path)
    try:
        if args.cutover_action == "status":
            payload = fence_status(
                users,
                environment=args.environment,
                expo_project_id=project_id,
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        apply = bool(getattr(args, "apply", False))
        if apply and bool(getattr(args, "dry_run", False)):
            print(
                "mobile-push-cutover: --dry-run and --apply are mutually exclusive",
                file=sys.stderr,
            )
            return 2
        operation_id = str(args.operation_id)
        request_hash = cutover_request_hash(
            environment=args.environment,
            expo_project_id=project_id,
            command=args.cutover_action,
            operation_id=operation_id,
        )
        if args.cutover_action == "close":
            result = close_fence(
                users,
                environment=args.environment,
                expo_project_id=project_id,
                operation_id=operation_id,
                request_hash=request_hash,
                apply=apply,
                skew_seconds=float(settings.cutover_clock_skew_seconds),
            )
        elif args.cutover_action == "purge":
            result = purge_fence(
                users,
                environment=args.environment,
                expo_project_id=project_id,
                operation_id=operation_id,
                request_hash=request_hash,
                apply=apply,
            )
        else:
            print("mobile-push-cutover: unknown action", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (
        PushFenceClosedError,
        PushCutoverConflictError,
        PushCutoverNotReadyError,
        PushFenceMismatchError,
    ) as exc:
        print(f"mobile-push-cutover: {exc}", file=sys.stderr)
        return 1
    finally:
        users.close()
