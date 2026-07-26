"""Auth throttling: sliding-window limits on /login (per IP and per email)
and /signup (per IP). Generic "wait" message, no permanent lockout — the
window expires by itself — and the JSON API's behavior is untouched."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.throttle import Throttle
from tests.test_web import fake_analyze_ok


# -- unit: the sliding window ------------------------------------------------

def test_window_slides_and_expires(tmp_path):
    t = Throttle(tmp_path / "t.db")
    for i in range(3):
        assert t.allow("login-ip", "1.2.3.4", limit=3, window_s=60, now=100 + i)
        t.record("login-ip", "1.2.3.4", now=100 + i)
    assert not t.allow("login-ip", "1.2.3.4", limit=3, window_s=60, now=110)
    # the OLDEST attempt ages out first — no lockout beyond the window
    assert t.allow("login-ip", "1.2.3.4", limit=3, window_s=60, now=161)


def test_keys_are_independent(tmp_path):
    t = Throttle(tmp_path / "t.db")
    for _ in range(2):
        t.record("login-email", "a@b.co", now=100)
        t.record("login-ip", "1.2.3.4", now=100)
    assert not t.allow("login-email", "a@b.co", limit=2, window_s=60, now=101)
    assert t.allow("login-email", "c@d.co", limit=2, window_s=60, now=101)
    assert t.allow("signup-ip", "1.2.3.4", limit=2, window_s=60, now=101)  # bucket-scoped


def test_zero_limit_and_missing_key_always_allow(tmp_path):
    t = Throttle(tmp_path / "t.db")
    t.record("login-ip", "1.2.3.4", now=100)
    assert t.allow("login-ip", "1.2.3.4", limit=0, window_s=60, now=100)
    assert t.allow("login-ip", None, limit=1, window_s=60, now=100)
    t.record("login-ip", None)  # no-op, never raises


# -- web wiring --------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["login_attempts_per_15min"] = 3
    cfg.web["signups_per_hour_per_ip"] = 2
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(client, email, password="longenough"):
    return client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )


def login(client, email, password):
    return client.post(
        "/login", data={"email": email, "password": password},
        follow_redirects=False,
    )


def age_attempts(client, seconds):
    """Backdate every recorded attempt, simulating the window passing."""
    import sqlite3

    db = client.app.state.jobs.sessions_dir / "swinglab.db"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE auth_attempts SET ts = ts - ?", (seconds,))
    conn.commit()
    conn.close()


def test_login_throttled_after_repeated_failures(app):
    client = TestClient(app)
    signup(client, "kyle@example.com")
    client.post("/logout")

    for _ in range(3):
        resp = login(client, "kyle@example.com", "wrongwrong")
        assert resp.status_code == 200
        assert "Wrong email or password" in resp.text

    blocked = login(client, "kyle@example.com", "wrongwrong")
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text
    # generic message: reveals neither the account's existence nor the limit
    assert "Wrong email" not in blocked.text

    # even the CORRECT password waits out the window (attacker can't tell)...
    assert login(client, "kyle@example.com", "longenough").status_code == 429

    # ...but the window slides: no lockout beyond it, owner logs right in.
    age_attempts(client, 16 * 60)
    ok = login(client, "kyle@example.com", "longenough")
    assert ok.status_code == 303


def test_successful_logins_are_not_counted(app):
    client = TestClient(app)
    signup(client, "kyle@example.com")
    for _ in range(5):  # log in repeatedly — well past the failure limit
        client.post("/logout")
        assert login(client, "kyle@example.com", "longenough").status_code == 303


def test_signup_throttled_per_ip(app):
    client = TestClient(app)
    assert signup(client, "one@example.com").status_code == 303
    client.post("/logout")
    assert signup(client, "two@example.com").status_code == 303
    client.post("/logout")

    blocked = signup(client, "three@example.com")
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text

    age_attempts(client, 61 * 60)
    assert signup(client, "three@example.com").status_code == 303


def test_invalid_signup_attempts_do_not_consume_slots(app):
    client = TestClient(app)
    for _ in range(5):  # typo'd passwords are cheap and not counted
        resp = signup(client, "x@example.com", password="short")
        assert "at least 8 characters" in resp.text
    assert signup(client, "x@example.com").status_code == 303


def test_zero_disables_throttling(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["login_attempts_per_15min"] = 0
    cfg.web["signups_per_hour_per_ip"] = 0
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    signup(client, "kyle@example.com")
    client.post("/logout")
    for _ in range(12):
        resp = login(client, "kyle@example.com", "wrongwrong")
        assert resp.status_code == 200 and "Wrong email" in resp.text
