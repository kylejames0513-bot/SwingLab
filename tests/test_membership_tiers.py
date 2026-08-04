"""The three-tier membership ladder: monthly and yearly passes plus the
lifetime tier (SL-PRO-LIFE — a 36,500-day grant, displayed as "Lifetime"),
and the pricing page that advertises the Pro locks instead of hiding them.

Lifetime is deliberately NOT a flag or column: it rides the existing
SKU→days ledger, so stacking, cancellation, idempotence, and pending-grant
parking all apply to it unchanged — these tests prove that.
"""

from __future__ import annotations

from pathlib import Path
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import DEFAULTS, Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app

from tests.test_shopify_billing import (
    DAY,
    get_user,
    order_webhook,
    pro_order,
    signup,
)
from tests.test_web import fake_analyze_ok


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "shpss_test_secret")
    cfg = Config()
    cfg.web["require_account"] = True
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


# -- config contracts ---------------------------------------------------------

def test_defaults_pin_the_tier_ladder():
    skus = DEFAULTS["billing"]["shopify_skus"]
    assert skus["SL-PRO-1MO"] == 31
    assert skus["SL-PRO-12MO"] == 365
    assert skus["SL-PRO-LIFE"] == 36500
    assert DEFAULTS["billing"]["free_per_month"] == 1
    assert DEFAULTS["billing"]["pro_price_monthly_text"] == "$9.99/month"
    assert (
        DEFAULTS["billing"]["pro_price_annual_text"]
        == "$69.99/year — $5.83/month"
    )
    assert (
        DEFAULTS["billing"]["pro_price_lifetime_text"]
        == "$149 once — the Founders Pass"
    )
    assert DEFAULTS["billing"]["pro_annual_badge_text"] == "Best value — save 42%"
    # Both gates ship OFF in bare-code defaults (white-label installs stay
    # ungated); the shipped config.yaml turns them on. Subscription copy is
    # also opt-in at the bare-code layer; CaddieInsight's shipped config
    # enables it only after the live selling plans have been provisioned.
    assert DEFAULTS["billing"]["replay_pro_only"] is False
    assert DEFAULTS["billing"]["progress_pro_only"] is False
    assert DEFAULTS["billing"]["store_subscriptions"] is False


def test_shipped_config_pins_the_live_membership_ladder():
    """The file copied into the production image must not drift from the
    prices, SKUs, quota, or renewal mode advertised by the live release."""
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")
    billing = shipped.billing

    assert billing["free_per_month"] == 1
    assert billing["shopify_skus"] == {
        "SL-PRO-1MO": 31,
        "SL-PRO-12MO": 365,
        "SL-PRO-LIFE": 36500,
    }
    assert billing["pro_price_monthly_text"] == "$9.99/month"
    assert billing["pro_price_annual_text"] == "$69.99/year — $5.83/month"
    assert billing["pro_price_lifetime_text"] == "$149 once — the Founders Pass"
    assert billing["pro_annual_badge_text"] == "Best value — save 42%"
    # The badge claim is arithmetic, not marketing: $69.99/year against
    # the $119.88 twelve months at $9.99 would cost really is 42% off.
    assert round((1 - 69.99 / (9.99 * 12)) * 100) == 42
    assert billing["store_subscriptions"] is True
    # The /pricing cards deep-link these variants so checkout preselects
    # the plan that was clicked — they must match the live store.
    assert billing["shopify_variant_ids"] == {
        "monthly": "46811170177196",
        "yearly": "46811170209964",
        "lifetime": "46839745282220",
    }


# -- the lifetime grant rides the day ledger ----------------------------------

def test_lifetime_sku_grants_a_hundred_years(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(sku="SL-PRO-LIFE"))
    user = get_user(client)
    assert user.is_pro
    assert abs(user.pro_until - (time.time() + 36500 * DAY)) < 60


def test_lifetime_stacks_on_a_running_month(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(order_id=1, sku="SL-PRO-1MO"))
    order_webhook(client, pro_order(order_id=2, sku="SL-PRO-LIFE"))
    # Buying early never loses days: 31 + 36500.
    assert abs(
        get_user(client).pro_until - (time.time() + 36531 * DAY)
    ) < 60


def test_cancelled_lifetime_gives_back_exactly_its_days(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(order_id=1, sku="SL-PRO-1MO"))
    order_webhook(client, pro_order(order_id=2, sku="SL-PRO-LIFE"))
    order_webhook(
        client, pro_order(order_id=2, sku="SL-PRO-LIFE"),
        topic="orders/cancelled",
    )
    # The separately-bought month survives the lifetime refund.
    user = get_user(client)
    assert user.is_pro
    assert abs(user.pro_until - (time.time() + 31 * DAY)) < 60


def test_lifetime_bought_before_signup_is_parked_and_claimed(app):
    client = TestClient(app)
    order_webhook(client, pro_order(email="new@example.com", sku="SL-PRO-LIFE"))
    signup(client, email="new@example.com")
    user = get_user(client, "new@example.com")
    assert user.is_pro
    assert abs(user.pro_until - (time.time() + 36500 * DAY)) < 60


def test_pro_header_replaces_generic_upgrade_with_personalized_member_state(app):
    client = TestClient(app)
    signup(client)
    user = get_user(client)
    app.state.users.upsert_golfer_profile(
        user.id,
        display_name="Kyle",
        experience_mode="improve",
        handicap_range=None,
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="iron",
    )

    free_header = client.get("/today").text
    assert 'href="/pricing"' in free_header
    assert "data-pro-member-nav" not in free_header

    order_webhook(client, pro_order(sku="SL-PRO-1MO"))
    pro_page = client.get("/today")

    assert pro_page.status_code == 200
    assert pro_page.headers["cache-control"] == "private, no-store"
    assert pro_page.text.count("data-pro-member-nav") == 2
    assert "Welcome back, Kyle" in pro_page.text
    assert "CaddieInsight Pro member" in pro_page.text
    assert 'href="/pricing"' not in pro_page.text


# -- account page: "Lifetime", not a date in 2126 -----------------------------

def test_account_shows_lifetime_instead_of_a_date(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(sku="SL-PRO-LIFE"))
    html = client.get("/account").text
    assert "Lifetime" in html
    assert "Pro access until" not in html
    # Nothing to extend, so no extend button either.
    assert "Extend Pro on the store" not in html


def test_account_keeps_the_dated_row_for_passes(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(sku="SL-PRO-1MO"))
    html = client.get("/account").text
    assert "Pro access until" in html
    assert "Lifetime" not in html
    assert "Extend Pro on the store" in html


# -- pricing page: three tiers, locks advertised ------------------------------

def make_pricing_app(
    tmp_path, monkeypatch, replay=False, progress=False,
    shopify=True, subscriptions=False, accounts=True,
):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    if shopify:
        monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
        monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "shpss_test_secret")
    else:
        monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
        monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    cfg = Config()
    cfg.web["require_account"] = accounts
    cfg.billing["replay_pro_only"] = replay
    cfg.billing["progress_pro_only"] = progress
    cfg.billing["store_subscriptions"] = subscriptions
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def test_pricing_page_shows_all_three_tiers(tmp_path, monkeypatch):
    client = TestClient(make_pricing_app(tmp_path, monkeypatch))
    html = client.get("/pricing").text
    assert "$9.99/month" in html
    assert "$69.99/year" in html
    assert "$149 once" in html
    assert "Best value" in html                     # the Season Pass hero badge
    assert "save 42%" in html                       # the honest savings math
    assert "Pro — Season Pass" in html
    assert "Pro — Founders Pass" in html
    assert "first 100 members" in html              # the honesty cap, advertised
    # free_per_month defaults to 1 — the copy goes singular.
    assert "1 full swing analysis" in html
    # The old false claim is gone for good.
    assert "only difference is how often you can film" not in html
    # No discount theatrics: no strikethrough compare-at price anywhere.
    assert "was $" not in html
    assert "<s>" not in html and "<del>" not in html


def test_pricing_cards_deep_link_their_store_variants(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "shpss_test_secret")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["shopify_variant_ids"] = {
        "monthly": "111", "yearly": "222", "lifetime": "333",
    }
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
    signup(client)

    html = client.get("/pricing").text
    base = "https://teststore.myshopify.com/products/swinglab-pro"
    yearly = html.index(f'href="{base}?variant=222"')
    monthly = html.index(f'href="{base}?variant=111"')
    lifetime = html.index(f'href="{base}?variant=333"')
    # One deep link per card, in the cards' own order (yearly hero first).
    assert yearly < monthly < lifetime


def test_pricing_cards_fall_back_to_the_plain_product_page(app):
    # No variant map (the bare-code default): every card links the product
    # page itself — never a guessed variant.
    client = TestClient(app)
    signup(client)
    html = client.get("/pricing").text
    assert "?variant=" not in html
    assert html.count(
        'href="https://teststore.myshopify.com/products/swinglab-pro"'
    ) == 3


def test_lifetime_card_needs_the_store(tmp_path, monkeypatch):
    # The Founders Pass exists only as a store SKU (SL-PRO-LIFE) — a
    # Stripe-only install has no one-payment product, so the card must
    # not promise one.
    client = TestClient(make_pricing_app(tmp_path, monkeypatch, shopify=False))
    html = client.get("/pricing").text
    assert "Pro — Founders Pass" not in html
    assert "Pro — Season Pass" in html  # the rest of the ladder still renders


def test_annual_badge_is_a_display_string(tmp_path, monkeypatch):
    # The savings arithmetic lives in config next to the prices — clearing
    # it removes the claim instead of stranding a stale one in the template.
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "shpss_test_secret")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["pro_annual_badge_text"] = ""
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    html = TestClient(app).get("/pricing").text
    assert "save 42%" not in html
    assert "Pro — Season Pass" in html


def test_pricing_page_advertises_gates_only_when_they_exist(
    tmp_path, monkeypatch
):
    # Both gates on: both Pro-only rows advertised.
    gated = TestClient(
        make_pricing_app(tmp_path / "a", monkeypatch, replay=True, progress=True)
    )
    html = gated.get("/pricing").text
    assert "Annotated coach replay video" in html
    assert "Progress dashboard &amp; trends" in html

    # Gates off (the white-label default): no Pro-only claim is rendered,
    # so the page never advertises a lock that doesn't exist.
    open_plan = TestClient(make_pricing_app(tmp_path / "b", monkeypatch))
    html = open_plan.get("/pricing").text
    assert "Annotated coach replay video" not in html
    assert "Progress dashboard &amp; trends" not in html


def test_lock_claims_track_the_effective_gate_not_the_raw_flag(
    tmp_path, monkeypatch
):
    # Raw flags on, but accounts OFF: nothing is actually locked for
    # anyone (open instances are never gated), so the public pricing page
    # must not claim a lock either.
    client = TestClient(
        make_pricing_app(
            tmp_path / "a", monkeypatch, replay=True, progress=True,
            accounts=False,
        )
    )
    html = client.get("/pricing").text
    assert "Annotated coach replay video" not in html
    assert "Progress dashboard &amp; trends" not in html

    # Replay flag on but the replay feature itself off: no replay exists
    # for anyone — the page must not sell one.
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["replay_pro_only"] = True
    cfg.slowmo["annotated"] = False
    app = create_app(cfg, sessions_dir=tmp_path / "b")
    html = TestClient(app).get("/pricing").text
    assert "Annotated coach replay video" not in html


def test_pricing_renewal_fineprint_is_honest(tmp_path, monkeypatch):
    # Passes-only store (the shipped default until the Subscriptions app
    # is actually installed): no auto-renew claim anywhere.
    passes = TestClient(make_pricing_app(tmp_path / "a", monkeypatch))
    html = passes.get("/pricing").text
    assert "renew automatically" not in html
    assert "nothing auto-renews" in html
    assert "single payment and never ends" in html
    assert "refundable within 14 days" in html

    # With billing.store_subscriptions on, the subscription mechanics are
    # described — including how cancellation works.
    subs = TestClient(
        make_pricing_app(tmp_path / "b", monkeypatch, subscriptions=True)
    )
    html = subs.get("/pricing").text
    assert "renew automatically" in html
    assert "cancel anytime" in html
    assert "refundable within 14 days" in html
