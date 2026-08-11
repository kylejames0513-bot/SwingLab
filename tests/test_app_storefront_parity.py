"""Contracts that keep the CaddieInsight app and storefront visually coherent."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "swinglab" / "templates"
LAYOUT = (TEMPLATES / "web_layout.html.j2").read_text(encoding="utf-8")
STOREFRONT = (ROOT / "storefront-theme" / "assets" / "base.css").read_text(
    encoding="utf-8"
)


def _token(source: str, name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*([^;]+);", source)
    assert match is not None, f"missing --{name}"
    return " ".join(match.group(1).split())


def test_shared_brand_tokens_match_the_storefront_source_of_truth():
    shared = (
        "sl-bg",
        "sl-bg-card",
        "sl-ink",
        "sl-ink-soft",
        "sl-green",
        "sl-green-btn",
        "sl-green-ink",
        "sl-orange",
        "sl-orange-soft",
        "sl-border",
        "sl-pad-x",
        "sl-radius-sm",
        "sl-radius-lg",
        "sl-radius-xl",
    )

    for name in shared:
        assert _token(LAYOUT, name) == _token(STOREFRONT, name)

    # These app values deliberately carry MORE headroom than the storefront's
    # display colors so small text and control edges retain AA contrast.
    #
    # They used to be darker, for exactly the same reason: the field was light
    # then. The rationale survived the inversion to a dark field and the
    # direction flipped with it, which is the useful thing for this test to
    # record — a future pass that re-derives these from a light-surface
    # assumption will fail here rather than silently halve the contrast.
    assert _token(LAYOUT, "sl-ink-muted") == "#8b968e"
    assert _token(LAYOUT, "sl-orange-text") == "#f5b833"
    assert _token(LAYOUT, "sl-control-border") == "#6b7a71"

    # ...and the storefront's own value is the display-copy counterpart, not a
    # drifted duplicate. Both clear AA on both surface backgrounds (6.33:1 and
    # 5.04:1 on --sl-bg); the app's is lighter because it sets small interface
    # text where the storefront sets prose. Pinning both ends stops a future
    # "unify the tokens" pass from quietly trading contrast for symmetry.
    assert _token(STOREFRONT, "sl-ink-muted") == "#78857d"

    # --sl-border is 1.33:1 on the field and is DECORATIVE ONLY. The control
    # border is the one that has to clear WCAG 1.4.11's 3:1 for non-text
    # contrast, on both surfaces. Two tokens, two jobs: collapsing them into
    # one is how interactive edges quietly stop being visible, so both ends
    # are pinned against a well-meaning simplification.
    assert _token(STOREFRONT, "sl-control-border") == "#5c6b62"
    assert _token(STOREFRONT, "sl-border") == _token(LAYOUT, "sl-border")
    assert _token(STOREFRONT, "sl-control-border") != _token(STOREFRONT, "sl-border")


def test_shared_type_scale_is_spelled_identically_on_both_surfaces():
    """The rungs both surfaces define must match as text, not just as numbers.

    They were already numerically equal while spelled differently (`.15vw`
    against `0.15vw`). That is the kind of difference a comparison gets
    "fixed" to ignore, and a normalising comparison eventually normalises away
    a real divergence too — so the sources are spelled the same instead.
    """
    for rung in ("xs", "sm", "base", "lg", "xl"):
        name = f"sl-text-{rung}"
        assert _token(LAYOUT, name) == _token(STOREFRONT, name), name

    # The full eight-rung scale ships on both surfaces now. The app used to
    # stop at xl (the larger rungs were "dead custom properties"), but every
    # missing rung was an invitation for the next display heading to be a
    # hand-rolled clamp() instead of a decision — the exact rot the theme
    # rebuild measured. Three inert declarations are cheaper than one fork.
    for rung in ("sl-text-2xl", "sl-text-3xl", "sl-text-4xl"):
        assert _token(LAYOUT, rung) == _token(STOREFRONT, rung), rung


def test_tokens_that_differ_only_in_name_still_carry_the_same_value():
    """Two spellings, one value — asserted so the pair cannot drift apart.

    Renaming either side would touch several hundred call sites across
    base.css, the sections and the app templates for no customer benefit, so
    both vocabularies stay. What must not happen is the numbers diverging
    while the names hide it.
    """
    assert _token(LAYOUT, "sl-content") == _token(STOREFRONT, "sl-maxw")
    assert _token(LAYOUT, "sl-radius-md") == _token(STOREFRONT, "sl-radius")
    assert _token(LAYOUT, "sl-radius-control") == _token(
        STOREFRONT, "sl-radius-control"
    )


def test_no_surface_asks_for_a_weight_the_brand_face_does_not_load():
    """Archivo ships 400-800. Asking for 900 gets a synthetic bold.

    Eight declarations across both surfaces asked for 900 — the header
    lockups, the footer wordmark, the shop and comparison headings, the 404
    and the report brand. A weight that is not loaded is either clamped or
    faux-bolded by the browser, and faux-bold on a wordmark is the difference
    between a designed mark and a smeared one.
    """
    # Both surfaces self-host the same variable face (one file, 400-800).
    theme_layout = (ROOT / "storefront-theme" / "layout" / "theme.liquid").read_text(
        encoding="utf-8"
    )
    for source in (theme_layout, LAYOUT):
        assert "font-weight: 400 800" in source
        assert "archivo-latin-var.woff2" in source
        assert "fonts.googleapis.com" not in source
        assert "fonts.gstatic.com" not in source

    styled = list((ROOT / "storefront-theme" / "sections").glob("*.liquid"))
    styled += [ROOT / "storefront-theme" / "assets" / "base.css"]
    styled += sorted(TEMPLATES.glob("*.html.j2"))
    for path in styled:
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"font-weight:\s*900\b", source), path.name


def test_shared_photography_ships_the_same_bytes_to_both_surfaces():
    """One photograph, one encode — whichever surface the visitor reaches.

    The dusk-range hero and the default share card are the two images that
    appear on both the storefront and the app. The hero pair had drifted to a
    lighter off-recipe encode on the app side (54,298 B against the theme's
    97,918 B for the same 1672x941 frame), so the surface people pay to use
    was serving the worse copy of the same photograph. The recipe of record is
    store-assets/plan_card_webp.py: quality=82, method=6, LANCZOS.
    """
    pairs = (
        ("caddieinsight-range-hero-desktop.webp", "caddieinsight-range-hero.webp"),
        (
            "caddieinsight-range-hero-mobile-v2.webp",
            "caddieinsight-range-hero-mobile-v2.webp",
        ),
        ("og-caddieinsight.png", "og-caddieinsight.png"),
    )
    for theme_name, app_name in pairs:
        theme_bytes = (ROOT / "storefront-theme" / "assets" / theme_name).read_bytes()
        app_bytes = (ROOT / "swinglab" / "web" / "static" / app_name).read_bytes()
        assert theme_bytes == app_bytes, theme_name
        # test_premium_landing.py holds the app to a 150 KB ceiling per asset;
        # assert it here too so the theme side can never push past it.
        assert len(theme_bytes) <= 150_000, theme_name


def test_app_and_storefront_share_tour_caddie_type_stack():
    theme = (ROOT / "storefront-theme" / "layout" / "theme.liquid").read_text(
        encoding="utf-8"
    )

    # Archivo is the wordmark's own typeface, so headings and the shipped
    # lockup are cut from one shape. The guided report already lists it
    # first and depends on the shell having loaded it.
    #
    # Display is a SEPARATE family name rather than a font-stretch on the
    # interface face, because it is a separate file: wdth 125 is a named
    # instance in Archivo's STAT table and ships pre-built at 14,536 bytes,
    # where the dual-axis variable font is 90,104. Asserting the family name
    # is what stops a later "simplify the stack" pass from collapsing the two
    # back into one declaration and quietly pulling in the larger file.
    assert 'font-family: "Archivo";' in theme  # self-hosted @font-face
    assert 'font-family: "Archivo Expanded";' in theme
    assert 'font-family: "DM Mono";' in theme
    assert 'font-family: "Archivo";' in LAYOUT  # the app ships the same files
    assert 'font-family: "Archivo Expanded";' in LAYOUT
    assert 'font-family: "DM Mono";' in LAYOUT
    assert '"Archivo"' in _token(STOREFRONT, "sl-font-sans")
    assert '"Archivo Expanded"' in _token(STOREFRONT, "sl-font-display")
    assert '"DM Mono"' in _token(STOREFRONT, "sl-font-mono")
    assert '"Archivo"' in _token(LAYOUT, "sl-font-sans")
    assert '"Archivo Expanded"' in _token(LAYOUT, "sl-font-display")
    assert '"DM Mono"' in _token(LAYOUT, "sl-font-mono")
    assert "Sora" not in _token(LAYOUT, "sl-font-display")
    # The display stack falls back to plain Archivo before any system face, so
    # a failed load of the 14 KB static degrades to the right typeface at the
    # wrong width rather than to Helvetica.
    assert '"Archivo Expanded", "Archivo"' in _token(LAYOUT, "sl-font-display")
    assert '"Archivo Expanded", "Archivo"' in _token(STOREFRONT, "sl-font-display")
    assert "--sl-font-display" in STOREFRONT
    # .sl-section-head is gone, and its absence is the point. It was ONE
    # centred eyebrow/h2/lede stack that all ten homepage bands rendered,
    # which is exactly the sameness this redesign set out to remove — so
    # the snippet and its CSS went with the last caller. The shared
    # vocabulary that survived is the mono eyebrow, which 31 sections use.
    assert ".sl-section-head" not in STOREFRONT
    assert ".sl-eyebrow" in STOREFRONT

    # store-assets/make_fonts.py writes every face into both surfaces in one
    # pass, so drift between them means somebody hand-placed a file. The
    # guided report asks for Archivo and relies on the app shell having loaded
    # the same face the storefront did; two builds of "the same" font are two
    # different faces as far as a swap is concerned.
    faces = (
        "archivo-latin-var.woff2",
        "archivo-expanded-latin-800.woff2",
        "dm-mono-latin-400.woff2",
        "dm-mono-latin-500.woff2",
    )
    for face in faces:
        theme_bytes = (ROOT / "storefront-theme" / "assets" / face).read_bytes()
        app_bytes = (ROOT / "swinglab" / "web" / "static" / face).read_bytes()
        assert theme_bytes == app_bytes, face

    # The whole type system is a third of the 150 KB single-asset ceiling.
    # Pinning the total is what keeps a future "just add a display weight"
    # from tripling the preload without anyone noticing: the dual-axis variable
    # file alone would be 90,104 bytes.
    total = sum(
        (ROOT / "storefront-theme" / "assets" / face).stat().st_size for face in faces
    )
    assert total < 100_000, total


def test_app_shell_uses_homepage_premium_chrome_and_footer():
    assert '<body class="sl-premium-chrome' in LAYOUT
    assert '<header class="sl-header sl-header--premium"' in LAYOUT
    assert 'class="sl-app-banner' in LAYOUT
    assert 'class="sl-app-footer"' in LAYOUT
    assert 'class="sl-app-footer__inner"' in LAYOUT
    assert ".sl-premium-chrome .sl-menu .sl-menu__panel" in LAYOUT
    # These two used to be pinned as the literals `rgba(6, 17, 12, .96)` and
    # `#f07a18`. Pinning a literal pins the wrong thing: it survives a palette
    # change by forcing the OLD colour to stay somewhere in the file, which is
    # precisely the fork the token sheet exists to prevent. 92 literals in this
    # template were mapped onto tokens; these are two of them, and the
    # assertion now checks that the menu panel and the signal are DERIVED.
    assert "background: rgba(var(--sl-night-rgb), .96);" in LAYOUT
    # The signal colour must NOT be a background anywhere in the shell. This
    # assertion originally pinned `background: #f07a18` on the header CTA, so
    # re-pointing it at the token preserved the very thing the palette forbids:
    # amber marks a value the engine measured, and the most prominent amber
    # object in the whole product was a button. Both CTAs are bone now, which
    # on a near-black field is louder than amber was anyway.
    assert "background: var(--sl-accent);" not in LAYOUT
    assert "background: var(--sl-orange);" not in LAYOUT
    # ...and that no raw hex survived below the token sheet at all. The one
    # allowed exception would be a colour with no token, and there is none:
    # the last holdout was an error red at 2.97:1 on the dark field, which
    # became --sl-danger rather than staying an unreadable literal.
    below = LAYOUT[LAYOUT.index("--sl-tabbar-h") :]
    assert not re.search(r"#[0-9a-fA-F]{6}\b", below), re.findall(
        r"#[0-9a-fA-F]{6}\b", below
    )
    assert ".sl-header--premium .sl-header__inner { min-height: 64px;" in LAYOUT
    assert "@media (max-width: 560px)" in LAYOUT
    assert ".sl-app-banner__detail { display: none; }" in LAYOUT


def test_free_and_pro_navigation_remain_dynamic(tmp_path, monkeypatch):
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    user = app.state.users.create("parity@example.com", "longenough")
    app.state.users.upsert_golfer_profile(
        user.id,
        display_name="Kyle",
        experience_mode="improve",
        handicap_range="10_to_14",
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="driver",
    )
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    free_shell = client.get("/today").text.split("</dialog>", 1)[0]
    assert 'href="/pricing"' in free_shell
    assert "data-pro-member-nav" not in free_shell
    assert free_shell.count('action="/logout" method="post"') == 2
    assert "Create free account" not in free_shell

    signed_out = TestClient(app).get("/").text.split("</dialog>", 1)[0]
    assert signed_out.count('class="sl-header__cta') == 2
    assert signed_out.count('href="/signup"') >= 3
    assert "Analyze free" in signed_out

    app.state.users.set_plan(user.id, "pro", "active")
    pro_shell = client.get("/today").text.split("</dialog>", 1)[0]
    assert 'href="/pricing"' not in pro_shell
    assert pro_shell.count("data-pro-member-nav") == 2
    assert pro_shell.count("Welcome back, Kyle") == 1
    assert 'data-pro-member-nav' in pro_shell
    assert ">Kyle</span>" in pro_shell or ">Game plan</span>" in pro_shell
    assert "Let&rsquo;s work on your swing" in pro_shell


def test_equal_height_rules_are_scoped_to_peer_cards():
    account = (TEMPLATES / "web_account.html.j2").read_text(encoding="utf-8")
    today = (TEMPLATES / "web_today.html.j2").read_text(encoding="utf-8")
    shop = (TEMPLATES / "web_shop.html.j2").read_text(encoding="utf-8")
    landing = (TEMPLATES / "web_login.html.j2").read_text(encoding="utf-8")

    assert ".account-grid" in account and "align-items: stretch;" in account
    assert ".account-card" in account and "height: 100%;" in account
    assert ".practice-option" in today and "height: 100%;" in today
    assert "aspect-ratio: 20 / 13;" in shop
    assert ".product__body" in shop and "flex: 1;" in shop
    assert ".flow-list li" in landing and "height: 100%;" in landing


def test_public_app_templates_do_not_reference_automated_image_generation():
    rendered_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATES.glob("web_*.html.j2"))
    ).lower()

    for phrase in ("ai-generated", "artificial intelligence", "synthetic"):
        assert phrase not in rendered_source


def test_manifest_uses_the_same_premium_page_chrome(tmp_path):
    cfg = Config()
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))

    manifest = client.get("/app.webmanifest").json()
    assert manifest["background_color"] == "#eef2ef"
    assert manifest["theme_color"] == "#06110c"
