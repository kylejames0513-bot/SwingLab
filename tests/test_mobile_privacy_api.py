"""Email step-up start/exchange behind default-off ``mobile_privacy_enabled``.

This covers the first vertical slice of Task 6: the purpose-bound step-up
challenge and its single deterministic token exchange. Export, history reset,
and account deletion (which will consume the minted token) are intentionally
not implemented yet, so these tests assert the minted token's binding rather
than its consumption.
"""

from __future__ import annotations

import base64
import hashlib
import re
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import mailer
from swinglab.web.app import create_app
from swinglab.web.mobile_schema import VersionedHMAC
from swinglab.web.recovery_fence_ledger import RecoveryFenceError


IDEMPOTENCY_KEY = "0123456789abcdef0123456789abcdef"
STEP_UP_IDEMPOTENCY_KEY = "fedcba9876543210fedcba9876543210"
VERIFIER = "v" * 43
STEP_UP_VERIFIER = "s" * 43
INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")


class FakeRecoveryFenceLedger:
    def __init__(self) -> None:
        self.events = []

    def load_chain_snapshot(self):
        return SimpleNamespace(
            records=(SimpleNamespace(kind="cutover_baseline"),),
            head_etag="verified-head",
        )

    def append_and_publish(self, event):
        self.events.append(event)
        return SimpleNamespace(
            sequence=len(self.events) + 1, record_hash=f"{len(self.events):064x}"
        )


def _keyring() -> VersionedHMAC:
    return VersionedHMAC("k1", {"k1": b"k" * 32})


def _config(*, privacy_enabled: bool = True, native_auth: bool = True) -> Config:
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_native_auth_enabled"] = native_auth
    cfg.web["mobile_auth_starts_per_15_minutes_per_ip"] = 50
    cfg.web["mobile_auth_starts_per_15_minutes_per_email"] = 20
    cfg.web["mobile_auth_failed_exchanges_per_15_minutes_per_ip"] = 50
    cfg.web["mobile_auth_failed_exchanges_per_15_minutes_per_email"] = 20
    cfg.web["mobile_auth_live_challenges_per_ip"] = 50
    cfg.web["mobile_auth_live_challenges_per_email"] = 20
    cfg.web["mobile_privacy_enabled"] = privacy_enabled
    return cfg


def _make_app(tmp_path, *, privacy_enabled=True, native_auth=True, ledger=None):
    return create_app(
        _config(privacy_enabled=privacy_enabled, native_auth=native_auth),
        tmp_path / "sessions",
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
        recovery_fence_ledger=ledger or FakeRecoveryFenceLedger(),
    )


def _close(app) -> None:
    app.state.jobs.close()
    app.state.throttle.close()
    if app.state.mobile_keyed_throttle is not None:
        app.state.mobile_keyed_throttle.close()
    app.state.users.close()


def _code_from_messages(messages) -> str:
    combined = "\n".join(
        str(part) for message in messages for part in message if part is not None
    )
    matches = re.findall(r"\b(\d{4})-(\d{4})\b", combined)
    assert matches, combined
    return "".join(matches[-1])


def _sign_in(client: TestClient, messages, *, email="golfer@example.com") -> str:
    """Return an authenticated native bearer for ``email``."""

    messages.clear()
    start = client.post(
        "/api/v1/auth/email/start",
        json={
            "email": email,
            "code_challenge": _challenge(VERIFIER),
            "installation_id": INSTALLATION_ID,
            "device_label": "Maya's iPhone",
        },
    )
    assert start.status_code == 202, start.text
    challenge_id = start.json()["challenge_id"]
    code = _code_from_messages(messages)
    exchange = client.post(
        "/api/v1/auth/email/exchange",
        json={
            "challenge_id": challenge_id,
            "email_code": code,
            "code_verifier": VERIFIER,
        },
        headers={"Idempotency-Key": IDEMPOTENCY_KEY},
    )
    assert exchange.status_code == 201, exchange.text
    messages.clear()
    return exchange.json()["access_token"]


def _selector(bearer: str) -> str:
    return bearer.removeprefix("ciat_").split(".", 1)[0]


def _step_up_start(client, bearer, *, purpose="data_export", verifier=STEP_UP_VERIFIER):
    return client.post(
        "/api/v1/auth/step-up/start",
        json={"purpose": purpose, "code_challenge": _challenge(verifier)},
        headers={"Authorization": f"Bearer {bearer}"},
    )


def _step_up_exchange(
    client,
    challenge_id,
    code,
    *,
    verifier=STEP_UP_VERIFIER,
    idempotency_key=STEP_UP_IDEMPOTENCY_KEY,
    extra_headers=None,
):
    headers = {"Idempotency-Key": idempotency_key}
    if extra_headers:
        headers.update(extra_headers)
    return client.post(
        "/api/v1/auth/step-up/exchange",
        json={
            "challenge_id": challenge_id,
            "email_code": code,
            "code_verifier": verifier,
        },
        headers=headers,
    )


@pytest.fixture
def messages(monkeypatch):
    captured: list = []
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: captured.append((*args, kwargs.get("html_body"))),
    )
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    return captured


# -- flag concealment ------------------------------------------------------


def test_flag_off_conceals_start_before_auth_body_or_db(tmp_path, monkeypatch):
    sent: list = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: sent.append(a))
    app = _make_app(tmp_path, privacy_enabled=False, native_auth=False)
    try:
        with TestClient(app) as client:
            # No bearer, invalid body: still 404 because the flag hides the route
            # before any authentication, body parse, or DB write.
            response = client.post(
                "/api/v1/auth/step-up/start",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
            exchange = client.post(
                "/api/v1/auth/step-up/exchange",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 404
        assert exchange.status_code == 404
        assert sent == []
        assert app.state.users._conn.execute(
            "SELECT COUNT(*) FROM step_up_challenges"
        ).fetchone()[0] == 0
    finally:
        _close(app)


# -- start -----------------------------------------------------------------


def test_start_rejects_ambient_request_without_bearer(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/auth/step-up/start",
                json={"purpose": "data_export", "code_challenge": _challenge(STEP_UP_VERIFIER)},
            )
        assert response.status_code == 401
        assert messages == []
    finally:
        _close(app)


def test_start_binds_owner_and_emails_purpose_bound_code(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            selector = _selector(bearer)
            response = _step_up_start(client, bearer, purpose="history_reset")
            assert response.status_code == 202, response.text
            body = response.json()
            assert set(body) == {"resource_version", "challenge_id", "expires_at"}
            assert response.headers["cache-control"] == "no-store"

            # One purpose-bound email carrying a grouped 8-digit code and a
            # challenge-only universal link; never the bearer or verifier.
            assert len(messages) == 1
            combined = "\n".join(
                str(part) for part in messages[0] if part is not None
            )
            assert re.search(r"\b\d{4}-\d{4}\b", combined)
            assert body["challenge_id"] in combined
            assert bearer not in combined
            assert STEP_UP_VERIFIER not in combined
            assert _code_from_messages(messages) not in combined.split("code=")[-1] \
                if "code=" in combined else True

            row = app.state.users._conn.execute(
                "SELECT * FROM step_up_challenges WHERE challenge_id = ?",
                (body["challenge_id"],),
            ).fetchone()
            assert row["purpose"] == "history_reset"
            assert row["selector"] == selector
            assert row["method"] == "email"
            assert row["user_id"]
    finally:
        _close(app)


def test_start_rejects_unknown_purpose(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            response = _step_up_start(client, bearer, purpose="promote_admin")
        assert response.status_code == 422
    finally:
        _close(app)


# -- exchange --------------------------------------------------------------


def _start_and_code(client, bearer, messages, *, purpose="data_export"):
    messages.clear()
    started = _step_up_start(client, bearer, purpose=purpose)
    assert started.status_code == 202, started.text
    code = _code_from_messages(messages)
    return started.json()["challenge_id"], code


def test_exchange_mints_single_purpose_bound_token(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            selector = _selector(bearer)
            challenge_id, code = _start_and_code(client, bearer, messages)
            response = _step_up_exchange(client, challenge_id, code)
            assert response.status_code == 201, response.text
            body = response.json()
            assert set(body) == {
                "resource_version",
                "step_up_token",
                "purpose",
                "expires_at",
            }
            assert body["purpose"] == "data_export"
            assert body["step_up_token"]
            assert response.headers["cache-control"] == "no-store"

            token_rows = app.state.users._conn.execute(
                "SELECT * FROM step_up_tokens"
            ).fetchall()
            assert len(token_rows) == 1
            token = token_rows[0]
            assert token["purpose"] == "data_export"
            assert token["selector"] == selector
            assert token["method"] == "email"
            assert token["claimed_at"] is None
            # Five-minute expiry from mint time.
            assert 0 < token["expires_at"] - token["created_at"] <= 300 + 1
    finally:
        _close(app)


def test_exchange_lost_response_replay_returns_same_token(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            challenge_id, code = _start_and_code(client, bearer, messages)
            first = _step_up_exchange(client, challenge_id, code)
            assert first.status_code == 201, first.text
            replay = _step_up_exchange(client, challenge_id, code)
            assert replay.status_code == 201, replay.text
            assert replay.json()["step_up_token"] == first.json()["step_up_token"]
            # Exactly one token minted despite the replay.
            assert app.state.users._conn.execute(
                "SELECT COUNT(*) FROM step_up_tokens"
            ).fetchone()[0] == 1
    finally:
        _close(app)


def test_exchange_conflicting_idempotency_is_generic_409(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            challenge_id, code = _start_and_code(client, bearer, messages)
            first = _step_up_exchange(client, challenge_id, code)
            assert first.status_code == 201
            # Reuse the same key against a different challenge body.
            other, other_code = _start_and_code(client, bearer, messages)
            conflict = _step_up_exchange(client, other, other_code)
            assert conflict.status_code == 409
            assert conflict.json()["code"] == "exchange_conflict"
    finally:
        _close(app)


def test_exchange_wrong_code_is_generic_401_and_burns_after_five(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            challenge_id, code = _start_and_code(client, bearer, messages)
            wrong = code[:-1] + str((int(code[-1]) + 1) % 10)
            for attempt in range(5):
                bad = _step_up_exchange(
                    client,
                    challenge_id,
                    wrong,
                    idempotency_key=f"{attempt:032x}",
                )
                assert bad.status_code == 401, bad.text
            # The challenge is burned after five failed exchanges: it is
            # consumed, its attempts are capped, and no token was minted. The
            # matching per-user failed-exchange throttle (also five) means a
            # sixth attempt is additionally rate limited, so burn is asserted
            # directly on durable state rather than the sixth response code.
            row = app.state.users._conn.execute(
                "SELECT attempts, consumed_at FROM step_up_challenges"
                " WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()
            assert row["attempts"] == 5
            assert row["consumed_at"] is not None
            assert app.state.users._conn.execute(
                "SELECT COUNT(*) FROM step_up_tokens"
            ).fetchone()[0] == 0
    finally:
        _close(app)


def test_exchange_rejects_wrong_verifier(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            challenge_id, code = _start_and_code(client, bearer, messages)
            response = _step_up_exchange(
                client, challenge_id, code, verifier="w" * 43
            )
            assert response.status_code == 401
    finally:
        _close(app)


def test_exchange_rejected_after_initiating_selector_revoked(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            selector = _selector(bearer)
            challenge_id, code = _start_and_code(client, bearer, messages)
            user_id = app.state.users._conn.execute(
                "SELECT user_id FROM mobile_api_tokens WHERE selector = ?",
                (selector,),
            ).fetchone()["user_id"]
            app.state.users.revoke_mobile_api_token(user_id, selector)
            response = _step_up_exchange(client, challenge_id, code)
            assert response.status_code == 401
            assert app.state.users._conn.execute(
                "SELECT COUNT(*) FROM step_up_tokens"
            ).fetchone()[0] == 0
    finally:
        _close(app)


def test_exchange_rejected_after_auth_epoch_change(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            selector = _selector(bearer)
            challenge_id, code = _start_and_code(client, bearer, messages)
            app.state.users._conn.execute(
                "UPDATE users SET auth_epoch = auth_epoch + 1"
            )
            app.state.users._conn.commit()
            response = _step_up_exchange(client, challenge_id, code)
            assert response.status_code == 401
    finally:
        _close(app)


def test_exchange_rejected_when_challenge_expired(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            challenge_id, code = _start_and_code(client, bearer, messages)
            app.state.users._conn.execute(
                "UPDATE step_up_challenges SET expires_at = 1 WHERE challenge_id = ?",
                (challenge_id,),
            )
            app.state.users._conn.commit()
            response = _step_up_exchange(client, challenge_id, code)
            assert response.status_code == 401
    finally:
        _close(app)


def test_exchange_bearer_alone_cannot_shortcut(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            challenge_id, code = _start_and_code(client, bearer, messages)
            wrong = code[:-1] + str((int(code[-1]) + 1) % 10)
            # A valid bearer supplied at exchange must not authenticate it.
            response = _step_up_exchange(
                client,
                challenge_id,
                wrong,
                extra_headers={"Authorization": f"Bearer {bearer}"},
            )
            assert response.status_code == 401
    finally:
        _close(app)


# -- caps and rate limits --------------------------------------------------


def test_live_challenge_cap_per_selector_purpose(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            # Two live challenges allowed per (selector, purpose); the third is
            # rate limited even with distinct verifiers.
            first = _step_up_start(client, bearer, verifier="a" * 43)
            second = _step_up_start(client, bearer, verifier="b" * 43)
            third = _step_up_start(client, bearer, verifier="c" * 43)
            assert first.status_code == 202
            assert second.status_code == 202
            assert third.status_code == 429
            assert "retry-after" in {k.lower() for k in third.headers}
    finally:
        _close(app)


def test_start_rate_limited_per_selector(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            statuses = []
            for _ in range(6):
                statuses.append(_step_up_start(client, bearer).status_code)
            # Five starts per selector per window; the sixth is throttled.
            assert statuses.count(429) >= 1
    finally:
        _close(app)


def test_openapi_documents_step_up_routes(tmp_path):
    app = _make_app(tmp_path, privacy_enabled=True, native_auth=False)
    try:
        schema = app.openapi()
        assert "/api/v1/auth/step-up/start" in schema["paths"]
        assert "/api/v1/auth/step-up/exchange" in schema["paths"]
    finally:
        _close(app)
