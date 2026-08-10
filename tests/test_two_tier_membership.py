"""Two-tier membership: Pro (unlimited analysis) and Coach (the proof cycle).

The ladder exists because the lower tier is what competitors sell and the
upper tier is what nobody else does. See
docs/superpowers/specs/2026-08-09-two-tier-membership-and-free-proof-cycle-design.md

The invariants that matter for money:

* a grant never DOWNGRADES an account — buying a month of Pro while holding
  Coach must not cost the Coach features already paid for;
* a grant never silently upgrades either — Coach is the only thing that
  unlocks the replay and the progress dashboard;
* both tiers share one expiry, and expiry falls all the way to free;
* a parked purchase remembers which tier it bought, so a Coach order claimed
  after signup does not arrive as Pro.
"""

from __future__ import annotations

import time

import pytest

from swinglab.web.users import COACH, FREE, PRO_TIER, UserStore

DAY = 86400


@pytest.fixture()
def users(tmp_path):
    store = UserStore(tmp_path / "users.db")
    yield store


@pytest.fixture()
def shipped_config():
    from pathlib import Path

    from swinglab.config import Config

    return Config.load(Path(__file__).resolve().parents[1] / "config.yaml")


def make(store, email="golfer@example.com"):
    """A claimed, inbox-verified account — what signup actually produces.

    email_verified matters for the parked-grant tests: claim_pending_grant
    only releases days that no order can be attributed to when the email has
    been proven, because an unverified address may have been registered by
    someone who does not control the inbox.
    """
    return store.create(email, "correct-horse-1", email_verified=True)


def test_a_new_account_is_free(users):
    user = make(users)
    assert user.tier == FREE
    assert not user.is_pro
    assert not user.has_coach


def test_pro_grant_unlocks_pro_but_not_coach(users):
    user = make(users)
    users.grant_pro_days(user.id, 31, tier=PRO_TIER)

    refreshed = users.get(user.id)
    assert refreshed.tier == PRO_TIER
    assert refreshed.is_pro
    assert not refreshed.has_coach, "Pro must not unlock the proof cycle"
    assert abs(refreshed.pro_until - (time.time() + 31 * DAY)) < 60


def test_coach_grant_unlocks_both(users):
    user = make(users)
    users.grant_pro_days(user.id, 31, tier=COACH)

    refreshed = users.get(user.id)
    assert refreshed.tier == COACH
    assert refreshed.is_pro, "Coach is a superset of Pro"
    assert refreshed.has_coach


def test_buying_coach_over_pro_upgrades_and_extends(users):
    user = make(users)
    users.grant_pro_days(user.id, 31, tier=PRO_TIER)
    users.grant_pro_days(user.id, 31, tier=COACH)

    refreshed = users.get(user.id)
    assert refreshed.tier == COACH
    assert refreshed.has_coach
    assert abs(refreshed.pro_until - (time.time() + 62 * DAY)) < 60


def test_buying_pro_over_coach_never_downgrades(users):
    """The failure this prevents is a customer paying and losing access.

    Someone holding Coach who buys a cheap month of Pro — or whose Coach
    renewal is processed after a Pro one-off — must keep the tier they are
    still paying for. Taking the maximum is what makes webhook delivery order
    stop mattering.
    """
    user = make(users)
    users.grant_pro_days(user.id, 31, tier=COACH)
    users.grant_pro_days(user.id, 31, tier=PRO_TIER)

    refreshed = users.get(user.id)
    assert refreshed.tier == COACH, "a Pro purchase revoked paid-for Coach"
    assert refreshed.has_coach
    assert abs(refreshed.pro_until - (time.time() + 62 * DAY)) < 60


def test_expiry_falls_all_the_way_to_free(users):
    user = make(users)
    users.grant_pro_days(user.id, 31, tier=COACH)
    users.revoke_pro_days(user.id, 31)

    refreshed = users.get(user.id)
    assert not refreshed.is_pro
    assert not refreshed.has_coach


def test_lapsed_coach_reports_no_entitlement(users):
    """An expired timestamp beats a stored tier, in both directions."""
    user = make(users)
    users.grant_pro_days(user.id, 31, tier=COACH)
    users._conn.execute(
        "UPDATE users SET pro_until = ? WHERE id = ?",
        (time.time() - 1, user.id),
    )
    users._conn.commit()

    refreshed = users.get(user.id)
    assert not refreshed.is_pro
    assert not refreshed.has_coach


def test_parked_purchase_remembers_its_tier(users):
    """A Coach order bought before signup must not arrive as Pro."""
    users.add_pending_grant("buyer@example.com", 365, tier=COACH)
    user = make(users, email="buyer@example.com")

    claimed = users.claim_pending_grant(user.id, user.email)
    assert claimed == 365

    refreshed = users.get(user.id)
    assert refreshed.has_coach, "the parked Coach purchase claimed as Pro"


def test_parked_grants_keep_the_highest_tier(users):
    users.add_pending_grant("buyer@example.com", 31, tier=PRO_TIER)
    users.add_pending_grant("buyer@example.com", 31, tier=COACH)
    users.add_pending_grant("buyer@example.com", 31, tier=PRO_TIER)

    user = make(users, email="buyer@example.com")
    assert users.claim_pending_grant(user.id, user.email) == 93
    assert users.get(user.id).has_coach


def test_the_shipped_config_sells_both_tiers(shipped_config):
    """config.yaml is the source of truth for what each SKU buys."""
    skus = shipped_config.billing["shopify_skus"]
    tiers = shipped_config.billing["shopify_sku_tiers"]

    assert {"SL-COACH-1MO", "SL-COACH-12MO"} <= set(skus)
    assert tiers["SL-PRO-1MO"] == PRO_TIER
    assert tiers["SL-PRO-12MO"] == PRO_TIER
    assert tiers["SL-COACH-1MO"] == COACH
    assert tiers["SL-COACH-12MO"] == COACH
    # The Founders Pass is Coach for life, on the original SKU.
    assert tiers["SL-PRO-LIFE"] == COACH
    # Every tier named must be a real tier, and every tiered SKU must be a
    # SKU that actually grants days — a tier on an unknown SKU is dead
    # config that reads as coverage.
    assert set(tiers) <= set(skus)
    assert set(tiers.values()) <= {PRO_TIER, COACH}


def test_order_tier_takes_the_strongest_line_item(shipped_config):
    from swinglab.web.shopify_billing import _order_tier

    def order(*skus):
        return {"line_items": [{"sku": s, "quantity": 1} for s in skus]}

    assert _order_tier(order("SL-PRO-1MO"), shipped_config) == PRO_TIER
    assert _order_tier(order("SL-COACH-1MO"), shipped_config) == COACH
    assert _order_tier(order("SL-PRO-LIFE"), shipped_config) == COACH
    # A mixed cart grants the days of both and the level of the better one.
    assert _order_tier(
        order("SL-PRO-1MO", "SL-COACH-12MO"), shipped_config
    ) == COACH
    # An order with no membership SKU buys no tier at all. It used to read
    # as Pro — the accumulator started at PRO_TIER and only climbed — so a
    # gear-only order's ledger row stored a tier the order didn't buy, and
    # claim_pending_grant reads that stored tier back for attribution. The
    # grant itself was always gated on days > 0 elsewhere; now the row tells
    # the truth on its own.
    from swinglab.web.users import FREE

    assert _order_tier(order("CI-TEMPO-01"), shipped_config) == FREE
    assert _order_tier(order(), shipped_config) == FREE


def test_bare_code_config_grants_only_pro():
    """A white-label install has no Coach SKUs and cannot reach Coach.

    The tier map is empty by default, so every configured SKU resolves to
    Pro — exactly what every SKU granted before two tiers existed.
    """
    from swinglab.config import Config
    from swinglab.web.shopify_billing import _order_tier

    bare = Config()
    assert bare.billing["shopify_sku_tiers"] == {}
    for sku in bare.billing["shopify_skus"]:
        order = {"line_items": [{"sku": sku, "quantity": 1}]}
        assert _order_tier(order, bare) == PRO_TIER


def test_the_replay_gate_opens_for_coach_and_stays_shut_for_pro(tmp_path):
    """The gate that separates the two tiers, exercised end to end.

    This is the assertion that fails if someone gates the replay on is_pro
    again: Pro would silently include the annotated coach replay, which is
    the single feature the upper tier exists to sell.
    """
    from swinglab.config import Config
    from swinglab.web.jobs import JobManager

    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["replay_pro_only"] = True
    cfg.billing["coach_tier_enabled"] = True  # the ladder, rolled out
    cfg.slowmo["annotated"] = True

    store = UserStore(tmp_path / "users.db")
    manager = JobManager(tmp_path / "sessions", cfg, store)

    free = store.create("free@example.com", "correct-horse-1")
    pro = store.create("pro@example.com", "correct-horse-1")
    coach = store.create("coach@example.com", "correct-horse-1")
    store.grant_pro_days(pro.id, 31, tier=PRO_TIER)
    store.grant_pro_days(coach.id, 31, tier=COACH)

    def state(user):
        return manager._capture_report_entitlements(user.id).coach_replay

    assert state(free) == "locked"
    assert state(pro) == "locked", "Pro must not include the coach replay"
    assert state(coach) == "available"


def test_before_rollout_any_paid_plan_still_opens_the_replay(tmp_path):
    """The compatibility floor, which is what makes this safe to merge.

    With coach_tier_enabled off there is nothing in the store to buy that
    reaches Coach, so gating on it would take the replay away from Pro
    buyers while the pricing page still advertised it. Off, the gate means
    exactly what it meant before two tiers existed.
    """
    from swinglab.config import Config
    from swinglab.web.jobs import JobManager

    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["replay_pro_only"] = True
    cfg.slowmo["annotated"] = True
    assert cfg.billing["coach_tier_enabled"] is False  # the shipped default

    store = UserStore(tmp_path / "users.db")
    manager = JobManager(tmp_path / "sessions", cfg, store)

    free = store.create("free@example.com", "correct-horse-1")
    pro = store.create("pro@example.com", "correct-horse-1")
    store.grant_pro_days(pro.id, 31, tier=PRO_TIER)

    def state(user):
        return manager._capture_report_entitlements(user.id).coach_replay

    assert state(free) == "locked"
    assert state(pro) == "available", "the pre-ladder promise was broken"


def _pricing_html(tmp_path, monkeypatch, coach: bool, name: str) -> str:
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from swinglab.config import Config
    from swinglab.web.app import create_app

    # commerce_enabled() needs BOTH the domain and the webhook secret — a
    # store link without a webhook would take money the app never hears
    # about, so the page correctly refuses to render one.
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "shpss_test_secret")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["replay_pro_only"] = True
    cfg.billing["progress_pro_only"] = True
    cfg.billing["coach_tier_enabled"] = coach
    cfg.billing["shopify_variant_ids"] = {
        "monthly": "1", "yearly": "2", "lifetime": "3",
        "coach_monthly": "4", "coach_yearly": "5",
    }
    cfg.billing["coach_price_monthly_text"] = "$19.99/month"
    cfg.billing["coach_price_annual_text"] = "$139.99/year"
    app = create_app(cfg, sessions_dir=tmp_path / name)
    return TestClient(app).get("/pricing").text


def test_anonymous_visitors_can_reach_checkout(tmp_path, monkeypatch):
    """Cold traffic used to convert at zero.

    Every store-link branch of the CTA macro was gated on `user`, so a
    logged-out visitor from an ad or a shared link fell through to
    "Log in to upgrade" — and that link carried no `next`, so even a
    visitor who did sign in lost the plan they had clicked.
    """
    html = _pricing_html(tmp_path, monkeypatch, coach=False, name="anon")

    assert "Log in to upgrade" not in html
    assert "No account needed to buy" in html
    # ...and the per-plan deep links survive, so checkout opens on the
    # plan that was actually clicked.
    for variant in ("variant=1", "variant=2", "variant=3"):
        assert variant in html


def test_pricing_page_sells_one_tier_until_the_ladder_is_rolled_out(
    tmp_path, monkeypatch
):
    html = _pricing_html(tmp_path, monkeypatch, coach=False, name="pre")

    assert "Coach — monthly" not in html
    assert "$19.99/month" not in html
    # With one paid tier, the replay and dashboard belong to Pro — which is
    # exactly what the gate enforces while the flag is off.
    assert "annotated coach replay" in html
    assert '<th scope="col">Coach</th>' not in html


def test_pricing_page_grows_a_coach_column_once_rolled_out(
    tmp_path, monkeypatch
):
    html = _pricing_html(tmp_path, monkeypatch, coach=True, name="post")

    assert "Coach — monthly" in html
    assert "Coach — yearly" in html
    assert "$19.99/month" in html and "$139.99/year" in html
    assert '<th scope="col">Coach</th>' in html
    assert "variant=4" in html and "variant=5" in html
    assert "Proof cycle" in html
    # The Season Pass must stop advertising the replay it no longer grants.
    assert "Upgrade and re-film with your annotated coach replay" not in html


def test_tier_defaults_to_pro_for_callers_that_do_not_pass_one(users):
    """Back-compatibility seam.

    Bare-code and white-label callers, the CLI, and every existing test
    predate the tier argument. They must keep granting exactly what they
    granted before, which is Pro — never Coach by accident.
    """
    user = make(users)
    users.grant_pro_days(user.id, 31)

    refreshed = users.get(user.id)
    assert refreshed.is_pro
    assert not refreshed.has_coach
