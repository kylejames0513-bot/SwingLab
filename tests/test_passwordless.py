"""Email-code sign-in — the "one account" system: the customer's email is
their identity on the store AND the app, and nobody sets a password unless
they want one.

The matrix pinned here: the same "Continue with email" flow logs into an
existing account, claims an unclaimed store stub (Pro and the Shopify link
kept), or creates a brand-new account; wrong/expired/replayed/burned codes
all fail; nothing — pages or emails — reveals which of the three states an
address is in; code requests and entries share the login throttle budget;
and with SMTP unset (or web.passwordless_login off) the login/signup pages
keep the classic password flows exactly, so white-label installs without
email are untouched. Passwordless accounts can add an optional password on
/account; password accounts keep working unchanged.
"""

from __future__ import annotations

import re
import time
import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.kpis import compute_kpis
from swinglab.web import jobs as jobs_module
from swinglab.web import mailer
from swinglab.web import users as users_module
from swinglab.web.app import create_app
from swinglab.web.users import UserStore

from tests.test_account_sync import (
    SECRET,
    count_users,
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


def request_code(client, email="kyle@example.com"):
    return client.post("/login/email", data={"email": email})


def enter_code(client, code, email="kyle@example.com", **kwargs):
    return client.post(
        "/login/code", data={"email": email, "code": code}, **kwargs
    )


def code_signin(client, outbox, email="kyle@example.com"):
    resp = request_code(client, email)
    assert resp.status_code == 200 and "Check your email" in resp.text
    ok = enter_code(client, last_code(outbox), email, follow_redirects=False)
    assert ok.status_code == 303
    return ok


# -- the unified login page --------------------------------------------------

def test_login_page_leads_with_email_when_smtp_on(app, outbox):
    client = TestClient(app)
    for page in (client.get("/login").text, client.get("/").text):  # + landing
        assert "Continue with your email" in page
        assert "use on the store" in page  # store is configured here
        assert "six-digit code" in page
        assert "Use your password instead" in page
        assert 'action="/login/email"' in page
        # The password cards are behind the fallback link, not shown here.
        assert "Create account" not in page

    fallback = client.get("/login?password=1").text
    assert "Create account" in fallback and "Log in" in fallback
    assert "Email me a sign-in code instead" in fallback


def test_store_line_dropped_when_no_store_is_configured(
    tmp_path, monkeypatch, outbox
):
    # White-label install with SMTP but no Shopify: the headline stays
    # honest — no store to mention.
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SHOPIFY_STOREFRONT_TOKEN", raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    page = client.get("/login").text
    assert "Continue with your email" in page
    assert "use on the store" not in page


# -- the three account states, one flow --------------------------------------

def test_code_signs_in_an_existing_password_account(app, outbox):
    client = TestClient(app)
    client.post(
        "/signup", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    client.post("/logout")

    code_signin(client, outbox)
    assert "Analyze your swing" in client.get("/").text
    user = get_user(client, "kyle@example.com")
    assert user.has_password  # the password is untouched
    assert user.email_verified  # ...and the sign-in verified the email


def test_code_claims_a_store_stub_and_keeps_pro(app, outbox):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(client, pro_order(), "orders/paid")
    stub = get_user(client)
    assert not stub.claimed and stub.pro_until > time.time()

    code_signin(client, outbox, "  BUYER@Example.COM ")  # normalization too
    user = get_user(client)
    assert user.id == stub.id  # same row — no duplicate user
    assert count_users(client) == 1
    assert user.email_verified and user.claimed
    assert not user.has_password  # no password was ever set
    assert user.is_pro  # the purchase carried over
    assert user.shopify_customer_id == "7001"
    assert "Analyze your swing" in client.get("/").text


def test_code_creates_an_account_for_a_new_email(app, outbox):
    client = TestClient(app)
    code_signin(client, outbox, "new@example.com")
    user = get_user(client, "new@example.com")
    assert user is not None and user.email_verified
    assert not user.has_password
    assert user.digest_opt_in is False  # no consent was ever asked for
    assert "Analyze your swing" in client.get("/").text


def test_code_signin_attaches_a_parked_presignup_purchase(app, outbox):
    client = TestClient(app)
    webhook(client, pro_order(email="new@example.com"), "orders/paid")
    code_signin(client, outbox, "new@example.com")
    assert get_user(client, "new@example.com").is_pro


def test_code_claimed_account_survives_store_side_deletion(app, outbox):
    # The code claim is a real claim: customers/delete must now unlink,
    # never delete, exactly as it does for password-claimed accounts.
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    code_signin(client, outbox, "buyer@example.com")

    webhook(client, {"id": 7001}, "customers/delete")
    user = get_user(client)
    assert user is not None and user.email_verified
    assert user.shopify_customer_id is None  # unlinked, not destroyed


# -- no account enumeration ---------------------------------------------------

def test_code_flow_is_identical_for_all_three_states(app, outbox):
    client = TestClient(app)
    client.post(  # state 1: existing app account
        "/signup", data={"email": "app@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    client.post("/logout")
    webhook(client, customer(email="stub@example.com"), "customers/create")
    # state 3: fresh@example.com has nothing at all

    pages, mails, errors = [], [], []
    for email in ("app@example.com", "stub@example.com", "fresh@example.com"):
        resp = request_code(client, email)
        assert resp.status_code == 200
        pages.append(resp.text.replace(email, "EMAIL"))
        to, subject, body = outbox[-1]
        assert to == email
        mails.append((
            re.sub(r"\d{6}", "CODE", subject), re.sub(r"\d{6}", "CODE", body),
        ))
        wrong = enter_code(client, "000000", email)
        errors.append((wrong.status_code, wrong.text.replace(email, "EMAIL")))

    assert pages[0] == pages[1] == pages[2]
    assert mails[0] == mails[1] == mails[2]  # a code goes to ALL of them
    assert errors[0] == errors[1] == errors[2]


# -- bad codes ----------------------------------------------------------------

def test_wrong_code_does_not_sign_in(app, outbox):
    client = TestClient(app)
    request_code(client, "new@example.com")
    resp = enter_code(client, "000000", "new@example.com")
    assert resp.status_code == 200 and "didn't match" in resp.text
    assert get_user(client, "new@example.com") is None  # nothing created
    assert "Continue with your email" in client.get("/").text  # logged out


def test_code_cannot_be_replayed_after_success(app, outbox):
    client = TestClient(app)
    request_code(client, "new@example.com")
    code = last_code(outbox)
    assert enter_code(client, code, "new@example.com",
                      follow_redirects=False).status_code == 303
    client.post("/logout")
    replay = enter_code(client, code, "new@example.com")
    assert replay.status_code == 200 and "didn't match" in replay.text


def test_fifth_wrong_attempt_burns_the_code(app, outbox):
    client = TestClient(app)
    request_code(client, "new@example.com")
    code = last_code(outbox)
    for _ in range(users_module.CODE_MAX_ATTEMPTS):
        assert "didn't match" in enter_code(client, "000000", "new@example.com").text
    # 6th attempt with the CORRECT code: burned, must be re-requested.
    resp = enter_code(client, code, "new@example.com")
    assert resp.status_code == 200 and "didn't match" in resp.text


def test_login_codes_expire_and_are_purpose_scoped(tmp_path, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(
        users_module, "time", types.SimpleNamespace(time=lambda: now[0])
    )
    users = UserStore(tmp_path / "db.sqlite")
    code = users.issue_email_code("a@b.co", "login")
    assert not users.check_email_code("a@b.co", "claim", code)  # wrong purpose
    now[0] += users_module.CODE_TTL_S + 1
    assert not users.check_email_code("a@b.co", "login", code)  # expired


def test_resend_is_rate_limited_but_original_code_still_works(app, outbox):
    client = TestClient(app)
    request_code(client, "new@example.com")
    resp = request_code(client, "new@example.com")  # immediate retry
    assert resp.status_code == 200 and "Check your email" in resp.text
    assert len(outbox) == 1  # only one email actually went out
    assert enter_code(client, last_code(outbox), "new@example.com",
                      follow_redirects=False).status_code == 303


# -- throttling ---------------------------------------------------------------

@pytest.fixture
def tight_app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["login_attempts_per_15min"] = 3
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def test_code_requests_and_wrong_entries_share_the_login_budget(
    tight_app, outbox
):
    client = TestClient(tight_app)
    request_code(client, "kyle@example.com")  # 1 of 3
    for _ in range(2):  # 2 wrong guesses -> 3 of 3
        assert "didn't match" in enter_code(client, "000000").text
    blocked = enter_code(client, "000000")
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text
    assert request_code(client, "kyle@example.com").status_code == 429


def test_correct_code_entries_are_never_recorded(tight_app, outbox):
    # limit 3: two request+success rounds spend only the 2 request slots.
    # Were successes recorded too, the 4th event here would already 429.
    client = TestClient(tight_app)
    for _ in range(2):
        assert request_code(client, "kyle@example.com").status_code == 200
        resp = enter_code(client, last_code(outbox), follow_redirects=False)
        assert resp.status_code == 303  # consumed on success, so the next
        client.post("/logout")          # request mints a fresh code
    assert len(outbox) == 2


# -- fallbacks: SMTP off, config off, password users --------------------------

def test_without_smtp_the_password_flows_are_exactly_as_before(app, monkeypatch):
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    client = TestClient(app)
    for page in (client.get("/login").text, client.get("/").text):
        assert "Create account" in page and "Log in" in page
        assert "Continue with your email" not in page
        assert 'action="/login/email"' not in page
    assert client.post("/login/email", data={"email": "a@b.co"}).status_code == 503
    assert client.post(
        "/login/code", data={"email": "a@b.co", "code": "123456"}
    ).status_code == 503
    # ...and password signup/login work untouched.
    resp = client.post(
        "/signup", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_config_flag_off_forces_password_flow_even_with_smtp(
    tmp_path, monkeypatch, outbox
):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    page = client.get("/login").text
    assert "Create account" in page and "Continue with your email" not in page
    assert client.post("/login/email", data={"email": "a@b.co"}).status_code == 503


def test_password_holders_can_still_use_their_password(app, outbox):
    client = TestClient(app)
    client.post(
        "/signup", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    client.post("/logout")
    wrong = client.post(
        "/login", data={"email": "kyle@example.com", "password": "wrongwrong"}
    )
    assert "Wrong email or password" in wrong.text
    assert "Log in" in wrong.text  # error renders on the password view
    ok = client.post(
        "/login", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_password_login_with_passwordless_account_points_at_code_flow(
    app, outbox
):
    client = TestClient(app)
    code_signin(client, outbox, "kyle@example.com")
    client.post("/logout")
    resp = client.post(
        "/login", data={"email": "kyle@example.com", "password": "whatever1"}
    )
    assert resp.status_code == 200
    assert "doesn't use a password" in resp.text
    assert "Wrong email or password" not in resp.text


def test_invalid_email_gets_an_error_not_a_code(app, outbox):
    client = TestClient(app)
    resp = request_code(client, "not-an-email")
    assert "look like an email" in resp.text
    assert outbox == []


# -- the optional password ----------------------------------------------------

def test_passwordless_account_can_add_a_password(app, outbox):
    client = TestClient(app)
    code_signin(client, outbox, "kyle@example.com")

    page = client.get("/account").text
    assert "Add a password (optional)" in page

    short = client.post("/account/password", data={"password": "short"})
    assert "at least 8 characters" in short.text

    ok = client.post(
        "/account/password", data={"password": "longenough"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert "Password added" in client.get(ok.headers["location"]).text
    assert "Add a password (optional)" not in client.get("/account").text

    client.post("/logout")  # the new password works at the classic form...
    resp = client.post(
        "/login", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    client.post("/logout")  # ...and the code path keeps working too
    code_signin(client, outbox, "kyle@example.com")


def test_password_accounts_do_not_see_add_password(app, outbox):
    client = TestClient(app)
    client.post(
        "/signup", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert "Add a password" not in client.get("/account").text
    # The route refuses to replace an existing password: changes go
    # through the code-verified reset flow only.
    resp = client.post(
        "/account/password", data={"password": "hijacked1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    users: UserStore = client.app.state.users
    assert users.authenticate("kyle@example.com", "longenough") is not None
    assert users.authenticate("kyle@example.com", "hijacked1") is None


def test_signup_with_passwordless_accounts_email_still_needs_the_code(
    app, outbox
):
    # "Add a password" via the signup form: allowed, but only with inbox
    # proof — a passwordless account must not be capturable by whoever
    # types its email into signup first.
    client = TestClient(app)
    code_signin(client, outbox, "kyle@example.com")
    client.post("/logout")

    form = {"email": "kyle@example.com", "password": "longenough"}
    resp = client.post("/signup", data=form, follow_redirects=False)
    assert resp.status_code == 200 and "6-digit code" in resp.text
    ok = client.post(
        "/signup", data={**form, "code": last_code(outbox)},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    user = get_user(client, "kyle@example.com")
    assert user.has_password and user.email_verified
    assert count_users(client) == 1


# -- KPI cohorts --------------------------------------------------------------

def test_kpis_count_code_claimed_accounts_as_claimed(tmp_path):
    users = UserStore(tmp_path / "swinglab.db")
    users.verify_email_signin("code@example.com")  # claimed, passwordless
    users.upsert_store_customer("stub@example.com", "9")  # unclaimed stub
    cfg = Config()
    cfg.web["require_account"] = True
    kpis = {k.key: k for k in compute_kpis(tmp_path / "swinglab.db", cfg)}
    # Cohort of 1: the code-claimed account counts, the stub never does.
    assert kpis["activation_rate"].denominator == 1


# -- the store layer ----------------------------------------------------------

def test_verify_email_signin_unit(tmp_path):
    users = UserStore(tmp_path / "db.sqlite")
    user = users.verify_email_signin("  New@Example.COM ")
    assert user.email == "new@example.com"  # normalized
    assert user.email_verified and not user.has_password

    again = users.verify_email_signin("new@example.com")
    assert again.id == user.id  # idempotent — same row
    assert again.email_verified_at == user.email_verified_at  # stamp kept

    with pytest.raises(ValueError):
        users.verify_email_signin("not-an-email")
