"""Command line interface.

    swinglab analyze path/to/video.mov --out results/ --hand right
    swinglab analyze path/to/folder --batch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .events import EventError
from .ffmpeg import FFmpegError
from .metrics import SwingMetrics
from .pipeline import SessionResult, VideoTooLongError, ZeroStrikesError, analyze_video

VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swinglab", description="Golf swing analysis from a single phone video."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ana = sub.add_parser("analyze", help="Analyze a video (or a folder with --batch).")
    ana.add_argument("path", type=Path, help="Video file, or folder with --batch")
    ana.add_argument("--out", type=Path, default=None, help="Output directory")
    ana.add_argument(
        "--hand", choices=("right", "left"), default="right", help="Golfer handedness"
    )
    ana.add_argument(
        "--angle",
        choices=("face-on", "dtl"),
        default="face-on",
        help="Camera angle. face-on (default) gives the full report; dtl "
        "(down the line) keeps tempo/rhythm and honestly leaves the "
        "face-on-defined body-drift and angle numbers unmeasured",
    )
    ana.add_argument(
        "--batch", action="store_true", help="Analyze every video in a folder"
    )
    ana.add_argument(
        "--strikes",
        type=str,
        default=None,
        help='Manual strike times in seconds, e.g. --strikes "12.5,31.0" '
        "(skips audio detection)",
    )
    ana.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    ana.add_argument(
        "--keep-work", action="store_true", help="Keep intermediate frames/audio"
    )
    ana.add_argument(
        "--fast",
        action="store_true",
        help="Skip motion-interpolated slow motion (the long step) -- much "
        "quicker, slightly less smooth clips",
    )

    batch = sub.add_parser(
        "batch",
        help="Analyze a validated JSONL manifest sequentially and resumably.",
    )
    batch.add_argument(
        "manifest", type=Path,
        help="JSONL rows with id, path, and optional per-clip context",
    )
    batch.add_argument("--out", type=Path, default=None, help="Output directory")
    batch.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate every row and print the plan without analyzing or writing state",
    )
    batch.add_argument(
        "--resume",
        action="store_true",
        help="Skip completed rows whose manifest instruction and report still match",
    )
    batch.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Resume-state JSON path (default: <manifest>.state.json)",
    )
    batch.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print one machine-readable summary to stdout",
    )
    batch.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    batch.add_argument(
        "--keep-work", action="store_true", help="Keep intermediate frames/audio"
    )
    batch.add_argument(
        "--fast",
        action="store_true",
        help="Skip motion-interpolated slow motion for every manifest row",
    )

    srv = sub.add_parser("serve", help="Run the web app (upload page + results).")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.add_argument(
        "--sessions-dir", type=Path, default=Path("sessions"),
        help="Where uploads and results are stored",
    )
    srv.add_argument("--config", type=Path, default=None, help="Path to config.yaml")

    kp = sub.add_parser(
        "kpis",
        help="Print the five business KPIs (activation, W1 re-film, "
        "free-to-Pro, weekly filmers, gear attach) from the "
        "web app's database.",
    )
    kp.add_argument(
        "--since", type=float, default=90.0, metavar="DAYS",
        help="Trailing window in days (default 90)",
    )
    kp.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Machine-readable output (same payload as GET /admin/kpis)",
    )
    kp.add_argument(
        "--sessions-dir", type=Path, default=Path("sessions"),
        help="The web app's sessions directory (its swinglab.db is read)",
    )
    kp.add_argument("--config", type=Path, default=None, help="Path to config.yaml")

    shopify_backfill = sub.add_parser(
        "shopify-backfill",
        help="Dry-run or apply one restartable batch of Shopify customer links.",
    )
    shopify_backfill.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path("sessions"),
        help="The web app's sessions directory (contains swinglab.db)",
    )
    shopify_backfill.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Users to inspect in this invocation (1-1000, default 50)",
    )
    shopify_backfill.add_argument(
        "--after",
        default=None,
        metavar="CURSOR",
        help="Opaque next_cursor printed by the previous batch",
    )
    shopify_backfill.add_argument(
        "--all-batches",
        action="store_true",
        help="Process every remaining page and print one cumulative summary",
    )
    shopify_backfill.add_argument(
        "--preflight-only",
        action="store_true",
        help="Inspect schema/store binding read-only; do not contact Shopify",
    )
    shopify_backfill.add_argument(
        "--confirm-store",
        default=None,
        metavar="STORE.myshopify.com",
        help="Exact canonical store confirmation for an unbound database",
    )
    apply_group = shopify_backfill.add_mutually_exclusive_group()
    apply_group.add_argument(
        "--dry-run",
        action="store_false",
        dest="apply",
        help="Read matches without changing customer links or sync state (default)",
    )
    apply_group.add_argument(
        "--apply",
        action="store_true",
        help="Perform the idempotent customer upserts for this batch",
    )
    apply_group.add_argument(
        "--bind-only",
        action="store_true",
        help="Verify and bind this database/store identity; touch no customers",
    )
    shopify_backfill.set_defaults(apply=False, bind_only=False)
    shopify_backfill.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the PII-minimized operator batch result as JSON",
    )
    shopify_backfill.add_argument(
        "--config", type=Path, default=None, help="Path to config.yaml"
    )

    shopify_resolve = sub.add_parser(
        "shopify-resolve-customer",
        help="Verify and transactionally resolve one Shopify customer link.",
    )
    shopify_resolve.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path("sessions"),
        help="The web app's sessions directory (contains swinglab.db)",
    )
    shopify_resolve.add_argument(
        "--user-ref",
        required=True,
        help="Protected 12-character user_ref from the admin sync view",
    )
    resolve_customer = shopify_resolve.add_mutually_exclusive_group(
        required=True
    )
    resolve_customer.add_argument(
        "--customer-id",
        help="Shopify customer id (never echoed; may remain in shell history)",
    )
    resolve_customer.add_argument(
        "--customer-id-env",
        metavar="ENV_VAR",
        help="Read the Shopify customer id from this environment variable",
    )
    shopify_resolve.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print a PII-minimized result as JSON",
    )
    shopify_resolve.add_argument(
        "--config", type=Path, default=None, help="Path to config.yaml"
    )

    from .integrations.shopify.privacy_cli import add_privacy_subparser

    add_privacy_subparser(sub)

    # Explicit operator tooling only. Merely installing the package or setting
    # credentials starts no backup process and changes no web runtime behavior.
    from .backups.cli import add_backup_subparser

    add_backup_subparser(sub)
    return parser


def _parse_strikes(raw: str | None) -> list[float] | None:
    if raw is None:
        return None
    try:
        times = [float(part) for part in raw.replace(";", ",").split(",") if part.strip()]
    except ValueError:
        raise SystemExit(f'--strikes must be comma-separated seconds, got: "{raw}"')
    if not times:
        raise SystemExit("--strikes was given but contained no times")
    return times


def _fmt(value: float) -> str:
    return f"{value:.2f}" if value == value else "-"  # NaN-safe


def _shopify_store_domains_match(admin_store_domain: str) -> bool:
    """Require the inbound webhook store and Admin target to be identical."""

    import os

    from .integrations.shopify.identity import normalize_shop_domain

    inbound_store = normalize_shop_domain(
        os.environ.get("SHOPIFY_STORE_DOMAIN")
    )
    outbound_store = normalize_shop_domain(admin_store_domain)
    return bool(
        inbound_store
        and outbound_store
        and inbound_store == outbound_store
    )


def print_summary(result: SessionResult) -> None:
    header = (
        f"{'Swing':>5} {'Strike':>8} {'Tempo':>7} "
        f"{'Sway A->T':>9} {'Slide A->T':>10}"
    )
    print()
    print(header)
    print("-" * len(header))
    for swing in result.swings:
        m: SwingMetrics = swing["metrics"]
        print(
            f"{m.swing:>5} {m.strike_s:>7.2f}s {_fmt(m.tempo_ratio):>7} "
            f"{_fmt(m.head_sway_backswing_sw):>9} {_fmt(m.hip_slide_backswing_sw):>10}"
        )
    tempo = result.stats.get("tempo_ratio")
    if tempo:
        print(f"\nTempo mean {tempo['mean']:.2f} ± {tempo['std']:.2f} across "
              f"{len(result.swings)} swing(s)")
    for msg in result.skipped:
        print(f"note: {msg}")
    print(f"\nReport: {result.report_path}")


def _analyze_one(path: Path, args: argparse.Namespace, cfg: Config) -> SessionResult:
    result = analyze_video(
        path,
        out_dir=args.out,
        hand=args.hand,
        manual_strikes=_parse_strikes(args.strikes),
        cfg=cfg,
        keep_work=args.keep_work,
        fast=args.fast,
        angle=args.angle,
    )
    print_summary(result)
    return result


def print_kpis(results, since_days: float, db_path: Path) -> None:
    """A clean table: value, numerator/denominator, and — for any metric
    the data cannot support — the honest reason instead of a number."""
    from .kpis import TARGETS, format_value

    print(f"KPIs over the last {since_days:g} days ({db_path})\n")
    header = f"{'KPI':<28} {'Value':>10}  {'n/d':>9}  Notes"
    print(header)
    print("-" * len(header))
    for kpi in results:
        if kpi.value is None:
            nd = "\N{EM DASH}"
            note = kpi.reason or ""
        else:
            den = kpi.denominator if kpi.denominator is not None else "\N{EM DASH}"
            nd = f"{kpi.numerator}/{den}"
            note = f"target {TARGETS[kpi.key]}" if kpi.key in TARGETS else ""
        print(f"{kpi.key:<28} {format_value(kpi):>10}  {nd:>9}  {note}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "backup":
        from .backups.cli import run_backup_command

        return run_backup_command(args)

    if args.command == "shopify-privacy":
        from .integrations.shopify.privacy_cli import run_privacy_command

        return run_privacy_command(args)

    cfg = Config.load(args.config)

    if args.command == "batch":
        import json

        from .batch_v2 import BatchManifestError, run_manifest_batch

        def batch_log(message: str) -> None:
            print(message, file=sys.stderr if args.as_json else sys.stdout)

        try:
            exit_code, summary = run_manifest_batch(
                args.manifest,
                cfg=cfg,
                out_dir=args.out,
                keep_work=args.keep_work,
                fast=args.fast,
                dry_run=args.dry_run,
                resume=args.resume,
                state_path=args.state,
                analyze=analyze_video,
                on_result=None if args.as_json else print_summary,
                log=batch_log,
                error=lambda message: print(message, file=sys.stderr),
            )
        except BatchManifestError as exc:
            print(f"batch: {exc}", file=sys.stderr)
            return 2

        if args.as_json:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            for item in summary["items"]:
                if item["status"] == "planned":
                    print(f"PLAN {item['id']}: {item['path']}")
            print(
                "\nBatch summary: "
                f"{summary['completed']} completed, {summary['resumed']} resumed, "
                f"{summary['planned']} planned, {summary['failed']} failed "
                f"({summary['total']} total)"
            )
            if not args.dry_run:
                print(f"State: {summary['state']}")
        return exit_code

    if args.command == "shopify-backfill":
        import json

        from .integrations.shopify.admin import (
            ShopifyAdminError,
            ShopifyAdminClient,
        )
        from .integrations.shopify.backfill import (
            BackfillSafetyError,
            ReadOnlyBackfillStore,
            authenticate_and_bind_backfill_database,
            preflight_backfill_database,
            require_matching_shopify_store_binding,
            run_backfill_all,
            run_backfill_batch,
        )
        from .integrations.shopify.customer_sync import (
            validate_sync_settings,
        )
        from .web.users import UserStore

        if not 1 <= args.batch_size <= 1000:
            print(
                "shopify-backfill: batch size must be between 1 and 1000",
                file=sys.stderr,
            )
            return 2
        if args.preflight_only and (
            args.apply or args.bind_only or args.after or args.all_batches
        ):
            print(
                "shopify-backfill: --preflight-only cannot be combined "
                "with execution or continuation options",
                file=sys.stderr,
            )
            return 2
        if args.bind_only and (args.after or args.all_batches):
            print(
                "shopify-backfill: --bind-only cannot be combined with "
                "--after or --all-batches",
                file=sys.stderr,
            )
            return 2
        db_path = args.sessions_dir / "swinglab.db"
        if not db_path.is_file():
            print(
                "shopify-backfill: database not found; pass the existing "
                "sessions directory with --sessions-dir",
                file=sys.stderr,
            )
            return 2
        try:
            if args.preflight_only:
                import os

                preflight = preflight_backfill_database(
                    db_path,
                    os.environ.get("SHOPIFY_ADMIN_STORE_DOMAIN", ""),
                )
                if args.as_json:
                    print(
                        json.dumps(
                            preflight.as_dict(),
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                else:
                    print(
                        "Shopify customer backfill - READ-ONLY PREFLIGHT\n"
                        f"database_ref={preflight.database_ref} "
                        f"store_ref={preflight.store_ref} "
                        f"schema_ready={str(preflight.schema_ready).lower()} "
                        f"binding={preflight.binding_status}"
                    )
                    if preflight.missing_columns:
                        print(
                            "missing_sync_columns="
                            f"{len(preflight.missing_columns)}"
                        )
                return (
                    1
                    if (
                        not preflight.schema_ready
                        or preflight.binding_status == "mismatch"
                    )
                    else 0
                )
            settings = validate_sync_settings(cfg.shopify_customer_sync)
            client = ShopifyAdminClient.from_env(
                timeout_seconds=settings["request_timeout_seconds"],
            )
            if not _shopify_store_domains_match(client.store_domain):
                raise BackfillSafetyError(
                    "Inbound and outbound Shopify store configuration does "
                    "not match."
                )
            preflight = preflight_backfill_database(
                db_path,
                client.store_domain,
            )
            if preflight.binding_status == "mismatch":
                raise BackfillSafetyError(
                    "The selected database is bound to a different "
                    "Shopify store."
                )
            if args.bind_only and not preflight.schema_ready:
                raise BackfillSafetyError(
                    "The database needs the additive Shopify sync migration "
                    "before it can be bound."
                )
            if args.bind_only and (
                preflight.binding_status in {"unbound", "incomplete"}
                and str(args.confirm_store or "").strip().lower()
                != client.store_domain
            ):
                raise BackfillSafetyError(
                    "An unbound database requires an exact --confirm-store "
                    "value before it can be bound."
                )
            if (
                not preflight.schema_ready
                and not args.apply
                and not args.bind_only
            ):
                raise BackfillSafetyError(
                    "The database needs the additive Shopify sync migration; "
                    "run the application migration before a read-only dry run."
                )
            if (
                not args.bind_only
                and preflight.binding_status in {"unbound", "incomplete"}
            ):
                raise BackfillSafetyError(
                    "Bind the database first with --bind-only and an exact "
                    "--confirm-store value before any customer request."
                )
            # Authenticate the exact canonical endpoint before any customer
            # read or before persisting the database-to-store binding. The
            # returned Shop GID is protected operational evidence and is
            # intentionally neither stored in output nor logged.
            if args.bind_only:
                preflight = authenticate_and_bind_backfill_database(
                    db_path,
                    client.store_domain,
                    client.verify_store_access,
                    confirmation=args.confirm_store,
                )
                payload = {
                    "action": "bound",
                    **preflight.as_dict(),
                }
                if args.as_json:
                    print(
                        json.dumps(payload, indent=2, ensure_ascii=False)
                    )
                else:
                    print(
                        "Shopify customer backfill - BIND ONLY\n"
                        f"database_ref={preflight.database_ref} "
                        f"store_ref={preflight.store_ref} "
                        f"shop_ref={preflight.shop_ref} "
                        f"binding={preflight.binding_status}"
                    )
                return 0
            shop_gid = client.verify_store_access()
            if preflight.binding_status == "matched":
                preflight = require_matching_shopify_store_binding(
                    db_path,
                    client.store_domain,
                    shop_gid,
                )
            if args.apply:
                users = UserStore(db_path)
            else:
                users = ReadOnlyBackfillStore(db_path)
            runner = (
                run_backfill_all
                if args.all_batches
                else run_backfill_batch
            )
            try:
                summary = runner(
                    users,
                    client,
                    batch_size=args.batch_size,
                    after=args.after,
                    dry_run=not args.apply,
                    settings=settings,
                )
            finally:
                if isinstance(users, ReadOnlyBackfillStore):
                    users.close()
            summary.database_ref = preflight.database_ref
            summary.store_ref = preflight.store_ref
            summary.binding_status = preflight.binding_status
        except (ShopifyAdminError, BackfillSafetyError) as exc:
            print(f"shopify-backfill: {exc.safe_summary}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"shopify-backfill: {exc}", file=sys.stderr)
            return 2

        if args.as_json:
            print(json.dumps(summary.as_dict(), indent=2, ensure_ascii=False))
        else:
            mode = "APPLY" if args.apply else "DRY RUN"
            print(
                f"Shopify customer backfill - {mode}\n"
                f"scanned={summary.scanned} linked={summary.linked} "
                f"would_link={summary.would_link} "
                f"would_create={summary.would_create} "
                f"requires_review={summary.requires_review} "
                f"failed={summary.failed} skipped={summary.skipped} "
                f"batches={summary.batches}\n"
                f"database_ref={summary.database_ref} "
                f"store_ref={summary.store_ref} "
                f"binding={summary.binding_status}"
            )
            if summary.next_cursor:
                print(f"next_cursor={summary.next_cursor}")
        return 1 if (summary.failed or summary.requires_review) else 0

    if args.command == "shopify-resolve-customer":
        import json

        from .integrations.shopify.admin import (
            ShopifyAdminError,
            ShopifyAdminClient,
        )
        from .integrations.shopify.backfill import (
            BackfillSafetyError,
            authenticate_and_require_backfill_binding,
            preflight_backfill_database,
        )
        from .integrations.shopify.customer_sync import (
            find_user_by_operator_ref,
            validate_sync_settings,
            verify_and_link_existing_shopify_customer,
        )
        from .web.users import SHOPIFY_SYNC_SYNCED, UserStore

        db_path = args.sessions_dir / "swinglab.db"
        if not db_path.is_file():
            print(
                "shopify-resolve-customer: database not found; pass the "
                "existing sessions directory with --sessions-dir",
                file=sys.stderr,
            )
            return 2
        if args.customer_id_env:
            import os

            customer_id = os.environ.get(args.customer_id_env, "")
            if not customer_id:
                print(
                    "shopify-resolve-customer: customer id environment "
                    "variable is missing or empty",
                    file=sys.stderr,
                )
                return 2
        else:
            customer_id = args.customer_id
        try:
            settings = validate_sync_settings(cfg.shopify_customer_sync)
            client = ShopifyAdminClient.from_env(
                timeout_seconds=settings["request_timeout_seconds"],
            )
            if not _shopify_store_domains_match(client.store_domain):
                raise BackfillSafetyError(
                    "Inbound and outbound Shopify store configuration does "
                    "not match."
                )
            preflight = preflight_backfill_database(
                db_path,
                client.store_domain,
            )
            if not preflight.schema_ready:
                raise BackfillSafetyError(
                    "The database needs the additive Shopify sync migration "
                    "before identity resolution."
                )
            if preflight.binding_status == "mismatch":
                raise BackfillSafetyError(
                    "The selected database is bound to a different "
                    "Shopify store."
                )
            if preflight.binding_status in {"unbound", "incomplete"}:
                raise BackfillSafetyError(
                    "Bind the database first with shopify-backfill "
                    "--bind-only before identity resolution."
                )
            preflight = authenticate_and_require_backfill_binding(
                db_path,
                client.store_domain,
                client.verify_store_access,
            )
            users = UserStore(db_path)
            user = find_user_by_operator_ref(users, args.user_ref)
            if user is None:
                raise BackfillSafetyError(
                    "The protected user reference was invalid, ambiguous, "
                    "or not found."
                )
            result = verify_and_link_existing_shopify_customer(
                users,
                user.id,
                customer_id,
                client,
            )
        except (ShopifyAdminError, BackfillSafetyError) as exc:
            print(
                f"shopify-resolve-customer: {exc.safe_summary}",
                file=sys.stderr,
            )
            return 2

        payload = {
            "database_ref": preflight.database_ref,
            "store_ref": preflight.store_ref,
            "user_ref": args.user_ref.lower(),
            "status": result.status,
            "action": result.action,
            "safe_error": result.safe_error,
        }
        if args.as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(
                "Shopify customer resolution\n"
                f"database_ref={payload['database_ref']} "
                f"store_ref={payload['store_ref']} "
                f"user_ref={payload['user_ref']} "
                f"status={payload['status']} action={payload['action']}"
            )
            if payload["safe_error"]:
                print(f"safe_error={payload['safe_error']}")
        return 0 if result.status == SHOPIFY_SYNC_SYNCED else 1

    if args.command == "kpis":
        import json
        import math

        from .kpis import compute_kpis

        # nan slips past a plain <= 0 (all nan comparisons are False), so
        # require a finite positive window explicitly.
        if not math.isfinite(args.since) or args.since <= 0:
            print("--since must be a positive number of days", file=sys.stderr)
            return 2
        db_path = args.sessions_dir / "swinglab.db"
        results = compute_kpis(db_path, cfg, since_days=args.since)
        if args.as_json:
            print(json.dumps(
                {
                    "window_days": args.since,
                    "kpis": {k.key: k.as_dict() for k in results},
                },
                indent=2, ensure_ascii=False,
            ))
        else:
            print_kpis(results, args.since, db_path)
        return 0

    if args.command == "serve":
        try:
            import uvicorn

            from .web.app import create_app
            from .web.access_log import access_log_config
        except ImportError as exc:
            print(
                f"Web dependencies missing ({exc.name}). Install them with: "
                'pip install "swinglab[web]"',
                file=sys.stderr,
            )
            return 2
        app = create_app(cfg, sessions_dir=args.sessions_dir)
        print(f"{cfg.brand['name']} web app on http://{args.host}:{args.port}")
        # X-Forwarded-For handling lives INSIDE the app (create_app adds
        # ProxyHeadersMiddleware per web.trusted_proxies — "*" as shipped
        # for PaaS proxies, a list of IPs, or "" to disable). uvicorn's own
        # proxy_headers layer is switched off so there is exactly one place
        # that decides which proxies to trust.
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            proxy_headers=False,
            log_config=access_log_config(),
        )
        return 0

    try:
        if args.batch:
            folder = args.path
            if not folder.is_dir():
                print(f"--batch needs a folder, got: {folder}", file=sys.stderr)
                return 2
            videos = sorted(
                p for p in folder.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES
            )
            if not videos:
                print(f"No videos found in {folder}", file=sys.stderr)
                return 2
            failures = 0
            for video in videos:
                print(f"\n=== {video.name} ===")
                try:
                    _analyze_one(video, args, cfg)
                except (
                    ZeroStrikesError, VideoTooLongError, EventError, FFmpegError
                ) as exc:
                    print(f"SKIPPED {video.name}: {exc}", file=sys.stderr)
                    failures += 1
            return 1 if failures == len(videos) else 0

        if not args.path.is_file():
            print(f"Video not found: {args.path}", file=sys.stderr)
            return 2
        _analyze_one(args.path, args, cfg)
        return 0

    except (ZeroStrikesError, VideoTooLongError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except FFmpegError as exc:
        print(f"\nffmpeg error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
