"""Review-only native authentication and immutable application identity."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.api.auth import MobileAuthContext
from swinglab.web.app import create_app
from swinglab.web.credential_mutations import CredentialMutationRejected
from swinglab.web.mobile_schema import VersionedHMAC
from swinglab.web.recovery_fence_ledger import RecoveryFenceError
from swinglab.web.review_auth import (
    APPLICATION_ID_POLICY_REVISION,
    AppIdentityHeaders,
    DenyReviewAuthAdmission,
    ReviewAuthGrant,
    ReviewAuthStartMatch,
    ReviewBearerScope,
    canonical_mobile_public_origin,
    resolve_mobile_deployment_environment,
    validate_review_auth_settings,
)
from swinglab.web.users import (
    MOBILE_API_TOKEN_PREFIX,
    UserStore,
    hash_password,
    verify_password,
)


IDEMPOTENCY_KEY = "0123456789abcdef0123456789abcdef"
VERIFIER = "r" * 43
INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"


def _challenge(verifier: str = VERIFIER) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")


class FakeRecoveryFenceLedger:
    def __init__(self) -> None:
        self.events = []
        self.outage = False

    def load_chain_snapshot(self):
        return SimpleNamespace(
            records=(SimpleNamespace(kind="cutover_baseline"),),
            head_etag="verified-head",
        )

    def append_and_publish(self, event):
        if self.outage:
            raise RecoveryFenceError("synthetic recovery outage")
        self.events.append(event)
        return SimpleNamespace(
            sequence=len(self.events) + 1,
            record_hash=f"{len(self.events):064x}",
        )


class FakeReviewAdmission:
    """Task-5-shaped fake with a dedicated, provider-scoped scrypt proof."""

    def __init__(self, records: dict[tuple[str, str], dict[str, object]]) -> None:
        self.records = records
        self.start_calls = 0
        self.exchange_calls = 0
        self.recheck_calls = 0
        self.lane_calls = 0
        self.open = True

    def any_lane_active(self) -> bool:
        self.lane_calls += 1
        return self.open and bool(self.records)

    def match_start(self, *, provider, account, identity, now):
        self.start_calls += 1
        record = self.records.get((provider, account))
        if (
            not self.open
            or record is None
            or record["platform"] != identity.platform
            or record["version"] != identity.app_version
            or record["build"] != identity.app_build
        ):
            return None
        return ReviewAuthStartMatch(user_id=str(record["user_id"]))

    def verify_exchange(self, *, challenge, password, identity, now):
        self.exchange_calls += 1
        record = next(
            (
                candidate
                for candidate in self.records.values()
                if candidate["user_id"] == challenge.matched_user_id
                and candidate["provider"] == challenge.provider
            ),
            None,
        )
        if (
            not self.open
            or record is None
            or identity != challenge.identity
            or not verify_password(password, str(record["password_hash"]))
        ):
            return None
        return ReviewAuthGrant(
            user_id=str(record["user_id"]),
            provider=str(record["provider"]),
            credential_hmac_key_id="entitlements-k1",
            credential_hmac=str(record["credential_hmac"]),
            lane_revision=int(record["lane_revision"]),
            bearer_expires_at=float(now) + 3600,
        )

    def recheck(self, scope: ReviewBearerScope, *, now: float) -> bool:
        self.recheck_calls += 1
        return self.open and any(
            record["user_id"] == scope.user_id
            and record["provider"] == scope.provider
            and record["build"] == scope.build
            and record["credential_hmac"] == scope.credential_hmac
            and record["lane_revision"] == scope.lane_revision
            for record in self.records.values()
        )


def _keyring() -> VersionedHMAC:
    return VersionedHMAC("k1", {"k1": b"k" * 32})


def _headers(
    *,
    environment: str = "development",
    platform: str = "ios",
    version: str = "1.2.3",
    build: str = "42",
    application_id: str = "com.caddieinsight.app.dev",
) -> dict[str, str]:
    return {
        "X-CaddieInsight-Environment": environment,
        "X-CaddieInsight-Platform": platform,
        "X-CaddieInsight-App-Version": version,
        "X-CaddieInsight-App-Build": build,
        "X-CaddieInsight-Application-Id": application_id,
    }


def _config() -> Config:
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_native_auth_enabled"] = False
    cfg.web["review_auth_starts_per_15_minutes_per_account"] = 20
    return cfg


def _seed_review_users(sessions, keyring):
    sessions.mkdir(parents=True, exist_ok=True)
    users = UserStore(sessions / "swinglab.db", mobile_state_hmac=keyring)
    apple = users.verify_email_signin("apple-review@synthetic.invalid")
    google = users.verify_email_signin("google-review@synthetic.invalid")
    users.close()
    return apple, google


def _admission(apple, google) -> FakeReviewAdmission:
    return FakeReviewAdmission(
        {
            ("apple", "apple-reviewer"): {
                "user_id": apple.id,
                "provider": "apple",
                "platform": "ios",
                "version": "1.2.3",
                "build": "42",
                "password_hash": hash_password("apple-only-passphrase"),
                "credential_hmac": "a" * 64,
                "lane_revision": 7,
            },
            ("google", "google-reviewer"): {
                "user_id": google.id,
                "provider": "google",
                "platform": "android",
                "version": "1.2.3",
                "build": "42",
                "password_hash": hash_password("google-only-passphrase"),
                "credential_hmac": "b" * 64,
                "lane_revision": 9,
            },
        }
    )


def _make_app(tmp_path, admission):
    sessions = tmp_path / "sessions"
    keyring = _keyring()
    apple, google = _seed_review_users(sessions, keyring)
    app = create_app(
        _config(),
        sessions,
        start_background_workers=False,
        mobile_state_hmac=keyring,
        recovery_fence_ledger=FakeRecoveryFenceLedger(),
        review_auth_admission=admission(apple, google),
    )
    return app, apple, google


def _create_review_app(sessions, keyring, admission, *, cfg=None, ledger=None):
    return create_app(
        cfg or _config(),
        sessions,
        start_background_workers=False,
        mobile_state_hmac=keyring,
        recovery_fence_ledger=ledger or FakeRecoveryFenceLedger(),
        review_auth_admission=admission,
    )


def _close(app) -> None:
    app.state.jobs.close()
    app.state.throttle.close()
    if app.state.mobile_keyed_throttle is not None:
        app.state.mobile_keyed_throttle.close()
    app.state.users.close()


def _start(
    client,
    *,
    provider="apple",
    account="apple-reviewer",
    headers=None,
    verifier=VERIFIER,
    installation_id=INSTALLATION_ID,
):
    return client.post(
        "/api/v1/auth/review/start",
        headers=headers or _headers(),
        json={
            "provider": provider,
            "account": account,
            "code_challenge": _challenge(verifier),
            "installation_id": installation_id,
            "device_label": "App Review iPhone",
        },
    )


def _exchange(
    client,
    challenge_id,
    *,
    password="apple-only-passphrase",
    headers=None,
    idempotency_key=IDEMPOTENCY_KEY,
    verifier=VERIFIER,
):
    return client.post(
        "/api/v1/auth/review/exchange",
        headers={
            **(headers or _headers()),
            "Idempotency-Key": idempotency_key,
        },
        json={
            "challenge_id": challenge_id,
            "password": password,
            "code_verifier": verifier,
        },
    )


def test_deployment_environment_and_public_origin_are_closed(monkeypatch):
    monkeypatch.delenv("CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", raising=False)
    assert resolve_mobile_deployment_environment() == "development"
    monkeypatch.setenv("CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", "staging")
    assert resolve_mobile_deployment_environment() == "staging"
    monkeypatch.setenv("CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", "preview")
    with pytest.raises(ValueError, match="development, staging, or production"):
        resolve_mobile_deployment_environment()

    assert canonical_mobile_public_origin("https://app.example/", "staging") == (
        "https://app.example"
    )
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        canonical_mobile_public_origin(None, "production")
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        canonical_mobile_public_origin("https://user@app.example/path", "staging")


@pytest.mark.parametrize("value", [True, 0, 101, "5"])
def test_review_auth_abuse_settings_are_closed(value):
    with pytest.raises(ValueError, match="integer from 1 to 100"):
        validate_review_auth_settings(
            {"review_auth_starts_per_15_minutes_per_ip": value}
        )


def test_default_deny_returns_404_before_body_or_mobile_state(tmp_path):
    app = create_app(
        _config(),
        tmp_path / "sessions",
        start_background_workers=False,
        review_auth_admission=DenyReviewAuthAdmission(),
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/auth/review/start",
                content=b"not-json",
            )
        assert response.status_code == 404
        assert app.state.users._conn.execute(
            "SELECT COUNT(*) FROM mobile_review_auth_challenges"
        ).fetchone()[0] == 0
    finally:
        _close(app)


def test_lane_cannot_activate_after_startup_skipped_recovery_gate(tmp_path):
    holder = {}

    def factory(apple, google):
        value = _admission(apple, google)
        value.open = False
        holder["admission"] = value
        return value

    app, _, _ = _make_app(tmp_path, factory)
    try:
        holder["admission"].open = True
        with TestClient(app) as client:
            response = _start(client)
        assert response.status_code == 404
        assert holder["admission"].start_calls == 0
        assert app.state.users._conn.execute(
            "SELECT COUNT(*) FROM mobile_review_auth_challenges"
        ).fetchone()[0] == 0
    finally:
        _close(app)


def test_exposed_lane_parses_identity_before_dynamic_availability(tmp_path):
    holder = {}

    def factory(apple, google):
        value = _admission(apple, google)
        holder["admission"] = value
        return value

    app, _, _ = _make_app(tmp_path, factory)
    try:
        holder["admission"].open = False
        calls_before_request = holder["admission"].lane_calls
        missing = _headers()
        missing.pop("X-CaddieInsight-App-Build")
        with TestClient(app) as client:
            response = _start(client, headers=missing)
        assert response.status_code == 422
        assert holder["admission"].lane_calls == calls_before_request
        assert app.state.users._conn.execute(
            "SELECT COUNT(*) FROM mobile_review_auth_challenges"
        ).fetchone()[0] == 0
    finally:
        _close(app)


def test_exchange_identity_mismatch_never_reaches_credential_admission(tmp_path):
    holder = {}

    def factory(apple, google):
        value = _admission(apple, google)
        holder["admission"] = value
        return value

    app, _, _ = _make_app(tmp_path, factory)
    try:
        with TestClient(app) as client:
            started = _start(client)
            calls_before_exchange = holder["admission"].exchange_calls
            rejected = _exchange(
                client,
                started.json()["challenge_id"],
                headers=_headers(build="43"),
            )
        assert rejected.status_code == 401
        assert rejected.json()["code"] == "authentication_rejected"
        assert holder["admission"].exchange_calls == calls_before_exchange
    finally:
        _close(app)


def test_identity_headers_fail_before_admission_or_state(tmp_path):
    holder = {}

    def factory(apple, google):
        value = _admission(apple, google)
        holder["admission"] = value
        return value

    app, _, _ = _make_app(tmp_path, factory)
    try:
        with TestClient(app) as client:
            missing = _headers()
            missing.pop("X-CaddieInsight-App-Build")
            assert _start(client, headers=missing).status_code == 422
            assert _start(
                client,
                headers=_headers(application_id="com.example.attacker"),
            ).status_code == 422
            assert _start(
                client,
                headers=_headers(environment="production"),
            ).status_code == 422
            assert _start(
                client,
                headers=[*_headers().items(), ("X-CaddieInsight-App-Build", "43")],
            ).status_code == 422
        assert holder["admission"].start_calls == 0
        assert app.state.users._conn.execute(
            "SELECT COUNT(*) FROM mobile_review_auth_challenges"
        ).fetchone()[0] == 0
    finally:
        _close(app)


def test_review_exchange_is_scoped_replayable_and_rechecked(tmp_path):
    holder = {}

    def factory(apple, google):
        value = _admission(apple, google)
        holder["admission"] = value
        return value

    app, apple, _ = _make_app(tmp_path, factory)
    try:
        with TestClient(app) as client:
            started = _start(client)
            assert started.status_code == 202
            first = _exchange(client, started.json()["challenge_id"])
            assert first.status_code == 201, first.text
            replay = _exchange(client, started.json()["challenge_id"])
            assert replay.status_code == 201
            assert replay.json()["access_token"] == first.json()["access_token"]

            token = first.json()["access_token"]
            me = client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
            )
            assert me.status_code == 200
            principal = app.state.users.authenticate_mobile_api_principal(token)
            assert principal is not None
            assert principal.user.id == apple.id
            assert principal.review_provider == "apple"
            assert principal.review_build == "42"
            assert principal.review_expires_at is not None
            assert holder["admission"].recheck_calls >= 1

            holder["admission"].open = False
            rejected = client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
            )
            assert rejected.status_code == 401
            row = app.state.users._conn.execute(
                "SELECT revoked_at FROM mobile_api_tokens WHERE selector = ?",
                (principal.selector,),
            ).fetchone()
            assert row["revoked_at"] is not None
    finally:
        _close(app)


def test_lane_close_cancels_an_existing_review_mutation_lease_before_commit(tmp_path):
    holder = {}

    def factory(apple, google):
        value = _admission(apple, google)
        holder["admission"] = value
        return value

    app, _, _ = _make_app(tmp_path, factory)
    try:
        with TestClient(app) as client:
            exchanged = _exchange(client, _start(client).json()["challenge_id"])
            principal = app.state.users.authenticate_mobile_api_principal(
                exchanged.json()["access_token"]
            )
            assert principal is not None
            context = MobileAuthContext(
                user=principal.user,
                via_bearer=True,
                selector=principal.selector,
                auth_epoch=principal.auth_epoch,
                review_provider=principal.review_provider,
                review_build=principal.review_build,
                review_expires_at=principal.review_expires_at,
                review_credential_hmac_key_id=(
                    principal.review_credential_hmac_key_id
                ),
                review_credential_hmac=principal.review_credential_hmac,
                review_lane_revision=principal.review_lane_revision,
            )
            lease = app.state.credential_mutation_guard.admit(context)
            holder["admission"].open = False

            rejected = client.get(
                "/api/v1/me",
                headers={"Authorization": f"Bearer {exchanged.json()['access_token']}"},
            )
            assert rejected.status_code == 401
            assert lease.cancellation_requested is True
            assert app.state.users._conn.execute(
                "SELECT revoked_at FROM mobile_api_tokens WHERE selector = ?",
                (principal.selector,),
            ).fetchone()[0] is None

            with app.state.users._lock:
                app.state.users._conn.execute("BEGIN IMMEDIATE")
                try:
                    with pytest.raises(CredentialMutationRejected):
                        lease.validate_locked(app.state.users)
                finally:
                    app.state.users._conn.rollback()
            lease.release()
            assert client.get(
                "/api/v1/me",
                headers={"Authorization": f"Bearer {exchanged.json()['access_token']}"},
            ).status_code == 401
            assert app.state.users._conn.execute(
                "SELECT revoked_at FROM mobile_api_tokens WHERE selector = ?",
                (principal.selector,),
            ).fetchone()[0] is not None
    finally:
        _close(app)


@pytest.mark.parametrize(
    ("column", "value"),
    (("review_provider", None), ("review_expires_at", float("inf"))),
)
def test_partial_or_nonfinite_review_scope_never_becomes_an_ordinary_bearer(
    tmp_path, column, value
):
    app, _, _ = _make_app(tmp_path, _admission)
    try:
        with TestClient(app) as client:
            exchanged = _exchange(client, _start(client).json()["challenge_id"])
            token = exchanged.json()["access_token"]
            principal = app.state.users.authenticate_mobile_api_principal(token)
            assert principal is not None
            app.state.users._conn.execute(
                f"UPDATE mobile_api_tokens SET {column} = ? WHERE selector = ?",
                (value, principal.selector),
            )
            app.state.users._conn.commit()

            rejected = client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert rejected.status_code == 401
    finally:
        _close(app)


def test_unknown_and_wrong_password_are_generic_and_secrets_are_not_persisted(tmp_path):
    app, _, _ = _make_app(tmp_path, _admission)
    try:
        with TestClient(app) as client:
            unknown = _start(client, account="unknown-reviewer")
            known = _start(client)
            assert unknown.status_code == known.status_code == 202
            assert set(unknown.json()) == set(known.json())
            wrong_unknown = _exchange(
                client,
                unknown.json()["challenge_id"],
                password="wrong-secret-password",
            )
            wrong_known = _exchange(
                client,
                known.json()["challenge_id"],
                password="wrong-secret-password",
                idempotency_key="fedcba9876543210fedcba9876543210",
            )
            assert wrong_unknown.status_code == wrong_known.status_code == 401
            assert wrong_unknown.json()["code"] == wrong_known.json()["code"]

        dump = "\n".join(app.state.users._conn.iterdump())
        for secret in (
            "unknown-reviewer",
            "apple-reviewer",
            "wrong-secret-password",
            VERIFIER,
            IDEMPOTENCY_KEY,
        ):
            assert secret not in dump
    finally:
        _close(app)


def test_review_start_account_throttle_uses_protected_candidates_across_rotation(
    tmp_path,
):
    sessions = tmp_path / "sessions"
    old_keyring = VersionedHMAC("k1", {"k1": b"1" * 32})
    apple, google = _seed_review_users(sessions, old_keyring)
    cfg = _config()
    cfg.web["review_auth_starts_per_15_minutes_per_account"] = 1
    first_app = _create_review_app(
        sessions, old_keyring, _admission(apple, google), cfg=cfg
    )
    try:
        with TestClient(first_app) as client:
            assert _start(client).status_code == 202
        challenge_key = first_app.state.users._conn.execute(
            "SELECT account_hmac_key_id, account_hmac"
            " FROM mobile_review_auth_challenges"
        ).fetchone()
        rate_key = first_app.state.users._conn.execute(
            "SELECT key_id, key_digest FROM mobile_rate_limit_events"
            " WHERE domain = 'review-auth-start-account'"
        ).fetchone()
        assert tuple(rate_key) == tuple(challenge_key)
    finally:
        _close(first_app)

    rotated_keyring = VersionedHMAC(
        "k2", {"k1": b"1" * 32, "k2": b"2" * 32}
    )
    restarted_app = _create_review_app(
        sessions, rotated_keyring, _admission(apple, google), cfg=cfg
    )
    try:
        with TestClient(restarted_app) as client:
            denied = _start(client)
        assert denied.status_code == 429
        assert denied.json()["code"] == "rate_limited"
        assert restarted_app.state.users._conn.execute(
            "SELECT COUNT(*) FROM mobile_rate_limit_events"
            " WHERE domain = 'review-auth-start-ip'"
        ).fetchone()[0] == 1
    finally:
        _close(restarted_app)


def test_known_review_exchange_account_throttle_keeps_its_rotation_anchor(tmp_path):
    sessions = tmp_path / "sessions"
    old_keyring = VersionedHMAC("k1", {"k1": b"1" * 32})
    apple, google = _seed_review_users(sessions, old_keyring)
    cfg = _config()
    cfg.web["review_auth_failed_exchanges_per_15_minutes_per_account"] = 1
    first_app = _create_review_app(
        sessions, old_keyring, _admission(apple, google), cfg=cfg
    )
    try:
        with TestClient(first_app) as client:
            challenge_id = _start(client).json()["challenge_id"]
            rejected = _exchange(client, challenge_id, password="wrong-password")
        assert rejected.status_code == 401
        challenge_key = first_app.state.users._conn.execute(
            "SELECT account_hmac_key_id, account_hmac"
            " FROM mobile_review_auth_challenges WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()
        rate_key = first_app.state.users._conn.execute(
            "SELECT key_id, key_digest FROM mobile_rate_limit_events"
            " WHERE domain = 'review-auth-exchange-account'"
        ).fetchone()
        assert tuple(rate_key) == tuple(challenge_key)
    finally:
        _close(first_app)

    rotated_keyring = VersionedHMAC(
        "k2", {"k1": b"1" * 32, "k2": b"2" * 32}
    )
    restarted_app = _create_review_app(
        sessions, rotated_keyring, _admission(apple, google), cfg=cfg
    )
    try:
        with TestClient(restarted_app) as client:
            limited = _exchange(client, challenge_id, password="wrong-password")
        assert limited.status_code == 429
        assert limited.json()["code"] == "rate_limited"
    finally:
        _close(restarted_app)


def test_old_and_new_rotation_challenges_share_one_exchange_account_window(tmp_path):
    sessions = tmp_path / "sessions"
    old_keyring = VersionedHMAC("k1", {"k1": b"1" * 32})
    apple, google = _seed_review_users(sessions, old_keyring)
    cfg = _config()
    cfg.web["review_auth_failed_exchanges_per_15_minutes_per_account"] = 2
    first_app = _create_review_app(
        sessions, old_keyring, _admission(apple, google), cfg=cfg
    )
    try:
        with TestClient(first_app) as client:
            old_challenge = _start(client).json()["challenge_id"]
            first = _exchange(client, old_challenge, password="wrong-password")
        assert first.status_code == 401
    finally:
        _close(first_app)

    rotated_keyring = VersionedHMAC(
        "k2", {"k1": b"1" * 32, "k2": b"2" * 32}
    )
    restarted_app = _create_review_app(
        sessions, rotated_keyring, _admission(apple, google), cfg=cfg
    )
    try:
        with TestClient(restarted_app) as client:
            new_challenge = _start(
                client,
                verifier="s" * 43,
                installation_id="22222222-2222-4222-8222-222222222222",
            ).json()["challenge_id"]
            second = _exchange(
                client,
                new_challenge,
                password="wrong-password",
                verifier="s" * 43,
                idempotency_key="2" * 32,
            )
            limited = _exchange(
                client,
                old_challenge,
                password="wrong-password",
                idempotency_key="3" * 32,
            )
        assert second.status_code == 401
        assert limited.status_code == 429
        assert limited.json()["code"] == "rate_limited"
        anchors = restarted_app.state.users._conn.execute(
            "SELECT DISTINCT account_hmac_key_id, account_hmac"
            " FROM mobile_review_auth_challenges"
            " WHERE challenge_id IN (?, ?)",
            (old_challenge, new_challenge),
        ).fetchall()
        assert len(anchors) == 1
    finally:
        _close(restarted_app)


def test_expired_known_review_challenge_debits_account_and_ip_without_admission(
    tmp_path,
):
    holder = {}

    def factory(apple, google):
        value = _admission(apple, google)
        holder["admission"] = value
        return value

    cfg = _config()
    cfg.web["review_auth_failed_exchanges_per_15_minutes_per_account"] = 1
    sessions = tmp_path / "sessions"
    keyring = _keyring()
    apple, google = _seed_review_users(sessions, keyring)
    app = _create_review_app(sessions, keyring, factory(apple, google), cfg=cfg)
    try:
        with TestClient(app) as client:
            challenge_id = _start(client).json()["challenge_id"]
            app.state.users._conn.execute(
                "UPDATE mobile_review_auth_challenges SET expires_at = ?"
                " WHERE challenge_id = ?",
                (app.state.review_auth_service._now() - 1, challenge_id),
            )
            app.state.users._conn.commit()
            calls_before = holder["admission"].exchange_calls
            first = _exchange(client, challenge_id, password="wrong-password")
            limited = _exchange(
                client,
                challenge_id,
                password="wrong-password",
                idempotency_key="4" * 32,
            )
        assert first.status_code == 401
        assert limited.status_code == 429
        assert holder["admission"].exchange_calls == calls_before
        domains = app.state.users._conn.execute(
            "SELECT domain, COUNT(*) FROM mobile_rate_limit_events"
            " WHERE domain LIKE 'review-auth-exchange-%'"
            " GROUP BY domain ORDER BY domain"
        ).fetchall()
        assert [tuple(row) for row in domains] == [
            ("review-auth-exchange-account", 1),
            ("review-auth-exchange-ip", 1),
        ]
    finally:
        _close(app)


def test_malformed_admission_grant_fails_as_generic_auth_rejection(tmp_path):
    class MalformedGrantAdmission(FakeReviewAdmission):
        def verify_exchange(self, **kwargs):
            grant = super().verify_exchange(**kwargs)
            assert grant is not None
            return replace(grant, credential_hmac_key_id="unsafe key id")

    def factory(apple, google):
        return MalformedGrantAdmission(_admission(apple, google).records)

    app, _, _ = _make_app(tmp_path, factory)
    try:
        with TestClient(app) as client:
            started = _start(client)
            rejected = _exchange(client, started.json()["challenge_id"])
        assert rejected.status_code == 401
        assert rejected.json()["code"] == "authentication_rejected"
    finally:
        _close(app)


def test_provider_scopes_cannot_cross_and_health_is_code_owned(tmp_path):
    app, apple, google = _make_app(tmp_path, _admission)
    try:
        with TestClient(app) as client:
            google_headers = _headers(
                platform="android",
                application_id="com.caddieinsight.app.dev",
            )
            started = _start(
                client,
                provider="google",
                account="google-reviewer",
                headers=google_headers,
            )
            assert started.status_code == 202
            exchanged = _exchange(
                client,
                started.json()["challenge_id"],
                password="google-only-passphrase",
                headers=google_headers,
            )
            assert exchanged.status_code == 201
            principal = app.state.users.authenticate_mobile_api_principal(
                exchanged.json()["access_token"]
            )
            assert principal is not None
            assert principal.user.id == google.id
            assert principal.user.id != apple.id
            assert principal.review_provider == "google"

            health = client.get("/healthz").json()
            assert health["mobile_deployment_environment"] == "development"
            assert health["mobile_public_origin"] is None
            assert health["mobile_application_id_policy_revision"] == (
                APPLICATION_ID_POLICY_REVISION
            )
            assert health["mobile_allowed_application_ids"] == [
                "com.caddieinsight.app.dev"
            ]
    finally:
        _close(app)


def test_review_device_cap_stays_five_and_expired_scope_frees_a_slot(tmp_path):
    app, _, _ = _make_app(tmp_path, _admission)
    try:
        with TestClient(app) as client:
            tokens = []
            for index in range(5):
                verifier = chr(ord("a") + index) * 43
                installation = f"{index + 1:08x}-1111-4111-8111-111111111111"
                started = _start(
                    client,
                    verifier=verifier,
                    installation_id=installation,
                )
                assert started.status_code == 202
                exchanged = _exchange(
                    client,
                    started.json()["challenge_id"],
                    verifier=verifier,
                    idempotency_key=f"{index + 1:032x}",
                )
                assert exchanged.status_code == 201, exchanged.text
                tokens.append(exchanged.json()["access_token"])

            sixth_verifier = "z" * 43
            sixth = _start(
                client,
                verifier=sixth_verifier,
                installation_id="66666666-1111-4111-8111-111111111111",
            )
            refused = _exchange(
                client,
                sixth.json()["challenge_id"],
                verifier=sixth_verifier,
                idempotency_key="f" * 32,
            )
            assert refused.status_code == 409
            assert refused.json()["code"] == "device_limit"

            first = app.state.users.authenticate_mobile_api_principal(tokens[0])
            assert first is not None
            app.state.users._conn.execute(
                "UPDATE mobile_api_tokens SET review_expires_at = 0"
                " WHERE selector = ?",
                (first.selector,),
            )
            app.state.users._conn.commit()

            replacement_verifier = "y" * 43
            replacement = _start(
                client,
                verifier=replacement_verifier,
                installation_id="77777777-1111-4111-8111-111111111111",
            )
            accepted = _exchange(
                client,
                replacement.json()["challenge_id"],
                verifier=replacement_verifier,
                idempotency_key="e" * 32,
            )
            assert accepted.status_code == 201, accepted.text
            assert app.state.users.authenticate_mobile_api_principal(tokens[0]) is None
            assert app.state.users._conn.execute(
                "SELECT 1 FROM mobile_api_tokens WHERE selector = ?",
                (first.selector,),
            ).fetchone() is None
    finally:
        _close(app)


def test_expired_review_purge_waits_for_nonterminal_exchange(tmp_path):
    app, _, _ = _make_app(tmp_path, _admission)
    try:
        client = TestClient(app)
        started = _start(client)
        exchanged = _exchange(client, started.json()["challenge_id"])
        assert exchanged.status_code == 201
        principal = app.state.users.authenticate_mobile_api_principal(
            exchanged.json()["access_token"]
        )
        assert principal is not None
        connection = app.state.users._conn
        exchange_id = connection.execute(
            "SELECT exchange_id FROM mobile_auth_exchange_journals"
            " WHERE replacement_selector = ?",
            (principal.selector,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE mobile_api_tokens SET review_expires_at = 0"
            " WHERE selector = ?",
            (principal.selector,),
        )
        connection.execute(
            "UPDATE mobile_auth_exchange_journals SET phase = 'prepared'"
            " WHERE exchange_id = ?",
            (exchange_id,),
        )
        connection.commit()

        app.state.review_auth_service.purge_expired()
        assert connection.execute(
            "SELECT 1 FROM mobile_api_tokens WHERE selector = ?",
            (principal.selector,),
        ).fetchone() is not None
        connection.execute(
            "UPDATE mobile_auth_exchange_journals SET phase = 'complete'"
            " WHERE exchange_id = ?",
            (exchange_id,),
        )
        connection.commit()

        app.state.review_auth_service.purge_expired()
        assert connection.execute(
            "SELECT 1 FROM mobile_api_tokens WHERE selector = ?",
            (principal.selector,),
        ).fetchone() is None
    finally:
        _close(app)


def test_expired_pending_review_replacement_terminalizes_after_recovery_readback(
    tmp_path,
):
    sessions = tmp_path / "sessions"
    keyring = VersionedHMAC(
        "k1", {"k1": b"k" * 32, "entitlements-k1": b"e" * 32}
    )
    apple, google = _seed_review_users(sessions, keyring)
    ledger = FakeRecoveryFenceLedger()
    app = _create_review_app(
        sessions, keyring, _admission(apple, google), ledger=ledger
    )
    replacement_selector = ""
    old_token = ""
    pending_challenge = ""
    try:
        with TestClient(app) as client:
            first = _exchange(client, _start(client).json()["challenge_id"])
            assert first.status_code == 201
            old_token = first.json()["access_token"]

            pending_verifier = "p" * 43
            pending_challenge = _start(
                client, verifier=pending_verifier
            ).json()["challenge_id"]
            ledger.outage = True
            pending = _exchange(
                client,
                pending_challenge,
                verifier=pending_verifier,
                idempotency_key="5" * 32,
            )
            assert pending.status_code == 202
            journal = app.state.users._conn.execute(
                "SELECT * FROM mobile_auth_exchange_journals"
                " WHERE exchange_id = ?",
                (pending.json()["exchange_id"],),
            ).fetchone()
            assert journal["phase"] == "prepared"
            replacement_selector = str(journal["replacement_selector"])
            expired_at = app.state.review_auth_service._now() - 1
            app.state.users._conn.execute(
                "UPDATE mobile_api_tokens SET expires_at = ?, review_expires_at = ?"
                " WHERE selector = ?",
                (expired_at, expired_at, replacement_selector),
            )
            app.state.users._conn.execute(
                "UPDATE mobile_auth_exchange_journals SET review_expires_at = ?"
                " WHERE exchange_id = ?",
                (expired_at, journal["exchange_id"]),
            )
            app.state.users._conn.commit()
    finally:
        _close(app)

    ledger.outage = False
    resumed = _create_review_app(
        sessions, keyring, _admission(apple, google), ledger=ledger
    )
    try:
        row = resumed.state.users._conn.execute(
            "SELECT phase, prior_selector, replacement_selector"
            " FROM mobile_auth_exchange_journals"
            " WHERE challenge_id = ?",
            (pending_challenge,),
        ).fetchone()
        assert row["phase"] == "complete"
        assert resumed.state.users._conn.execute(
            "SELECT 1 FROM mobile_api_tokens WHERE selector = ?",
            (replacement_selector,),
        ).fetchone() is None
        prior = resumed.state.users._conn.execute(
            "SELECT state, revoked_at, fenced_at FROM mobile_api_tokens"
            " WHERE selector = ?",
            (row["prior_selector"],),
        ).fetchone()
        assert tuple(prior)[:1] == ("fenced",)
        assert prior["revoked_at"] is not None and prior["fenced_at"] is not None
        assert resumed.state.users.authenticate_mobile_api_principal(old_token) is None
        assert len(ledger.events) == 1

        with TestClient(resumed) as client:
            terminal = _exchange(
                client,
                pending_challenge,
                verifier="p" * 43,
                idempotency_key="5" * 32,
            )
            terminal_replay = _exchange(
                client,
                pending_challenge,
                verifier="p" * 43,
                idempotency_key="5" * 32,
            )
            fresh_verifier = "q" * 43
            fresh = _start(client, verifier=fresh_verifier)
            completed = _exchange(
                client,
                fresh.json()["challenge_id"],
                verifier=fresh_verifier,
                idempotency_key="6" * 32,
            )
        assert terminal.status_code == 409
        assert terminal.json()["code"] == "exchange_conflict"
        assert terminal_replay.status_code == 409
        assert terminal_replay.json() == terminal.json()
        assert completed.status_code == 201
    finally:
        _close(resumed)


def test_expired_prior_fenced_review_replacement_cancels_without_republishing(
    tmp_path, monkeypatch
):
    sessions = tmp_path / "sessions"
    keyring = VersionedHMAC(
        "k1", {"k1": b"k" * 32, "entitlements-k1": b"e" * 32}
    )
    apple, google = _seed_review_users(sessions, keyring)
    ledger = FakeRecoveryFenceLedger()
    app = _create_review_app(
        sessions, keyring, _admission(apple, google), ledger=ledger
    )
    exchange_id = ""
    replacement_selector = ""
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            assert _exchange(
                client, _start(client).json()["challenge_id"]
            ).status_code == 201
            verifier = "u" * 43
            challenge_id = _start(client, verifier=verifier).json()["challenge_id"]

            def crash_after_prior_readback(_row):
                raise RuntimeError("synthetic post-readback crash")

            monkeypatch.setattr(
                app.state.mobile_auth_service,
                "_activate_replacement",
                crash_after_prior_readback,
            )
            crashed = _exchange(
                client,
                challenge_id,
                verifier=verifier,
                idempotency_key="7" * 32,
            )
        assert crashed.status_code == 500
        journal = app.state.users._conn.execute(
            "SELECT * FROM mobile_auth_exchange_journals"
            " WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()
        assert journal["phase"] == "prior_recovery_fenced"
        assert len(ledger.events) == 1
        exchange_id = str(journal["exchange_id"])
        replacement_selector = str(journal["replacement_selector"])
        expired_at = app.state.review_auth_service._now() - 1
        app.state.users._conn.execute(
            "UPDATE mobile_api_tokens SET expires_at = ?, review_expires_at = ?"
            " WHERE selector = ?",
            (expired_at, expired_at, replacement_selector),
        )
        app.state.users._conn.execute(
            "UPDATE mobile_auth_exchange_journals SET review_expires_at = ?"
            " WHERE exchange_id = ?",
            (expired_at, exchange_id),
        )
        app.state.users._conn.commit()
    finally:
        _close(app)

    resumed = _create_review_app(
        sessions, keyring, _admission(apple, google), ledger=ledger
    )
    try:
        assert resumed.state.users._conn.execute(
            "SELECT phase FROM mobile_auth_exchange_journals"
            " WHERE exchange_id = ?",
            (exchange_id,),
        ).fetchone()[0] == "complete"
        assert resumed.state.users._conn.execute(
            "SELECT 1 FROM mobile_api_tokens WHERE selector = ?",
            (replacement_selector,),
        ).fetchone() is None
        assert len(ledger.events) == 1
    finally:
        _close(resumed)


def test_expired_active_review_replacement_is_deleted_after_precomplete_crash(
    tmp_path, monkeypatch
):
    sessions = tmp_path / "sessions"
    keyring = VersionedHMAC(
        "k1", {"k1": b"k" * 32, "entitlements-k1": b"e" * 32}
    )
    apple, google = _seed_review_users(sessions, keyring)
    ledger = FakeRecoveryFenceLedger()
    app = _create_review_app(
        sessions, keyring, _admission(apple, google), ledger=ledger
    )
    challenge_id = ""
    replacement_selector = ""
    replacement_token = ""
    prior_selector = ""
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            assert _exchange(
                client, _start(client).json()["challenge_id"]
            ).status_code == 201
            verifier = "v" * 43
            challenge_id = _start(client, verifier=verifier).json()["challenge_id"]

            def crash_before_completion(_row):
                raise RuntimeError("synthetic pre-completion crash")

            monkeypatch.setattr(
                app.state.mobile_auth_service, "_complete", crash_before_completion
            )
            crashed = _exchange(
                client,
                challenge_id,
                verifier=verifier,
                idempotency_key="8" * 32,
            )
        assert crashed.status_code == 500
        journal = app.state.users._conn.execute(
            "SELECT * FROM mobile_auth_exchange_journals"
            " WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()
        assert journal["phase"] == "replacement_active"
        assert len(ledger.events) == 1
        replacement_selector = str(journal["replacement_selector"])
        prior_selector = str(journal["prior_selector"])
        secret = app.state.users._native_token_secret(verifier, challenge_id)
        replacement_token = f"{MOBILE_API_TOKEN_PREFIX}{replacement_selector}.{secret}"
        assert (
            app.state.users.authenticate_mobile_api_principal(replacement_token)
            is not None
        )
        expired_at = app.state.review_auth_service._now() - 1
        app.state.users._conn.execute(
            "UPDATE mobile_api_tokens SET expires_at = ?, review_expires_at = ?"
            " WHERE selector = ?",
            (expired_at, expired_at, replacement_selector),
        )
        app.state.users._conn.execute(
            "UPDATE mobile_auth_exchange_journals SET review_expires_at = ?"
            " WHERE exchange_id = ?",
            (expired_at, journal["exchange_id"]),
        )
        app.state.users._conn.commit()
    finally:
        _close(app)

    resumed = _create_review_app(
        sessions, keyring, _admission(apple, google), ledger=ledger
    )
    try:
        journal = resumed.state.users._conn.execute(
            "SELECT phase FROM mobile_auth_exchange_journals"
            " WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()
        assert journal["phase"] == "complete"
        assert resumed.state.users._conn.execute(
            "SELECT 1 FROM mobile_api_tokens WHERE selector = ?",
            (replacement_selector,),
        ).fetchone() is None
        prior = resumed.state.users._conn.execute(
            "SELECT state, revoked_at, fenced_at FROM mobile_api_tokens"
            " WHERE selector = ?",
            (prior_selector,),
        ).fetchone()
        assert tuple(prior)[:1] == ("fenced",)
        assert prior["revoked_at"] is not None and prior["fenced_at"] is not None
        assert (
            resumed.state.users.authenticate_mobile_api_principal(replacement_token)
            is None
        )
        assert len(ledger.events) == 1
        with TestClient(resumed) as client:
            terminal = _exchange(
                client,
                challenge_id,
                verifier="v" * 43,
                idempotency_key="8" * 32,
            )
        assert terminal.status_code == 409
        assert terminal.json()["code"] == "exchange_conflict"
    finally:
        _close(resumed)


def test_app_identity_is_immutable():
    identity = AppIdentityHeaders(
        environment="development",
        platform="ios",
        app_version="1.2.3",
        app_build="42",
        application_id="com.caddieinsight.app.dev",
    )
    with pytest.raises(Exception):
        identity.app_build = "43"
    assert replace(identity, app_build="43").app_build == "43"
