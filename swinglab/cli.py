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
        help="Skip motion-interpolated slow motion (the long step) — much "
        "quicker, slightly less smooth clips",
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
        "free\N{RIGHTWARDS ARROW}Pro, weekly filmers, gear attach) from the "
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
    shopify_backfill.set_defaults(apply=False)
    shopify_backfill.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the PII-minimized operator batch result as JSON",
    )
    shopify_backfill.add_argument(
        "--config", type=Path, default=None, help="Path to config.yaml"
    )

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
    return f"{value:.2f}" if value == value else "—"  # NaN-safe


def print_summary(result: SessionResult) -> None:
    header = f"{'Swing':>5} {'Strike':>8} {'Tempo':>7} {'Sway A→T':>9} {'Slide A→T':>10}"
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

    cfg = Config.load(args.config)

    if args.command == "shopify-backfill":
        import json

        from .integrations.shopify.admin import (
            ShopifyAdminClient,
            ShopifyAdminConfigurationError,
        )
        from .integrations.shopify.backfill import run_backfill_batch
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
        db_path = args.sessions_dir / "swinglab.db"
        if not db_path.is_file():
            print(
                "shopify-backfill: database not found; pass the existing "
                "sessions directory with --sessions-dir",
                file=sys.stderr,
            )
            return 2
        try:
            settings = validate_sync_settings(cfg.shopify_customer_sync)
            client = ShopifyAdminClient.from_env(
                timeout_seconds=settings["request_timeout_seconds"],
            )
            users = UserStore(db_path)
            summary = run_backfill_batch(
                users,
                client,
                batch_size=args.batch_size,
                after=args.after,
                dry_run=not args.apply,
                settings=settings,
            )
        except ShopifyAdminConfigurationError as exc:
            print(f"shopify-backfill: {exc.safe_summary}", file=sys.stderr)
            return 2

        if args.as_json:
            print(json.dumps(summary.as_dict(), indent=2, ensure_ascii=False))
        else:
            mode = "APPLY" if args.apply else "DRY RUN"
            print(
                f"Shopify customer backfill — {mode}\n"
                f"scanned={summary.scanned} linked={summary.linked} "
                f"would_link={summary.would_link} "
                f"would_create={summary.would_create} "
                f"requires_review={summary.requires_review} "
                f"failed={summary.failed} skipped={summary.skipped}"
            )
            if summary.next_cursor:
                print(f"next_cursor={summary.next_cursor}")
        return 1 if summary.failed else 0

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
        uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False)
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
