"""Premium hierarchy and truthful allowance copy across secondary pages."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app


TEMPLATES = Path(__file__).resolve().parents[1] / "swinglab" / "templates"


def _clear_integrations(monkeypatch) -> None:
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def _h1_count(html: str) -> int:
    return len(re.findall(r"<h1(?:\s|>)", html))


def test_public_secondary_pages_have_one_real_page_heading(tmp_path, monkeypatch):
    _clear_integrations(monkeypatch)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))

    pages = {
        "/login?password=1": "Sign in with your password",
        "/signup?password=1": "Create your free account",
        "/pricing": "One analysis engine. Two ways to use it.",
        "/drills": "The drill library",
        "/offline": "You’re offline",
    }
    for path, heading in pages.items():
        response = client.get(path)
        assert response.status_code == 200
        assert _h1_count(response.text) == 1
        assert f"<h1>{heading}</h1>" in response.text


def test_private_setup_and_progress_share_heading_and_cache_contracts(
    tmp_path, monkeypatch
):
    _clear_integrations(monkeypatch)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.billing["progress_pro_only"] = False
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    user = app.state.users.create("secondary@example.com", "longenough")
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    onboarding = client.get("/onboarding")
    progress = client.get("/progress")

    for response in (onboarding, progress):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert _h1_count(response.text) == 1
    assert "<h1>Your golfer setup</h1>" in onboarding.text
    assert "<h1>Progress</h1>" in progress.text
    assert "Nothing to chart yet" in progress.text


def test_unlimited_free_copy_is_truthful_on_pricing_and_drills(
    tmp_path, monkeypatch
):
    _clear_integrations(monkeypatch)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 0
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))

    pricing = client.get("/pricing").text
    drills = client.get("/drills").text

    assert "unlimited full reports" in pricing
    assert "Unlimited full swing analyses" in pricing
    assert "unlimited full analyses" in drills
    assert "0 full report" not in pricing
    assert "0 full swing" not in pricing
    assert "0 full analys" not in drills


def test_static_secondary_templates_keep_premium_semantics():
    shop = (TEMPLATES / "web_shop.html.j2").read_text(encoding="utf-8")
    unsubscribed = (TEMPLATES / "web_unsubscribed.html.j2").read_text(
        encoding="utf-8"
    )
    pricing = (TEMPLATES / "web_pricing.html.j2").read_text(encoding="utf-8")
    drills = (TEMPLATES / "web_drills.html.j2").read_text(encoding="utf-8")
    progress = (TEMPLATES / "web_progress.html.j2").read_text(encoding="utf-8")
    onboarding = (TEMPLATES / "web_onboarding.html.j2").read_text(
        encoding="utf-8"
    )

    assert "<h1>Golf gear</h1>" in shop
    assert "<h1>You're unsubscribed</h1>" in unsubscribed
    assert '<thead><tr><th scope="col">Feature</th>' in pricing
    assert pricing.count('scope="row"') >= 5
    assert 'role="region" aria-label="Free and Pro feature comparison"' in pricing
    assert "var(--sl-control-border)" in onboarding
    assert "<h2>New to golf? The four-week Beginner Path</h2>" in drills
    assert "<h2>{{ fam.title }}</h2>" in drills
    assert "<h3>{{ d.name }}</h3>" in drills
    assert "<h2>{{ p.title }}</h2>" in shop
    assert "<h2>{{ c.label }}</h2>" in progress


def test_cached_offline_shell_is_anonymous_while_drills_keep_pro_header(
    tmp_path, monkeypatch
):
    _clear_integrations(monkeypatch)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    user = app.state.users.create(
        "private-shell@example.com", "longenough", email_verified=True
    )
    app.state.users.upsert_golfer_profile(
        user.id,
        display_name="Private Golfer Name",
        experience_mode="improve",
        handicap_range="20_to_29",
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="driver",
        reduced_motion=True,
    )
    app.state.users.set_plan(user.id, "pro", "active")
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    offline = client.get("/offline")
    assert offline.status_code == 200
    assert offline.headers["cache-control"] == "public, max-age=300"
    assert "Private Golfer Name" not in offline.text
    assert "data-pro-member-nav" not in offline.text
    assert 'class="sl-reduced-motion"' not in offline.text

    drills = client.get("/drills")
    assert drills.status_code == 200
    assert drills.headers["cache-control"] == "private, no-store"
    assert "Private Golfer Name" in drills.text
    assert "data-pro-member-nav" in drills.text
    assert 'class="sl-reduced-motion"' in drills.text
    assert "a free account</a> gets" not in drills.text

    personalized = client.get("/progress")
    assert 'class="sl-reduced-motion"' in personalized.text

    worker = client.get("/service-worker.js").text
    assert 'caddieinsight-public-shell-v2' in worker
    assert 'const PUBLIC_SHELL = ["/offline"];' in worker
    assert "private|no-store" in worker
    assert "key !== CACHE_NAME" in worker
