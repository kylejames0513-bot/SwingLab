"""No theme lookup may resolve to a retired v3 brand filename.

A Shopify **Files** entry always beats a theme asset of the same name. So a
`images['<name>']` lookup pointing at a filename the v3 brand also used serves
the *old* art no matter what the theme packages — and it does it silently,
because the theme asset of that name sits right there looking correct.

That is not hypothetical. Files holds `swinglab-logo.png` at 1400x214 with alt
"SwingLab logo"; the theme ships the Tour Caddie v4 lockup at 1400x279 under
the same name. The og and favicon marks were moved onto `caddieinsight-*`
names for exactly this reason, and the schema.org Organization logo was missed
— so structured data served Google the retired mark while the header beside it
rendered the current one.

This is the check that would have caught it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

THEME = Path(__file__).resolve().parent.parent / "storefront-theme"

# Names that exist in Shopify Files under the retired brand. A theme lookup
# for any of these resolves to v3 art, whatever the theme ships.
RETIRED_IN_FILES = (
    "swinglab-logo.png",
    "swinglab-logo-inverse.png",
    "swinglab-favicon.png",
    "og-swinglab.png",
    "swinglab-hero.png",
)

LIQUID = sorted(THEME.rglob("*.liquid"))


def lookups(text: str) -> list[str]:
    """Every filename the theme resolves through the Files-first `images[]`."""
    return re.findall(r"images\[\s*['\"]([^'\"]+)['\"]\s*\]", text)


@pytest.mark.parametrize("path", LIQUID, ids=lambda p: p.name)
def test_no_images_lookup_targets_a_retired_brand_filename(path):
    retired = sorted(
        set(lookups(path.read_text(encoding="utf-8"))) & set(RETIRED_IN_FILES)
    )
    assert not retired, (
        f"{path.relative_to(THEME)} resolves {retired} through images[], which "
        "Shopify serves from Files — the retired v3 art — regardless of the "
        "theme asset of the same name. Ship a new caddieinsight-* name and "
        "point the lookup at that; never overwrite a filename the live theme "
        "already references."
    )


@pytest.mark.parametrize("path", LIQUID, ids=lambda p: p.name)
def test_no_asset_url_serves_a_retired_brand_filename(path):
    """asset_url resolves to the theme copy, so this is a naming problem
    rather than a wrong-art problem — but a v3 filename in the markup is how
    the next person concludes the rename was finished when it was not."""
    text = path.read_text(encoding="utf-8")
    served = sorted(
        name
        for name in RETIRED_IN_FILES
        if re.search(rf"['\"]{re.escape(name)}['\"]\s*\|\s*asset_url", text)
    )
    assert not served, (
        f"{path.relative_to(THEME)} serves {served} via asset_url. The art may "
        "be current, but the name is the retired one — rename the asset."
    )


def test_the_replacement_assets_are_actually_packaged():
    """A repointed lookup with no asset behind it is a broken image.

    The fallback in theme.liquid is what lets a theme upload fix the logo
    without also uploading to Files, and it only works if the file ships.
    """
    missing = [
        name
        for name in ("caddieinsight-logo.png", "caddieinsight-logo-inverse.png")
        if not (THEME / "assets" / name).is_file()
    ]
    assert not missing, f"{missing} are referenced but not packaged in assets/."


def test_the_organization_logo_has_a_theme_fallback():
    """Without the fallback, a store that has not uploaded to Files emits no
    logo at all — which is how repointing the lookup could quietly make the
    structured data worse rather than better."""
    text = (THEME / "layout" / "theme.liquid").read_text(encoding="utf-8")
    assert "images['caddieinsight-logo.png']" in text
    assert "'caddieinsight-logo.png' | asset_url" in text, (
        "The Organization logo must fall back to the packaged asset when the "
        "Files entry is absent."
    )
