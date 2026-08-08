"""Native swing-history reset behind default-off ``mobile_privacy_enabled``.

This slice consumes a ``history_reset`` step-up token at an exact history epoch,
drives a durable journal (``prepared`` → ``exports_quiescing`` →
``erasure_recorded`` → ``local_erased`` → ``complete``), publishes one
recovery-fence ``history_reset`` record before erasing anything locally, and
purges the owner's privacy-export receipts and archives so an old epoch stays
undownloadable.
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
from swinglab.web.mobile_privacy import PRIVACY_EXPORT_DIRNAME
from swinglab.web.mobile_schema import VersionedHMAC
from swinglab.web.recovery_fence_ledger import RecoveryFenceError


RESET_IDEMPOTENCY_KEY = "1111222233334444aaaabbbbccccdddd"
OTHER_IDEMPOTENCY_KEY = "9999888877776666eeeeffff00001111"
STEP_UP_IDEMPOTENCY_KEY = "fedcba9876543210fedcba9876543210"
EXPORT_IDEMPOTENCY_KEY = "aaaabbbbccccddddaaaabbbbccccdddd"
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
        self.fail_kinds: set[str] = set()

    def load_chain_snapshot(self):
        return SimpleNamespace(
            records=(SimpleNamespace(kind="cutover_baseline"),),
            head_etag="verified-head",
        )

    def append_and_publish(self, event):
        if event.kind.value in self.fail_kinds:
            raise RecoveryFenceError("The fence head is unavailable.")
        self.events.append(event)
        return SimpleNamespace(
            sequence=len(self.events) + 1, record_hash=f"{len(self.events):064x}"
        )

    def kinds(self) -> list[str]:
        return [event.kind.value for event in self.events]


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


def _sign_in(client: TestClient, messages, *, email="golfer@example.com") -> str:
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
    idempotency_key = hashlib.sha256(email.encode("ascii")).hexdigest()[:32]
    exchange = client.post(
        "/api/v1/auth/email/exchange",
        json={
            "challenge_id": challenge_id,
            "email_code": code,
            "code_verifier": VERIFIER,
        },
        headers={"Idempotency-Key": idempotency_key},
    )
    assert exchange.status_code == 201, exchange.text
    messages.clear()
    return exchange.json()["access_token"]


def _mint_step_up_token(
    client,
    bearer,
    messages,
    *,
    purpose="history_reset",
    verifier=STEP_UP_VERIFIER,
    idempotency_key=STEP_UP_IDEMPOTENCY_KEY,
) -> str:
    messages.clear()
    started = client.post(
        "/api/v1/auth/step-up/start",
        json={"purpose": purpose, "code_challenge": _challenge(verifier)},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert started.status_code == 202, started.text
    challenge_id = started.json()["challenge_id"]
    code = _code_from_messages(messages)
    exchanged = client.post(
        "/api/v1/auth/step-up/exchange",
        json={
            "challenge_id": challenge_id,
            "email_code": code,
            "code_verifier": verifier,
        },
        headers={"Idempotency-Key": idempotency_key},
    )
    assert exchanged.status_code == 201, exchanged.text
    messages.clear()
    return exchanged.json()["step_up_token"]


def _terminal_job(app, user_id: str):
    """One finished, owned session, as the analyzer would leave it."""

    manager = app.state.jobs
    job = manager.create_session(source_name="swing.mov", user_id=user_id)
    (job.session_dir / "source.mov").write_bytes(b"video")
    output = job.session_dir / "out"
    output.mkdir()
    (output / "report.html").write_text(
        "<html>legacy coaching report</html>", encoding="utf-8"
    )
    job.report_rel = "out/report.html"
    job.status = "done"
    manager._save(job)
    return job


def _reset(
    client,
    bearer,
    token,
    *,
    expected_history_epoch=0,
    idempotency_key=RESET_IDEMPOTENCY_KEY,
):
    return client.post(
        "/api/v1/privacy/history-reset",
        json={
            "step_up_token": token,
            "expected_history_epoch": expected_history_epoch,
        },
        headers={
            "Authorization": f"Bearer {bearer}",
            "Idempotency-Key": idempotency_key,
        },
    )


# -- flag concealment ------------------------------------------------------


def test_flag_off_conceals_history_reset_before_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    app = _make_app(tmp_path, privacy_enabled=False, native_auth=False)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/privacy/history-reset",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
    finally:
        _close(app)


# -- authorization ---------------------------------------------------------


def test_bearer_alone_cannot_reset_history(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            response = _reset(client, bearer, "custg_nope.deadbeefdeadbeef")
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_rejected"
    finally:
        _close(app)


def test_step_up_alone_cannot_reset_history(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            response = client.post(
                "/api/v1/privacy/history-reset",
                json={"step_up_token": token, "expected_history_epoch": 0},
                headers={"Idempotency-Key": RESET_IDEMPOTENCY_KEY},
            )
        assert response.status_code == 401
    finally:
        _close(app)


def test_wrong_purpose_step_up_token_is_rejected(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(
                client, bearer, messages, purpose="data_export"
            )
            response = _reset(client, bearer, token)
            users = app.state.users
            owner = users.get_by_email("golfer@example.com")
        assert response.status_code == 401
        # A rejected authorization erases nothing and advances no epoch.
        assert owner is not None and owner.history_epoch == 0
    finally:
        _close(app)


def test_missing_idempotency_key_is_rejected_before_the_journal(
    tmp_path, messages
):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            response = client.post(
                "/api/v1/privacy/history-reset",
                json={"step_up_token": token, "expected_history_epoch": 0},
                headers={"Authorization": f"Bearer {bearer}"},
            )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_idempotency_key"
    finally:
        _close(app)


# -- reset ------------------------------------------------------------------


def test_reset_erases_history_fences_the_epoch_and_returns_204(
    tmp_path, messages
):
    ledger = FakeRecoveryFenceLedger()
    app = _make_app(tmp_path, ledger=ledger)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            response = _reset(client, bearer, token)
            users = app.state.users
            owner = users.get_by_email("golfer@example.com")
        assert response.status_code == 204, response.text
        assert response.content == b""
        assert owner is not None and owner.history_epoch == 1
        reset_events = [
            event for event in ledger.events if event.kind.value == "history_reset"
        ]
        assert len(reset_events) == 1
        # The fence carries only a versioned owner digest and the epoch it
        # erased through — never a raw account id or email.
        assert reset_events[0].erased_through_history_epoch == 1
        assert reset_events[0].stable_user_hmac_key_id == "k1"
        assert owner.id not in reset_events[0].stable_user_hmac
    finally:
        _close(app)


def test_reset_deletes_owned_sessions(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            owner = app.state.users.get_by_email("golfer@example.com")
            _terminal_job(app, owner.id)
            assert app.state.jobs.list_recent(10, user_id=owner.id)
            token = _mint_step_up_token(client, bearer, messages)
            response = _reset(client, bearer, token)
            remaining = app.state.jobs.list_recent(10, user_id=owner.id)
        assert response.status_code == 204, response.text
        assert remaining == []
    finally:
        _close(app)


def test_history_epoch_mismatch_conflicts_without_erasing(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            owner = app.state.users.get_by_email("golfer@example.com")
            _terminal_job(app, owner.id)
            token = _mint_step_up_token(client, bearer, messages)
            response = _reset(client, bearer, token, expected_history_epoch=7)
            remaining = app.state.jobs.list_recent(10, user_id=owner.id)
            still = app.state.users.get(owner.id)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "history_epoch_conflict"
        assert len(remaining) == 1
        assert still.history_epoch == 0
    finally:
        _close(app)


def test_active_analysis_blocks_the_reset(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            owner = app.state.users.get_by_email("golfer@example.com")
            app.state.jobs.create_session(
                source_name="in-flight.mov", user_id=owner.id
            )
            token = _mint_step_up_token(client, bearer, messages)
            response = _reset(client, bearer, token)
            still = app.state.users.get(owner.id)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "erasure_busy"
        assert still.history_epoch == 0
    finally:
        _close(app)


# -- idempotency ------------------------------------------------------------


def test_exact_replay_returns_204_without_a_second_step_up(tmp_path, messages):
    ledger = FakeRecoveryFenceLedger()
    app = _make_app(tmp_path, ledger=ledger)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            first = _reset(client, bearer, token)
            assert first.status_code == 204, first.text
            # The claimed token is spent; the exact idempotency key still
            # answers, because a lost 204 must stay replayable.
            replay = _reset(client, bearer, token)
            # Even a client that has lost its bearer can replay the receipt.
            pre_auth = client.post(
                "/api/v1/privacy/history-reset",
                json={"step_up_token": token, "expected_history_epoch": 0},
                headers={"Idempotency-Key": RESET_IDEMPOTENCY_KEY},
            )
            owner = app.state.users.get_by_email("golfer@example.com")
        assert replay.status_code == 204, replay.text
        assert pre_auth.status_code == 204, pre_auth.text
        assert owner.history_epoch == 1
        assert ledger.kinds().count("history_reset") == 1
    finally:
        _close(app)


def test_conflicting_idempotency_key_is_rejected(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            first = _reset(client, bearer, token)
            assert first.status_code == 204, first.text
            # Same key, different semantic request: never silently accepted.
            conflict = client.post(
                "/api/v1/privacy/history-reset",
                json={"step_up_token": token, "expected_history_epoch": 1},
                headers={
                    "Authorization": f"Bearer {bearer}",
                    "Idempotency-Key": RESET_IDEMPOTENCY_KEY,
                },
            )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "erasure_conflict"
    finally:
        _close(app)


def test_a_second_reset_needs_a_fresh_key_and_epoch(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            first_token = _mint_step_up_token(client, bearer, messages)
            assert _reset(client, bearer, first_token).status_code == 204
            second_token = _mint_step_up_token(
                client,
                bearer,
                messages,
                verifier="t" * 43,
                idempotency_key=OTHER_IDEMPOTENCY_KEY,
            )
            second = _reset(
                client,
                bearer,
                second_token,
                expected_history_epoch=1,
                idempotency_key=OTHER_IDEMPOTENCY_KEY,
            )
            owner = app.state.users.get_by_email("golfer@example.com")
        assert second.status_code == 204, second.text
        assert owner.history_epoch == 2
    finally:
        _close(app)


# -- export quiesce ---------------------------------------------------------


def test_reset_purges_old_epoch_export_receipts_and_archives(
    tmp_path, messages
):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            export_token = _mint_step_up_token(
                client, bearer, messages, purpose="data_export"
            )
            created = client.post(
                "/api/v1/privacy/exports",
                json={"step_up_token": export_token},
                headers={
                    "Authorization": f"Bearer {bearer}",
                    "Idempotency-Key": EXPORT_IDEMPOTENCY_KEY,
                },
            )
            assert created.status_code == 202, created.text
            export_id = created.json()["export_id"]
            assert app.state.privacy_export_worker.drain_once() is True
            ready = client.get(
                f"/api/v1/privacy/exports/{export_id}",
                headers={"Authorization": f"Bearer {bearer}"},
            )
            assert ready.json()["status"] == "ready", ready.text
            archive = (
                tmp_path
                / "sessions"
                / PRIVACY_EXPORT_DIRNAME
                / f"{export_id}.zip"
            )
            assert archive.exists()

            reset_token = _mint_step_up_token(
                client,
                bearer,
                messages,
                verifier="r" * 43,
                idempotency_key=OTHER_IDEMPOTENCY_KEY,
            )
            response = _reset(client, bearer, reset_token)
            after = client.get(
                f"/api/v1/privacy/exports/{export_id}",
                headers={"Authorization": f"Bearer {bearer}"},
            )
        assert response.status_code == 204, response.text
        # Neither the receipt nor the ZIP of the erased epoch survives.
        assert after.status_code == 404, after.text
        assert not archive.exists()
    finally:
        _close(app)


# -- fence outage -----------------------------------------------------------


def test_fence_outage_returns_202_and_erases_nothing_until_it_recovers(
    tmp_path, messages
):
    ledger = FakeRecoveryFenceLedger()
    ledger.fail_kinds = {"history_reset"}
    app = _make_app(tmp_path, ledger=ledger)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            owner = app.state.users.get_by_email("golfer@example.com")
            _terminal_job(app, owner.id)
            token = _mint_step_up_token(client, bearer, messages)
            pending = _reset(client, bearer, token)
            assert pending.status_code == 202, pending.text
            assert pending.json()["status"] == "pending"
            assert int(pending.headers["Retry-After"]) >= 1
            # Nothing local may be erased before the fence record exists.
            assert app.state.users.get(owner.id).history_epoch == 0
            assert len(app.state.jobs.list_recent(10, user_id=owner.id)) == 1

            ledger.fail_kinds = set()
            resumed = _reset(client, bearer, token)
            assert resumed.status_code == 204, resumed.text
            assert app.state.users.get(owner.id).history_epoch == 1
            assert app.state.jobs.list_recent(10, user_id=owner.id) == []
        assert ledger.kinds().count("history_reset") == 1
    finally:
        _close(app)


def test_crash_recovery_finishes_a_prepared_journal_at_startup(
    tmp_path, messages
):
    ledger = FakeRecoveryFenceLedger()
    ledger.fail_kinds = {"history_reset"}
    first = _make_app(tmp_path, ledger=ledger)
    owner_id = None
    try:
        with TestClient(first) as client:
            bearer = _sign_in(client, messages)
            owner_id = first.state.users.get_by_email("golfer@example.com").id
            token = _mint_step_up_token(client, bearer, messages)
            assert _reset(client, bearer, token).status_code == 202
            journals = first.state.users.nonterminal_privacy_erasure_operations(
                "history_reset"
            )
            assert [journal.phase for journal in journals] == [
                "exports_quiescing"
            ]
    finally:
        _close(first)

    recovered_ledger = FakeRecoveryFenceLedger()
    second = _make_app(tmp_path, ledger=recovered_ledger)
    try:
        # Startup resumption is what finishes the journal; no request is made.
        assert (
            second.state.users.nonterminal_privacy_erasure_operations(
                "history_reset"
            )
            == ()
        )
        assert second.state.users.get(owner_id).history_epoch == 1
        assert recovered_ledger.kinds() == ["history_reset"]
    finally:
        _close(second)


# -- browser parity ---------------------------------------------------------


def _browser_app(tmp_path, monkeypatch, ledger):
    """A cookie-only app whose password signup signs the browser straight in.

    Browser cookie signup/login is independent of native email auth, so this
    keeps native auth off and email delivery unconfigured: the confirmation page
    stays an ordinary session form.
    """

    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = _config(native_auth=False)
    cfg.web["history_reset_enabled"] = True
    cfg.web["passwordless_login"] = False
    return create_app(
        cfg,
        tmp_path / "sessions",
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
        recovery_fence_ledger=ledger,
    )


def test_browser_confirmation_uses_the_same_journal_and_fence(
    tmp_path, monkeypatch
):
    ledger = FakeRecoveryFenceLedger()
    app = _browser_app(tmp_path, monkeypatch, ledger)
    try:
        with TestClient(app) as client:
            signup = client.post(
                "/signup",
                data={"email": "browser@example.com", "password": "longenough"},
                follow_redirects=False,
            )
            assert signup.status_code == 303, signup.text
            page = client.get("/account/history/delete")
            assert page.status_code == 200, page.text
            nonce = re.search(r'name="nonce" value="([^"]+)"', page.text).group(1)
            confirmed = client.post(
                "/account/history/delete",
                data={
                    "nonce": nonce,
                    "confirmation": "START OVER",
                    "password": "longenough",
                },
                follow_redirects=False,
            )
            owner = app.state.users.get_by_email("browser@example.com")
        assert confirmed.status_code == 303, confirmed.text
        assert confirmed.headers["location"] == "/account"
        assert owner.history_epoch == 1
        assert ledger.kinds() == ["history_reset"]
    finally:
        _close(app)


def test_browser_confirmation_reports_202_while_the_fence_is_down(
    tmp_path, monkeypatch
):
    ledger = FakeRecoveryFenceLedger()
    ledger.fail_kinds = {"history_reset"}
    app = _browser_app(tmp_path, monkeypatch, ledger)
    try:
        with TestClient(app) as client:
            assert client.post(
                "/signup",
                data={"email": "browser@example.com", "password": "longenough"},
                follow_redirects=False,
            ).status_code == 303
            page = client.get("/account/history/delete")
            nonce = re.search(r'name="nonce" value="([^"]+)"', page.text).group(1)
            pending = client.post(
                "/account/history/delete",
                data={
                    "nonce": nonce,
                    "confirmation": "START OVER",
                    "password": "longenough",
                },
                follow_redirects=False,
            )
            assert pending.status_code == 202, pending.text
            assert int(pending.headers["Retry-After"]) >= 1
            assert "still finishing" in pending.text
            owner = app.state.users.get_by_email("browser@example.com")
            assert owner.history_epoch == 0

            # A later page view resumes the same operation rather than
            # redirecting to a history that has not gone yet.
            ledger.fail_kinds = set()
            resumed = client.get(
                "/account/history/delete", follow_redirects=False
            )
            assert resumed.status_code == 303, resumed.text
            assert resumed.headers["location"] == "/account"
            assert app.state.users.get(owner.id).history_epoch == 1
        assert ledger.kinds() == ["history_reset"]
    finally:
        _close(app)


# -- documented contract ----------------------------------------------------


def test_openapi_documents_the_history_reset_route(tmp_path):
    app = _make_app(tmp_path, native_auth=False)
    try:
        schema = app.openapi()
        path = schema["paths"]["/api/v1/privacy/history-reset"]["post"]
        assert {"MobileBearer": []} in path["security"]
        assert any(
            parameter["name"] == "Idempotency-Key"
            for parameter in path["parameters"]
        )
        assert set(path["responses"]) >= {"202", "204", "401", "409"}
        body = path["requestBody"]["content"]["application/json"]["schema"]
        assert set(body["required"]) == {
            "step_up_token",
            "expected_history_epoch",
        }
    finally:
        _close(app)
