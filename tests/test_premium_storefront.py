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


def webp_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    chunk = data[12:16]
    if chunk == b"VP8X":
        return (
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )
    if chunk == b"VP8 ":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    assert chunk == b"VP8L"
    bits = int.from_bytes(data[21:25], "little")
    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1


def test_storefront_leads_with_the_evidence_loop_and_real_sample():
    hero = INDEX["sections"]["hero"]["settings"]

    assert hero["heading"] == "Bring one clear move to the range."
    assert hero["primary_label"] == "Analyze a swing free"
    assert hero["primary_url"] == "https://app.caddieinsight.com/signup"
    assert hero["secondary_label"] == "Explore the sample report"
    assert hero["secondary_url"] == "https://app.caddieinsight.com/sample-report/"
    assert [hero[f"chip{number}"] for number in range(1, 4)] == [
        "1 REPORT / MONTH",
        "NO CARD",
        "CLUB SAVED",
    ]

    stats = INDEX["sections"]["stats"]
    assert [stats["blocks"][key]["settings"]["value"] for key in stats["block_order"]] == [
        "Club saved",
        "View locked",
        "One priority",
        "Proof loop",
    ]
    assert INDEX["order"][:8] == [
        "hero",
        "how_it_works",
        "report",
        "plans",
        "stats",
        "gear",
        "comparison",
        "coach_notes",
    ]
    assert INDEX["sections"]["email"]["disabled"] is False


def test_membership_card_art_candidates_are_crop_safe_campaign_assets():
    candidates = (
        "caddieinsight-pro-card-v2.png",
        "caddieinsight-free-card-v2.png",
        "caddieinsight-founders-card-v2.png",
    )

    for filename in candidates:
        path = ASSET_ROOT / filename
        assert path.exists(), f"Missing membership card art: {filename}"
        assert png_dimensions(path) == (1536, 1024)


def test_membership_card_media_is_photoreal_without_overlay_stickers():
    plans = INDEX["sections"]["plans"]["blocks"]
    plans_band = source("sections/plans-band.liquid")

    assert plans["monthly"]["settings"]["name"]
    assert plans["season"]["settings"]["name"]
    assert plans["founders"]["settings"]["name"] == "Founders Pass"
    assert plans["free"]["settings"]["name"] == "CaddieInsight Free"
    # Plan identity lives in the card body — no detached labels on the photo.
    assert 'class="sl-plans__media-label"' not in plans_band
    assert 'class="sl-plans__name"' in plans_band
    # The band binds WebP rungs, not the source PNGs. Asserting the .png name
    # appears in the section would pass on the explanatory comments alone —
    # a tautology of exactly the kind claude-build-brief.md warns about, green
    # with the binding it exists to protect deleted.
    requested = plans_band_requested_assets()
    for stem in (
        "caddieinsight-pro-card-v2",
        "caddieinsight-founders-card-v2",
        "caddieinsight-free-card-v2",
    ):
        for width in (480, 960, 1536):
            rung = f"{stem}-{width}.webp"
            assert rung in requested, f"Card art rung not bound: {rung}"
            assert (THEME / "assets" / rung).is_file(), f"Rung not packaged: {rung}"

        # The source photograph stays in assets/ as the regeneration record.
        source_png = THEME / "assets" / f"{stem}.png"
        assert source_png.is_file()
        assert png_dimensions(source_png) == (1536, 1024)


def test_premium_section_hierarchy_prioritizes_method_report_and_pro():
    how = INDEX["sections"]["how_it_works"]
    report = INDEX["sections"]["report"]
    plans = INDEX["sections"]["plans"]

    assert how["settings"]["anchor"] == "how"
    assert report["settings"]["anchor"] == "report"
    assert plans["settings"]["anchor"] == "plans"
    assert plans["block_order"] == ["monthly", "season", "founders", "free"]
    assert plans["blocks"]["season"]["settings"]["featured"] is True
    assert plans["blocks"]["monthly"]["settings"]["featured"] is False
    assert plans["blocks"]["founders"]["settings"]["featured"] is False
    assert plans["blocks"]["free"]["type"] == "free_band"
    assert '<div class="sl-report__inner sl-wrap">' in source(
        "sections/report-feature.liquid"
    )
    assert '<p class="sl-how__eyebrow">' in source("sections/how-it-works.liquid")
    assert "sl-plans__card--featured" in source("sections/plans-band.liquid")


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
    assert "sample report" in report["body"].lower()
    assert report_locale["file_label"] == "Sample report"
    assert report_locale["disclosure"] == (
        "Sample report built with demonstration data. Not a customer result or testimonial."
    )
    assert report["caption"] == "SAMPLE REPORT · DEMONSTRATION DATA"
    assert INDEX["sections"]["report"]["blocks"]["p1"]["settings"]["text"] == (
        "See the selected club and capture context"
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

    assert coach["settings"]["heading"] == "Coaching that shows its work"
    assert coach["settings"]["footnote"] == (
        "PRODUCT FEATURES SHOWN · NOT A CUSTOMER TESTIMONIAL"
    )
    assert "<blockquote" not in coach_source
    assert '<article class="sl-coach__card"' in coach_source
    assert '<h3 class="sl-coach__label">' in coach_source


def test_storefront_cards_stay_balanced_across_responsive_layouts():
    plans = INDEX["sections"]["plans"]
    paid_plans = [
        key
        for key in plans["block_order"]
        if plans["blocks"][key]["type"] == "plan"
    ]
    assert len(paid_plans) == 3
    plan_words = [
        len(plans["blocks"][key]["settings"]["description"].split())
        for key in paid_plans
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

    plans_source = source("sections/plans-band.liquid")
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in plans_source
    assert "grid-template-columns: minmax(0, 1fr)" in plans_source
    assert "aspect-ratio: 3 / 2" in plans_source
    assert "height: 100%" in plans_source

    # Both sections sit on the four-stop system now (560/750/1000/1280) —
    # tests/test_storefront_design_system.py polices the full census; these
    # pins hold the two grids' specific stops.
    how_source = source("sections/how-it-works.liquid")
    assert "@media (min-width: 750px)" in how_source
    assert "@media (min-width: 1280px)" in how_source
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in how_source

    coach_source = source("sections/coach-notes.liquid")
    assert "@media (min-width: 560px)" in coach_source
    assert "@media (min-width: 1000px)" in coach_source


def test_shared_store_cards_buttons_and_purchase_rail_use_one_geometry():
    base = source("assets/base.css")
    assert "--sl-radius-sm: 6px" in base
    assert "--sl-radius-lg: 16px" in base
    assert "--sl-radius-xl: 22px" in base
    assert "--sl-radius-control: 12px" in base
    assert ".sl-btn {\n  min-height: 46px" in base
    assert "border-radius: var(--sl-radius-control)" in base.split(".sl-btn {", 1)[1].split("}", 1)[0]
    assert "@media (min-width: 560px)" in base
    assert "@media (min-width: 1000px)" in base
    assert "@media (min-width: 1280px)" in base
    assert "min-block-size: 2.8em" in base
    assert ".sl-pcard-price { margin: auto 0 0" in base

    product = source("sections/main-product.liquid")
    assert ".sl-product--pro .sl-product-form" in product
    assert "max-width: 520px" in product

    comparison = source("sections/comparison.liquid")
    mobile_comparison = comparison.split("@media (max-width: 749px)", 1)[1]
    assert "min-width: 0" in mobile_comparison
    assert "display: grid" in mobile_comparison
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in mobile_comparison
    assert "grid-column: 1 / -1" in mobile_comparison
    assert "padding: var(--sl-dense-inset)" in mobile_comparison
    assert "overflow-wrap: anywhere" in mobile_comparison
    assert "table-layout: fixed" not in mobile_comparison
    assert "padding: 8px" not in mobile_comparison


def test_caddie_window_hero_is_responsive_fast_and_mobile_focused():
    hero_source = source("sections/hero.liquid")
    hero_locale = LOCALE["homepage"]["hero"]
    stats_source = source("sections/stats-band.liquid")

    assert '<section id="home-hero" class="sl-hero" aria-labelledby=' in hero_source
    assert hero_source.count("<h1") == 1
    assert '<figure class="sl-hero__backdrop">' in hero_source
    assert "sl-hero__disclosure" not in hero_source
    assert "sl-hero__capture" not in hero_source
    assert "<picture>" in hero_source
    assert 'media="(max-width: 749px)"' in hero_source
    assert "hero_mobile_image | image_url: width: 1122" in hero_source
    assert 'widths: \'750, 1100, 1400, 1672\'' in hero_source
    assert "sizes: '100vw'" in hero_source
    assert "loading: 'eager'" in hero_source
    assert "preload: true" not in hero_source
    assert "fetchpriority: 'high'" in hero_source
    assert "homepage.hero.signal_disclosure" in hero_source
    assert "assign hero_image_alt = hero_image_label" in hero_source
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
        "Golfer filming a driver swing at a dawn driving range"
    )
    assert hero_locale["signal_label"] == "CaddieInsight example analysis"
    assert hero_locale["signal_status"] == "Example session"
    assert "demonstration data" in hero_locale["signal_disclosure"].lower()
    assert '<aside class="sl-hero__signal' in hero_source
    assert 'class="sl-hero__signal-band' in hero_source
    assert "grid-template-columns: minmax(0, 1fr);" in hero_source
    assert "grid-template-columns: minmax(0, 1.15fr) minmax(260px, 0.5fr)" not in hero_source
    mobile_hero = hero_source.split("@media (max-width: 749px)", 1)[1]
    assert "min-height: 720px" not in mobile_hero
    assert "min-height: 980px" not in mobile_hero
    assert "min-height: 1020px" not in mobile_hero
    assert ".sl-hero__signal { display: none; }" in mobile_hero
    assert ".sl-hero__signal-band { display: none; }" in mobile_hero
    assert ".sl-hero__fine,\n  .sl-hero__signal { display: none; }" not in mobile_hero
    assert ".sl-hero__fine { display: none; }" not in mobile_hero
    mobile_stats = stats_source.split("@media (max-width: 749px)", 1)[1]
    assert 'class="sl-stats__grid sl-reveal"' in stats_source
    assert 'tabindex="0"' not in stats_source
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in mobile_stats
    assert "overflow: visible" in mobile_stats
    assert "min-height: 136px" in mobile_stats
    assert "padding: var(--sl-card-inset)" in mobile_stats
    assert "grid-auto-flow: column" not in mobile_stats
    assert "grid-auto-columns:" not in mobile_stats


def test_homepage_bordered_surfaces_preserve_reading_hierarchy():
    base = source("assets/base.css")
    assert "--sl-pad-x: clamp(24px, 5vw, 64px)" in base
    assert "--sl-card-inset: clamp(24px, 3vw, 36px)" in base
    assert "--sl-dense-inset: clamp(14px, 1.5vw, 20px)" in base

    left_flow_surfaces = {
        "sections/how-it-works.liquid": ".sl-step",
        "sections/plans-band.liquid": ".sl-plans__card-body",
        "sections/gear-showcase.liquid": ".sl-gear__body",
        "sections/coach-notes.liquid": ".sl-coach__card",
    }
    for relative, selector in left_flow_surfaces.items():
        section_source = source(relative)
        rule = section_source.split(f"{selector} {{", 1)[1].split("}", 1)[0]
        assert "align-items: stretch" in rule
        assert "justify-content: flex-start" in rule
        assert "text-align: left" in rule
        if relative == "sections/how-it-works.liquid":
            assert "padding: clamp(24px, 2.5vw, 32px)" in rule
        else:
            assert "padding: var(--sl-card-inset)" in rule

    hero = source("sections/hero.liquid")
    hero_title = hero.split(".sl-hero__title {", 1)[1].split("}", 1)[0]
    hero_body = hero.split(".sl-hero__body {", 1)[1].split("}", 1)[0]
    hero_proof = hero.split(".sl-hero__proof {", 1)[1].split("}", 1)[0]
    assert "text-align: left" in hero_title
    assert "text-align: left" in hero_body
    assert "justify-content: flex-start" in hero_proof
    assert "background: transparent" in hero_proof
    assert 'class="sl-hero__brand"' in hero

    stats = source("sections/stats-band.liquid")
    stats_cell = stats.split(".sl-stats__cell {", 1)[1].split("}", 1)[0]
    assert "align-items: center" in stats_cell
    assert "text-align: center" in stats_cell
    assert "margin-top: -54px" not in stats
    assert "padding-top: clamp(36px, 5vw, 64px)" in stats

    report = source("sections/report-feature.liquid")
    assert ".sl-report__card {" in report
    assert "padding: var(--sl-card-inset)" in report
    assert "margin: 20px 0 0" in report
    assert "text-align: left" in report.split(".sl-report__body {", 1)[1].split("}", 1)[0]

    comparison = source("sections/comparison.liquid")
    assert "padding: var(--sl-dense-inset)" in comparison
    assert "vertical-align: middle" in comparison
    assert "margin: 30px auto 0" in comparison
    feature_column = comparison.split(".sl-compare__table tbody th {", 1)[1].split("}", 1)[0]
    values = comparison.split(".sl-compare__table tbody td {", 1)[1].split("}", 1)[0]
    assert "text-align: left" in feature_column
    assert "text-align: center" in values

    faq = source("sections/faq.liquid")
    assert "padding: var(--sl-dense-inset) var(--sl-card-inset)" in faq
    assert "padding: 0 var(--sl-card-inset) var(--sl-card-inset) calc(var(--sl-card-inset) + 48px)" in faq
    assert "padding: 18px 4px" not in faq
    assert "text-align: left" in faq.split(".sl-faq__q {", 1)[1].split("}", 1)[0]
    assert "text-align: left" in faq.split(".sl-faq__a {", 1)[1].split("}", 1)[0]


def test_storefront_copy_stays_inside_the_measurement_boundary():
    binary_suffixes = {".png", ".webp", ".jpg", ".jpeg", ".gif", ".woff", ".woff2"}
    all_theme_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in THEME.rglob("*")
        if path.is_file() and path.suffix.lower() not in binary_suffixes
    ).lower()

    for forbidden in (
        "ai-generated",
        "artificial intelligence",
        "synthetic",
        "tour-grade",
        "tour-average",
        "no matter where you set the camera",
        "guaranteed improvement",
    ):
        assert forbidden not in all_theme_text


def test_theme_uses_tour_caddie_type_and_store_aware_routes():
    layout = source("layout/theme.liquid")
    settings = json.loads(source("config/settings_schema.json"))
    base_css = source("assets/base.css")
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
    assert "Legacy font picker" in font_setting["label"]
    assert "Archivo carries display" in typography["settings"][-1]["content"]
    assert "IBM Plex Mono" in typography["settings"][-1]["content"]
    # The faces are self-hosted theme assets now — no third-party sheet.
    # tests/test_storefront_design_system.py holds the files + preloads;
    # these pins hold the declarations.
    assert 'font-family: "Archivo";' in layout
    assert 'font-family: "IBM Plex Mono";' in layout
    assert "fonts.googleapis.com" not in layout
    assert "fonts.gstatic.com" not in layout
    assert '"Archivo"' in base_css
    assert '"IBM Plex Mono"' in base_css
    assert "--sl-font-display" in base_css
    assert "font_face" not in layout
    assert "font_modify" not in layout

    # The variable Archivo file carries 400-800; Plex Mono ships 400 + 500.
    loaded_weights = {400, 500, 600, 700, 800}
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
    assert 'href="/collections' not in route_sources
    assert route_sources.count("routes.collections_url") == 4

    how_source = source("sections/how-it-works.liquid")
    assert "section.settings.cta_label" not in how_source
    assert '"id": "cta_label"' not in how_source
    assert '"id": "cta_url"' not in how_source
    assert "default: '#'" not in how_source


def test_storefront_account_and_pro_actions_follow_the_app_session():
    how = source("sections/how-it-works.liquid")
    hero = source("sections/hero.liquid")
    comparison = source("sections/comparison.liquid")
    plans = source("sections/plans-band.liquid")
    banner = source("sections/cta-banner.liquid")
    product = source("sections/main-product.liquid")
    product_card = source("snippets/product-card.liquid")
    footer = source("sections/footer.liquid")
    faq = INDEX["sections"]["faq"]["blocks"]["q_pro_unlock"]["settings"]["answer"]

    assert "Create a free account" not in how
    assert "cta_label" not in INDEX["sections"]["how_it_works"]["settings"]
    assert "data-app-primary-cta" in hero
    assert "data-app-primary-cta" in banner
    assert "data-app-primary-cta" in comparison
    assert "data-app-pro-sales-link" in banner
    assert "data-app-pro-sales-link" in comparison
    assert "data-app-upgrade-section" in comparison
    assert "data-app-upgrade-section" in plans
    assert INDEX["sections"]["plans"]["settings"]["membership_section"] is True
    assert INDEX["sections"]["plans"]["blocks"]["free"]["settings"][
        "member_visibility"
    ] == "signed_out"
    assert "data-app-pro-member-only hidden" in product
    assert "product.handle == 'swinglab-pro'" in product_card
    assert footer.count("data-app-pro-sales-link") >= 2
    assert "create your account" not in faq.lower()
    assert INDEX["sections"]["cta"]["settings"]["primary_label"] == (
        "Analyze a swing free"
    )
    assert '<button type="submit" class="sl-btn sl-btn--sm sl-btn--light">Join the list</button>' in footer


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

    # The hero binds the packaged webp campaign pair by immutable asset
    # name; the image pickers stay empty so theme-owned art is what ships.
    assert "image" not in hero
    assert "mobile_image" not in hero
    assert hero_source.count("'caddieinsight-range-hero-desktop.webp'") >= 1
    assert hero_source.count("'caddieinsight-range-hero-mobile.webp'") >= 1
    # Webp theme assets must be served whole: asset_img_url has no webp
    # support and renders the no-image placeholder in production.
    assert "asset_img_url" not in hero_source
    assert hero_source.count("| asset_url") >= 2
    assert "images['caddieinsight-report-preview-136534a.png']" in report_source
    assert "images['report-keyframes.png']" not in report_source
    assert "immutable, release-specific Shopify File names" in readme

    expected_theme_webps = {
        "caddieinsight-range-hero-desktop.webp": (
            (1672, 941),
            "db5cab06d63517ddf90218e00e28a975b4b344d79277dfbc7fc4fdd2fa2e75b7",
        ),
        "caddieinsight-range-hero-mobile.webp": (
            (1122, 1402),
            "778fa0bd0a007e8b07c4ff20884e16763a9071256b5349088554a1e17b11d197",
        ),
    }
    for filename, (dimensions, expected_sha256) in expected_theme_webps.items():
        asset = THEME / "assets" / filename
        assert asset.is_file()
        assert webp_dimensions(asset) == dimensions
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == expected_sha256

    # The approved source PNG pair stays archived and hash-pinned in
    # store-assets/out as the release record behind the webp conversions.
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


def test_brand_marks_do_not_resolve_against_retired_filenames():
    """The layout resolves brand marks through asset_url, never through Files.

    This test previously asserted the opposite — that theme.liquid *did* look
    these names up in Files — on the theory that a name absent from Files
    falls through to the packaged asset. It does not. ``images['missing.png']``
    returns a truthy drop rather than nil, so the ``{% if %}`` guard passes,
    ``image_url`` then throws, and the fallback branch is unreachable. The
    live consequence was every page of caddieinsight.com rendering
    ``href="Liquid error (layout/theme line 72): invalid url input"`` into the
    favicon link, the og:image, and — unquoted — into the Organization
    JSON-LD, which made the whole block invalid JSON.

    The Files-beats-theme hazard the old docstring described is real, and it
    is why nothing here may name a retired v3 filename. The fix is not to look
    up a *different* name in Files; it is not to use Files at all.
    """
    layout = (THEME / "layout" / "theme.liquid").read_text(encoding="utf-8")

    # No brand mark, current or retired, may resolve through Files.
    for name in (
        "og-caddieinsight.png",
        "caddieinsight-favicon.png",
        "caddieinsight-logo.png",
        "og-swinglab.png",
        "swinglab-favicon.png",
        "swinglab-logo.png",
    ):
        assert f"images['{name}']" not in layout, (
            f"{name} resolves through Shopify Files — a truthy drop for a "
            "missing file makes image_url throw and renders the error text "
            "into the page."
        )

    # ...and each one the layout needs still resolves to a packaged asset.
    assert "'og-caddieinsight.png' | asset_url" in layout
    assert "'caddieinsight-favicon.png' | asset_url" in layout
    assert "'caddieinsight-logo.png' | asset_url" in layout


def test_current_brand_marks_ship_inside_the_theme():
    for name, size in (
        ("caddieinsight-favicon.png", (512, 512)),
        ("og-caddieinsight.png", (1200, 630)),
    ):
        asset = THEME / "assets" / name
        assert asset.exists(), name
        assert png_dimensions(asset) == size, name


def plans_band_requested_assets() -> list[str]:
    """Theme asset images the plans band actually asks a browser to fetch.

    Matches both filters deliberately: asset_url (how a webp must be served,
    whole) and asset_img_url (the legacy sizing filter the section used to
    use). Anything the section requests through either one is on the wire.

    A filename does not have to be piped in place. The paid cards assign the
    NAME to a variable and apply asset_url at the img tag, because
    theme-check 3.58.2 — pinned in CI at --fail-level warning — reads the src
    attribute literally and flags a bare `{{ art_md }}` as a RemoteAsset,
    having no way to know the value already went through asset_url. So a name
    counts as requested when it is either piped directly or assigned to a
    variable the section later pipes.
    """
    text = source("sections/plans-band.liquid")
    piped = set(
        re.findall(
            r"'([^']+\.(?:png|webp|jpe?g|gif))'\s*\|\s*asset(?:_img)?_url",
            text,
        )
    )
    # `assign art_md = 'name.webp'` paired with `{{ art_md | asset_url }}`.
    # A LIST, not a dict keyed by variable: the same variable is assigned
    # once per plan branch (art_sm is the pro rung and then the founders
    # rung), so keying by name silently keeps only the last branch and drops
    # the other plan's art from the result entirely.
    assigned = re.findall(
        r"assign\s+(\w+)\s*=\s*'([^']+\.(?:png|webp|jpe?g|gif))'\s*$",
        text,
        re.MULTILINE,
    )
    piped_variables = set(
        re.findall(r"\{\{\s*(\w+)\s*\|\s*asset(?:_img)?_url", text)
    )
    for variable, name in assigned:
        if variable in piped_variables:
            piped.add(name)
    return sorted(piped)


def test_plans_band_serves_its_card_photography_as_pre_encoded_webp_rungs():
    """The three plan photographs ship as a webp ladder, not as PNG.

    Theme webps have to be served whole — asset_img_url does not process
    webp and renders the no-image placeholder — so the responsive candidates
    cannot be cut on the CDN the way a Files image can. They are pre-encoded
    by store-assets/plan_card_webp.py, and every plan gets the same three
    widths so the free card cannot silently be the low-resolution one.
    """
    plans_band = source("sections/plans-band.liquid")

    requested = plans_band_requested_assets()
    for plan in ("pro", "founders", "free"):
        for width in (480, 960, 1536):
            name = f"caddieinsight-{plan}-card-v2-{width}.webp"
            assert name in requested, f"not bound: {name}"
            asset = THEME / "assets" / name
            assert asset.is_file(), f"missing theme asset: {name}"
            assert webp_dimensions(asset) == (width, round(width * 2 / 3)), name

    # The full-size PNGs stay in assets/ as the source of record behind the
    # rungs, but nothing in the section may request one.
    # Matched as a filter application, not as a bare word: the section's own
    # comment names asset_img_url to explain why it cannot be used here.
    assert not re.search(r"\|\s*asset_img_url", plans_band)
    for name in plans_band_requested_assets():
        assert name.endswith(".webp"), f"plans band still requests {name}"


def test_plan_card_art_the_shopper_downloads_stays_inside_a_byte_budget():
    """No plan-card image on the buy page may grow back into a megabyte.

    The plans band is where money changes hands, so its art is on the
    critical path — and it is exactly the surface that quietly regresses,
    because a fresh photograph dropped in as PNG looks right in the theme
    editor and costs 2 MB on the wire. 400 KB is generous for a photograph
    at 1536px of webp; the current ladder's largest rung is 163 KB.
    """
    budget = 400 * 1024
    requested = plans_band_requested_assets()
    assert requested, "the plans band requests no packaged art at all"

    for plan in ("pro", "founders", "free"):
        stem = f"caddieinsight-{plan}-card-v2"
        assert any(name.startswith(stem) for name in requested), plan

    for name in requested:
        asset = THEME / "assets" / name
        assert asset.is_file(), f"plans band binds a missing asset: {name}"
        weight = asset.stat().st_size
        assert weight <= budget, (
            f"{name} is {weight:,} B, over the {budget:,} B plan-card budget"
        )
