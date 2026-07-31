"""Accounts, monthly quotas, session ownership, and billing plumbing.

Stripe itself is never called: checkout/portal redirect out to Stripe's
hosted pages, and plan changes arrive as webhook events — so the tests drive
billing.apply_event directly with event payloads shaped like Stripe's.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import billing
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.users import PRO, UserStore

from tests.test_web import fake_analyze_ok, wait_for


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 2
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(client, email="kyle@example.com", password="longenough"):
    resp = client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return resp


def upload(client, filename="swing.mov"):
    resp = client.post(
        "/upload",
        files={"video": (filename, b"fake video bytes", "video/quicktime")},
        follow_redirects=False,
    )
    return resp


def test_logged_out_visitors_see_landing_and_cannot_analyze(app):
    client = TestClient(app)
    html = client.get("/").text
    assert "Create a free account" in html and "Sign in" in html
    assert "2 full analyses every month" in html
    assert "Automated estimates from a single camera" in html
    assert upload(client).status_code == 401
    assert client.get("/sessions", follow_redirects=False).status_code == 303
    assert client.get("/api/sessions").status_code == 401


def test_header_connects_store_app_and_account_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "example.myshopify.com")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shop["store_url"] = "https://caddieinsight.com"
    cfg.brand["logo_url"] = "https://cdn.example.test/caddieinsight-logo.png"
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))

    page = client.get("/login").text
    assert 'href="https://caddieinsight.com"' in page
    assert 'aria-label="CaddieInsight home"' in page
    assert 'href="/signup"' in page
    assert 'href="/shop"' in page and 'href="/pricing"' in page
    assert 'href="https://caddieinsight.com/cart"' in page
    assert 'href="https://caddieinsight.com/account"' in page
    assert "Orders &amp; subscriptions" in page
    assert 'src="https://cdn.example.test/caddieinsight-logo.png"' in page
    assert "Analyze a swing" in page
    assert "App sign in" not in page
    assert "Start free" not in page
    assert ">Analyze</a>" not in page
    assert 'aria-controls="sl-mobile-menu"' in page
    assert 'aria-haspopup="dialog"' in page
    assert '<dialog class="sl-menu"' in page

    signup(client)
    signed_in_page = client.get("/").text
    assert 'href="/sessions"' in signed_in_page
    assert 'href="/account"' in signed_in_page
    assert "CaddieInsight profile" in signed_in_page
    assert "Analyze a swing" in signed_in_page


def test_open_mode_keeps_public_history_navigation(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = False
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))

    page = client.get("/").text
    assert 'href="/sessions"' in page
    assert 'href="/progress"' not in page
    assert 'href="/account"' not in page
    assert 'href="/login"' not in page
    assert 'href="/signup"' not in page
    assert "<span>Sign in</span>" not in page
    assert "<span>Create free account</span>" not in page
    assert "Analyze a swing" in page


def test_free_account_landing_uses_configured_brand_and_allowance(tmp_path):
    cfg = Config()
    cfg.brand["name"] = "AceCoach"
    cfg.brand["footer_text"] = "AceCoach swing analysis."
    cfg.billing["free_per_month"] = 7
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))

    page = client.get("/").text
    assert "Create a free AceCoach account for 7 full analyses" in page
    assert "CaddieInsight" not in page


def test_signup_login_logout_flow(app):
    client = TestClient(app)
    signup(client)
    assert "Analyze your swing" in client.get("/").text

    client.post("/logout")
    assert "Create a free account" in client.get("/").text

    bad = client.post(
        "/login", data={"email": "kyle@example.com", "password": "wrongwrong"}
    )
    assert "Wrong email or password" in bad.text

    client.post(
        "/login", data={"email": "KYLE@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert "Analyze your swing" in client.get("/").text  # email case-insensitive


def test_bad_signups_rejected(app):
    client = TestClient(app)
    signup(client, email="a@b.co")
    client.post("/logout")
    dup = client.post(
        "/signup", data={"email": "a@b.co", "password": "longenough"}
    )
    assert "already exists" in dup.text
    short = client.post(
        "/signup", data={"email": "c@d.co", "password": "short"}
    )
    assert "at least 8 characters" in short.text
    notmail = client.post(
        "/signup", data={"email": "not-an-email", "password": "longenough"}
    )
    assert "look like an email" in notmail.text


def test_free_quota_enforced_and_pro_unlimited(app):
    client = TestClient(app)
    signup(client)
    for _ in range(2):  # free_per_month = 2
        resp = upload(client)
        assert resp.status_code == 303
        wait_for(client, resp.headers["location"].rsplit("/", 1)[-1])

    blocked = upload(client)
    assert blocked.status_code == 402
    assert "Upgrade" in blocked.json()["detail"]
    assert "used this month" in client.get("/").text

    users: UserStore = client.app.state.users
    user = users.get_by_email("kyle@example.com")
    users.set_plan(user.id, PRO, "active")
    assert upload(client).status_code == 303  # unlimited now


def test_sessions_are_private_to_their_owner(app):
    alice, bob = TestClient(app), TestClient(app)
    signup(alice, email="alice@example.com")
    signup(bob, email="bob@example.com")

    resp = upload(alice)
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(alice, job_id)

    assert alice.get(f"/session/{job_id}").status_code == 200
    assert bob.get(f"/session/{job_id}").status_code == 404
    assert bob.get(f"/api/session/{job_id}").status_code == 404
    assert bob.get(f"/session/{job_id}/files/out/source/report.html").status_code == 404

    assert [s["id"] for s in alice.get("/api/sessions").json()["sessions"]] == [job_id]
    assert bob.get("/api/sessions").json()["sessions"] == []


def test_ownerless_jobs_stay_reachable_by_link(app):
    """Sessions from before accounts (user_id NULL) keep working via URL."""
    client = TestClient(app)
    job = client.app.state.jobs.create_session(source_name="old.mov")
    assert client.get(f"/session/{job.id}").status_code == 200


def test_account_page_shows_usage(app):
    client = TestClient(app)
    signup(client)
    resp = upload(client)
    wait_for(client, resp.headers["location"].rsplit("/", 1)[-1])
    html = client.get("/account").text
    assert "kyle@example.com" in html
    assert "1 left" in html  # 2/month, 1 used
    assert "Free" in html


def test_stripe_events_flip_plan_state(tmp_path):
    users = UserStore(tmp_path / "db.sqlite")
    user = users.create("pro@example.com", "longenough")

    billing.apply_event(
        {
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": user.id, "customer": "cus_1"}},
        },
        users,
    )
    user = users.get(user.id)
    assert user.is_pro and user.stripe_customer_id == "cus_1"

    billing.apply_event(
        {
            "type": "customer.subscription.updated",
            "data": {"object": {"customer": "cus_1", "status": "past_due"}},
        },
        users,
    )
    assert users.get(user.id).is_pro  # grace period while Stripe retries

    billing.apply_event(
        {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_1"}},
        },
        users,
    )
    refreshed = users.get(user.id)
    assert not refreshed.is_pro and refreshed.plan == "free"


def test_checkout_unavailable_until_stripe_configured(app, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    client = TestClient(app)
    signup(client)
    assert client.post("/billing/checkout", follow_redirects=False).status_code == 503
    assert "coming soon" in client.get("/account").text
