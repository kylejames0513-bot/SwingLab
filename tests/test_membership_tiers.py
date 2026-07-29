"""The three-tier membership ladder: monthly and yearly passes plus the
lifetime tier (SL-PRO-LIFE — a 36,500-day grant, displayed as "Lifetime"),
and the pricing page that advertises the Pro locks instead of hiding them.

Lifetime is deliberately NOT a flag or column: it rides the existing
SKU→days ledger, so stacking, cancellation, idempotence, and pending-grant
parking all apply to it unchanged — these tests prove that.
"""

from __future__ import annotations

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
    assert DEFAULTS["billing"]["pro_price_monthly_text"] == "$4.99/month"
    assert (
        DEFAULTS["billing"]["pro_price_annual_text"]
        == "$39.99/year — $3.33/month"
    )
    assert (
        DEFAULTS["billing"]["pro_price_lifetime_text"]
        == "$79.99 once — Pro for good"
    )
    # Both gates ship OFF in bare-code defaults (white-label installs stay
    # ungated); the shipped config.yaml turns them on.
    assert DEFAULTS["billing"]["replay_pro_only"] is False
    assert DEFAULTS["billing"]["progress_pro_only"] is False


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

def make_pricing_app(tmp_path, monkeypatch, replay=False, progress=False):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["replay_pro_only"] = replay
    cfg.billing["progress_pro_only"] = progress
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def test_pricing_page_shows_all_three_tiers(tmp_path, monkeypatch):
    client = TestClient(make_pricing_app(tmp_path, monkeypatch))
    html = client.get("/pricing").text
    assert "$4.99/month" in html
    assert "$39.99/year" in html
    assert "$79.99 once" in html
    assert "Best value" in html                     # the yearly hero badge
    assert "Pro — lifetime" in html
    # free_per_month defaults to 1 — the copy goes singular.
    assert "1 full swing analysis" in html
    # The old false claim is gone for good.
    assert "only difference is how often you can film" not in html


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


def test_pricing_renewal_fineprint_is_honest(tmp_path, monkeypatch):
    client = TestClient(make_pricing_app(tmp_path, monkeypatch))
    html = client.get("/pricing").text
    assert "renew automatically" in html
    assert "Lifetime is a single payment and never" in html
    assert "refundable within 14 days" in html
