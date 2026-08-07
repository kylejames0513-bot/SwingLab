"""Render deterministic guided-report fixtures into an explicit QA folder."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from swinglab.config import Config  # noqa: E402
from swinglab.report_html import write_report_document_html  # noqa: E402
from swinglab.sample import (  # noqa: E402
    build_guided_sample_report,
    ensure_sample_report,
)
from tests.report_view_fixtures import (  # noqa: E402
    GUIDED_DOCUMENT_QA_FIXTURE_NAMES,
    QA_SYNTHETIC_MP4,
    report_document_fixture,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render synthetic guided-report documents for local QA."
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Empty output directory to create or populate.",
    )
    return parser


def _validated_output(
    parser: argparse.ArgumentParser, requested: Path
) -> Path:
    output = requested.expanduser().resolve()
    if output == REPOSITORY_ROOT:
        parser.error("--output must not resolve to the repository root")
    if not output.parent.is_dir():
        parser.error("--output parent must already exist")
    if output.exists():
        if not output.is_dir():
            parser.error("--output must be a directory")
        if any(output.iterdir()):
            parser.error("--output directory must be empty")
    else:
        output.mkdir()
    return output


def _safe_target(fixture_root: Path, relative_path: str) -> Path:
    target = (fixture_root / relative_path).resolve()
    if not target.is_relative_to(fixture_root):
        raise ValueError(f"fixture media escapes output root: {relative_path}")
    return target


def _write_declared_media(fixture_root: Path, document) -> None:
    for entry in sorted(
        document.media_by_key.values(), key=lambda item: item.relative_path
    ):
        target = _safe_target(fixture_root, entry.relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.mime_type.startswith("image/"):
            image = Image.new("RGB", (1280, 720), "#25302a")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (72, 72, 1208, 648),
                radius=42,
                fill="#2e3a33",
                outline="#e8720c",
                width=12,
            )
            draw.text((120, 128), "SYNTHETIC QA MEDIA", fill="#ffffff")
            draw.text((120, 180), entry.key, fill="#ffffff")
            draw.text(
                (120, 222),
                entry.role.value.replace("_", " "),
                fill="#c9d4cc",
            )
            image.save(
                target,
                format="PNG" if target.suffix.lower() == ".png" else "JPEG",
            )
        elif entry.mime_type == "video/mp4":
            target.write_bytes(QA_SYNTHETIC_MP4)
        else:
            raise ValueError(
                f"unsupported QA media type for {entry.key}: {entry.mime_type}"
            )


def _render_document_fixture(output: Path, name: str, cfg: Config) -> Path:
    fixture_root = output / name
    fixture_root.mkdir()
    document = report_document_fixture(name)
    _write_declared_media(fixture_root, document)
    return write_report_document_html(
        fixture_root / "report.html", document, cfg=cfg
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    output = _validated_output(parser, args.output)
    cfg = Config()

    for name in GUIDED_DOCUMENT_QA_FIXTURE_NAMES:
        report = _render_document_fixture(output, name, cfg)
        print(report)

    guided = build_guided_sample_report(output / "guided-sample-preview", cfg)
    legacy = ensure_sample_report(output / "legacy-sample-default", Config())
    print(guided)
    print(legacy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
