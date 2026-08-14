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
    # The DIRECTION of "more headroom" has now flipped twice — darker on the
    # original paper ground, lighter on the 2026-08-10 dark field, darker again
    # on Industry's paper — while the reason never changed: the app sets small
    # interface text where the storefront sets display prose. That invariance
    # is the useful thing for this test to record. A future pass that
    # re-derives these from the wrong ground fails here rather than silently
    # halving the contrast.
    assert _token(LAYOUT, "sl-ink-muted") == "#626265"
    assert _token(LAYOUT, "sl-orange-text") == "#375169"
    assert _token(LAYOUT, "sl-control-border") == "#6e6e71"

    # ...and the storefront's own value is the display-copy counterpart, not a
    # drifted duplicate. The app's is darker because it sets small interface
    # text where the storefront sets prose. Pinning both ends stops a future
    # "unify the tokens" pass from quietly trading contrast for symmetry.
    #
    # 2026-08-12: the storefront's moved from #6a6a6d to #666669, and NOT
    # toward the app — it is still the lighter of the two. #6a6a6d was verified
    # against the paper ground only, and seven sections then painted it
    # straight onto the --sl-night well, where it computes to 4.44 and fails AA
    # at the 12px this token is always set at. #666669 clears both grounds
    # (5.12 on --sl-bg, 4.72 on --sl-night), which is what a token used on two
    # grounds has to do.
    assert _token(STOREFRONT, "sl-ink-muted") == "#666669"

    # --sl-border is 1.37:1 on paper and is DECORATIVE ONLY. The control
    # border is the one that has to clear WCAG 1.4.11's 3:1 for non-text
    # contrast, on both surfaces. Two tokens, two jobs: collapsing them into
    # one is how interactive edges quietly stop being visible, so both ends
    # are pinned against a well-meaning simplification.
    assert _token(STOREFRONT, "sl-control-border") == "#7a7a7d"

    # THE TWO SIGNALS, pinned at both ends. Steel-deep marks a value the engine
    # measured; steel-lit marks the live readout. Each is legible on exactly
    # one ground — the signal is 5.78 on paper and 3.00 on the field, the trace
    # 9.76 on the field and 1.78 on paper — so the palette confines each to its
    # own surface by contrast alone. A pass that "harmonises" them onto a
    # single mid-steel would break that property silently.
    for source in (LAYOUT, STOREFRONT):
        assert _token(source, "sl-accent") == "#416180"
        assert _token(source, "sl-trace") == "#94bce3"
        assert _token(source, "sl-steel") == "#5980a6"
        # The structural accent is a THIRD colour on purpose: chrome, kickers
        # and active nav have somewhere to go that is not a signal. Collapsing
        # it into --sl-accent is precisely how amber stopped meaning "measured"
        # three times before.
        assert _token(source, "sl-steel") != _token(source, "sl-accent")
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
    """Barlow ships 400 and 500 here. Anything else gets a synthetic bold.

    Under Archivo this test policed a single value, 900, because the variable
    file covered 400-800 and only 900 fell off the end. Barlow has NO variable
    font — Google serves it static at v13 — so each weight is its own 22 KB
    file and the covered set is a deliberate budget rather than a range. That
    makes 600/700/800 exactly as synthetic as 900 was, and there were 152 of
    them across the two surfaces when the palette was free.

    A weight that is not loaded is faux-bolded by the browser, and faux-bold on
    a wordmark is the difference between a designed mark and a smeared one. The
    fix is never a heavier number: display type is a separate FAMILY here, so a
    rule that wants more weight reaches for --sl-font-display.
    """
    # password.liquid declares its own faces — it is a separate layout that
    # never loads theme.liquid — so it is checked here too. It was missed by
    # the previous rewrite and would have served two 404s behind the password
    # wall, which is the one page a merchant previews before launch.
    layouts = ROOT / "storefront-theme" / "layout"
    theme_layout = (layouts / "theme.liquid").read_text(encoding="utf-8")
    password_layout = (layouts / "password.liquid").read_text(encoding="utf-8")
    for source in (theme_layout, password_layout, LAYOUT):
        # Three faces, declared at the weights that actually ship.
        assert "barlow-latin-400.woff2" in source
        assert "barlow-latin-500.woff2" in source
        assert "barlow-condensed-latin-600.woff2" in source
        # No REFERENCE to a retired face survives. Prose that explains why the
        # type stack changed may still name Archivo — the failure mode this
        # guards is a src/preload pointing at a file make_fonts.py deletes.
        assert not re.search(r"archivo[a-z0-9-]*\.woff2", source, re.I), source[:40]
        assert not re.search(r'font-family:\s*"Archivo', source)
        assert "fonts.googleapis.com" not in source
        assert "fonts.gstatic.com" not in source

    loadable = {"400", "500", "600"}
    styled = list((ROOT / "storefront-theme" / "sections").glob("*.liquid"))
    styled += list((ROOT / "storefront-theme" / "snippets").glob("*.liquid"))
    styled += [ROOT / "storefront-theme" / "assets" / "base.css"]
    styled += sorted(TEMPLATES.glob("*.html.j2"))
    offenders = []
    for path in styled:
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"font-weight:\s*(\d{3})\b", source):
            if match.group(1) not in loadable:
                offenders.append(f"{path.name}: {match.group(0)}")
    assert offenders == [], (
        "weights no shipped face carries — 600 is the display FACE, not a "
        "heavier body weight:\n" + "\n".join(offenders)
    )


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

    # Barlow Condensed over Barlow is Industry's pairing, and both surfaces
    # declare it identically. The guided report asks for Barlow by name and
    # depends on the shell having loaded it.
    #
    # Display is a SEPARATE family, not a font-stretch or a heavier weight on
    # the interface face. That is not a stylistic preference: Barlow ships no
    # variable font, so a condensed display voice can ONLY come from the
    # condensed family. Asserting the family name stops a later "simplify the
    # stack" pass from collapsing the two into one declaration and silently
    # replacing the display voice with a synthetic bold.
    assert 'font-family: "Barlow";' in theme  # self-hosted @font-face
    assert 'font-family: "Barlow Condensed";' in theme
    assert 'font-family: "DM Mono";' in theme
    assert 'font-family: "Barlow";' in LAYOUT  # the app ships the same files
    assert 'font-family: "Barlow Condensed";' in LAYOUT
    assert 'font-family: "DM Mono";' in LAYOUT
    for source in (STOREFRONT, LAYOUT):
        assert '"Barlow"' in _token(source, "sl-font-sans")
        assert '"Barlow Condensed"' in _token(source, "sl-font-display")
        assert '"DM Mono"' in _token(source, "sl-font-mono")
        # The display stack falls back to plain Barlow before any system face,
        # so a failed load of the condensed static degrades to the right
        # typeface at the wrong width rather than to Helvetica.
        assert '"Barlow Condensed", "Barlow"' in _token(source, "sl-font-display")
    assert "Sora" not in _token(LAYOUT, "sl-font-display")
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
    # guided report asks for Barlow and relies on the app shell having loaded
    # the same face the storefront did; two builds of "the same" font are two
    # different faces as far as a swap is concerned.
    faces = (
        "barlow-latin-400.woff2",
        "barlow-latin-500.woff2",
        "barlow-condensed-latin-600.woff2",
        "dm-mono-latin-400.woff2",
        "dm-mono-latin-500.woff2",
    )
    for face in faces:
        theme_bytes = (ROOT / "storefront-theme" / "assets" / face).read_bytes()
        app_bytes = (ROOT / "swinglab" / "web" / "static" / face).read_bytes()
        assert theme_bytes == app_bytes, face

    # The retired variable face is deleted, not orphaned in assets/. A theme
    # zip ships every file in the directory, so a leftover font is dead weight
    # in every release. store-assets/make_fonts.py removes them on each run.
    for retired in ("archivo-latin-var.woff2", "archivo-expanded-latin-800.woff2"):
        assert not (ROOT / "storefront-theme" / "assets" / retired).exists(), retired
        assert not (ROOT / "swinglab" / "web" / "static" / retired).exists(), retired

    # 96,320 bytes against a 100 KB ceiling, and that 3.7 KB of headroom is the
    # point rather than an accident. Barlow ships no variable font, so a fourth
    # weight is a whole extra 22 KB file — this gate is what makes "just add a
    # semibold" a decision somebody has to argue for instead of a diff nobody
    # notices. The answer is nearly always the display FACE, which is already
    # loaded.
    total = sum(
        (ROOT / "storefront-theme" / "assets" / face).stat().st_size for face in faces
    )
    assert total < 100_000, total


def test_app_shell_uses_one_paper_header_and_the_shared_footer():
    """Industry has ONE header, on paper.

    This used to assert the app adopted the storefront's dark "premium"
    chrome, which the app applied unconditionally — so the premium variant
    *was* the app header, and the base .sl-header rule underneath it was
    unreachable, still carrying a pre-inversion light background nothing
    rendered. 31 override rules and both dead class hooks are gone.

    The premium_chrome flag itself survives on the STOREFRONT, where it
    selects which navigation a page shows. That is product logic and is
    covered by tests/test_storefront_header.py; it has nothing to do with the
    colour it used to also carry.
    """
    assert '<header class="sl-header"' in LAYOUT
    # Search the TEMPLATE, not its commentary. The surviving .sl-header rule
    # carries a comment naming both retired hooks and explaining what was
    # removed and why; a substring check over the raw file would forbid the
    # template from recording its own history, which is the opposite of what
    # this codebase wants from a comment.
    code = re.sub(r"\{#.*?#\}", "", LAYOUT, flags=re.S)
    assert "sl-header--premium" not in code
    assert "sl-premium-chrome" not in code
    assert 'class="sl-app-banner' in LAYOUT
    assert 'class="sl-app-footer"' in LAYOUT
    assert 'class="sl-app-footer__inner"' in LAYOUT
    # The header is a translucent paper bar over the ground it sticks to, and
    # it is DERIVED rather than pinned as a literal: pinning a literal pins
    # the wrong thing, because it survives a palette change by forcing the old
    # colour to stay somewhere in the file — precisely the fork the token
    # sheet exists to prevent.
    assert "background: rgba(var(--sl-cream-rgb), 0.94);" in LAYOUT
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
    # A hex colour is never written `&#…`, but an HTML character reference
    # always is — and the Pro lock, &#128274;, is six decimal digits that
    # also read as hex. Without the lookbehind this gate flagged the lock,
    # and the rewrite that satisfied it turned the entity's body into a
    # token reference, which phones rendered as literal menu text
    # ("&var(--sl-trace-dim);"). tests/test_pwa_shell.py pins the entity.
    hex_literal = r"(?<!&)#[0-9a-fA-F]{6}\b"
    assert not re.search(hex_literal, below), re.findall(hex_literal, below)
    # The compact phone header, kept when the dark chrome it used to hang off
    # was removed. The treatment was cosmetic; the 8px it takes off a 72px bar
    # at the top of every phone screen is not.
    assert ".sl-header, .sl-header__inner { min-height: 64px; }" in LAYOUT
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


def test_no_surface_uses_a_class_the_other_surface_defines():
    """The two sheets are separate. A class from one renders as nothing on the
    other, and nothing in the toolchain says so.

    This bit three times in one overhaul:

      - the blueprint frame's panel list was written into base.css with the
        APP's class names, and matched nothing on either surface;
      - a new app page reached for `.sl-btn .sl-btn--outline`, a storefront
        class, and shipped a primary action that rendered as a bare underlined
        link;
      - `--sl-card-inset` and `--sl-dense-inset` are storefront-only tokens,
        which is why two app templates carry a comment warning about exactly
        this and compute their insets from --sl-space-* instead.

    Each one looks fine in the diff and produces no error anywhere: unmatched
    CSS is silent, and an unstyled element is still an element. The only
    reliable signal is the join, so it gets asserted here.

    Deliberately narrow — the `sl-btn` family and the two inset tokens, all
    demonstrably load-bearing on the storefront and absent from the app.
    Broadening it to every shared prefix would flag the ~40 names both
    surfaces genuinely define side by side.
    """
    storefront_only = ("sl-btn", "--sl-card-inset", "--sl-dense-inset")
    comment = re.compile(r"\{#.*?#\}", re.DOTALL)
    with_fallback = re.compile(r"\s*,")

    offenders = []
    for path in sorted(TEMPLATES.glob("web_*.j2")):
        source = path.read_text(encoding="utf-8")
        # Prose explaining the trap is the right thing to find, so Jinja
        # comments are stripped before looking rather than matched line by
        # line — the warnings these files carry are multi-line.
        live = comment.sub("", source)
        for token in storefront_only:
            for match in re.finditer(re.escape(token), live):
                start = live.rfind("\n", 0, match.start()) + 1
                end = live.find("\n", match.start())
                line = live[start : end if end != -1 else len(live)]
                # `var(--storefront-token, fallback)` is safe by construction:
                # the token is absent here, so the fallback is what renders.
                # web_offline.html.j2 does this deliberately.
                if with_fallback.match(live[match.end() : match.end() + 4]):
                    continue
                offenders.append(f"{path.name}: {line.strip()[:80]}")
    assert offenders == [], (
        "storefront-only names used on the app surface — these resolve to "
        "nothing and report nothing:\n  " + "\n  ".join(offenders)
    )
