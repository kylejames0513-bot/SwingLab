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
from swinglab.web.app import create_app
from swinglab.web.mobile_schema import VersionedHMAC
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
from swinglab.web.users import UserStore, hash_password, verify_password


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

    def load_chain_snapshot(self):
        return SimpleNamespace(
            records=(SimpleNamespace(kind="cutover_baseline"),),
            head_etag="verified-head",
        )

    def append_and_publish(self, event):
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
        self.open = True

    def any_lane_active(self) -> bool:
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
