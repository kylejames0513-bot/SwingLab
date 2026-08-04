"""Guided account completion and personalized profile integration."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app

from tests.test_web import fake_analyze_ok


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
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
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def profile_form(**overrides):
    payload = {
        "display_name": "  Kyle   James  ",
        "experience_mode": "improve",
        "handicap_range": "",
        "primary_goal": "consistency",
        "practice_minutes": "20",
        "sessions_per_week": "2",
        "handedness": "right",
        "camera_angle": "face-on",
        "preferred_club": "iron",
    }
    payload.update(overrides)
    return payload


def test_password_signup_creates_claimed_profile_shell_and_guides_completion(app):
    client = TestClient(app)

    signup = client.post(
        "/signup",
        data={"email": "profile@example.com", "password": "longenough"},
        follow_redirects=False,
    )

    assert signup.status_code == 303
    assert signup.headers["location"] == "/onboarding?welcome=1"
    user = app.state.users.get_by_email("profile@example.com")
    assert user is not None
    shell = app.state.users.get_golfer_profile(user.id)
    assert shell is not None
    assert shell.display_name is None
    assert shell.is_complete is False

    welcome = client.get(signup.headers["location"])
    assert welcome.status_code == 200
    assert "Create your golfer profile" in welcome.text
    assert "What should your caddie call you?" in welcome.text
    assert 'value="None"' not in welcome.text
    assert "Add a backup password" not in welcome.text
    assert 'name="preferred_club"' in welcome.text
    assert 'aria-describedby="club-help" required' in welcome.text
    assert '<option value="" disabled selected>Choose a club</option>' in welcome.text
    assert welcome.headers["cache-control"] == "private, no-store"

    missing_name = client.post(
        "/onboarding", data=profile_form(display_name="")
    )
    assert missing_name.status_code == 200
    assert 'class="error"' in missing_name.text
    assert "name" in missing_name.text.lower()

    missing_goal = client.post(
        "/onboarding", data=profile_form(primary_goal="")
    )
    assert missing_goal.status_code == 200
    assert "Choose a main goal" in missing_goal.text

    missing_club = client.post(
        "/onboarding", data=profile_form(preferred_club="")
    )
    assert missing_club.status_code == 200
    assert "Choose a club" in missing_club.text

    saved = client.post(
        "/onboarding", data=profile_form(), follow_redirects=False
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/today?setup_saved"
    profile = app.state.users.get_golfer_profile(user.id)
    assert profile is not None
    assert profile.display_name == "Kyle James"
    assert profile.handicap_range is None
    assert profile.primary_goal == "consistency"
    assert profile.preferred_club == "iron"
    assert profile.is_complete is True
    assert "Kyle James" in client.get("/account").text


def test_onboarding_offers_skip_to_filming_and_weekly_digest_opt_in(app):
    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "skip@example.com", "password": "longenough"},
    )
    user = app.state.users.get_by_email("skip@example.com")
    assert user is not None
    assert user.digest_opt_in is False

    welcome = client.get("/onboarding?welcome=1")
    assert "Skip for now — film your first swing" in welcome.text
    assert 'href="/#upload-form"' in welcome.text
    assert 'name="digest"' in welcome.text
    assert 'name="digest" checked' not in welcome.text

    saved = client.post(
        "/onboarding", data=profile_form(digest="on"), follow_redirects=False
    )
    assert saved.status_code == 303
    assert app.state.users.get(user.id).digest_opt_in is True

    # A complete profile makes onboarding an edit page: no skip link, the
    # checkbox reflects the saved consent, and unchecking is a real opt-out
    # through the same path the account page uses.
    edit = client.get("/onboarding")
    assert "Skip for now" not in edit.text
    assert 'name="digest" checked' in edit.text
    client.post("/onboarding", data=profile_form())
    assert app.state.users.get(user.id).digest_opt_in is False


def test_today_setup_state_lets_the_user_film_before_finishing_setup(app):
    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "film-first@example.com", "password": "longenough"},
    )

    today = client.get("/today")

    assert today.status_code == 200
    assert 'data-today-state="setup"' in today.text
    assert 'data-primary-next-move href="/#upload-form"' in today.text
    assert "Film your first swing" in today.text
    assert 'href="/onboarding"' in today.text


def test_existing_incomplete_account_gets_banner_without_forced_redirect(app):
    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "legacy@example.com", "password": "longenough"},
    )

    home = client.get("/")

    assert home.status_code == 200
    assert "Make CaddieInsight yours" in home.text
    assert 'href="/onboarding">Finish golfer profile</a>' in home.text


def test_v1_profile_update_remains_compatible_without_display_name(app):
    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "mobile-v1@example.com", "password": "longenough"},
    )
    legacy_v1_payload = {
        "experience_mode": "improve",
        "handicap_range": "20_to_29",
        "primary_goal": "",
        "practice_minutes": 20,
        "sessions_per_week": 2,
        "handedness": "right",
        "camera_angle": "face-on",
        "preferred_club": "iron",
        "reduced_motion": False,
        "marketing_email_opt_in": False,
    }

    response = client.put("/api/v1/profile", json=legacy_v1_payload)

    assert response.status_code == 200
    assert response.json()["profile"]["display_name"] is None
    assert response.json()["profile"]["primary_goal"] is None
    assert response.json()["profile"]["is_complete"] is False


def test_display_name_is_escaped_in_attributes_member_header_and_account(app):
    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "escape@example.com", "password": "longenough"},
    )
    payload = '\"><img src=x onerror=alert(1)>'
    saved = client.post(
        "/onboarding",
        data=profile_form(display_name=payload),
        follow_redirects=False,
    )
    assert saved.status_code == 303
    user = app.state.users.get_by_email("escape@example.com")
    assert user is not None
    app.state.users.set_plan(user.id, "pro", "active")

    for path in ("/account", "/onboarding", "/today"):
        html = client.get(path).text
        assert payload not in html
        assert "<img src=x onerror=alert(1)>" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html
