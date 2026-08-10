"""Package `storefront-theme/` into the zip Shopify's theme uploader accepts.

    python scripts/package_theme.py            # -> dist/caddieinsight-theme.zip
    python scripts/package_theme.py --strict   # fail if theme check cannot run

The storefront deploys by hand — merging to `main` changes nothing on
caddieinsight.com (CLAUDE.md). So the artifact this produces is the actual
deploy, and the two ways a hand-built zip goes wrong are both structural
rather than visible:

  * a wrapper directory at the archive root. Shopify expects `assets/`,
    `config/`, `layout/`, `locales/`, `sections/`, `snippets/` and
    `templates/` as the top-level entries. Zipping the folder instead of its
    contents puts `storefront-theme/` there and the upload is rejected with
    an error that does not say which of the two mistakes you made.
  * a stray top-level file. `storefront-theme/README.md` is source
    documentation, not theme code, and it is enough on its own.

Both are structural, so both are asserted here rather than remembered.

What is deliberately left out of the archive, though it stays in git:

  * The three plan-card source photographs (~5.9 MB of PNG). They are the
    input to `store-assets/plan_card_webp.py` and the record behind the webp
    ladder the plans band actually serves; `store-assets/out/` holds the
    archive copy. Nothing in the theme references them outside a comment, so
    every upload was carrying six megabytes it would never serve.
  * The three retired v3 marks (`swinglab-logo.png`, `-inverse`,
    `swinglab-favicon.png`). The v4 lockup is 1400x279 against v3's 1400x214,
    so a mis-pick in the header logo setting does not just show the wrong
    mark, it breaks header layout. They are already deleted from Shopify
    Files; not shipping them removes the last copy the store can reach.

The exclusion list is verified, not assumed: `_verify_unreferenced` greps the
theme for each excluded filename and refuses to package if one is referenced
anywhere outside a comment. An asset that becomes referenced later fails the
build rather than 404-ing on the storefront.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "storefront-theme"
DIST = ROOT / "dist"
ARCHIVE = DIST / "caddieinsight-theme.zip"

# Shopify recognises exactly these. Anything else at the archive root is a
# rejection, which is why this is an allowlist and not an ignore list.
THEME_DIRS = (
    "assets",
    "config",
    "layout",
    "locales",
    "sections",
    "snippets",
    "templates",
)

EXCLUDED_ASSETS = {
    "caddieinsight-free-card-v2.png": "plan-card source; the webp ladder is what ships",
    "caddieinsight-pro-card-v2.png": "plan-card source; the webp ladder is what ships",
    "caddieinsight-founders-card-v2.png": "plan-card source; the webp ladder is what ships",
    # The retired v3 swinglab-* marks used to be excluded here; they were
    # deleted from the theme directory outright in the 2026-08 rebuild.
}

# Naming a file is not referencing it. `sections/header.liquid` carries the
# schema `info` string "Do not point this at swinglab-logo.png — that Files
# entry is the retired v3 mark", which is a warning *against* using it;
# `layout/theme.liquid` and `sections/plans-band.liquid` explain in comments
# why the retired marks and the plan-card sources are not referenced. A bare
# substring search calls all three a reference and refuses to build.
#
# So this looks for the syntax that actually resolves an asset: a quoted
# filename piped to one of Liquid's URL filters, a Files lookup, or the name
# appearing in a JSON template where settings are wired.
_URL_FILTERS = r"asset_url|asset_img_url|image_url|file_url|file_img_url"


def _reference_patterns(name: str) -> tuple[re.Pattern[str], ...]:
    quoted = re.escape(name)
    return (
        # 'name' | asset_url   (any of the URL filters, any spacing)
        re.compile(rf"['\"]{quoted}['\"]\s*\|\s*(?:{_URL_FILTERS})"),
        # images['name'] / files['name']
        re.compile(rf"(?:images|files)\[\s*['\"]{quoted}['\"]\s*\]"),
        # a plain src/srcset/href/url() path, e.g. in CSS or a JSON template
        re.compile(rf"""(?:src|srcset|href|url\()[^;\n]{{0,80}}{quoted}"""),
    )


def _verify_unreferenced(theme: Path) -> list[str]:
    """Return the excluded filenames the theme actually resolves.

    Conservative in the direction that matters: a filename reached by syntax
    not listed here would be missed, so anything added to EXCLUDED_ASSETS
    still deserves a look at the diff. What it prevents is the common case —
    somebody wires an excluded asset into a section and the next upload
    silently 404s it on the storefront.
    """
    referenced: list[str] = []
    sources = [
        path
        for directory in THEME_DIRS
        for path in sorted((theme / directory).rglob("*"))
        if path.is_file() and path.suffix in {".liquid", ".json", ".css", ".js"}
    ]
    for name in EXCLUDED_ASSETS:
        patterns = _reference_patterns(name)
        for path in sources:
            body = path.read_text(encoding="utf-8", errors="replace")
            if name not in body:
                continue
            if any(pattern.search(body) for pattern in patterns):
                referenced.append(f"{name} referenced by {path.relative_to(theme)}")
    return referenced


def _theme_check(strict: bool) -> None:
    """Run Shopify's own validator when it is installed.

    It is not installed in every environment that needs to build a zip, and a
    packaging script that cannot run without a Ruby toolchain is a packaging
    script nobody runs. CI runs theme-check on every PR
    (.github/workflows/theme-check.yml), so the gate exists either way —
    --strict is for when this build is the only gate.
    """
    binary = shutil.which("shopify")
    if binary is None:
        message = "shopify CLI not found — skipping theme check"
        if strict:
            sys.exit(f"error: {message} (required by --strict)")
        print(f"  ! {message}; CI still runs it on every PR")
        return

    print("  · shopify theme check")
    result = subprocess.run(
        [binary, "theme", "check", "--path", str(THEME), "--fail-level", "warning"],
        check=False,
    )
    if result.returncode != 0:
        sys.exit("error: theme check failed — not packaging a theme that fails its own linter")


def build(strict: bool = False) -> Path:
    if not THEME.is_dir():
        sys.exit(f"error: {THEME} does not exist")

    print(f"packaging {THEME.relative_to(ROOT)}")
    _theme_check(strict)

    referenced = _verify_unreferenced(THEME)
    if referenced:
        joined = "\n  ".join(referenced)
        sys.exit(
            "error: an excluded asset is referenced by the theme — excluding it "
            f"would 404 on the storefront:\n  {joined}\n"
            "Remove it from EXCLUDED_ASSETS or stop referencing it."
        )

    DIST.mkdir(exist_ok=True)
    if ARCHIVE.exists():
        ARCHIVE.unlink()

    written = 0
    skipped_bytes = 0
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for directory in THEME_DIRS:
            source_dir = THEME / directory
            if not source_dir.is_dir():
                continue
            for path in sorted(source_dir.rglob("*")):
                if not path.is_file():
                    continue
                if path.name in EXCLUDED_ASSETS:
                    skipped_bytes += path.stat().st_size
                    continue
                # arcname is relative to the THEME root, never to the repo —
                # this is the line that decides whether Shopify accepts it.
                bundle.write(path, path.relative_to(THEME).as_posix())
                written += 1

    _assert_shape(ARCHIVE)
    _write_upload_notes()

    size = ARCHIVE.stat().st_size
    print(f"  · {written} files, {size / 1_048_576:.2f} MiB")
    print(f"  · {skipped_bytes / 1_048_576:.2f} MiB of source art left out of the upload")
    print(f"\nwrote {ARCHIVE.relative_to(ROOT)}")
    print(f"read  {(DIST / 'UPLOAD.md').relative_to(ROOT)} before uploading")
    return ARCHIVE


def _assert_shape(archive: Path) -> None:
    """The two structural rejections, checked against the built artifact."""
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()

    roots = {name.split("/", 1)[0] for name in names}
    unexpected = roots - set(THEME_DIRS)
    if unexpected:
        sys.exit(f"error: unexpected entries at the archive root: {sorted(unexpected)}")

    top_level_files = [name for name in names if "/" not in name]
    if top_level_files:
        sys.exit(f"error: stray files at the archive root: {top_level_files}")

    for required in ("layout/theme.liquid", "config/settings_schema.json"):
        if required not in names:
            sys.exit(f"error: {required} missing from the archive")


UPLOAD_NOTES = """# Uploading the CaddieInsight theme

`dist/caddieinsight-theme.zip` is built by `python scripts/package_theme.py`.
Rebuild it rather than editing it.

## Upload

1. Shopify admin -> **Online Store -> Themes -> Add theme -> Upload zip**.
   This creates a new **unpublished** theme. Do not use "Edit code" on the
   live one — that is the path with no preview and no rollback.
2. **Preview** the new theme and check, in this order:

   - [ ] The browser tab shows the CaddieInsight mark, not a default globe.
   - [ ] View source, search `Liquid error` — **zero hits**. The live theme
         has five today (favicon, apple-touch-icon, og:image, twitter:image
         and the Organization JSON-LD logo), because it resolves those marks
         through a Shopify Files lookup and none of those filenames exist in
         Files. A missing Files lookup returns a *truthy* drop, so the guard
         passes and `image_url` throws. This build resolves them through
         `asset_url` instead, so the theme carries its own marks.
   - [ ] Paste the preview URL into iMessage or Slack — an image card
         appears rather than a bare grey link.
   - [ ] `/products/swinglab-pro` still has its premium dark header. This is
         keyed off the product handle in `layout/theme.liquid` and
         `sections/header.liquid`; if the Pro page looks like an ordinary
         product page, stop and say so.
   - [ ] `/products/swinglab-pro?view=membership` (the `?view=` parameter
         forces the new membership template without assigning it) shows the
         membership buy box: plan radios, benefits list, the Founders note
         on the Founders variant, and a three-line terms rail — no quantity
         field, no shipping copy.
   - [ ] The gear collection populates.
   - [ ] Homepage plans band: all three cards render at the same size.

3. **Publish.** Leave the previous theme in the list — that is the rollback.

4. **Assign the membership template** (once, right after publishing this
   build): Shopify admin -> **Products -> CaddieInsight Pro -> Theme
   template** (right-hand column) -> pick **membership** -> Save. The
   membership buy box now lives in `templates/product.membership.json`
   rather than a product-type branch inside the gear template. Until this
   dropdown is set, the Pro page renders the gear buy box — quantity field,
   shipping rail, no plan radios — which sells the wrong story. Gear
   products stay on **Default product**.

## Do not

- Do not upload Shopify **Files** named `og-caddieinsight.png`,
  `caddieinsight-favicon.png` or `caddieinsight-logo.png`. A Files entry
  beats a theme asset of the same name, so doing that would override what you
  just previewed, with no preview of its own and no rollback.
- Do not overwrite an existing Files entry to change artwork. Ship a new
  filename instead (`store-assets/README.md`).

## What is not in the archive, on purpose

Six megabytes of source photography (the three plan-card PNGs) and the three
retired v3 marks. They stay in git and in `store-assets/out/`; they were
never served by the theme. Excluding them makes every upload smaller and
removes the last copy of the v3 mark the store could accidentally pick.
"""


def _write_upload_notes() -> None:
    (DIST / "UPLOAD.md").write_text(UPLOAD_NOTES, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when the Shopify CLI is unavailable instead of skipping theme check",
    )
    build(strict=parser.parse_args().strict)


if __name__ == "__main__":
    main()
