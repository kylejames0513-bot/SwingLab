"""Optional SMTP email: inert until configured, and — once configured —
code-verified account claims plus password reset.

No SMTP server is ever contacted: the URL parser and smtplib calls are
tested against fakes, and the app-level flows monkeypatch mailer.send to
capture the outgoing mail (and the 6-digit codes inside it).
"""

from __future__ import annotations

import re
import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web import mailer
from swinglab.web import users as users_module
from swinglab.web.app import create_app
from swinglab.web.users import UserStore

from tests.test_account_sync import (
    SECRET,
    customer,
    get_user,
    pro_order,
    webhook,
)
from tests.test_web import fake_analyze_ok


@pytest.fixture
def outbox(monkeypatch):
    """Turn mail 'on' (env set) but capture sends instead of doing SMTP."""
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp+starttls://u:p@mail.test:587")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")
    monkeypatch.setattr(
        mailer, "send", lambda to, subject, body: sent.append((to, subject, body))
    )
    return sent


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", SECRET)
    cfg = Config()
    cfg.web["require_account"] = True
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def last_code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][2]).group(1)


# -- inert until configured ------------------------------------------------

def test_mailer_inert_until_configured(app, monkeypatch):
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    assert not mailer.enabled()
    with pytest.raises(RuntimeError):
        mailer.send("a@b.co", "hi", "there")

    # Without SMTP, claiming keeps today's behavior exactly: no code step.
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    resp = client.post(
        "/signup",
        data={"email": "buyer@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert get_user(client).has_password

    # ...and password reset is unavailable — no /reset link, but the login
    # page says WHY, honestly, instead of showing nothing.
    assert client.get("/reset").status_code == 503
    assert client.post("/reset/request", data={"email": "x@y.co"}).status_code == 503
    client.post("/logout")
    login_html = client.get("/login").text
    assert 'href="/reset"' not in login_html
    assert "Password reset requires the" in login_html
    assert "operator to configure email" in login_html


def test_login_reset_guidance_uses_brand_support_text(tmp_path, monkeypatch):
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.brand["support_text"] = "Email help@acecoach.example."
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    assert "Email help@acecoach.example." in client.get("/login").text


# -- the SMTP plumbing itself ----------------------------------------------

def test_smtp_url_parsing():
    assert mailer._parse_url("smtp://relay.local") == (
        "smtp", "relay.local", 25, None, None
    )
    assert mailer._parse_url("smtp+starttls://user%40x.com:p%40ss@h.io:587") == (
        "smtp+starttls", "h.io", 587, "user@x.com", "p@ss"
    )
    assert mailer._parse_url("smtps://u:p@mail.io") == (
        "smtps", "mail.io", 465, "u", "p"
    )
    with pytest.raises(ValueError):
        mailer._parse_url("imap://mail.io")
    with pytest.raises(ValueError):
        mailer._parse_url("smtp://")


def test_send_drives_smtplib(monkeypatch):
    monkeypatch.setenv(
        "SWINGLAB_SMTP_URL", "smtp+starttls://user%40x.com:secret@mail.test:587"
    )
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")

    class FakeSMTP:
        instances = []

        def __init__(self, host, port, timeout=None):
            self.host, self.port, self.calls = host, port, []
            FakeSMTP.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            self.calls.append("starttls")

        def login(self, username, password):
            self.calls.append(("login", username, password))

        def send_message(self, message):
            self.calls.append(("send", message))

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    mailer.send("kyle@example.com", "Your code", "123456 inside")

    (server,) = FakeSMTP.instances
    assert (server.host, server.port) == ("mail.test", 587)
    assert server.calls[0] == "starttls"
    assert server.calls[1] == ("login", "user@x.com", "secret")
    kind, message = server.calls[2]
    assert kind == "send"
    assert message["To"] == "kyle@example.com"
    assert message["From"] == "CaddieInsight <no-reply@test.example>"
    assert "123456" in message.get_content()


# -- verified claims -------------------------------------------------------

def test_claiming_a_stub_requires_the_emailed_code(app, outbox):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(client, pro_order(), "orders/paid")

    form = {"email": "buyer@example.com", "password": "longenough"}
    resp = client.post("/signup", data=form, follow_redirects=False)
    assert resp.status_code == 200  # not signed up yet — code step shown
    assert "6-digit code" in resp.text
    assert len(outbox) == 1 and outbox[0][0] == "buyer@example.com"
    assert not get_user(client).has_password  # still a stub

    wrong = client.post("/signup", data={**form, "code": "000000"})
    assert "didn't match" in wrong.text
    assert not get_user(client).has_password

    ok = client.post(
        "/signup", data={**form, "code": last_code(outbox)},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    user = get_user(client)
    assert user.has_password and user.is_pro  # claim kept the purchase
    assert user.shopify_customer_id == "7001"


def test_parked_presignup_purchase_also_requires_code(app, outbox):
    client = TestClient(app)
    webhook(client, pro_order(email="new@example.com"), "orders/paid")
    form = {"email": "new@example.com", "password": "longenough"}
    assert client.post("/signup", data=form, follow_redirects=False).status_code == 200
    resp = client.post(
        "/signup", data={**form, "code": last_code(outbox)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert get_user(client, "new@example.com").is_pro


def test_fresh_email_signs_up_without_any_code(app, outbox):
    client = TestClient(app)
    resp = client.post(
        "/signup",
        data={"email": "nobody@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert outbox == []


def test_resend_is_rate_limited_per_email(app, outbox):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    form = {"email": "buyer@example.com", "password": "longenough"}
    client.post("/signup", data=form)
    resp = client.post("/signup", data=form)  # immediate retry
    assert "6-digit code" in resp.text  # still guided to the code step
    assert len(outbox) == 1  # ...but only one email went out
    # The originally-sent code still works.
    ok = client.post(
        "/signup", data={**form, "code": last_code(outbox)},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_codes_are_single_use_expiring_and_brute_force_resistant(tmp_path, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(users_module, "time", types.SimpleNamespace(time=lambda: now[0]))
    users = UserStore(tmp_path / "db.sqlite")

    code = users.issue_email_code("a@b.co", "claim")
    assert re.fullmatch(r"\d{6}", code)
    assert users.issue_email_code("a@b.co", "claim") is None  # rate-limited
    assert users.check_email_code("a@b.co", "claim", code)
    assert not users.check_email_code("a@b.co", "claim", code)  # single-use

    # Wrong guesses burn the code after CODE_MAX_ATTEMPTS.
    code = users.issue_email_code("a@b.co", "claim")
    for _ in range(users_module.CODE_MAX_ATTEMPTS):
        assert not users.check_email_code("a@b.co", "claim", "999999")
    assert not users.check_email_code("a@b.co", "claim", code)

    # Expired codes never match, and a purpose is scoped to itself.
    code = users.issue_email_code("a@b.co", "claim")
    assert not users.check_email_code("a@b.co", "reset", code)
    now[0] += users_module.CODE_TTL_S + 1
    assert not users.check_email_code("a@b.co", "claim", code)
    # After expiry a fresh code can be issued immediately.
    assert users.issue_email_code("a@b.co", "claim") is not None


# -- password reset --------------------------------------------------------

def test_password_reset_flow(app, outbox):
    client = TestClient(app)
    client.post(
        "/signup", data={"email": "kyle@example.com", "password": "oldpassword"},
        follow_redirects=False,
    )
    client.post("/logout")

    # With SMTP on, code sign-in is the primary flow — the password card
    # (and its reset link) lives behind "use your password instead".
    assert "Use your password instead" in client.get("/login").text
    assert "Forgot your password?" in client.get("/login?password=1").text
    assert "Reset your password" in client.get("/reset").text

    resp = client.post("/reset/request", data={"email": "Kyle@Example.com"})
    assert resp.status_code == 200 and "code is on its way" in resp.text
    assert len(outbox) == 1 and outbox[0][0] == "kyle@example.com"

    wrong = client.post(
        "/reset/confirm",
        data={"email": "kyle@example.com", "code": "000000",
              "password": "newpassword"},
    )
    assert "didn't match" in wrong.text

    ok = client.post(
        "/reset/confirm",
        data={"email": "kyle@example.com", "code": last_code(outbox),
              "password": "newpassword"},
        follow_redirects=False,
    )
    assert ok.status_code == 303

    client.post("/logout")
    old = client.post(
        "/login", data={"email": "kyle@example.com", "password": "oldpassword"}
    )
    assert "Wrong email or password" in old.text
    new = client.post(
        "/login", data={"email": "kyle@example.com", "password": "newpassword"},
        follow_redirects=False,
    )
    assert new.status_code == 303


def test_reset_request_never_reveals_whether_an_account_exists(app, outbox):
    client = TestClient(app)
    resp = client.post("/reset/request", data={"email": "ghost@example.com"})
    assert resp.status_code == 200 and "code is on its way" in resp.text
    assert outbox == []  # nothing sent for unknown emails


def test_short_new_password_does_not_burn_the_code(app, outbox):
    client = TestClient(app)
    client.post(
        "/signup", data={"email": "kyle@example.com", "password": "oldpassword"},
        follow_redirects=False,
    )
    client.post("/logout")
    client.post("/reset/request", data={"email": "kyle@example.com"})
    code = last_code(outbox)
    short = client.post(
        "/reset/confirm",
        data={"email": "kyle@example.com", "code": code, "password": "short"},
    )
    assert "at least 8 characters" in short.text
    ok = client.post(
        "/reset/confirm",
        data={"email": "kyle@example.com", "code": code, "password": "longenough2"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
