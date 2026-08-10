"""The theme zip is the storefront deploy, so its shape is a contract.

Merging to `main` changes nothing on caddieinsight.com — the theme is
uploaded by hand (CLAUDE.md). That makes this archive the artifact that
actually ships, and the two ways it goes wrong are structural: a wrapper
directory at the root, or a stray top-level file. Neither is visible by
looking at the zip; both are a rejected upload.
"""

from __future__ import annotations

import zipfile

import pytest

from scripts.package_theme import (
    EXCLUDED_ASSETS,
    THEME,
    THEME_DIRS,
    _verify_unreferenced,
    build,
)


@pytest.fixture(scope="module")
def archive():
    """Build once for the module — the artifact under test is the real one.

    It lands in `dist/`, which is a build output and gitignored, so building
    it here has no more consequence than running the script by hand.
    """
    return build()


@pytest.fixture(scope="module")
def names(archive):
    with zipfile.ZipFile(archive) as bundle:
        return bundle.namelist()


def test_archive_root_holds_the_theme_directories_and_nothing_else(names):
    assert {name.split("/", 1)[0] for name in names} <= set(THEME_DIRS)


def test_archive_has_no_wrapper_directory(names):
    """The single most common hand-built-zip rejection.

    Zipping the folder rather than its contents puts `storefront-theme/` at
    the root, and Shopify's error does not say which mistake was made.
    """
    assert "storefront-theme" not in {name.split("/", 1)[0] for name in names}
    assert "layout/theme.liquid" in names


def test_archive_has_no_stray_top_level_files(names):
    """`storefront-theme/README.md` is source documentation, not theme code."""
    assert [name for name in names if "/" not in name] == []
    assert not any(name.endswith("README.md") for name in names)


def test_the_three_brand_marks_ship_with_the_theme(names):
    """This is what makes the upload sufficient on its own.

    The live theme resolves these through a Shopify Files lookup, and none of
    the three exist in Files — a missing Files lookup returns a truthy drop,
    so the guard passes, `image_url` throws, and every page ships a literal
    "Liquid error (...)" into the favicon href, both share-image tags and the
    Organization JSON-LD. Resolving them through `asset_url` only works if
    the files are actually in the archive.
    """
    for mark in (
        "assets/caddieinsight-favicon.png",
        "assets/caddieinsight-logo.png",
        "assets/og-caddieinsight.png",
    ):
        assert mark in names


def test_excluded_source_art_is_not_uploaded(names):
    shipped = {name.rsplit("/", 1)[-1] for name in names}
    assert shipped.isdisjoint(EXCLUDED_ASSETS)


def test_excluded_assets_are_genuinely_unreferenced():
    """The guard that stops an exclusion from becoming a 404.

    If somebody wires one of these into a section later, the build fails
    instead of quietly shipping a theme that cannot resolve it.
    """
    assert _verify_unreferenced(THEME) == []


def test_the_webp_ladder_the_plans_band_serves_is_present(names):
    """The PNGs are excluded; their derivatives are what the band renders."""
    for plan in ("free", "pro", "founders"):
        for rung in (480, 960, 1536):
            assert f"assets/caddieinsight-{plan}-card-v2-{rung}.webp" in names


def test_archive_stays_small_enough_to_upload_comfortably(archive):
    """Shopify's ceiling is 50 MB; the point here is the direction of travel.

    Dropping the unreferenced source photography took the archive from about
    seven megabytes to roughly one, and a theme upload is a thing done under
    time pressure during a release.
    """
    assert archive.stat().st_size < 5 * 1024 * 1024


def test_upload_notes_ship_beside_the_archive(archive):
    notes = archive.parent / "UPLOAD.md"
    assert notes.is_file()
    body = notes.read_text(encoding="utf-8")
    # The preview checks that decide whether the release is good.
    assert "Liquid error" in body
    assert "unpublished" in body
    assert "swinglab-pro" in body
