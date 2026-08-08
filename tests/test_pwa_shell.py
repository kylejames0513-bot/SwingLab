"""Installed-app contracts: manifest, icons, tab bar, safe areas, worker.

These guard the parts of the mobile experience that only exist once the app
is installed to a home screen, where there is no browser chrome to fall back
on and no way for a user to work around a broken shell.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from PIL import Image

from swinglab.config import Config
from swinglab.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "swinglab" / "templates"
LAYOUT = (TEMPLATES / "web_layout.html.j2").read_text(encoding="utf-8")
STATIC = ROOT / "swinglab" / "web" / "static"
WORKER = (STATIC / "service-worker.js").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    return TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))


# -- manifest ------------------------------------------------------------

def test_manifest_declares_installable_identity_and_icons(client):
    manifest = client.get("/app.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()

    # A stable id survives a start_url change; without it the browser treats
    # an updated start_url as a different app and orphans the installed one.
    assert data["id"] == "/?installed=1"
    assert data["scope"] == "/"
    assert data["display"] == "standalone"
    assert data["start_url"].startswith("/today")

    by_purpose: dict[str, list[dict]] = {}
    for icon in data["icons"]:
        by_purpose.setdefault(icon["purpose"], []).append(icon)

    # Declaring one icon as both "any" and "maskable" leaves the mark either
    # padded in the tab or clipped in the launcher. They must be separate.
    assert "any" in by_purpose and "maskable" in by_purpose
    for icon in data["icons"]:
        assert icon["purpose"] in {"any", "maskable"}

    any_sizes = {icon["sizes"] for icon in by_purpose["any"]}
    assert {"192x192", "512x512"} <= any_sizes
    assert "512x512" in {icon["sizes"] for icon in by_purpose["maskable"]}


def test_every_manifest_icon_and_shortcut_icon_resolves(client):
    data = client.get("/app.webmanifest").json()
    referenced = [icon["src"] for icon in data["icons"]]
    for shortcut in data.get("shortcuts", []):
        referenced += [icon["src"] for icon in shortcut.get("icons", [])]

    assert referenced
    for src in referenced:
        response = client.get(src)
        assert response.status_code == 200, src


def test_manifest_png_icons_are_square_at_their_declared_size(client):
    for icon in client.get("/app.webmanifest").json()["icons"]:
        if icon["type"] != "image/png":
            continue
        width, height = (int(part) for part in icon["sizes"].split("x"))
        with Image.open(BytesIO(client.get(icon["src"]).content)) as image:
            assert image.format == "PNG"
            assert image.size == (width, height), icon["src"]


def test_maskable_icon_keeps_the_mark_inside_the_safe_circle(client):
    """A launcher may crop a maskable icon to the circle inscribed in the
    middle 80%. Anything outside that ring must be background only."""
    data = client.get("/app.webmanifest").json()
    src = next(
        icon["src"] for icon in data["icons"] if icon["purpose"] == "maskable"
    )
    with Image.open(BytesIO(client.get(src).content)) as image:
        rgb = image.convert("RGB")
        size = rgb.width
        corner = rgb.getpixel((2, 2))
        # Sample the corners and edge midpoints, all of which fall outside
        # the safe circle and so may be cropped away.
        outside = [
            (2, 2), (size - 3, 2), (2, size - 3), (size - 3, size - 3),
        ]
        for point in outside:
            assert rgb.getpixel(point) == corner, point


def test_manifest_shortcuts_point_into_scope(client):
    shortcuts = client.get("/app.webmanifest").json()["shortcuts"]
    assert len(shortcuts) >= 2
    for shortcut in shortcuts:
        assert shortcut["url"].startswith("/")
        assert shortcut["name"] and shortcut["short_name"]


# -- installed shell -----------------------------------------------------

def test_viewport_opts_into_safe_area_insets():
    viewport = re.search(r'<meta name="viewport" content="([^"]+)"', LAYOUT)
    assert viewport is not None
    # Without viewport-fit=cover, env(safe-area-inset-*) resolves to 0 and the
    # tab bar would sit under the home indicator.
    assert "viewport-fit=cover" in viewport.group(1)


def test_bottom_tab_bar_reserves_its_own_height_and_the_home_indicator():
    assert 'class="sl-tabbar"' in LAYOUT
    assert "--sl-safe-bottom: env(safe-area-inset-bottom, 0px)" in LAYOUT
    # The bar pads itself past the indicator, and the footer reserves the
    # bar's height so the last row of content is never trapped underneath.
    assert "padding-bottom: var(--sl-safe-bottom)" in LAYOUT
    assert (
        "padding-bottom: calc(38px + var(--sl-tabbar-h) + var(--sl-safe-bottom))"
        in LAYOUT
    )


def test_tab_bar_is_hidden_before_its_media_query_turns_it_on():
    """`display: none` must be declared before the media query that shows the
    bar; CSS of equal specificity resolves by source order, so the reverse
    silently hides the tab bar on phones."""
    hidden = LAYOUT.index(".sl-tabbar { display: none; }")
    shown = LAYOUT.index("@media (max-width: 980px)")
    assert hidden < shown


def test_tab_targets_meet_the_minimum_touch_size():
    block = LAYOUT[LAYOUT.index(".sl-tabbar__item {"):]
    height = re.search(r"min-height:\s*(\d+)px", block)
    assert height is not None and int(height.group(1)) >= 44


def test_tab_bar_marks_the_current_section_beyond_colour_alone():
    # Colour is not a sufficient state signal on its own, so the active tab
    # also carries a rule above the icon.
    assert ".sl-tabbar__item.is-current::before" in LAYOUT
    assert 'aria-current="page"' in LAYOUT


def test_installed_app_drops_the_plan_banner():
    assert "@media (display-mode: standalone), (display-mode: minimal-ui)" in LAYOUT


def test_tab_bar_renders_for_a_signed_out_visitor(client):
    html = client.get("/").text
    assert 'class="sl-tabbar"' in html
    assert ">Analyze<" in html
    # Today and History need an account; they must not appear as dead ends.
    assert ">Today<" not in html


def test_tab_bar_more_button_shares_the_header_menu_dialog(client):
    html = client.get("/").text
    # Two openers, one dialog: the header hamburger and the tab bar's More.
    assert html.count('aria-controls="sl-mobile-menu"') == 2
    # Attribute form only — the bare name also appears in the script's
    # querySelectorAll argument.
    assert html.count("data-menu-open>") == 2
    # Exactly one dialog; the header also names it via data-menu-id.
    assert html.count('<dialog class="sl-menu" id="sl-mobile-menu"') == 1


# -- service worker ------------------------------------------------------

def test_worker_cache_is_an_allowlist_not_a_denylist():
    """Personal reports, sessions, and uploads must never reach Cache
    Storage. An allowlist keeps a future route private by default."""
    assert "function isCacheable(pathname)" in WORKER
    body = WORKER[WORKER.index("function isCacheable"):]
    body = body[: body.index("}")]
    assert 'pathname === "/offline"' in body
    assert 'pathname.startsWith("/static/")' in body


def test_worker_refuses_to_store_private_or_no_store_responses():
    assert re.search(r"/\(\?:private\|no-store\)/i", WORKER)


def test_worker_only_handles_same_origin_get_requests():
    assert 'if (request.method !== "GET") return;' in WORKER
    assert "if (url.origin !== self.location.origin) return;" in WORKER


def test_worker_falls_back_to_the_offline_page_for_navigations():
    assert 'caches.match("/offline")' in WORKER


def test_worker_cache_name_is_versioned():
    name = re.search(r'CACHE_NAME = "([^"]+)"', WORKER)
    assert name is not None and re.search(r"-v\d+$", name.group(1))


def test_offline_page_renders_without_an_account(client):
    response = client.get("/offline")
    assert response.status_code == 200
    assert "You’re offline" in response.text


def test_precached_shell_paths_all_exist(client):
    listed = re.search(r"const PRECACHE = \[(.*?)\];", WORKER, re.S)
    assert listed is not None
    for path in re.findall(r'"([^"]+)"', listed.group(1)):
        assert client.get(path).status_code == 200, path
