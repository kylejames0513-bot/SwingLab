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

There is a second, worse failure mode in the same lookup, and the tests below
now cover both. `images['<name>']` does not return nil when the entry is
absent — it returns a **truthy** drop. So `{% if %}`-guarding a lookup does
not protect anything: the guard passes, the `image_url` behind it throws
"invalid url input", and Liquid renders that error as literal text into
whichever attribute it sat in. The asset_url fallback written beside it is
dead code. In `layout/theme.liquid` that shipped a broken favicon, image-less
social cards, and — because the error landed unquoted inside JSON-LD —
structured data Google discarded outright, on every page of the store.

Hence the stricter rule for the layout: its brand marks ship *with the theme*,
so they resolve through `asset_url` unconditionally and never through a Files
lookup at all, whatever the filename.
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

# The marks layout/theme.liquid is responsible for, under both brands. These
# ship inside the theme, so none of them has any business in a Files lookup —
# see test_the_layout_never_resolves_a_brand_mark_through_images.
BRAND_MARKS = frozenset(RETIRED_IN_FILES) | {
    "og-caddieinsight.png",
    "caddieinsight-favicon.png",
    "caddieinsight-logo.png",
    "caddieinsight-logo-inverse.png",
}

LIQUID = sorted(THEME.rglob("*.liquid"))
LAYOUT = THEME / "layout" / "theme.liquid"


def lookups(text: str) -> list[str]:
    """Every filename the theme resolves through the Files-first `images[]`."""
    return re.findall(r"images\[\s*['\"]([^'\"]+)['\"]\s*\]", text)


def without_comments(text: str) -> str:
    """Liquid with `{% comment %}` blocks removed.

    The layout explains the images[] trap in prose, and that explanation is
    worth more than a regex's convenience — quoting the exact broken lookup is
    how the next person recognises it. Strip comments so the check measures
    what the theme *executes* rather than what it documents.
    """
    return re.sub(
        r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}",
        "",
        text,
        flags=re.DOTALL,
    )


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
    # The inverse lockup ships only to the app (service-worker precache);
    # the theme inverts the standard mark with a CSS filter instead.
    missing = [
        name
        for name in ("caddieinsight-logo.png",)
        if not (THEME / "assets" / name).is_file()
    ]
    assert not missing, f"{missing} are referenced but not packaged in assets/."


def test_the_organization_logo_resolves_through_the_packaged_asset():
    """The logo must come from the theme, and must be quoted as JSON.

    This test previously required an `images['caddieinsight-logo.png']` lookup
    with an asset_url fallback behind it. That fallback could never run — see
    the module docstring — and worse, the error the lookup produced landed
    *unquoted* in the logo value and invalidated the whole block. Requiring
    the lookup is therefore requiring the outage, so the assertion is inverted
    here rather than merely relaxed.
    """
    text = LAYOUT.read_text(encoding="utf-8")
    assert "images['caddieinsight-logo.png']" not in text, (
        "The Organization logo must not go through images[]: a missing Files "
        "entry yields a truthy drop, image_url throws, and the error text is "
        "interpolated unquoted into the JSON — Google drops the whole block."
    )
    assert (
        "\"logo\": {{ 'caddieinsight-logo.png' | asset_url | prepend: 'https:' | json }}"
        in text
    ), (
        "The Organization logo must be an absolute theme-asset URL passed "
        "through `json`, which supplies the quoting the structured data needs."
    )


def test_the_layout_never_resolves_a_brand_mark_through_images():
    """A brand mark in `images[]` is an outage, not a stale picture.

    `images['<missing>']` returns a truthy drop rather than nil, so an
    `{% if %}` guard around it always passes and the `image_url` behind it
    throws "invalid url input" — which Liquid renders as literal text into
    whatever attribute it sat in. That shipped a broken favicon, image-less
    social cards and invalid JSON-LD to every page of the store at once.

    So the layout's rule is stricter than the Files-beats-theme rule above:
    these marks are packaged with the theme and must resolve through
    asset_url unconditionally, never through a Files lookup — current
    filename or retired one.

    Scoped to the layout deliberately. sections/report-feature.liquid still
    carries the same truthy-drop pattern for its report preview and needs the
    same treatment; widening this test is the right move once it is fixed.
    """
    live = without_comments(LAYOUT.read_text(encoding="utf-8"))
    found = sorted(set(lookups(live)) & BRAND_MARKS)
    assert not found, (
        f"layout/theme.liquid resolves {found} through images[]. A missing "
        "Files entry is truthy, so the guard passes and image_url throws — "
        "the fallback branch beside it is dead code. Use "
        "`| asset_url` unconditionally; the file ships with the theme."
    )


def test_every_asset_the_layout_references_is_packaged():
    """asset_url has no fallback behind it any more, so a typo is a 404.

    Removing the (dead) images[] branches removed the illusion of a second
    chance: whatever the layout names now has to exist in assets/ or the mark
    simply does not render.
    """
    text = LAYOUT.read_text(encoding="utf-8")
    referenced = sorted(set(re.findall(r"['\"]([^'\"]+)['\"]\s*\|\s*asset_url", text)))
    assert referenced, "Expected the layout to reference packaged assets."
    missing = [name for name in referenced if not (THEME / "assets" / name).is_file()]
    assert not missing, (
        f"layout/theme.liquid references {missing} via asset_url, but they are "
        "not in storefront-theme/assets/ — they will 404 on the live store."
    )
