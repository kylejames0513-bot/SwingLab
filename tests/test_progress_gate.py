"""The progress-dashboard Pro gate (billing.progress_pro_only) — the same
shape as the coach-replay gate: shipped on, bare default off, never gated
for open instances, and the locked teaser leaks no trend data."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.users import UserStore

from tests.test_trends import (
    make_fake_analyze,
    payload_for,
    signup,
    upload_and_wait,
)

PAYLOADS = [
    payload_for([{"tempo_ratio": 2.2}]),
    payload_for([{"tempo_ratio": 2.7}]),
]


def make_app(tmp_path, monkeypatch, gate=True):
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(PAYLOADS)
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 0  # quota out of the way
    cfg.billing["progress_pro_only"] = gate
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def grant_pro(client, email="kyle@example.com"):
    users: UserStore = client.app.state.users
    users.grant_pro_days(users.get_by_email(email).id, 31)


def test_free_account_sees_teaser_and_no_trend_data(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch))
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)

    html = client.get("/progress").text
    assert "Included with Pro" in html
    assert "See Pro plans" in html
    # Two real sessions exist, but the locked view computes and shows
    # nothing from them.
    assert "2.20:1" not in html and "2.70:1" not in html
    assert "Latest" not in html


def test_pro_account_gets_the_dashboard(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch))
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)
    grant_pro(client)

    html = client.get("/progress").text
    assert "Included with Pro" not in html
    assert "2.70:1" in html  # the latest session's tempo, charted


def test_gate_off_keeps_the_dashboard_free(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch, gate=False))
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)

    html = client.get("/progress").text
    assert "Included with Pro" not in html
    assert "2.70:1" in html


def test_nav_shows_the_lock_to_free_users_only(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch))
    signup(client)
    # The lock marker is advertising: visible to free accounts...
    assert 'title="Included with Pro"' in client.get("/").text
    # ...and gone once the account is Pro.
    grant_pro(client)
    assert 'title="Included with Pro"' not in client.get("/").text


def test_gate_needs_accounts_progress_stays_404_without_them(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(PAYLOADS)
    )
    cfg = Config()  # open instance: require_account off
    cfg.billing["progress_pro_only"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    # Same rule as before the gate existed: no accounts, no /progress.
    assert client.get("/progress").status_code == 404


def test_digest_links_free_users_to_sessions_not_the_lock(
    tmp_path, monkeypatch
):
    from swinglab.web.digest import compose_digest
    from swinglab.web.users import UserStore

    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    upload_and_wait(client)

    users: UserStore = app.state.users
    user = users.get_by_email("kyle@example.com")
    jobs = app.state.jobs.list_recent(user_id=user.id)
    cfg = app.state.cfg if hasattr(app.state, "cfg") else None
    # compose against the same gated config the app runs
    from swinglab.config import Config as _C
    gated_cfg = _C()
    gated_cfg.web["require_account"] = True
    gated_cfg.billing["progress_pro_only"] = True

    subject, html = compose_digest(
        user, gated_cfg, jobs, base_url="https://app.example", secret="s"
    )
    assert "Your sessions" in html and "/sessions" in html
    assert "Your progress" not in html

    users.grant_pro_days(user.id, 31)
    pro_user = users.get_by_email("kyle@example.com")
    subject, html = compose_digest(
        pro_user, gated_cfg, jobs, base_url="https://app.example", secret="s"
    )
    assert "Your progress" in html and "/progress" in html
