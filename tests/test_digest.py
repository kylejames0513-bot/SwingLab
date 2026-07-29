"""Weekly practice-plan digest: consent columns + in-place migration, HMAC
unsubscribe tokens, digest composition (drills with dosage and pass mark,
links, self-contained HTML), the pure eligibility rule and claim-before-send
semantics, run_once's send gates (no email / no consent / no finished session
-> nothing), and the app surfaces (signup checkbox, account toggle,
logged-out unsubscribe, scheduler gating). No email provider is contacted."""

from __future__ import annotations

import json
import sqlite3
import types
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import digest, mailer
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE, JobManager
from swinglab.web.users import UserStore

from tests.test_trends import SESSION_PAYLOADS, make_fake_analyze, payload_for

SECRET = "test-signing-secret"
WEEK = digest.DIGEST_INTERVAL_S


def stub_jobs(tmp_path, payloads: list[dict]):
    """Duck-typed finished jobs with metrics.json on disk, oldest first."""
    jobs = []
    for n, payload in enumerate(payloads, start=1):
        session_dir = tmp_path / f"job{n}"
        (session_dir / "out").mkdir(parents=True)
        (session_dir / "out" / "metrics.json").write_text(json.dumps(payload))
        jobs.append(types.SimpleNamespace(
            id=f"job{n}", session_dir=session_dir, status="done",
            created_at=1000.0 + n, report_rel="out/report.html",
        ))
    return jobs


def stub_user(**overrides):
    base = dict(id="u1", email="kyle@example.com", digest_opt_in=True,
                digest_last_sent_at=None)
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture
def outbox(monkeypatch):
    """SMTP 'configured' (env set) but captured instead of sent."""
    sent: list[tuple] = []
    monkeypatch.delenv("SWINGLAB_MAIL_TRANSPORT", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp+starttls://u:p@mail.test:587")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")
    monkeypatch.setattr(
        mailer, "send",
        lambda to, subject, body, html=False: sent.append((to, subject, body, html)),
    )
    return sent


# -- migration ---------------------------------------------------------------

def test_migration_adds_digest_columns_to_preexisting_db(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)  # a pre-digest (even pre-Shopify) users table
    conn.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, created_at REAL NOT NULL,
            stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',
            subscription_status TEXT NOT NULL DEFAULT 'none'
        );
        INSERT INTO users (id, email, password_hash, created_at)
        VALUES ('old1', 'old@example.com', 'x', 1.0);
        """
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    user = users.get_by_email("old@example.com")
    assert user.digest_opt_in is False          # column added, default off
    assert user.digest_last_sent_at is None
    users.set_digest_opt_in(user.id, True)
    assert users.get(user.id).digest_opt_in is True
    assert [u.id for u in users.digest_optins()] == ["old1"]


# -- unsubscribe tokens ------------------------------------------------------

def test_unsubscribe_token_roundtrip_and_forgery_rejection():
    token = digest.unsubscribe_token("abc123", SECRET)
    assert digest.verify_unsubscribe_token(token, SECRET) == "abc123"
    # Forgeries: wrong secret, swapped user id, tampered mac, garbage.
    assert digest.verify_unsubscribe_token(token, "other-secret") is None
    _, _, mac = token.partition(".")
    assert digest.verify_unsubscribe_token(f"victim.{mac}", SECRET) is None
    assert digest.verify_unsubscribe_token(token[:-1] + "0", SECRET) is None
    assert digest.verify_unsubscribe_token("", SECRET) is None
    assert digest.verify_unsubscribe_token("no-dot-here", SECRET) is None
    # Non-ASCII in the mac must fail verification, never raise (a str
    # compare_digest would TypeError and 500 the unsubscribe route).
    assert digest.verify_unsubscribe_token("abc123.éé", SECRET) is None
    # A token signed for another purpose with the right secret is still a forgery.
    import hashlib
    import hmac as hmac_mod
    other = hmac_mod.new(
        SECRET.encode(), b"password-reset:abc123", hashlib.sha256
    ).hexdigest()
    assert digest.verify_unsubscribe_token(f"abc123.{other}", SECRET) is None


# -- eligibility + the claim (pure logic, no threads) ------------------------

def test_eligibility_rule():
    now = 1_000_000.0
    assert digest.eligible(stub_user(), now)                       # never sent
    assert not digest.eligible(stub_user(digest_opt_in=False), now)
    assert not digest.eligible(stub_user(digest_last_sent_at=now - 3600), now)
    assert digest.eligible(stub_user(digest_last_sent_at=now - WEEK - 1), now)


def test_claim_digest_send_is_atomic_and_weekly(tmp_path):
    users = UserStore(tmp_path / "db.sqlite")
    user = users.create("kyle@example.com", "longenough")
    now = 1_000_000.0

    assert not users.claim_digest_send(user.id, now, WEEK)  # no consent, no claim
    users.set_digest_opt_in(user.id, True)
    assert users.claim_digest_send(user.id, now, WEEK)
    assert users.get(user.id).digest_last_sent_at == now    # stamped pre-send
    assert not users.claim_digest_send(user.id, now + 3600, WEEK)  # same week
    assert users.claim_digest_send(user.id, now + WEEK + 1, WEEK)  # next week


# -- composing ---------------------------------------------------------------

def test_compose_digest_content(tmp_path, cfg):
    jobs = stub_jobs(tmp_path, [
        payload_for([{"tempo_ratio": 2.2}]),
        payload_for([{"tempo_ratio": 2.1, "finish_balance_sw": 0.3}]),
    ])
    subject, html = digest.compose_digest(
        stub_user(), cfg, jobs, base_url="https://swing.example", secret=SECRET,
    )
    assert subject == "This week: tame the tempo (3 drills)"
    # Drill name + dosage + pass mark, straight from the practice plan.
    assert "Three-beat count" in html
    assert "3 x 10 swings, 3x/week" in html
    assert "tempo ratio at or above 2.4:1" in html
    assert "Also flagged: Finish balance" in html
    # One honest progress line (two sessions of tempo data exist).
    assert "Tempo has moved 2.20:1" in html
    # Links: latest report, /progress, and a verifiable unsubscribe.
    assert "https://swing.example/session/job2/report" in html
    assert "https://swing.example/progress" in html
    token = html.split("/email/unsubscribe?token=")[1].split('"')[0]
    assert digest.verify_unsubscribe_token(token, SECRET) == "u1"
    # Self-contained: brand colors inline, no images, no external assets.
    assert cfg.brand["primary_color"] in html
    assert "<img" not in html and "http://" not in html
    assert html.count("https://") == html.count("https://swing.example")


def test_compose_digest_clean_session_and_no_sessions(tmp_path, cfg):
    clean = stub_jobs(tmp_path, [payload_for([{"tempo_ratio": 3.0}])])
    subject, html = digest.compose_digest(
        stub_user(), cfg, clean, base_url="", secret=SECRET,
    )
    assert subject == "This week: keep it clean (2 drills)"
    assert "Baseline re-film" in html            # the maintenance drills
    assert "came back clean" in html

    assert digest.compose_digest(stub_user(), cfg, [], secret=SECRET) is None
    unfinished = [types.SimpleNamespace(
        id="q1", session_dir=tmp_path / "q1", status="queued",
        created_at=1.0, report_rel=None,
    )]
    assert digest.compose_digest(stub_user(), cfg, unfinished, secret=SECRET) is None


# -- run_once send gates -----------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """A JobManager + UserStore sharing one SQLite file, like the app."""
    cfg = Config()
    manager = JobManager(tmp_path / "sessions", cfg)
    users = UserStore(tmp_path / "sessions" / "swinglab.db")
    return cfg, manager, users


def finished_session(manager, user_id, payload=None):
    job = manager.create_session(source_name="swing.mov", user_id=user_id)
    (job.session_dir / "out").mkdir()
    (job.session_dir / "out" / "metrics.json").write_text(
        json.dumps(payload or payload_for([{"tempo_ratio": 2.2}]))
    )
    job.status = DONE
    job.report_rel = "out/report.html"
    manager._save(job)
    return job


def test_run_once_sends_nothing_without_email(store, monkeypatch):
    cfg, manager, users = store
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    user = users.create("kyle@example.com", "longenough")
    users.set_digest_opt_in(user.id, True)
    finished_session(manager, user.id)

    assert digest.run_once(users, manager, cfg, SECRET) == 0
    assert users.get(user.id).digest_last_sent_at is None  # untouched


def test_run_once_sends_nothing_when_disabled_in_config(store, outbox):
    cfg, manager, users = store
    cfg.web["digest_enabled"] = False
    user = users.create("kyle@example.com", "longenough")
    users.set_digest_opt_in(user.id, True)
    finished_session(manager, user.id)

    assert digest.run_once(users, manager, cfg, SECRET) == 0
    assert outbox == []


def test_run_once_respects_consent_sessions_and_the_week(store, outbox):
    cfg, manager, users = store
    now = 1_000_000.0

    silent = users.create("quiet@example.com", "longenough")  # never opted in
    finished_session(manager, silent.id)

    keen = users.create("keen@example.com", "longenough")     # opted in, no film
    users.set_digest_opt_in(keen.id, True)

    filmed = users.create("filmed@example.com", "longenough")  # opted in + film
    users.set_digest_opt_in(filmed.id, True)
    finished_session(manager, filmed.id)

    assert digest.run_once(users, manager, cfg, SECRET, now=now) == 1
    (to, subject, body, html) = outbox[0]
    assert to == "filmed@example.com" and html is True
    assert subject.startswith("This week:")
    assert "/email/unsubscribe?token=" in body
    assert users.get(filmed.id).digest_last_sent_at == now
    # No finished session: nothing sent AND nothing claimed — the first
    # digest goes out the week they actually film.
    assert users.get(keen.id).digest_last_sent_at is None
    assert users.get(silent.id).digest_last_sent_at is None

    # Same week: rate-limited. Next week: sends again.
    assert digest.run_once(users, manager, cfg, SECRET, now=now + 3600) == 0
    assert len(outbox) == 1
    assert digest.run_once(users, manager, cfg, SECRET, now=now + WEEK + 1) == 1
    assert len(outbox) == 2


def test_failed_send_is_claimed_not_retried(store, outbox, monkeypatch):
    """The claim lands BEFORE the SMTP attempt, so a crash or failure
    mid-send can never double-email within the same week."""
    cfg, manager, users = store
    user = users.create("kyle@example.com", "longenough")
    users.set_digest_opt_in(user.id, True)
    finished_session(manager, user.id)
    now = 1_000_000.0

    def boom(to, subject, body, html=False):
        raise ConnectionError("smtp down")

    monkeypatch.setattr(mailer, "send", boom)
    assert digest.run_once(users, manager, cfg, SECRET, now=now) == 0  # no raise
    assert users.get(user.id).digest_last_sent_at == now  # claimed regardless
    monkeypatch.setattr(
        mailer, "send",
        lambda to, subject, body, html=False: outbox.append((to, subject, body, html)),
    )
    assert digest.run_once(users, manager, cfg, SECRET, now=now + 60) == 0
    assert outbox == []  # this week stays skipped — never a double-send


# -- app surfaces ------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SWINGLAB_SECRET", SECRET)
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(SESSION_PAYLOADS)
    )
    cfg = Config()
    cfg.web["require_account"] = True
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def get_user(app, email="kyle@example.com"):
    return app.state.users.get_by_email(email)


def test_signup_checkbox_is_opt_in(app):
    client = TestClient(app)
    client.post(  # box left unchecked -> no consent
        "/signup", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert get_user(app).digest_opt_in is False

    client2 = TestClient(app)
    client2.post(
        "/signup",
        data={"email": "opted@example.com", "password": "longenough",
              "digest": "on"},
        follow_redirects=False,
    )
    assert get_user(app, "opted@example.com").digest_opt_in is True

    assert "Email me one drill a week" in TestClient(app).get("/signup").text


def test_account_page_toggle(app):
    client = TestClient(app)
    client.post(
        "/signup", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    html = client.get("/account").text
    assert "Weekly drill email" in html and "Email me one drill a week" in html

    assert client.post(
        "/account/digest", data={"enabled": "on"}, follow_redirects=False
    ).status_code == 303
    assert get_user(app).digest_opt_in is True
    assert "Turn off the weekly drill email" in client.get("/account").text

    client.post("/account/digest", data={}, follow_redirects=False)
    assert get_user(app).digest_opt_in is False


def test_unsubscribe_route_works_logged_out(app):
    client = TestClient(app)
    client.post(
        "/signup",
        data={"email": "kyle@example.com", "password": "longenough",
              "digest": "on"},
        follow_redirects=False,
    )
    user = get_user(app)
    assert user.digest_opt_in is True

    anonymous = TestClient(app)  # no session cookie — straight from an inbox
    token = digest.unsubscribe_token(user.id, SECRET)
    resp = anonymous.get(f"/email/unsubscribe?token={token}")
    assert resp.status_code == 200 and "unsubscribed" in resp.text.lower()
    assert get_user(app).digest_opt_in is False
    # Idempotent, and forgeries bounce without touching anything.
    assert anonymous.get(f"/email/unsubscribe?token={token}").status_code == 200
    users = app.state.users
    users.set_digest_opt_in(user.id, True)
    forged = digest.unsubscribe_token(user.id, "wrong-secret")
    assert anonymous.get(f"/email/unsubscribe?token={forged}").status_code == 404
    assert anonymous.get("/email/unsubscribe").status_code == 404
    # Non-ASCII garbage in the token is a 404, never a 500.
    assert anonymous.get(
        f"/email/unsubscribe?token={user.id}.%C3%A9"
    ).status_code == 404
    assert get_user(app).digest_opt_in is True


def test_scheduler_only_starts_with_email_transport_and_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(SESSION_PAYLOADS)
    )
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    app = create_app(Config(), sessions_dir=tmp_path / "a")
    assert app.state.digest_thread is None      # zero behavior without email

    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp+starttls://u:p@mail.test:587")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    cfg = Config()
    cfg.web["digest_enabled"] = False
    app = create_app(cfg, sessions_dir=tmp_path / "b")
    assert app.state.digest_thread is None      # config kill-switch respected

    app = create_app(Config(), sessions_dir=tmp_path / "c")
    thread = app.state.digest_thread
    assert thread is not None and thread.daemon  # dies with the process

    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    app = create_app(Config(), sessions_dir=tmp_path / "d")
    thread = app.state.digest_thread
    assert thread is not None and thread.daemon
