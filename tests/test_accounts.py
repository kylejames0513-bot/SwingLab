"""Accounts, monthly quotas, session ownership, and billing plumbing.

Stripe itself is never called: checkout/portal redirect out to Stripe's
hosted pages, and plan changes arrive as webhook events — so the tests drive
the Shopify order path (tests/test_shopify_billing.py owns its webhooks).
"""

from __future__ import annotations

import json
import re

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.report_bundle import CoreReportBundleError
from swinglab.report_view import GUIDED_REPORT_PRESENTATION_VERSION
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import FAILED, JobManager
from swinglab.web.users import PRO, UserStore

from tests.report_bundle_fixtures import write_test_report_html
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
        data={"club": "iron"},
        follow_redirects=False,
    )
    return resp


def test_logged_out_visitors_see_landing_and_cannot_analyze(app):
    client = TestClient(app)
    html = client.get("/").text
    assert "Analyze a swing free" in html and "Sign in" in html
    assert "2 reports / month" in html
    assert "Supported 2D movement and timing estimates from phone video" in html
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
    assert "Analyze free" in page
    assert "App sign in" not in page
    assert 'aria-label="Membership status"' in page
    assert "Start free" in page
    assert ">Analyze</a>" not in page
    assert 'aria-controls="sl-mobile-menu"' in page
    assert 'aria-haspopup="dialog"' in page
    assert '<dialog class="sl-menu"' in page
    signed_out_shell = page.split("</dialog>", 1)[0]
    assert 'href="/sessions"' not in signed_out_shell
    assert 'href="/today"' not in signed_out_shell
    assert 'href="/progress"' not in signed_out_shell
    assert 'action="/logout"' not in signed_out_shell
    signed_out_footer = page.split('<footer class="sl-app-footer">', 1)[1]
    assert 'href="/sessions"' not in signed_out_footer
    assert 'href="/account"' not in signed_out_footer

    signup(client)
    signed_in_page = client.get("/").text
    assert 'href="/sessions"' in signed_in_page
    assert 'href="/account"' in signed_in_page
    assert "CaddieInsight profile" in signed_in_page
    assert "Analyze a swing" in signed_in_page
    assert signed_in_page.count('action="/logout" method="post"') == 2
    assert "Create free account" not in signed_in_page


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
    assert "7 reports / month" in page
    assert "AceCoach example analysis" in page
    assert "CaddieInsight" not in page


def test_signup_login_logout_flow(app):
    client = TestClient(app)
    signup(client)
    assert "Analyze your swing" in client.get("/").text

    client.post("/logout")
    assert "Analyze a swing free" in client.get("/").text

    bad = client.post(
        "/login", data={"email": "kyle@example.com", "password": "wrongwrong"}
    )
    assert "Wrong email or password" in bad.text

    client.post(
        "/login", data={"email": "KYLE@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert "Analyze your swing" in client.get("/").text  # email case-insensitive


def test_password_login_rejects_cross_origin_form_post(app):
    client = TestClient(app)
    signup(client)
    client.post("/logout")

    rejected = client.post(
        "/login",
        data={"email": "kyle@example.com", "password": "longenough"},
        headers={"Origin": "https://attacker.example"},
        follow_redirects=False,
    )
    assert rejected.status_code == 403
    assert client.get("/account", follow_redirects=False).status_code == 303

    allowed = client.post(
        "/login",
        data={"email": "kyle@example.com", "password": "longenough"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert allowed.status_code == 303


@pytest.mark.parametrize(
    ("path", "data"),
    (
        ("/logout", {}),
        ("/account/digest", {"enabled": "on"}),
    ),
)
def test_session_mutating_forms_reject_cross_origin(app, path, data):
    client = TestClient(app)
    signup(client)
    users: UserStore = app.state.users
    user = users.get_by_email("kyle@example.com")
    assert user is not None and not user.digest_opt_in

    rejected = client.post(
        path,
        data=data,
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )

    assert rejected.status_code == 403
    assert client.get("/account").status_code == 200
    assert not users.get(user.id).digest_opt_in
def test_first_analysis_is_framed_as_a_fast_baseline(app):
    client = TestClient(app)
    signup(client)

    first = client.get("/").text
    assert "Build your swing baseline" in first
    assert re.search(r'id="fast"[^>]*checked', first)

    resp = upload(client)
    wait_for(client, resp.headers["location"].rsplit("/", 1)[-1])

    later = client.get("/").text
    assert "Your next coaching check-in" in later
    assert not re.search(r'id="fast"[^>]*checked', later)


def test_refilm_result_preserves_free_retry_and_first_baseline(tmp_path, monkeypatch):
    attempts = 0
    warning = (
        "Low confidence: this clip looks like it was filmed down the line, "
        "but it was uploaded as face-on — numbers may not mean what they say."
    )

    def first_unreliable_then_valid(video_path, **kwargs):
        nonlocal attempts
        attempts += 1
        result = fake_analyze_ok(video_path, **kwargs)
        if attempts == 1:
            result.metrics_path.write_text(
                json.dumps(
                    {
                        "meta": {"camera_angle": "face-on"},
                        "session_notes": [warning],
                        "swings": [{"metrics": {"tempo_ratio": 2.0}}],
                    }
                ),
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(
        jobs_module, "analyze_video", first_unreliable_then_valid
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 1
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    app.state.users.create("kyle@example.com", "longenough")
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    first = upload(client)
    first_id = first.headers["location"].rsplit("/", 1)[-1]
    assert wait_for(client, first_id)["status"] == "done"
    first_status = client.get(f"/session/{first_id}").text
    assert "Re-film before coaching" in first_status
    assert "Every later upload uses the normal allowance" in first_status
    assert client.app.state.jobs.usage_this_month(
        client.app.state.users.get_by_email("kyle@example.com").id
    ) == 0
    first_home = client.get("/").text
    assert "Build your swing baseline" in first_home
    assert "Your next upload uses the normal allowance" in first_home

    retry = upload(client)
    assert retry.status_code == 303
    retry_id = retry.headers["location"].rsplit("/", 1)[-1]
    assert wait_for(client, retry_id)["status"] == "done"
    assert client.app.state.jobs.usage_this_month(
        client.app.state.users.get_by_email("kyle@example.com").id
    ) == 1
    assert "Your next coaching check-in" in client.get("/").text


def test_guided_core_failure_releases_the_single_active_allowance(
    tmp_path, monkeypatch,
):
    users = UserStore(tmp_path / "users.db")
    user = users.create("guided@example.com", "longenough")
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        user_store=users,
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        user_id=user.id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")

    def fail_core(*args, **kwargs):
        assert manager.usage_this_month(user.id) == 1
        raise CoreReportBundleError("guided core failed")

    monkeypatch.setattr(jobs_module, "analyze_video", fail_core)
    assert manager.usage_this_month(user.id) == 1

    manager._run(job, source)

    assert manager.get(job.id).status == FAILED
    assert manager.usage_this_month(user.id) == 0


def test_repeated_refilm_results_eventually_use_the_allowance(
    tmp_path, monkeypatch
):
    warning = (
        "Tracking was unstable for this swing — numbers may be off; "
        "film with a clear view."
    )

    def always_unreliable(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.metrics_path.write_text(
            json.dumps(
                {
                    "swings": [
                        {
                            "metrics": {"tempo_ratio": 2.0},
                            "notes": [warning],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(jobs_module, "analyze_video", always_unreliable)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 1
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
    signup(client)
    user = client.app.state.users.get_by_email("kyle@example.com")

    first = upload(client)
    wait_for(client, first.headers["location"].rsplit("/", 1)[-1])
    assert client.app.state.jobs.usage_this_month(user.id) == 0

    courtesy_retry = upload(client)
    assert courtesy_retry.status_code == 303
    wait_for(
        client, courtesy_retry.headers["location"].rsplit("/", 1)[-1]
    )
    assert client.app.state.jobs.usage_this_month(user.id) == 1

    blocked = upload(client)
    assert blocked.status_code == 402
    home = client.get("/").text
    assert "Your baseline still needs a clear clip" in home
    assert "first rejected clip did not use an analysis" in home
    assert "Build your swing baseline" not in home


def test_charged_refilm_is_explained_before_allowance_is_empty(
    tmp_path, monkeypatch
):
    warning = (
        "Tracking was unstable for this swing — numbers may be off; "
        "film with a clear view."
    )

    def always_unreliable(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.metrics_path.write_text(
            json.dumps(
                {
                    "swings": [
                        {
                            "metrics": {"tempo_ratio": 2.0},
                            "notes": [warning],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(jobs_module, "analyze_video", always_unreliable)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 3
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
    signup(client)

    for _ in range(2):
        response = upload(client)
        wait_for(client, response.headers["location"].rsplit("/", 1)[-1])

    home = client.get("/").text
    assert "Your baseline still needs a clear clip" in home
    assert "2 analyses left" in home
    assert "1 additional unusable" in home
    assert "first rejected clip did" in home


def test_finite_pro_is_not_upsold_after_rejected_clips(
    tmp_path, monkeypatch
):
    warning = (
        "Tracking was unstable for this swing — numbers may be off; "
        "film with a clear view."
    )

    def always_unreliable(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.metrics_path.write_text(
            json.dumps(
                {
                    "swings": [
                        {
                            "metrics": {"tempo_ratio": 2.0},
                            "notes": [warning],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(jobs_module, "analyze_video", always_unreliable)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["pro_per_month"] = 1
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    users: UserStore = app.state.users
    users.create("kyle@example.com", "longenough")
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303
    users: UserStore = client.app.state.users
    user = users.get_by_email("kyle@example.com")
    users.set_plan(user.id, PRO, "active")

    for _ in range(2):
        response = upload(client)
        wait_for(client, response.headers["location"].rsplit("/", 1)[-1])

    home = client.get("/").text
    assert "Your baseline still needs a clear clip" in home
    assert "see Pro options" not in home
    assert "try again after the reset on the 1st" in home


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


def test_shopify_only_pro_purchase_is_offered_at_both_quota_states(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "test-secret")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 1
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    app.state.users.create("kyle@example.com", "longenough")
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    available = client.get("/").text
    assert "go unlimited with Pro" in available

    resp = upload(client)
    wait_for(client, resp.headers["location"].rsplit("/", 1)[-1])
    exhausted = client.get("/").text
    assert "Upgrade to Pro" in exhausted
    assert 'href="/pricing"' in exhausted


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
    assert "Allowance used this month" in html
    assert "1 left" in html  # 2/month, 1 used
    assert "Free" in html


def test_the_stripe_purchase_path_is_gone(tmp_path):
    """Purchases go through the Shopify store, and only the store.

    Owner decision, 2026-08-10. The dormant Stripe path was removed rather
    than kept configured-off: a second payment path is a second place for
    money to go wrong, and the one time it half-activated it would have
    charged cards and granted nothing. These routes must not exist — a 404
    here is the feature.
    """
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )
    for path in ("/billing/checkout", "/billing/portal", "/webhooks/stripe"):
        assert client.post(path).status_code in (404, 405), path


def test_pro_reads_coming_soon_without_the_store(app):
    """No store configured means nothing to sell — deliberately."""
    client = TestClient(app)
    signup(client)
    assert "coming soon" in client.get("/account").text


def test_store_pricing_keeps_the_annual_led_offer(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "test-secret")
    cfg = Config()
    cfg.web["require_account"] = True
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    # The Shopify pair switches /signup to inbox-proof semantics (503 without
    # a mailer) — create the account directly and log in.
    app.state.users.create("kyle@example.com", "longenough")
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    html = client.get("/pricing").text
    # Every buy control is a store link; no on-app checkout form exists.
    assert 'action="/billing/checkout"' not in html
    assert html.count("on the CaddieInsight store") >= 2
    assert "Pro — Season Pass" in html
    assert "Pro — monthly" in html
    assert html.index("Pro — Season Pass") < html.index("Pro — monthly")
    assert "$69.99/year" in html
    assert "The store shows the available terms" in html


def test_finite_pro_allowance_is_described_as_finite(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "test-secret")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["pro_per_month"] = 12
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    # The Shopify pair switches /signup to inbox-proof semantics (503 without
    # a mailer) — create the account directly and log in.
    app.state.users.create("kyle@example.com", "longenough")
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    html = client.get("/pricing").text
    assert "Up to 12 analyses a month" in " ".join(html.split())
    assert ">12</td>" in html
    assert "Unlimited swing analyses" not in html
    assert "Coaching-ready clip" in html
    assert "for every session" not in html
