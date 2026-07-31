"""Protected operator commands for durable Shopify privacy exports.

The commands in this module deliberately keep customer snapshots out of
stdout, stderr, and application logs.  Export is the only operation that
materializes snapshot data, and it can create only a brand-new private file.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path


def add_privacy_subparser(subparsers) -> None:
    """Register the privacy-request operator surface on the main CLI."""

    privacy = subparsers.add_parser(
        "shopify-privacy",
        help="List, export, deliver, or expire Shopify privacy requests.",
    )
    privacy.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path("sessions"),
        help="The web app's sessions directory (contains swinglab.db)",
    )
    actions = privacy.add_subparsers(
        dest="privacy_action",
        required=True,
    )

    list_requests = actions.add_parser(
        "list",
        help="List only PII-free request status metadata.",
    )
    list_requests.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum requests to list (default 100)",
    )
    list_requests.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print PII-free metadata as JSON",
    )

    export = actions.add_parser(
        "export",
        help="Write one integrity-checked snapshot to a new private file.",
    )
    export.add_argument(
        "request_id",
        help="Opaque request ID from shopify-privacy list",
    )
    export.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Explicit new file; an existing path is never overwritten",
    )
    export.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print PII-free completion metadata as JSON",
    )

    delivered = actions.add_parser(
        "mark-delivered",
        help="Record delivery only after an approved external handoff.",
    )
    delivered.add_argument(
        "request_id",
        help="Opaque request ID from shopify-privacy list",
    )
    delivered.add_argument(
        "--confirm-external-delivery",
        action="store_true",
        help="Confirm the export was already delivered through an approved channel",
    )
    delivered.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print PII-free completion metadata as JSON",
    )

    purge = actions.add_parser(
        "purge-expired",
        help="Delete snapshots whose fixed retention deadline has elapsed.",
    )
    purge.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the PII-free removed count as JSON",
    )


def _metadata_payload(request) -> dict[str, object]:
    """Whitelist the intentionally PII-free metadata fields for output."""

    return {
        "request_id": request.request_id,
        "status": request.status,
        "record_count": request.record_count,
        "snapshot_bytes": request.snapshot_bytes,
        "created_at": request.created_at,
        "completed_at": request.completed_at,
        "expires_at": request.expires_at,
        "delivered_at": request.delivered_at,
    }


def _write_new_private_file(path: Path, payload: bytes) -> None:
    """Create ``path`` exclusively and remove it if any write step fails."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
        created = True
        try:
            # POSIX honors the mode passed to os.open subject to umask.  The
            # explicit chmod tightens an unusually permissive umask and is a
            # best-effort hardening step on platforms with limited mode bits.
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _print_request_metadata(request, *, as_json: bool) -> None:
    payload = _metadata_payload(request)
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    delivered_at = (
        "set" if payload["delivered_at"] is not None else "not_set"
    )
    print(
        f"request_id={payload['request_id']} "
        f"status={payload['status']} "
        f"records={payload['record_count']} "
        f"bytes={payload['snapshot_bytes']} "
        f"created_at={payload['created_at']} "
        f"expires_at={payload['expires_at']} "
        f"delivered_at={delivered_at}"
    )


def run_privacy_command(args: argparse.Namespace) -> int:
    """Run one privacy operator action without loading unrelated app config."""

    from ...web.users import UserStore

    db_path = args.sessions_dir / "swinglab.db"
    if not db_path.is_file():
        print(
            "shopify-privacy: database not found; pass the existing "
            "sessions directory with --sessions-dir",
            file=sys.stderr,
        )
        return 2

    try:
        users = UserStore(db_path)
        if args.privacy_action == "list":
            requests = users.list_shopify_privacy_requests(limit=args.limit)
            if args.as_json:
                print(
                    json.dumps(
                        [_metadata_payload(request) for request in requests],
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                for request in requests:
                    _print_request_metadata(request, as_json=False)
                print(f"request_count={len(requests)}")
            return 0

        if args.privacy_action == "export":
            snapshot = users.export_shopify_privacy_request(args.request_id)
            if snapshot is None:
                print(
                    "shopify-privacy: request not found, invalid, or expired",
                    file=sys.stderr,
                )
                return 1
            encoded = (
                json.dumps(
                    snapshot,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            try:
                _write_new_private_file(args.output, encoded)
            except FileExistsError:
                print(
                    "shopify-privacy: refusing to overwrite an existing "
                    "export file",
                    file=sys.stderr,
                )
                return 2
            except OSError:
                print(
                    "shopify-privacy: could not create the requested export "
                    "file",
                    file=sys.stderr,
                )
                return 2
            payload = {
                "request_id": args.request_id,
                "status": "exported",
                "bytes": len(encoded),
            }
            if args.as_json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(
                    f"request_id={payload['request_id']} "
                    f"status={payload['status']} bytes={payload['bytes']}"
                )
            return 0

        if args.privacy_action == "mark-delivered":
            if not args.confirm_external_delivery:
                print(
                    "shopify-privacy: mark-delivered requires "
                    "--confirm-external-delivery after the approved handoff",
                    file=sys.stderr,
                )
                return 2
            request = users.mark_shopify_privacy_request_delivered(
                args.request_id
            )
            if request is None:
                print(
                    "shopify-privacy: request not found, invalid, or expired",
                    file=sys.stderr,
                )
                return 1
            _print_request_metadata(request, as_json=args.as_json)
            return 0

        if args.privacy_action == "purge-expired":
            removed = users.purge_expired_shopify_privacy_requests()
            if args.as_json:
                print(json.dumps({"removed": removed}, indent=2))
            else:
                print(f"removed={removed}")
            return 0
    except (RuntimeError, ValueError):
        # UserStore validation and integrity errors are intentionally reduced
        # to a fixed safe summary.  Snapshot or customer fields never appear.
        print(
            "shopify-privacy: request validation or snapshot integrity failed",
            file=sys.stderr,
        )
        return 2

    raise RuntimeError("Unsupported Shopify privacy action.")
