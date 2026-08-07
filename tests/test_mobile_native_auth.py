"""Challenge-bound native email authentication and safe token rotation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.api.contracts import NativeAuthStartRequest
from swinglab.api.errors import MobileAPIHTTPError
from swinglab.api.mobile_routes import _native_auth_payload
from swinglab.web import mailer
from swinglab.web import users as users_module
from swinglab.web.app import create_app
from swinglab.web.mobile_auth import (
    MobileNativeAuthRejected,
    MobileNativeAuthUnavailable,
    validate_mobile_native_auth_settings,
)
from swinglab.web.mobile_schema import VersionedHMAC
from swinglab.web.recovery_fence_ledger import RecoveryFenceError


IDEMPOTENCY_KEY = "0123456789abcdef0123456789abcdef"
VERIFIER = "v" * 43
INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"


def _challenge(verifier: str = VERIFIER) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")


class FakeRecoveryFenceLedger:
    def __init__(self, *, outage: bool = False) -> None:
        self.outage = outage
        self.loads = 0
        self.events = []

    def load_chain_snapshot(self):
        self.loads += 1
        if self.outage:
            raise RecoveryFenceError("synthetic recovery outage")
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
            record_hash=(f"{len(self.events):064x}"),
        )


def _keyring() -> VersionedHMAC:
    return VersionedHMAC("k1", {"k1": b"k" * 32})


def _config(*, enabled: bool = True) -> Config:
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_native_auth_enabled"] = enabled
    cfg.web["mobile_auth_starts_per_15_minutes_per_ip"] = 20
    cfg.web["mobile_auth_starts_per_15_minutes_per_email"] = 5
    cfg.web["mobile_auth_failed_exchanges_per_15_minutes_per_ip"] = 20
    cfg.web["mobile_auth_failed_exchanges_per_15_minutes_per_email"] = 10
    cfg.web["mobile_auth_live_challenges_per_ip"] = 20
    cfg.web["mobile_auth_live_challenges_per_email"] = 3
    return cfg


def _make_app(tmp_path, *, enabled=True, ledger=None, config=None):
    return create_app(
        config or _config(enabled=enabled),
        tmp_path / "sessions",
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
        recovery_fence_ledger=ledger or FakeRecoveryFenceLedger(),
    )


def _start_body(
    email: str = "golfer@example.com",
    *,
    installation_id: str = INSTALLATION_ID,
    verifier: str = VERIFIER,
) -> dict[str, str]:
    return {
        "email": email,
        "code_challenge": _challenge(verifier),
        "installation_id": installation_id,
        "device_label": "Maya's iPhone",
    }


def _code_from_messages(messages) -> str:
    combined = "\n".join(
        str(part)
        for message in messages
        for part in message
        if part is not None
    )
    # The universal-link challenge UUID can itself contain two all-numeric
    # groups.  The human-readable code is rendered after every link, so the
    # final grouped match is the one-time code rather than a UUID fragment.
    matches = re.findall(r"\b(\d{4})-(\d{4})\b", combined)
    assert matches
    return "".join(matches[-1])


def _wrong_code(code: str) -> str:
    return code[:-1] + str((int(code[-1]) + 1) % 10)


def _start(client: TestClient, **overrides):
    body = _start_body(**overrides)
    response = client.post("/api/v1/auth/email/start", json=body)
    assert response.status_code == 202, response.text
    return response


def _close_app_resources(app) -> None:
    app.state.jobs.close()
    app.state.throttle.close()
    if app.state.mobile_keyed_throttle is not None:
        app.state.mobile_keyed_throttle.close()
    app.state.users.close()


def _exchange(
    client: TestClient,
    challenge_id: str,
    code: str,
    *,
    verifier: str = VERIFIER,
    idempotency_key: str = IDEMPOTENCY_KEY,
):
    return client.post(
        "/api/v1/auth/email/exchange",
        json={
            "challenge_id": challenge_id,
            "email_code": code,
            "code_verifier": verifier,
        },
        headers={"Idempotency-Key": idempotency_key},
    )


def test_native_auth_payload_stops_reading_at_the_wire_size_cap():
    class ChunkedRequest:
        headers = {}

        async def body(self):
            raise AssertionError("the route must not buffer an unbounded body")

        async def stream(self):
            yield b"{" + (b" " * 3000)
            yield b"x" * 2000
            raise AssertionError("chunks after the size cap must not be read")

    with pytest.raises(MobileAPIHTTPError) as raised:
        asyncio.run(_native_auth_payload(ChunkedRequest(), NativeAuthStartRequest))
    assert raised.value.status_code == 422


def test_native_auth_flag_off_is_404_with_zero_side_effects(
    tmp_path, monkeypatch
):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda *args, **kwargs: sent.append(args))
    app = _make_app(tmp_path, enabled=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/email/start", json=_start_body())
    assert response.status_code == 404
    assert sent == []
    assert app.state.users._conn.execute(
        "SELECT COUNT(*) FROM mobile_auth_challenges"
    ).fetchone()[0] == 0


def test_native_auth_flag_off_conceals_routes_before_body_validation(tmp_path):
    app = _make_app(tmp_path, enabled=False)
    with TestClient(app) as client:
        start = client.post(
            "/api/v1/auth/email/start",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        exchange = client.post(
            "/api/v1/auth/email/exchange",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
    assert start.status_code == exchange.status_code == 404


def test_callback_is_constant_no_store_and_never_reflects_query_secrets(tmp_path):
    app = _make_app(tmp_path, enabled=False)
    secret = "12345678-secret-challenge"
    with TestClient(app) as client:
        response = client.get(
            "/app/auth/callback",
            params={"challenge_id": secret, "code": "12345678"},
        )
    assert response.status_code == 200
    assert "Open CaddieInsight" in response.text
    assert "device where sign-in started" in response.text
    assert "code expired" in response.text
    assert secret not in response.text
    assert "12345678" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_health_readback_reports_only_native_auth_feature_and_closed_bounds(
    tmp_path,
):
    app = _make_app(tmp_path, enabled=False)
    with TestClient(app) as client:
        native = client.get("/healthz").json()["native_email_auth"]
    assert native == {
        "enabled": False,
        "recovery_ready": False,
        "starts_per_15_minutes_per_ip": 20,
        "starts_per_15_minutes_per_email": 5,
        "failed_exchanges_per_15_minutes_per_ip": 20,
        "failed_exchanges_per_15_minutes_per_email": 10,
        "live_challenges_per_ip": 20,
        "live_challenges_per_email": 3,
    }


def test_native_start_is_generic_and_sends_grouped_code_with_pkce_link(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    ledger = FakeRecoveryFenceLedger()
    app = _make_app(tmp_path, ledger=ledger)
    app.state.users.verify_email_signin("existing@example.com")
    with TestClient(app) as client:
        existing = _start(client, email="existing@example.com")
        unknown = _start(
            client,
            email="unknown@example.com",
            installation_id="22222222-2222-4222-8222-222222222222",
        )

    assert existing.status_code == unknown.status_code == 202
    assert set(existing.json()) == {"resource_version", "challenge_id", "expires_at"}
    assert set(unknown.json()) == set(existing.json())
    assert existing.headers["cache-control"] == "no-store"
    assert ledger.loads == 1
    assert len(messages) == 2
    for _recipient, subject, text_body, html_body in messages:
        assert re.search(r"\d{4}-\d{4}", subject) is None
        assert re.search(r"\b\d{8}\b", subject) is None
        assert re.search(r"\b\d{4}-\d{4}\b", text_body)
        assert re.search(r"\b\d{4}-\d{4}\b", html_body)
        assert "https://app.example/app/auth/callback?challenge_id=" in text_body
        assert "https://app.example/app/auth/callback?challenge_id=" in html_body


def test_exchange_is_single_use_and_exact_lost_response_replays_raw_token(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        code = _code_from_messages(messages)
        first = _exchange(client, started.json()["challenge_id"], code)
        replay = _exchange(client, started.json()["challenge_id"], code)
        conflict = _exchange(
            client,
            started.json()["challenge_id"],
            code,
            idempotency_key="f" * 32,
        )

        assert first.status_code == 201, first.text
        assert replay.status_code == 201, replay.text
        assert replay.json() == first.json()
        token = first.json()["access_token"]
        assert token.startswith("ciat_")
        identity = client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert identity.status_code == 200
        assert identity.json()["identity"]["email"] == "golfer@example.com"
        assert conflict.status_code == 409


def test_same_installation_rotation_is_pending_until_recovery_readback(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    ledger = FakeRecoveryFenceLedger()
    app = _make_app(tmp_path, ledger=ledger)
    with TestClient(app) as client:
        first_start = _start(client)
        first_code = _code_from_messages(messages)
        first = _exchange(client, first_start.json()["challenge_id"], first_code)
        assert first.status_code == 201
        old_token = first.json()["access_token"]

        # A later same-installation sign-in locally fences the old selector
        # before any replacement credential can be returned.
        app.state.mobile_auth_service._now = lambda: 1_000_061.0
        second_start = _start(client)
        second_code = _code_from_messages(messages[1:])
        ledger.outage = True
        pending = _exchange(
            client,
            second_start.json()["challenge_id"],
            second_code,
            idempotency_key="a" * 32,
        )
        assert pending.status_code == 202, pending.text
        assert set(pending.json()) == {
            "resource_version",
            "exchange_id",
            "status",
            "retry_after_seconds",
        }
        pending_replay = _exchange(
            client,
            second_start.json()["challenge_id"],
            second_code,
            idempotency_key="a" * 32,
        )
        assert pending_replay.status_code == 202
        assert pending_replay.json() == pending.json()
        assert client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {old_token}"}
        ).status_code == 401
        ledger.outage = False
        completed = _exchange(
            client,
            second_start.json()["challenge_id"],
            second_code,
            idempotency_key="a" * 32,
        )
        assert completed.status_code == 201, completed.text
        assert completed.json()["access_token"] != old_token


def test_resend_suppression_keeps_one_live_challenge_outside_its_own_cap(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    cfg = _config()
    cfg.web["mobile_auth_live_challenges_per_ip"] = 1
    cfg.web["mobile_auth_live_challenges_per_email"] = 1
    app = _make_app(tmp_path, config=cfg)
    with TestClient(app) as client:
        first = _start(client)
        created_at = first.json()["expires_at"] - 600
        app.state.mobile_auth_service._now = lambda: created_at + 59
        suppressed = _start(client)
        app.state.mobile_auth_service._now = lambda: created_at + 61
        resent = _start(client)
    assert suppressed.json() == first.json()
    assert resent.json()["challenge_id"] == first.json()["challenge_id"]
    assert len(messages) == 2


def test_live_email_cap_blocks_a_second_installation_without_sending(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    cfg = _config()
    cfg.web["mobile_auth_live_challenges_per_email"] = 1
    app = _make_app(tmp_path, config=cfg)
    with TestClient(app) as client:
        _start(client)
        denied = client.post(
            "/api/v1/auth/email/start",
            json=_start_body(
                installation_id="22222222-2222-4222-8222-222222222222"
            ),
        )
    assert denied.status_code == 429
    assert denied.json()["code"] == "rate_limited"
    assert len(messages) == 1


def test_wrong_code_or_verifier_burns_challenge_after_five_failures(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        challenge_id = started.json()["challenge_id"]
        code = _code_from_messages(messages)
        failures = [
            _exchange(client, challenge_id, code, verifier="w" * 43),
            *[
                _exchange(client, challenge_id, _wrong_code(code))
                for _ in range(4)
            ],
        ]
        burned = _exchange(client, challenge_id, code)
    assert {response.status_code for response in failures} == {401}
    assert burned.status_code == 401
    row = app.state.users._conn.execute(
        "SELECT attempts, consumed_at FROM mobile_auth_challenges"
        " WHERE challenge_id = ?",
        (challenge_id,),
    ).fetchone()
    assert tuple(row) == (5, row["consumed_at"])
    assert row["consumed_at"] is not None


def test_five_parallel_wrong_proofs_burn_before_a_correct_exchange_can_race(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        challenge_id = started.json()["challenge_id"]
        code = _code_from_messages(messages)
        wrong = _wrong_code(code)
        service = app.state.mobile_auth_service
        original_debit = service._debit_failed_exchange
        arrived = 0
        arrived_lock = threading.Lock()
        all_arrived = threading.Event()
        release = threading.Event()

        def held_debit(**kwargs):
            nonlocal arrived
            if kwargs["client_ip"].startswith("wrong-"):
                with arrived_lock:
                    arrived += 1
                    if arrived == 5:
                        all_arrived.set()
                assert release.wait(timeout=5)
            return original_debit(**kwargs)

        monkeypatch.setattr(service, "_debit_failed_exchange", held_debit)

        def reject(index: int):
            with pytest.raises(MobileNativeAuthRejected):
                service.exchange(
                    challenge_id=challenge_id,
                    email_code=wrong,
                    code_verifier=VERIFIER,
                    idempotency_key=f"{index + 1:032x}",
                    client_ip=f"wrong-{index}",
                )

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(reject, index) for index in range(5)]
            assert all_arrived.wait(timeout=5)
            try:
                with pytest.raises(MobileNativeAuthRejected):
                    service.exchange(
                        challenge_id=challenge_id,
                        email_code=code,
                        code_verifier=VERIFIER,
                        idempotency_key="f" * 32,
                        client_ip="correct",
                    )
            finally:
                release.set()
            for future in futures:
                future.result(timeout=5)


def test_wrong_code_still_executes_both_constant_time_proof_comparisons(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        code = _code_from_messages(messages)
        comparisons = []
        original_compare = users_module.hmac.compare_digest

        def observed_compare(left, right):
            comparisons.append((left, right))
            return original_compare(left, right)

        monkeypatch.setattr(users_module.hmac, "compare_digest", observed_compare)
        rejected = _exchange(
            client,
            started.json()["challenge_id"],
            _wrong_code(code),
        )
    assert rejected.status_code == 401
    assert len(comparisons) == 2


def test_expired_challenge_is_indistinguishable_and_cannot_issue_token(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        app.state.mobile_auth_service._now = lambda: started.json()["expires_at"]
        response = _exchange(
            client,
            started.json()["challenge_id"],
            _code_from_messages(messages),
        )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_rejected"
    assert app.state.users._conn.execute(
        "SELECT COUNT(*) FROM mobile_api_tokens"
    ).fetchone()[0] == 0


def test_exact_replay_expires_without_revoking_the_already_issued_token(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        code = _code_from_messages(messages)
        issued = _exchange(client, started.json()["challenge_id"], code)
        assert issued.status_code == 201
        replay_deadline = app.state.users._conn.execute(
            "SELECT expires_at FROM mobile_auth_exchange_journals"
        ).fetchone()[0]
        app.state.mobile_auth_service._now = lambda: replay_deadline
        expired_replay = _exchange(client, started.json()["challenge_id"], code)
        still_authenticated = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {issued.json()['access_token']}"},
        )
    assert expired_replay.status_code == 401
    assert "access_token" not in expired_replay.json()
    assert still_authenticated.status_code == 200


def test_sixth_installation_is_refused_without_consuming_its_challenge(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    cfg = _config()
    cfg.web["mobile_auth_starts_per_15_minutes_per_email"] = 20
    app = _make_app(tmp_path, config=cfg)
    with TestClient(app) as client:
        for index in range(5):
            verifier = chr(ord("a") + index) * 43
            started = _start(
                client,
                installation_id=str(uuid.UUID(int=index + 1)),
                verifier=verifier,
            )
            issued = _exchange(
                client,
                started.json()["challenge_id"],
                _code_from_messages(messages[index:]),
                verifier=verifier,
                idempotency_key=f"{index + 1:032x}",
            )
            assert issued.status_code == 201, f"installation {index}: {issued.text}"

        sixth_verifier = "z" * 43
        sixth = _start(
            client,
            installation_id=str(uuid.UUID(int=6)),
            verifier=sixth_verifier,
        )
        refused = _exchange(
            client,
            sixth.json()["challenge_id"],
            _code_from_messages(messages[5:]),
            verifier=sixth_verifier,
            idempotency_key="f" * 32,
        )
    assert refused.status_code == 409
    assert refused.json()["code"] == "device_limit"
    assert app.state.users._conn.execute(
        "SELECT COUNT(*) FROM mobile_api_tokens"
        " WHERE state = 'active' AND revoked_at IS NULL"
    ).fetchone()[0] == 5
    assert app.state.users._conn.execute(
        "SELECT consumed_at FROM mobile_auth_challenges WHERE challenge_id = ?",
        (sixth.json()["challenge_id"],),
    ).fetchone()[0] is None


def test_start_and_failed_exchange_limits_use_hmac_keys_and_atomic_domains(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    cfg = _config()
    cfg.web["mobile_auth_starts_per_15_minutes_per_ip"] = 2
    cfg.web["mobile_auth_starts_per_15_minutes_per_email"] = 50
    app = _make_app(tmp_path / "ip", config=cfg)
    with TestClient(app) as client:
        assert _start(client, email="one@example.com").status_code == 202
        assert _start(
            client,
            email="two@example.com",
            installation_id="22222222-2222-4222-8222-222222222222",
        ).status_code == 202
        denied = client.post(
            "/api/v1/auth/email/start",
            json=_start_body(
                "three@example.com",
                installation_id="33333333-3333-4333-8333-333333333333",
            ),
        )
    assert denied.status_code == 429
    assert denied.json()["code"] == "rate_limited"
    assert 1 <= int(denied.headers["retry-after"]) <= 900
    dump = "\n".join(app.state.users._conn.iterdump())
    assert "one@example.com" in dump  # challenge identity is intentionally retained
    assert "testclient" not in dump

    unknown_cfg = _config()
    unknown_cfg.web["mobile_auth_failed_exchanges_per_15_minutes_per_ip"] = 2
    unknown_cfg.web["mobile_auth_failed_exchanges_per_15_minutes_per_email"] = 2
    unknown_app = _make_app(tmp_path / "unknown", config=unknown_cfg)
    with TestClient(unknown_app) as client:
        for key in ("1" * 32, "2" * 32):
            rejected = _exchange(
                client,
                "00000000-0000-4000-8000-000000000000",
                "00000000",
                idempotency_key=key,
            )
            assert rejected.status_code == 401
        limited = _exchange(
            client,
            "00000000-0000-4000-8000-000000000000",
            "00000000",
            idempotency_key="3" * 32,
        )
    assert limited.status_code == 429
    domains = unknown_app.state.users._conn.execute(
        "SELECT domain, COUNT(*) FROM mobile_rate_limit_events"
        " WHERE domain LIKE 'auth-exchange-%' GROUP BY domain"
    ).fetchall()
    assert [tuple(row) for row in domains] == [("auth-exchange-ip", 2)]


def test_known_challenge_failure_debits_ip_and_email_together(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        rejected = _exchange(
            client,
            started.json()["challenge_id"],
            _wrong_code(_code_from_messages(messages)),
        )
    assert rejected.status_code == 401
    domains = app.state.users._conn.execute(
        "SELECT domain, COUNT(*) FROM mobile_rate_limit_events"
        " WHERE domain LIKE 'auth-exchange-%' GROUP BY domain ORDER BY domain"
    ).fetchall()
    assert [tuple(row) for row in domains] == [
        ("auth-exchange-email", 1),
        ("auth-exchange-ip", 1),
    ]


def test_consumed_challenge_replay_mismatch_is_rate_limited(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    cfg = _config()
    cfg.web["mobile_auth_failed_exchanges_per_15_minutes_per_ip"] = 1
    cfg.web["mobile_auth_failed_exchanges_per_15_minutes_per_email"] = 1
    app = _make_app(tmp_path, config=cfg)
    with TestClient(app) as client:
        started = _start(client)
        challenge_id = started.json()["challenge_id"]
        code = _code_from_messages(messages)
        assert _exchange(client, challenge_id, code).status_code == 201

        conflict = _exchange(client, challenge_id, _wrong_code(code))
        limited = _exchange(client, challenge_id, _wrong_code(code))

    assert conflict.status_code == 409
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    domains = app.state.users._conn.execute(
        "SELECT domain, COUNT(*) FROM mobile_rate_limit_events"
        " WHERE domain LIKE 'auth-exchange-%' GROUP BY domain ORDER BY domain"
    ).fetchall()
    assert [tuple(row) for row in domains] == [
        ("auth-exchange-email", 1),
        ("auth-exchange-ip", 1),
    ]


def test_native_exchange_persists_no_raw_proof_installation_or_bearer(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        code = _code_from_messages(messages)
        exchanged = _exchange(client, started.json()["challenge_id"], code)
        assert exchanged.status_code == 201
        token = exchanged.json()["access_token"]
    persisted = b"".join(
        path.read_bytes()
        for path in (tmp_path / "sessions").glob("swinglab.db*")
        if path.is_file()
    )
    for raw_secret in (
        code,
        VERIFIER,
        INSTALLATION_ID,
        IDEMPOTENCY_KEY,
        token,
        "testclient",
    ):
        assert raw_secret.encode("ascii") not in persisted


def test_native_convergence_claims_pending_pro_and_creates_profile(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    app.state.users.add_pending_grant("golfer@example.com", 31)
    with TestClient(app) as client:
        started = _start(client)
        exchanged = _exchange(
            client,
            started.json()["challenge_id"],
            _code_from_messages(messages),
        )
    assert exchanged.status_code == 201
    user = app.state.users.get_by_email("golfer@example.com")
    assert user is not None and user.email_verified and user.is_pro
    assert app.state.users.pending_grant_days(user.email) == 0
    assert app.state.users.get_golfer_profile(user.id) is not None


def test_enabled_startup_fails_closed_without_https_or_recovery_readback(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    with pytest.raises(MobileNativeAuthUnavailable, match="HTTPS"):
        _make_app(tmp_path / "no-origin")

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    with pytest.raises(MobileNativeAuthUnavailable, match="not ready"):
        _make_app(
            tmp_path / "outage",
            ledger=FakeRecoveryFenceLedger(outage=True),
        )

    for index, invalid_origin in enumerate(
        (
            "https://app.example/not-an-origin",
            "https://user:secret@app.example",
            "https://app.example?secret=query",
            "https://app.example?",
            "https://app.example#",
            "https://app.\texample",
            "https://app.example:",
        )
    ):
        monkeypatch.setenv("PUBLIC_BASE_URL", invalid_origin)
        with pytest.raises(MobileNativeAuthUnavailable, match="canonical HTTPS"):
            _make_app(tmp_path / f"invalid-origin-{index}")


def test_nonterminal_rotation_recovery_runs_even_when_feature_is_later_off(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    sessions = tmp_path / "sessions"
    ledger = FakeRecoveryFenceLedger()
    app = create_app(
        _config(),
        sessions,
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
        recovery_fence_ledger=ledger,
    )
    with TestClient(app) as client:
        first = _start(client)
        first_exchange = _exchange(
            client, first.json()["challenge_id"], _code_from_messages(messages)
        )
        assert first_exchange.status_code == 201
        first_created = first.json()["expires_at"] - 600
        app.state.mobile_auth_service._now = lambda: first_created + 61
        second = _start(client)
        ledger.outage = True
        pending = _exchange(
            client,
            second.json()["challenge_id"],
            _code_from_messages(messages[1:]),
            idempotency_key="a" * 32,
        )
        assert pending.status_code == 202
    _close_app_resources(app)

    with pytest.raises(MobileNativeAuthUnavailable, match="could not complete"):
        create_app(
            _config(enabled=False),
            sessions,
            start_background_workers=False,
            mobile_state_hmac=_keyring(),
            recovery_fence_ledger=ledger,
        )


def test_startup_resumes_prepared_initial_issuance_with_feature_off(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    sessions = tmp_path / "sessions"
    ledger = FakeRecoveryFenceLedger()
    app = create_app(
        _config(),
        sessions,
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
        recovery_fence_ledger=ledger,
    )
    with TestClient(app) as client:
        started = _start(client)
        monkeypatch.setattr(
            app.state.mobile_auth_service,
            "_advance",
            lambda _exchange_id: None,
        )
        pending = _exchange(
            client,
            started.json()["challenge_id"],
            _code_from_messages(messages),
        )
        assert pending.status_code == 202
        exchange_id = pending.json()["exchange_id"]
        assert app.state.users.mobile_auth_exchange_journal(exchange_id).phase == (
            "prepared"
        )
    _close_app_resources(app)

    resumed = create_app(
        _config(enabled=False),
        sessions,
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
        recovery_fence_ledger=ledger,
    )
    journal = resumed.state.users.mobile_auth_exchange_journal(exchange_id)
    assert journal is not None and journal.phase == "complete"
    raw_token, _expires_at = resumed.state.users.recover_mobile_email_exchange_credential(
        journal, VERIFIER
    )
    assert resumed.state.users.authenticate_mobile_api_principal(raw_token) is not None
    _close_app_resources(resumed)


@pytest.mark.parametrize(
    ("crash_method", "durable_phase"),
    (
        ("_activate_replacement", "prior_recovery_fenced"),
        ("_complete", "replacement_active"),
    ),
)
def test_startup_resumes_rotation_after_each_post_publish_crash_phase(
    tmp_path, monkeypatch, crash_method, durable_phase
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    sessions = tmp_path / durable_phase
    ledger = FakeRecoveryFenceLedger()
    app = create_app(
        _config(),
        sessions,
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
        recovery_fence_ledger=ledger,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        first = _start(client)
        issued = _exchange(
            client, first.json()["challenge_id"], _code_from_messages(messages)
        )
        assert issued.status_code == 201
        old_token = issued.json()["access_token"]
        created_at = first.json()["expires_at"] - 600
        app.state.mobile_auth_service._now = lambda: created_at + 61
        second = _start(client)

        def synthetic_crash(_row):
            raise RuntimeError("synthetic post-commit crash")

        monkeypatch.setattr(
            app.state.mobile_auth_service, crash_method, synthetic_crash
        )
        crashed = _exchange(
            client,
            second.json()["challenge_id"],
            _code_from_messages(messages[1:]),
            idempotency_key="b" * 32,
        )
        assert crashed.status_code == 500
        row = app.state.users._conn.execute(
            "SELECT exchange_id, phase, replacement_selector"
            " FROM mobile_auth_exchange_journals"
            " WHERE challenge_id = ?",
            (second.json()["challenge_id"],),
        ).fetchone()
        assert row["phase"] == durable_phase
        replacement_state = app.state.users._conn.execute(
            "SELECT state FROM mobile_api_tokens WHERE selector = ?",
            (row["replacement_selector"],),
        ).fetchone()[0]
        assert replacement_state == (
            "inactive"
            if durable_phase == "prior_recovery_fenced"
            else "active"
        )
        assert client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {old_token}"}
        ).status_code == 401
        exchange_id = row["exchange_id"]
    _close_app_resources(app)

    resumed = create_app(
        _config(enabled=False),
        sessions,
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
        recovery_fence_ledger=ledger,
    )
    journal = resumed.state.users.mobile_auth_exchange_journal(exchange_id)
    assert journal is not None and journal.phase == "complete"
    new_token, _expires_at = resumed.state.users.recover_mobile_email_exchange_credential(
        journal, VERIFIER
    )
    assert resumed.state.users.authenticate_mobile_api_principal(new_token) is not None
    _close_app_resources(resumed)


def test_native_auth_setting_bounds_are_strict_and_defaults_match_shipped():
    defaults = validate_mobile_native_auth_settings(Config().web)
    assert defaults.enabled is False
    assert (
        defaults.starts_per_ip,
        defaults.starts_per_email,
        defaults.failed_exchanges_per_ip,
        defaults.failed_exchanges_per_email,
        defaults.live_challenges_per_ip,
        defaults.live_challenges_per_email,
    ) == (20, 5, 20, 10, 20, 3)
    for name, invalid in (
        ("mobile_auth_starts_per_15_minutes_per_ip", 0),
        ("mobile_auth_starts_per_15_minutes_per_email", 51),
        ("mobile_auth_failed_exchanges_per_15_minutes_per_ip", True),
        ("mobile_auth_failed_exchanges_per_15_minutes_per_email", -1),
        ("mobile_auth_live_challenges_per_ip", 101),
        ("mobile_auth_live_challenges_per_email", 21),
    ):
        web = dict(Config().web)
        web[name] = invalid
        with pytest.raises(ValueError, match=name):
            validate_mobile_native_auth_settings(web)


def test_start_boundedly_purges_expired_challenge_replay_and_rate_metadata(
    tmp_path, monkeypatch
):
    messages = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SWINGLAB_MAIL_FROM", "CaddieInsight <noreply@example.com>")
    monkeypatch.setenv("SWINGLAB_SMTP_URL", "smtp://mail.example")
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: messages.append((*args, kwargs.get("html_body"))),
    )
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        started = _start(client)
        completed = _exchange(
            client,
            started.json()["challenge_id"],
            _code_from_messages(messages),
        )
        assert completed.status_code == 201
        exchange_id = app.state.users._conn.execute(
            "SELECT exchange_id FROM mobile_auth_exchange_journals"
        ).fetchone()[0]
        app.state.users._conn.execute(
            "UPDATE mobile_auth_challenges SET expires_at = 1"
        )
        app.state.users._conn.execute(
            "UPDATE mobile_auth_exchange_journals SET expires_at = 1"
        )
        app.state.users._conn.execute(
            "UPDATE mobile_auth_exchange_receipts SET expires_at = 1"
        )
        app.state.users._conn.execute(
            "UPDATE mobile_rate_limit_events SET occurred_at = 1"
        )
        app.state.users._conn.commit()
        app.state.mobile_auth_service._now = lambda: 90_002.0
        _start(
            client,
            email="fresh@example.com",
            installation_id="99999999-9999-4999-8999-999999999999",
        )
    assert app.state.users.mobile_auth_exchange_journal(exchange_id) is None
    assert app.state.users._conn.execute(
        "SELECT COUNT(*) FROM mobile_auth_exchange_receipts"
    ).fetchone()[0] == 0
    assert app.state.users._conn.execute(
        "SELECT COUNT(*) FROM mobile_auth_challenges WHERE challenge_id = ?",
        (started.json()["challenge_id"],),
    ).fetchone()[0] == 0
    rate_times = app.state.users._conn.execute(
        "SELECT occurred_at FROM mobile_rate_limit_events ORDER BY occurred_at"
    ).fetchall()
    assert [row[0] for row in rate_times] == [90_002.0, 90_002.0]
