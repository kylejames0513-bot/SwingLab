"""Premium Shopify storefront trust, proof, and release contracts."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "storefront-theme"
ASSET_ROOT = ROOT / "store-assets" / "out"
DESKTOP_HERO_NAME = "caddieinsight-premium-range-hero-0852e38d.png"
MOBILE_HERO_NAME = "caddieinsight-premium-range-hero-mobile-2e4ee946.png"
INDEX = json.loads((THEME / "templates" / "index.json").read_text(encoding="utf-8"))
LOCALE = json.loads(
    (THEME / "locales" / "en.default.json").read_text(encoding="utf-8")
)


def source(relative: str) -> str:
    return (THEME / relative).read_text(encoding="utf-8")


def png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", header[16:24])


def test_storefront_leads_with_the_evidence_loop_and_real_sample():
    hero = INDEX["sections"]["hero"]["settings"]

    assert hero["heading"] == "Practice the move that matters."
    assert hero["primary_label"] == "Analyze a swing free"
    assert hero["primary_url"] == "https://app.caddieinsight.com/signup"
    assert hero["secondary_label"] == "Explore the sample report"
    assert hero["secondary_url"] == "https://app.caddieinsight.com/sample-report/"
    assert [hero[f"chip{number}"] for number in range(1, 4)] == [
        "1 FULL REPORT / MONTH",
        "NO CREDIT CARD",
        "CLUB CONTEXT SAVED",
    ]

    stats = INDEX["sections"]["stats"]
    assert [stats["blocks"][key]["settings"]["value"] for key in stats["block_order"]] == [
        "Club saved",
        "View locked",
        "One priority",
        "Proof loop",
    ]
    assert INDEX["order"][:6] == [
        "hero",
        "stats",
        "how_it_works",
        "report",
        "plans",
        "comparison",
    ]
    assert INDEX["order"].index("comparison") < INDEX["order"].index("gear")


def test_premium_section_hierarchy_prioritizes_method_report_and_pro():
    how = INDEX["sections"]["how_it_works"]
    report = INDEX["sections"]["report"]
    plans = INDEX["sections"]["plans"]

    assert how["settings"]["anchor"] == "how"
    assert report["settings"]["anchor"] == "report"
    assert plans["settings"]["anchor"] == "plans"
    assert plans["block_order"] == ["pro", "free"]
    assert plans["blocks"]["pro"]["settings"]["featured"] is True
    assert plans["blocks"]["free"]["settings"]["featured"] is False
    assert '<div class="sl-report__inner sl-wrap">' in source(
        "sections/report-feature.liquid"
    )
    assert '<p class="sl-how__eyebrow">' in source("sections/how-it-works.liquid")
    assert "sl-card--featured" in source("sections/product-grid.liquid")


def test_storefront_makes_club_and_capture_context_part_of_the_product():
    how = INDEX["sections"]["how_it_works"]
    titles = [how["blocks"][key]["settings"]["title"] for key in how["block_order"]]

    assert titles == [
        "Choose the club",
        "Film a repeatable view",
        "Work one plan",
        "Re-film to prove it",
    ]
    boundary = how["settings"]["footer_note"]
    for unsupported in (
        "club path",
        "face angle",
        "launch",
        "spin",
        "carry",
        "strike",
        "ball flight",
    ):
        assert unsupported in boundary.lower()

    faq_blocks = INDEX["sections"]["faq"]["blocks"]
    club_answer = faq_blocks["q_club"]["settings"]["answer"]
    assert "A wedge is stored and used for matched comparisons" in club_answer
    assert "coaching remains neutral until shot intent and lie are captured" in club_answer
    assert "delete-history flow" in faq_blocks["q_history"]["settings"]["answer"]

    refilm = how["blocks"]["s4"]["settings"]["body"]
    assert "saved club, handedness, and selected camera angle" in refilm
    assert "height, distance, and framing remain part of your setup" in refilm

    trim_answer = faq_blocks["q_trim"]["settings"]["answer"]
    assert "listens for audible strikes and separates detected swings" in trim_answer
    assert "manual strike time" in trim_answer
    assert "finds every swing automatically" not in trim_answer.lower()


def test_sample_proof_is_disclosed_and_avoids_fabricated_metrics():
    report = INDEX["sections"]["report"]["settings"]
    report_source = source("sections/report-feature.liquid")
    report_locale = LOCALE["homepage"]["report"]

    assert report["sample_url"] == "https://app.caddieinsight.com/sample-report/"
    assert "synthetic measurements" in report["body"].lower()
    assert report_locale["file_label"] == "Illustrated preview"
    assert report_locale["disclosure"] == (
        "Illustrated preview of the linked synthetic sample report. The linked full "
        "report was generated from synthetic measurements through the real report "
        "engine; this preview is not engine output, a customer result, or a testimonial."
    )
    assert "ILLUSTRATED PREVIEW" in report["caption"]
    assert "LINKED REAL-ENGINE SAMPLE" in report["caption"]
    assert INDEX["sections"]["report"]["blocks"]["p1"]["settings"]["text"].startswith(
        "The linked full report"
    )
    assert '<dl class="sl-report__proof">' in report_source
    assert "homepage.report.disclosure" in report_source
    assert "homepage.report.image_alt" in report_source
    assert "<table" not in report_source
    assert "2.6 : 1" not in report_source
    assert "0.42 SW" not in report_source
    file_css = report_source.split(".sl-report__file {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere" in file_css
    assert "white-space: nowrap" not in file_css


def test_product_behavior_cards_are_not_presented_as_quotes_or_testimonials():
    coach = INDEX["sections"]["coach_notes"]
    coach_source = source("sections/coach-notes.liquid")

    assert coach["settings"]["heading"] == "A coach that shows its work"
    assert coach["settings"]["footnote"] == (
        "PRODUCT BEHAVIOR — NOT A CUSTOMER TESTIMONIAL"
    )
    assert "<blockquote" not in coach_source
    assert '<article class="sl-coach__card"' in coach_source
    assert '<h3 class="sl-coach__label">' in coach_source


def test_storefront_cards_stay_balanced_across_responsive_layouts():
    plans = INDEX["sections"]["plans"]
    plan_words = [
        len(plans["blocks"][key]["settings"]["description"].split())
        for key in plans["block_order"]
    ]
    assert max(plan_words) - min(plan_words) <= 3

    how = INDEX["sections"]["how_it_works"]
    how_words = [
        len(how["blocks"][key]["settings"]["body"].split())
        for key in how["block_order"]
    ]
    assert max(how_words) - min(how_words) <= 3

    coach = INDEX["sections"]["coach_notes"]
    coach_words = [
        len(coach["blocks"][key]["settings"]["quote"].split())
        for key in coach["block_order"]
    ]
    assert max(coach_words) - min(coach_words) <= 2

    plans_source = source("sections/product-grid.liquid")
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in plans_source
    assert "aspect-ratio: 20 / 13" in plans_source
    assert "height: 100%" in plans_source

    how_source = source("sections/how-it-works.liquid")
    assert "@media (min-width: 640px)" in how_source
    assert "@media (min-width: 1100px)" in how_source
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in how_source

    coach_source = source("sections/coach-notes.liquid")
    assert "@media (min-width: 640px)" in coach_source
    assert "@media (min-width: 1000px)" in coach_source


def test_shared_store_cards_buttons_and_purchase_rail_use_one_geometry():
    base = source("assets/base.css")
    assert "--sl-radius-sm: 8px" in base
    assert "--sl-radius-lg: 22px" in base
    assert "--sl-radius-xl: 32px" in base
    assert ".sl-btn {\n  min-height: 46px" in base
    assert "border-radius: 999px" in base.split(".sl-btn {", 1)[1].split("}", 1)[0]
    assert "@media (min-width: 480px)" in base
    assert "@media (min-width: 900px)" in base
    assert "@media (min-width: 1200px)" in base
    assert "min-block-size: 2.8em" in base
    assert ".sl-pcard-price { margin: auto 0 0" in base

    product = source("sections/main-product.liquid")
    assert ".sl-product--pro .sl-product-form" in product
    assert "max-width: 520px" in product

    comparison = source("sections/comparison.liquid")
    mobile_comparison = comparison.split("@media (max-width: 749px)", 1)[1]
    assert "min-width: 0" in mobile_comparison
    assert "table-layout: fixed" in mobile_comparison
    assert "overflow-wrap: anywhere" in mobile_comparison


def test_caddie_window_hero_is_responsive_fast_and_disclosed():
    hero_source = source("sections/hero.liquid")
    hero_locale = LOCALE["homepage"]["hero"]

    assert '<section id="home-hero" class="sl-hero" aria-labelledby=' in hero_source
    assert hero_source.count("<h1") == 1
    assert '<figure class="sl-hero__backdrop" aria-describedby=' in hero_source
    assert '<figcaption id="{{ disclosure_id }}" class="sl-hero__disclosure">' in hero_source
    assert "<picture>" in hero_source
    assert 'media="(max-width: 749px)"' in hero_source
    assert "hero_mobile_image | image_url: width: 1122" in hero_source
    assert 'widths: \'750, 1100, 1400, 1672\'' in hero_source
    assert "sizes: '100vw'" in hero_source
    assert "loading: 'eager'" in hero_source
    assert "preload: true" not in hero_source
    assert "fetchpriority: 'high'" in hero_source
    assert "homepage.hero.art_disclosure" in hero_source
    assert "homepage.hero.signal_disclosure" in hero_source
    assert "section.settings.image.alt | default: hero_image_label" in hero_source
    assert "alt: hero_image_alt" in hero_source
    assert "alt: section.settings.heading" not in hero_source
    assert (
        "section.settings.primary_label != blank and "
        "section.settings.primary_url != blank"
    ) in hero_source
    assert (
        "section.settings.secondary_label != blank and "
        "section.settings.secondary_url != blank"
    ) in hero_source
    assert "default: '#'" not in hero_source
    assert hero_locale["image_label"] == (
        "AI-generated golfer filming a driver swing at a dawn driving range"
    )
    assert hero_locale["art_disclosure"] == (
        "AI-generated range scene — not a customer, testimonial, or analyzed swing. "
        "Product signals are a clearly labeled synthetic example."
    )
    assert "synthetic" in hero_locale["signal_label"].lower()
    assert "synthetic" in hero_locale["signal_status"].lower()
    assert "synthetic" in hero_locale["signal_disclosure"].lower()
    assert '<aside class="sl-hero__signal' in hero_source


def test_storefront_copy_stays_inside_the_measurement_boundary():
    all_theme_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in THEME.rglob("*")
        if path.is_file()
    ).lower()

    for forbidden in (
        "tour-grade",
        "tour-average",
        "no matter where you set the camera",
        "guaranteed improvement",
    ):
        assert forbidden not in all_theme_text


def test_theme_uses_shopify_fonts_and_store_aware_routes():
    layout = source("layout/theme.liquid")
    settings = json.loads(source("config/settings_schema.json"))
    route_sources = "\n".join(
        source(path)
        for path in (
            "sections/main-404.liquid",
            "sections/main-cart.liquid",
            "sections/main-search.liquid",
        )
    )

    typography = next(group for group in settings if group.get("name") == "Typography")
    font_setting = next(
        setting for setting in typography["settings"] if setting.get("id") == "type_body_font"
    )
    assert font_setting["type"] == "font_picker"
    assert "font_face" in layout and "font_modify" in layout
    for weight in ("500", "600", "700", "800", "900"):
        assert f"font_modify: 'weight', '{weight}'" in layout
    assert layout.count("font_face: font_display: 'swap'") == 6

    loaded_weights = {400, 500, 600, 700, 800, 900}
    used_weights = {
        int(weight)
        for path in THEME.rglob("*")
        if path.suffix in {".css", ".liquid"}
        for weight in re.findall(
            r"font-weight\s*:\s*(\d{3})(?!\d)",
            path.read_text(encoding="utf-8"),
        )
    }
    assert used_weights <= loaded_weights
    assert "fonts.googleapis.com" not in layout
    assert "fonts.gstatic.com" not in layout
    assert 'href="/collections' not in route_sources
    assert route_sources.count("routes.collections_url") == 4

    how_source = source("sections/how-it-works.liquid")
    assert (
        "section.settings.cta_label != blank and section.settings.cta_url != blank"
        in how_source
    )
    assert "default: '#'" not in how_source


def test_generated_storefront_art_avoids_unsupported_measurement_claims():
    campaign_assets = (ROOT / "store-assets" / "campaign_assets.py").read_text(
        encoding="utf-8"
    )
    report_keyframes = campaign_assets.split("def report_keyframes():", 1)[1].split(
        "def og_card():", 1
    )[0]
    assert "club-path arc" not in report_keyframes
    assert "swing_arc(" not in report_keyframes

    pro_assets = (ROOT / "store-assets" / "pro_home_assets.py").read_text(
        encoding="utf-8"
    )
    hero_art = pro_assets.split("def hero_image():", 1)[1].split(
        "def report_band():", 1
    )[0]
    for invented_metric in ("TEMPO 3.0", "SWAY 0.18 SW", "SLIDE 0.10 SW"):
        assert invented_metric not in hero_art
    assert "CHOOSE CLUB · FILM THE VIEW · WORK ONE PLAN · RE-FILM" in hero_art


def test_storefront_uses_immutable_release_artwork_references():
    hero = INDEX["sections"]["hero"]["settings"]
    hero_source = source("sections/hero.liquid")
    report_source = source("sections/report-feature.liquid")
    readme = source("README.md")

    assert hero["image"] == f"shopify://shop_images/{DESKTOP_HERO_NAME}"
    assert hero["mobile_image"] == f"shopify://shop_images/{MOBILE_HERO_NAME}"
    assert f"images['{DESKTOP_HERO_NAME}']" in hero_source
    assert f"images['{MOBILE_HERO_NAME}']" in hero_source
    assert "images['caddieinsight-report-preview-136534a.png']" in report_source
    assert "images['report-keyframes.png']" not in report_source
    assert "immutable, release-specific Shopify File names" in readme

    expected_assets = {
        DESKTOP_HERO_NAME: (
            (1672, 941),
            "0852e38d5184c428d427519b5f1944d7986114a1aef7ebb836d80cfe1e08ba0e",
        ),
        MOBILE_HERO_NAME: (
            (1122, 1402),
            "2e4ee946358c68e175dd46247c75760d7cc07aee6edd75901e9a589a2259e36e",
        ),
    }
    for filename, (dimensions, expected_sha256) in expected_assets.items():
        asset = ASSET_ROOT / filename
        assert asset.is_file()
        assert png_dimensions(asset) == dimensions
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == expected_sha256

    prompt_record = (
        ROOT
        / "store-assets"
        / "prompts"
        / "caddieinsight-premium-range-hero-v2.md"
    ).read_text(encoding="utf-8")
    assert DESKTOP_HERO_NAME in prompt_record
    assert MOBILE_HERO_NAME in prompt_record
    assert "not customer photos" in prompt_record
    assert "testimonials" in prompt_record


def test_theme_check_is_pinned_and_release_docs_have_no_stale_theme_ids():
    workflow = (ROOT / ".github" / "workflows" / "theme-check.yml").read_text(
        encoding="utf-8"
    )
    readme = source("README.md")

    assert "Shopify/theme-check-action@58fd69afdfc30110f997ba9e212b302671e00d3b" in workflow
    assert "--fail-level warning" in workflow
    assert "version: 3.58.2" in workflow
    assert "source PR is not a Shopify preview" in readme
    assert "duplicate\nunpublished theme" in readme
    assert not re.search(r"OnlineStoreTheme/\d+", readme)
