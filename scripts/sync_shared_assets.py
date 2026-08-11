"""Copy the assets both surfaces must ship identically.

The storefront and the app are one product with two deploy paths — the theme
goes up as a manual zip, the app auto-deploys from `main` — so anything that
appears on both has to be copied deliberately rather than edited twice. The
storefront copy is the source of record because that is where the theme's
own tooling (theme-check, `make theme-zip`) can see it.

Fonts are NOT handled here; they come from store-assets/make_fonts.py, which
fetches and writes both copies in one pass.

Run from the repository root:

    python scripts/sync_shared_assets.py          # copy, report
    python scripts/sync_shared_assets.py --check  # verify only, non-zero on drift

`--check` is what CI and the parity test want: it never writes.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "storefront-theme" / "assets"
APP = ROOT / "swinglab" / "web" / "static"

# (source name in the theme, destination name in the app)
SHARED = [
    ("swing-trace.js", "swing-trace.js"),
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the copies match without writing anything",
    )
    args = parser.parse_args(argv)

    drift = []
    for source_name, dest_name in SHARED:
        source = THEME / source_name
        dest = APP / dest_name
        if not source.exists():
            print(f"missing source: {source}", file=sys.stderr)
            return 2

        same = dest.exists() and _digest(source) == _digest(dest)
        if same:
            print(f"ok    {source_name}  {source.stat().st_size:,} B")
            continue

        if args.check:
            drift.append(source_name)
            print(f"DRIFT {source_name}", file=sys.stderr)
        else:
            shutil.copyfile(source, dest)
            print(f"wrote {source_name} -> {dest.relative_to(ROOT)}")

    if drift:
        print(
            f"\n{len(drift)} shared asset(s) differ. "
            f"Run: python scripts/sync_shared_assets.py",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
