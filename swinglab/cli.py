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
from .pipeline import SessionResult, ZeroStrikesError, analyze_video

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
    )
    print_summary(result)
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)

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
        uvicorn.run(app, host=args.host, port=args.port)
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
                except (ZeroStrikesError, EventError, FFmpegError) as exc:
                    print(f"SKIPPED {video.name}: {exc}", file=sys.stderr)
                    failures += 1
            return 1 if failures == len(videos) else 0

        if not args.path.is_file():
            print(f"Video not found: {args.path}", file=sys.stderr)
            return 2
        _analyze_one(args.path, args, cfg)
        return 0

    except ZeroStrikesError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except FFmpegError as exc:
        print(f"\nffmpeg error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
