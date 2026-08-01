"""Optional email delivery: inert until configured, and — once configured —
code-verified account claims plus password reset.

No provider is ever contacted: HTTPS and SMTP calls are tested against fakes,
and app-level flows capture the outgoing mail and six-digit codes.
"""

from __future__ import annotations

import io
import json
import re
import types
from html import unescape

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
    monkeypatch.delenv("SWINGLAB_MAIL_TRANSPORT", raising=False)
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


def signup_intent(response):
    match = re.search(
        r'name="signup_intent" value="([^"]+)"', response.text
    )
    assert match is not None
    return match.group(1)


def verified_password_signup(client, outbox, email, password):
    pending = client.post(
        "/signup",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    if pending.status_code == 303:
        return pending
    assert pending.status_code == 200
    completed = client.post(
        "/signup",
        data={
            "signup_intent": signup_intent(pending),
            "code": last_code(outbox),
        },
        follow_redirects=False,
    )
    assert completed.status_code == 303
    return completed


# -- inert until configured ------------------------------------------------

def test_mailer_inert_until_configured(app, monkeypatch):
    monkeypatch.delenv("SWINGLAB_MAIL_TRANSPORT", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    assert not mailer.enabled()
    with pytest.raises(RuntimeError):
        mailer.send("a@b.co", "hi", "there")

    # A Shopify identity cannot be claimed without inbox proof. The safe
    # failure leaves the stub and everything attached to it untouched.
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    resp = client.post(
        "/signup",
        data={"email": "buyer@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 503
    assert not get_user(client).has_password

    # A genuinely standalone deployment still keeps the historical local
    # account path. Disconnect the bridge before exercising that contract.
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    local = client.post(
        "/signup",
        data={"email": "local@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert local.status_code == 303

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
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.brand["support_text"] = "Email help@acecoach.example."
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    assert "Email help@acecoach.example." in client.get("/login").text


# -- delivery plumbing -----------------------------------------------------

def test_partial_and_whitespace_configuration_stays_inert(monkeypatch):
    monkeypatch.delenv("SWINGLAB_MAIL_TRANSPORT", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "   ")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "   ")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")
    assert not mailer.enabled()

    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "   ")
    assert not mailer.enabled()

    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")
    assert mailer.enabled()

    monkeypatch.setenv("SWINGLAB_MAIL_TRANSPORT", "")
    assert mailer.enabled()  # blank means the documented auto default

    monkeypatch.setenv("SWINGLAB_MAIL_TRANSPORT", "typo")
    assert mailer.enabled()  # configured intent stays fail-closed
    with pytest.raises(mailer.EmailDeliveryRejected) as raised:
        mailer.send("a@b.co", "hi", "there")
    assert "must be auto, resend, or smtp" in str(raised.value)


def test_send_uses_resend_https_api(monkeypatch):
    requests = []
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://must-not-be-used.test")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def getcode(self):
            return self.status

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(mailer.urllib_request, "urlopen", fake_urlopen)
    mailer.send("kyle@example.com", "Your code", "123456 inside")

    [(request, timeout)] = requests
    assert request.full_url == "https://api.resend.com/emails"
    assert request.method == "POST"
    assert timeout == mailer._DELIVERY_TIMEOUT_S
    assert request.get_header("Authorization") == "Bearer re_test_secret"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("User-agent")
    idempotency_key = request.get_header("Idempotency-key")
    assert idempotency_key.startswith("caddie-")
    assert "kyle@example.com" not in idempotency_key
    assert "123456" not in idempotency_key
    assert json.loads(request.data) == {
        "from": "CaddieInsight <no-reply@test.example>",
        "to": ["kyle@example.com"],
        "subject": "Your code",
        "text": "123456 inside",
    }


def test_existing_resend_smtp_url_is_upgraded_to_https(monkeypatch):
    requests = []
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv(
        "SWINGLAB_SMTP_URL",
        "smtp+starttls://resend:re_existing_key@smtp.resend.com:587",
    )
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def getcode(self):
            return self.status

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse()

    monkeypatch.setattr(mailer.urllib_request, "urlopen", fake_urlopen)
    mailer.send("kyle@example.com", "Your code", "123456 inside")

    assert len(requests) == 1
    assert requests[0].get_header("Authorization") == "Bearer re_existing_key"


def test_resend_html_uses_html_field(monkeypatch):
    payloads = []
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret")
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def getcode(self):
            return self.status

    def fake_urlopen(request, timeout):
        payloads.append(json.loads(request.data))
        return FakeResponse()

    monkeypatch.setattr(mailer.urllib_request, "urlopen", fake_urlopen)
    mailer.send("kyle@example.com", "Weekly plan", "<h1>Drill</h1>", html=True)

    assert payloads[0]["html"] == "<h1>Drill</h1>"
    assert "text" not in payloads[0]


def test_resend_http_error_is_sanitized(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_super_secret")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")

    def reject(request, timeout):
        raise mailer.urllib_error.HTTPError(
            request.full_url,
            403,
            "provider body contains re_super_secret and 123456",
            {},
            io.BytesIO(b"provider body contains re_super_secret and 123456"),
        )

    monkeypatch.setattr(mailer.urllib_request, "urlopen", reject)
    with pytest.raises(mailer.EmailDeliveryError) as raised:
        mailer.send("kyle@example.com", "Your code", "123456 inside")
    message = str(raised.value)
    assert message == "Resend returned HTTP 403."
    assert "re_super_secret" not in message
    assert "123456" not in message


def test_resend_network_failure_is_uncertain_and_sanitized(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_super_secret")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")

    def disconnect(request, timeout):
        raise mailer.urllib_error.URLError(
            "connection lost after re_super_secret and 123456"
        )

    monkeypatch.setattr(mailer.urllib_request, "urlopen", disconnect)
    with pytest.raises(mailer.EmailDeliveryUncertain) as raised:
        mailer.send("kyle@example.com", "Your code", "123456 inside")
    assert str(raised.value) == "Resend delivery could not be confirmed."
    assert "re_super_secret" not in str(raised.value)
    assert "123456" not in str(raised.value)


@pytest.mark.parametrize("status", [408, 409, 500, 503])
def test_resend_ambiguous_http_status_is_uncertain(monkeypatch, status):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")

    def ambiguous_error(request, timeout):
        raise mailer.urllib_error.HTTPError(
            request.full_url, status, "ambiguous", {}, io.BytesIO()
        )

    monkeypatch.setattr(mailer.urllib_request, "urlopen", ambiguous_error)
    with pytest.raises(mailer.EmailDeliveryUncertain):
        mailer.send("kyle@example.com", "Your code", "123456 inside")


# -- SMTP fallback ---------------------------------------------------------

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
    monkeypatch.delenv("SWINGLAB_MAIL_TRANSPORT", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
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


def test_smtp_cleanup_failure_after_acceptance_is_not_reported_as_failure(
    monkeypatch
):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_TRANSPORT", raising=False)
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.test:25")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.sent = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            raise OSError("QUIT disconnected")

        def send_message(self, message):
            self.sent = True

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    mailer.send("kyle@example.com", "Your code", "123456 inside")


def test_explicit_smtp_transport_is_a_rollback_override(monkeypatch):
    calls = []
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.test:25")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")
    monkeypatch.setenv("SWINGLAB_MAIL_TRANSPORT", "smtp")
    monkeypatch.setattr(
        mailer,
        "_send_smtp",
        lambda *args: calls.append(args),
    )
    monkeypatch.setattr(
        mailer,
        "_send_resend",
        lambda *args: pytest.fail("Resend API should not be used"),
    )

    mailer.send("kyle@example.com", "Your code", "123456 inside")
    assert len(calls) == 1


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

    intent = signup_intent(resp)
    assert "longenough" not in resp.text
    wrong = client.post(
        "/signup",
        data={"signup_intent": intent, "code": "000000"},
    )
    assert "didn't match" in unescape(wrong.text)
    assert not get_user(client).has_password

    ok = client.post(
        "/signup",
        data={"signup_intent": intent, "code": last_code(outbox)},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    user = get_user(client)
    assert user.has_password and user.is_pro  # claim kept the purchase
    assert user.shopify_customer_id == "7001"


def test_signup_intent_can_only_finish_in_the_initiating_session(
    app, outbox
):
    owner = TestClient(app)
    second_client = TestClient(app)
    webhook(owner, customer(), "customers/create")
    form = {"email": "buyer@example.com", "password": "longenough"}

    pending = owner.post("/signup", data=form, follow_redirects=False)
    intent = signup_intent(pending)
    code = last_code(outbox)

    stolen = second_client.post(
        "/signup",
        data={"signup_intent": intent, "code": code},
        follow_redirects=False,
    )
    assert stolen.status_code != 303
    assert not get_user(owner).has_password

    completed = owner.post(
        "/signup",
        data={"signup_intent": intent, "code": code},
        follow_redirects=False,
    )
    assert completed.status_code == 303
    assert get_user(owner).has_password
    assert owner.get("/account", follow_redirects=False).status_code == 200
    assert (
        second_client.get("/account", follow_redirects=False).status_code
        == 303
    )


def test_uncertain_claim_delivery_still_shows_a_working_code_form(
    app, outbox, monkeypatch
):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    attempted = []

    def uncertain(to, subject, body):
        attempted.append((to, subject, body))
        raise mailer.EmailDeliveryUncertain("outcome unknown")

    monkeypatch.setattr(mailer, "send", uncertain)
    form = {"email": "buyer@example.com", "password": "longenough"}
    response = client.post("/signup", data=form)
    assert response.status_code == 503
    assert 'action="/signup"' in response.text
    assert "Verification code" in response.text
    assert "longenough" not in response.text

    verified = client.post(
        "/signup",
        data={
            "signup_intent": signup_intent(response),
            "code": last_code(attempted),
        },
        follow_redirects=False,
    )
    assert verified.status_code == 303
    assert get_user(client).shopify_customer_id == "7001"


def test_invalid_transport_mode_cannot_bypass_store_claim_verification(
    app, monkeypatch
):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.test:25")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>")
    monkeypatch.setenv("SWINGLAB_MAIL_TRANSPORT", "typo")

    response = client.post(
        "/signup",
        data={"email": "buyer@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert response.status_code == 503
    user = get_user(client)
    assert user.shopify_customer_id == "7001"
    assert not user.has_password


def test_parked_presignup_purchase_also_requires_code(app, outbox):
    client = TestClient(app)
    webhook(client, pro_order(email="new@example.com"), "orders/paid")
    form = {"email": "new@example.com", "password": "longenough"}
    pending = client.post(
        "/signup", data=form, follow_redirects=False
    )
    assert pending.status_code == 200
    resp = client.post(
        "/signup",
        data={
            "signup_intent": signup_intent(pending),
            "code": last_code(outbox),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert get_user(client, "new@example.com").is_pro


def test_shopify_connected_fresh_email_must_verify_before_signup(app, outbox):
    client = TestClient(app)
    pending = client.post(
        "/signup",
        data={"email": "nobody@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert pending.status_code == 200
    assert len(outbox) == 1
    assert get_user(client, "nobody@example.com") is None

    completed = client.post(
        "/signup",
        data={
            "signup_intent": signup_intent(pending),
            "code": last_code(outbox),
        },
        follow_redirects=False,
    )
    assert completed.status_code == 303
    user = get_user(client, "nobody@example.com")
    assert user is not None and user.email_verified and user.has_password


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
        "/signup",
        data={
            "signup_intent": signup_intent(resp),
            "code": last_code(outbox),
        },
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


def test_signup_intent_stores_only_hash_and_is_expiring_single_use(tmp_path):
    users = UserStore(tmp_path / "db.sqlite")
    token = users.issue_signup_intent(
        "Person@Example.com",
        "plaintext-must-not-survive",
        digest_opt_in=True,
        now=1_000,
    )

    stored = users._conn.execute(
        "SELECT * FROM signup_intents"
    ).fetchone()
    assert stored["email"] == "person@example.com"
    assert stored["password_hash"].startswith("scrypt$")
    assert "plaintext-must-not-survive" not in repr(tuple(stored))
    assert token not in repr(tuple(stored))
    metadata = users.get_signup_intent(token, now=1_001)
    assert metadata is not None and metadata.digest_opt_in
    assert not hasattr(metadata, "password_hash")

    user = users.complete_signup_intent(token, now=1_001)
    assert user.email_verified
    assert user.digest_opt_in
    assert (
        users.authenticate(
            "person@example.com", "plaintext-must-not-survive"
        )
        is not None
    )
    with pytest.raises(ValueError, match="expired"):
        users.complete_signup_intent(token, now=1_002)

    expired = users.issue_signup_intent(
        "later@example.com", "another-password", now=2_000
    )
    assert users.get_signup_intent(expired, now=2_601) is None
    with pytest.raises(ValueError, match="expired"):
        users.complete_signup_intent(expired, now=2_601)


def test_new_signup_intent_revokes_older_browser_token(tmp_path):
    users = UserStore(tmp_path / "db.sqlite")

    old = users.issue_signup_intent(
        "same@example.com", "first-password"
    )
    new = users.issue_signup_intent(
        "same@example.com", "second-password"
    )

    assert users.get_signup_intent(old) is None
    assert users.get_signup_intent(new) is not None


# -- password reset --------------------------------------------------------

def test_password_reset_flow(app, outbox):
    client = TestClient(app)
    verified_password_signup(
        client, outbox, "kyle@example.com", "oldpassword"
    )
    client.post("/logout")
    outbox.clear()

    # Password help is visible from the primary code-sign-in screen instead
    # of being hidden behind the password fallback.
    assert "Forgot or reset your password?" in client.get("/login").text
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
    assert "didn't match" in unescape(wrong.text)

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


def test_reset_delivery_failure_keeps_response_private_and_allows_retry(
    app, outbox, monkeypatch
):
    client = TestClient(app)
    verified_password_signup(
        client, outbox, "kyle@example.com", "oldpassword"
    )
    client.post("/logout")
    outbox.clear()

    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            mailer.EmailDeliveryRejected("provider unavailable")
        ),
    )
    known = client.post("/reset/request", data={"email": "kyle@example.com"})
    unknown = client.post("/reset/request", data={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.text.replace("kyle@example.com", "EMAIL") == unknown.text.replace(
        "ghost@example.com", "EMAIL"
    )

    sent = []
    monkeypatch.setattr(
        mailer, "send", lambda to, subject, body: sent.append((to, subject, body))
    )
    retry = client.post("/reset/request", data={"email": "kyle@example.com"})
    assert retry.status_code == 200
    assert len(sent) == 1


def test_short_new_password_does_not_burn_the_code(app, outbox):
    client = TestClient(app)
    verified_password_signup(
        client, outbox, "kyle@example.com", "oldpassword"
    )
    client.post("/logout")
    outbox.clear()
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


def test_reset_confirmation_rejects_cross_origin_without_consuming_code(
    app, outbox
):
    client = TestClient(app)
    verified_password_signup(
        client, outbox, "kyle@example.com", "oldpassword"
    )
    client.post("/logout")
    outbox.clear()
    client.post("/reset/request", data={"email": "kyle@example.com"})
    code = last_code(outbox)
    form = {
        "email": "kyle@example.com",
        "code": code,
        "password": "newpassword",
    }

    rejected = client.post(
        "/reset/confirm",
        data=form,
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert rejected.status_code == 403

    completed = client.post(
        "/reset/confirm", data=form, follow_redirects=False
    )
    assert completed.status_code == 303
