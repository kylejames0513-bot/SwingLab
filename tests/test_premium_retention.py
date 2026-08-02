"""Premium retention surfaces preserve privacy and destructive-action gates."""

from __future__ import annotations

import re
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.report import REPORT_FORMAT_VERSION, REPORT_OUTCOME_CAPTURE
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE, FAILED

from tests.test_web import fake_analyze_ok


def make_app(tmp_path, monkeypatch, *, history_reset: bool = True):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "STRIPE_SECRET_KEY",
        "STRIPE_PRICE_ID",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
        "SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED",
        "SHOPIFY_CUSTOMER_ACCOUNT_STOREFRONT_DOMAIN",
        "SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID",
        "SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.web["history_reset_enabled"] = history_reset
    cfg.billing["free_per_month"] = 2
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def login_member(
    app,
    *,
    email: str = "member@example.com",
    display_name: str = "Avery",
    pro: bool = False,
):
    users = app.state.users
    user = users.create(email, "longenough", email_verified=True)
    users.upsert_golfer_profile(
        user.id,
        display_name=display_name,
        experience_mode="improve",
        handicap_range="20_to_29",
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="driver",
    )
    if pro:
        users.set_plan(user.id, "pro", "active")
    client = TestClient(app)
    response = client.post(
        "/login",
        data={"email": email, "password": "longenough"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client, user


def save_session(
    app,
    user,
    *,
    source_name: str,
    club: str = "driver",
    status: str = DONE,
    refilm: bool = False,
):
    manager = app.state.jobs
    job = manager.create_session(
        source_name=source_name,
        club=club,
        user_id=user.id,
        client_ip="198.51.100.12",
    )
    job.status = status
    job.swings_total = 2 if status == DONE else 0
    if status == DONE:
        result_dir = job.session_dir / "out"
        result_dir.mkdir(parents=True, exist_ok=True)
        report = result_dir / "report.html"
        if refilm:
            report.write_text(
                "<html><head>"
                f'<meta name="caddieinsight-report-format" content="{REPORT_FORMAT_VERSION}">'
                f'<meta name="caddieinsight-report-outcome" content="{REPORT_OUTCOME_CAPTURE}">'
                "</head><body>capture guidance</body></html>",
                encoding="utf-8",
            )
        else:
            report.write_text(
                "<html><body>preserved coaching report</body></html>",
                encoding="utf-8",
            )
        job.report_rel = "out/report.html"
    manager._save(job)
    return job


def test_history_uses_one_responsive_semantic_table_and_clear_result_actions(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    client, owner = login_member(app)
    private_name = "range <private>.mov"
    coaching = save_session(app, owner, source_name=private_name)
    refilm = save_session(
        app, owner, source_name="camera-check.mov", club="iron", refilm=True
    )
    failed = save_session(
        app, owner, source_name="retry.mov", status=FAILED
    )
    _, stranger = login_member(
        app, email="stranger@example.com", display_name="Stranger"
    )
    foreign = save_session(app, stranger, source_name="foreign-secret.mov")

    page = client.get("/sessions")
    html = page.text

    assert page.status_code == 200
    assert page.headers["cache-control"] == "private, no-store"
    assert html.count('<h1 id="history-title">Session history</h1>') == 1
    assert html.count('<table class="history-table">') == 1
    assert "<thead>" in html and "<tbody>" in html
    assert '@media (max-width: 800px)' in html
    assert ".history-table td::before" in html
    assert private_name not in html
    assert html.count("range &lt;private&gt;.mov") == 1
    assert "Driver" in html and "Iron" in html
    assert "Coaching ready" in html
    assert "Re-film needed" in html
    assert "Needs attention" in html
    assert f'href="/session/{coaching.id}/report"' in html
    assert f'href="/session/{refilm.id}"' in html
    assert 'href="/#upload-form">Re-film</a>' in html
    assert failed.id in html
    assert foreign.id not in html
    assert "foreign-secret.mov" not in html


def test_empty_history_is_a_real_next_step_without_an_empty_table(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    client, _ = login_member(app, email="empty@example.com")

    html = client.get("/sessions").text

    assert '<h1 id="history-title">Session history</h1>' in html
    assert "Your first result starts here" in html
    assert 'href="/#upload-form">Analyze your first swing</a>' in html
    assert '<table class="history-table">' not in html


def test_account_sections_free_and_pro_states_and_escaped_profile(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    payload = '<svg onload="alert(1)">'
    client, user = login_member(
        app,
        email="account@example.com",
        display_name=payload,
    )

    free_page = client.get("/account")
    free_html = free_page.text

    assert free_page.headers["cache-control"] == "private, no-store"
    assert free_html.count('<h1 id="account-title">Account</h1>') == 1
    for section in (
        "Profile",
        "Membership &amp; allowance",
        "Sign-in &amp; security",
        "Preferences",
        "Swing history controls",
    ):
        assert section in free_html
    assert "Free plan" in free_html
    assert "Allowance used this month" in free_html
    assert "2 left" in free_html
    assert payload not in free_html
    assert '&lt;svg onload=&#34;alert(1)&#34;&gt;' in free_html
    assert 'id="shopify-heading"' not in free_html
    assert 'action="/account/digest"' in free_html
    assert 'name="enabled" value="on"' in free_html
    assert 'action="/logout"' in free_html

    app.state.users.set_plan(user.id, "pro", "active")
    pro_html = client.get("/account").text

    assert "Pro member" in pro_html
    assert "Your Pro access is active." in pro_html
    assert "Upgrade to Pro" not in pro_html


def test_data_controls_and_shopify_card_render_only_with_live_context(
    tmp_path, monkeypatch
):
    disabled_app = make_app(
        tmp_path / "disabled", monkeypatch, history_reset=False
    )
    disabled_client, _ = login_member(
        disabled_app, email="disabled@example.com"
    )
    disabled = disabled_client.get("/account")

    assert "Swing history controls" not in disabled.text
    assert "/account/history/delete" not in disabled.text
    assert disabled_client.get("/account/history/delete").status_code == 404
    assert 'id="shopify-heading"' not in disabled.text

    linked_app = make_app(tmp_path / "linked", monkeypatch)
    linked_client, linked_user = login_member(
        linked_app, email="linked@example.com"
    )
    linked = linked_app.state.users.upsert_store_customer(
        linked_user.email, "7001", updated_at=time.time()
    )
    assert linked is not None and linked.id == linked_user.id

    linked_html = linked_client.get("/account").text

    assert 'id="shopify-heading"' in linked_html
    assert "Connected to the CaddieInsight store" in linked_html
    assert "no store password is copied here" in linked_html
    assert "Shopify sign-in is connected" not in linked_html


def test_delete_history_keeps_exact_security_fields_and_scope_disclosures(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    client, _ = login_member(app, email="delete@example.com")

    page = client.get("/account/history/delete")
    html = page.text

    assert page.status_code == 200
    assert "no-store" in page.headers["cache-control"]
    assert html.count(
        '<h1 id="reset-title">Delete swing history and start over</h1>'
    ) == 1
    assert "uploaded swing sessions, generated reports, practice check-ins, and Proof Cycle evidence" in html
    assert "It cannot be undone." in html
    assert "Your account and golfer profile" in html
    assert "Your Free or Pro membership and purchases" in html
    assert "Shopify connection and connected-device sign-ins" in html
    assert "This month's analysis allowance and security records" in html
    assert 'action="/account/history/delete" method="post"' in html
    assert re.search(r'name="nonce" value="[A-Za-z0-9_-]{32,}"', html)
    assert '<code class="reset-code">START OVER</code>' in html
    assert 'name="confirmation" type="text" required autocomplete="off"' in html
    assert 'name="password" type="password" required autocomplete="current-password"' in html
    assert 'name="confirmation" type="text" value="START OVER"' not in html
    assert ">Delete swing history</button>" in html
    assert 'href="/account">Cancel</a>' in html
