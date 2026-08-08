"""Native account deletion behind default-off ``mobile_privacy_enabled``.

``DELETE /api/v1/account`` consumes one ``account_delete`` step-up token, opens a
durable journal (``prepared`` → ``analysis_quiescing`` → ``jobs_closed`` →
``files_quarantined`` → ``erasure_recorded`` → ``identity_deleted`` →
``complete``), and publishes one recovery-fence ``account_delete`` record before
the identity is removed. Because the journal revokes every credential while it is
still running, a lost 202/204 is replayed from the idempotency key alone, with no
credential left to authenticate.

Review-scoped deletion and Shopify-side erasure are deliberately out of scope:
this path never mutates the store, and merchant-side erasure stays owned by the
Shopify privacy webhooks.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
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
from swinglab.web.users import ACCOUNT_DELETE_REPLAY_TTL_S


DELETE_IDEMPOTENCY_KEY = "abcdabcdabcdabcd1234123412341234"
OTHER_IDEMPOTENCY_KEY = "5555666677778888aaaabbbbccccdddd"
STEP_UP_IDEMPOTENCY_KEY = "fedcba9876543210fedcba9876543210"
EXPORT_IDEMPOTENCY_KEY = "aaaabbbbccccddddaaaabbbbccccdddd"
VERIFIER = "v" * 43
STEP_UP_VERIFIER = "s" * 43
INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"
OWNER_EMAIL = "golfer@example.com"


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


def _sign_in(
    client: TestClient,
    messages,
    *,
    email=OWNER_EMAIL,
    exchange_idempotency_key: str | None = None,
) -> str:
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
    if exchange_idempotency_key is None:
        exchange_idempotency_key = hashlib.sha256(
            f"signin:{email}:{challenge_id}".encode("ascii")
        ).hexdigest()[:32]
    exchange = client.post(
        "/api/v1/auth/email/exchange",
        json={
            "challenge_id": challenge_id,
            "email_code": code,
            "code_verifier": VERIFIER,
        },
        headers={"Idempotency-Key": exchange_idempotency_key},
    )
    assert exchange.status_code == 201, exchange.text
    messages.clear()
    return exchange.json()["access_token"]


def _mint_step_up_token(
    client,
    bearer,
    messages,
    *,
    purpose="account_delete",
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


def _delete(
    client, bearer, token, *, idempotency_key=DELETE_IDEMPOTENCY_KEY
):
    return client.request(
        "DELETE",
        "/api/v1/account",
        json={"step_up_token": token},
        headers={
            "Authorization": f"Bearer {bearer}",
            "Idempotency-Key": idempotency_key,
        },
    )


# -- flag concealment ------------------------------------------------------


def test_flag_off_conceals_account_deletion_before_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    app = _make_app(tmp_path, privacy_enabled=False, native_auth=False)
    try:
        with TestClient(app) as client:
            response = client.request(
                "DELETE",
                "/api/v1/account",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
    finally:
        _close(app)


# -- authorization ---------------------------------------------------------


def test_bearer_alone_cannot_delete_the_account(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            response = _delete(client, bearer, "custg_nope.deadbeefdeadbeef")
            owner = app.state.users.get_by_email(OWNER_EMAIL)
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_rejected"
        assert owner is not None
    finally:
        _close(app)


def test_step_up_alone_cannot_delete_the_account(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            response = client.request(
                "DELETE",
                "/api/v1/account",
                json={"step_up_token": token},
                headers={"Idempotency-Key": DELETE_IDEMPOTENCY_KEY},
            )
            owner = app.state.users.get_by_email(OWNER_EMAIL)
        assert response.status_code == 401
        assert owner is not None
    finally:
        _close(app)


def test_wrong_purpose_step_up_token_cannot_delete_the_account(
    tmp_path, messages
):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(
                client, bearer, messages, purpose="history_reset"
            )
            response = _delete(client, bearer, token)
            owner = app.state.users.get_by_email(OWNER_EMAIL)
        assert response.status_code == 401
        assert owner is not None
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
            response = client.request(
                "DELETE",
                "/api/v1/account",
                json={"step_up_token": token},
                headers={"Authorization": f"Bearer {bearer}"},
            )
            owner = app.state.users.get_by_email(OWNER_EMAIL)
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_idempotency_key"
        assert owner is not None
    finally:
        _close(app)


# -- deletion --------------------------------------------------------------


def test_deletion_removes_the_account_and_fences_it(tmp_path, messages):
    ledger = FakeRecoveryFenceLedger()
    app = _make_app(tmp_path, ledger=ledger)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            owner = app.state.users.get_by_email(OWNER_EMAIL)
            _terminal_job(app, owner.id)
            token = _mint_step_up_token(client, bearer, messages)
            response = _delete(client, bearer, token)
            users = app.state.users
        assert response.status_code == 204, response.text
        assert response.content == b""
        assert users.get(owner.id) is None
        assert users.get_by_email(OWNER_EMAIL) is None
        assert app.state.jobs.list_recent(10, user_id=owner.id) == []
        delete_events = [
            event
            for event in ledger.events
            if event.kind.value == "account_delete"
        ]
        assert len(delete_events) == 1
        event = delete_events[0]
        # Only versioned digests reach the chain: no raw account id or email.
        assert event.stable_user_hmac_key_id == "k1"
        assert event.normalized_email_hmac_key_id == "k1"
        assert owner.id not in event.stable_user_hmac
        assert OWNER_EMAIL not in event.normalized_email_hmac
    finally:
        _close(app)


def test_deletion_revokes_the_bearer_it_authenticated_with(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            assert _delete(client, bearer, token).status_code == 204
            after = client.post(
                "/api/v1/auth/step-up/start",
                json={
                    "purpose": "account_delete",
                    "code_challenge": _challenge(STEP_UP_VERIFIER),
                },
                headers={"Authorization": f"Bearer {bearer}"},
            )
        assert after.status_code == 401, after.text
    finally:
        _close(app)


def test_deletion_purges_export_receipts_and_archives(tmp_path, messages):
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
            archive = (
                tmp_path
                / "sessions"
                / PRIVACY_EXPORT_DIRNAME
                / f"{export_id}.zip"
            )
            assert archive.exists()
            delete_token = _mint_step_up_token(
                client,
                bearer,
                messages,
                verifier="d" * 43,
                idempotency_key=OTHER_IDEMPOTENCY_KEY,
            )
            response = _delete(client, bearer, delete_token)
        assert response.status_code == 204, response.text
        assert not archive.exists()
        assert app.state.users.privacy_export_ids() == frozenset()
    finally:
        _close(app)


def test_deletion_leaves_no_owned_rows_behind(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            owner = app.state.users.get_by_email(OWNER_EMAIL)
            token = _mint_step_up_token(client, bearer, messages)
            assert _delete(client, bearer, token).status_code == 204
            connection = app.state.users._conn
            leftovers = {}
            with app.state.users._lock:
                tables = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                ]
                for table in tables:
                    columns = [
                        str(column[1])
                        for column in connection.execute(
                            f"PRAGMA table_info({table})"
                        ).fetchall()
                    ]
                    if "user_id" not in columns:
                        continue
                    count = connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE user_id = ?",
                        (owner.id,),
                    ).fetchone()[0]
                    if count:
                        leftovers[table] = count
        # Only the deletion journal may reference the owner, and it is replaced
        # by a non-PII receipt on completion.
        assert leftovers == {}, leftovers
    finally:
        _close(app)


def test_active_analysis_defers_deletion_with_202(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            owner = app.state.users.get_by_email(OWNER_EMAIL)
            job = app.state.jobs.create_session(
                source_name="in-flight.mov", user_id=owner.id
            )
            token = _mint_step_up_token(client, bearer, messages)
            pending = _delete(client, bearer, token)
            assert pending.status_code == 202, pending.text
            assert pending.json()["status"] == "pending"
            assert int(pending.headers["Retry-After"]) >= 1
            # The in-flight analysis is never destroyed underneath its worker.
            assert app.state.users.get(owner.id) is not None
            assert app.state.jobs.get(job.id) is not None

            # Drain the in-flight analysis to a terminal failed state so the
            # journal can advance past analysis_quiescing.
            job.status = "failed"
            app.state.jobs._save(job)
            resumed = client.request(
                "DELETE",
                "/api/v1/account",
                json={"step_up_token": token},
                headers={"Idempotency-Key": DELETE_IDEMPOTENCY_KEY},
            )
            assert resumed.status_code == 204, resumed.text
            assert app.state.users.get(owner.id) is None
    finally:
        _close(app)


# -- idempotency ------------------------------------------------------------


def test_lost_204_replays_after_every_credential_is_gone(tmp_path, messages):
    ledger = FakeRecoveryFenceLedger()
    app = _make_app(tmp_path, ledger=ledger)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            assert _delete(client, bearer, token).status_code == 204
            # No bearer, no account, no step-up token left: possession of the
            # exact 128-bit key is the only thing that answers.
            replay = client.request(
                "DELETE",
                "/api/v1/account",
                json={"step_up_token": token},
                headers={"Idempotency-Key": DELETE_IDEMPOTENCY_KEY},
            )
        assert replay.status_code == 204, replay.text
        assert ledger.kinds().count("account_delete") == 1
    finally:
        _close(app)


def test_conflicting_idempotency_key_is_rejected(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            assert _delete(client, bearer, token).status_code == 204
            # The account-delete request hash carries no request-specific field,
            # so a reused key can only ever mean the same operation. A different
            # owner's key must never resolve to this receipt.
            other = client.request(
                "DELETE",
                "/api/v1/account",
                json={"step_up_token": token},
                headers={"Idempotency-Key": OTHER_IDEMPOTENCY_KEY},
            )
        assert other.status_code == 401, other.text
    finally:
        _close(app)


def test_another_owners_key_replays_without_deleting_the_survivor(
    tmp_path, messages
):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            first = _sign_in(client, messages, email="first@example.com")
            first_token = _mint_step_up_token(client, first, messages)
            assert _delete(client, first, first_token).status_code == 204

            second = _sign_in(client, messages, email="second@example.com")
            second_token = _mint_step_up_token(
                client,
                second,
                messages,
                verifier="q" * 43,
                idempotency_key=OTHER_IDEMPOTENCY_KEY,
            )
            # Possession of the deleted owner's exact key answers 204 from the
            # receipt before auth; it must not erase a different live account.
            reused = _delete(
                client,
                second,
                second_token,
                idempotency_key=DELETE_IDEMPOTENCY_KEY,
            )
            survivor = app.state.users.get_by_email("second@example.com")
        assert reused.status_code == 204, reused.text
        assert survivor is not None
    finally:
        _close(app)


def test_a_deletion_receipt_stops_replaying_once_its_ttl_expires(
    tmp_path, messages
):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            assert _delete(client, bearer, token).status_code == 204
            users = app.state.users
            assert (
                users.find_privacy_erasure_operation(
                    "account_delete", DELETE_IDEMPOTENCY_KEY
                )
                is not None
            )
            # A deletion tombstone is a replay window, not a permanent record.
            service = app.state.privacy_erasure_service
            service._now = lambda: time.time() + ACCOUNT_DELETE_REPLAY_TTL_S + 60
            aged_out = client.request(
                "DELETE",
                "/api/v1/account",
                json={"step_up_token": token},
                headers={"Idempotency-Key": DELETE_IDEMPOTENCY_KEY},
            )
            swept = users.find_privacy_erasure_operation(
                "account_delete", DELETE_IDEMPOTENCY_KEY
            )
        assert aged_out.status_code == 401, aged_out.text
        assert swept is None
    finally:
        _close(app)


# -- fence outage -----------------------------------------------------------


def test_fence_outage_returns_202_and_keeps_the_identity(tmp_path, messages):
    ledger = FakeRecoveryFenceLedger()
    ledger.fail_kinds = {"account_delete"}
    app = _make_app(tmp_path, ledger=ledger)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            owner = app.state.users.get_by_email(OWNER_EMAIL)
            job = _terminal_job(app, owner.id)
            token = _mint_step_up_token(client, bearer, messages)
            pending = _delete(client, bearer, token)
            assert pending.status_code == 202, pending.text
            # Nothing irreversible may land before the fence record exists:
            # identity, owned sessions, and history_epoch all stay put.
            still = app.state.users.get(owner.id)
            assert still is not None
            assert still.history_epoch == 0
            assert app.state.jobs.get(job.id) is not None
            assert len(app.state.jobs.list_recent(10, user_id=owner.id)) == 1
            journals = app.state.users.nonterminal_privacy_erasure_operations(
                "account_delete"
            )
            assert [journal.phase for journal in journals] == [
                "files_quarantined"
            ]
            assert ledger.kinds() == []

            ledger.fail_kinds = set()
            resumed = client.request(
                "DELETE",
                "/api/v1/account",
                json={"step_up_token": token},
                headers={"Idempotency-Key": DELETE_IDEMPOTENCY_KEY},
            )
            assert resumed.status_code == 204, resumed.text
            assert app.state.users.get(owner.id) is None
            assert app.state.jobs.list_recent(10, user_id=owner.id) == []
        assert ledger.kinds().count("account_delete") == 1
    finally:
        _close(app)


def test_crash_recovery_finishes_a_pending_deletion_at_startup(
    tmp_path, messages
):
    ledger = FakeRecoveryFenceLedger()
    ledger.fail_kinds = {"account_delete"}
    first = _make_app(tmp_path, ledger=ledger)
    owner_id = None
    try:
        with TestClient(first) as client:
            bearer = _sign_in(client, messages)
            owner_id = first.state.users.get_by_email(OWNER_EMAIL).id
            token = _mint_step_up_token(client, bearer, messages)
            assert _delete(client, bearer, token).status_code == 202
    finally:
        _close(first)

    recovered = FakeRecoveryFenceLedger()
    second = _make_app(tmp_path, ledger=recovered)
    try:
        assert (
            second.state.users.nonterminal_privacy_erasure_operations(
                "account_delete"
            )
            == ()
        )
        assert second.state.users.get(owner_id) is None
        assert recovered.kinds() == ["account_delete"]
    finally:
        _close(second)


# -- a later account with the same address ----------------------------------


def test_a_new_account_can_reuse_the_deleted_address(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            deleted_id = app.state.users.get_by_email(OWNER_EMAIL).id
            token = _mint_step_up_token(client, bearer, messages)
            assert _delete(client, bearer, token).status_code == 204

            fresh_bearer = _sign_in(client, messages)
            reborn = app.state.users.get_by_email(OWNER_EMAIL)
            # Privacy stays on; step-up start proves the new bearer is live.
            probe = client.post(
                "/api/v1/auth/step-up/start",
                json={
                    "purpose": "data_export",
                    "code_challenge": _challenge("n" * 43),
                },
                headers={"Authorization": f"Bearer {fresh_bearer}"},
            )
        assert reborn is not None
        # A recycled account identifier would let an old fence record erase the
        # new account on a restore.
        assert reborn.id != deleted_id
        assert reborn.history_epoch == 0
        assert probe.status_code == 202, probe.text
    finally:
        _close(app)


# -- documented contract ----------------------------------------------------


def test_openapi_documents_the_account_delete_route(tmp_path):
    app = _make_app(tmp_path, native_auth=False)
    try:
        schema = app.openapi()
        path = schema["paths"]["/api/v1/account"]["delete"]
        assert {"MobileBearer": []} in path["security"]
        assert any(
            parameter["name"] == "Idempotency-Key"
            for parameter in path["parameters"]
        )
        assert set(path["responses"]) >= {"202", "204", "401", "409"}
        body = path["requestBody"]["content"]["application/json"]["schema"]
        assert body["required"] == ["step_up_token"]
    finally:
        _close(app)
