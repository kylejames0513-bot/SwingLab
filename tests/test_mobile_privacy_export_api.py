"""Native privacy export behind default-off ``mobile_privacy_enabled``.

This is the next vertical slice of Task 6: a ``data_export`` step-up token is
consumed to create an owned :class:`PrivacyExportReceipt`, a leased worker
builds a small ZIP (profile + sessions summary), and the ready archive streams
same-origin with an exact ``Content-Length``. History reset, account deletion,
review step-up, and the full durable download-admission budget model are
intentionally out of scope for this slice.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import zipfile
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import mailer
from swinglab.web.app import create_app
from swinglab.web.mobile_schema import VersionedHMAC


IDEMPOTENCY_KEY = "0123456789abcdef0123456789abcdef"
STEP_UP_IDEMPOTENCY_KEY = "fedcba9876543210fedcba9876543210"
EXPORT_IDEMPOTENCY_KEY = "aaaabbbbccccddddaaaabbbbccccdddd"
VERIFIER = "v" * 43
STEP_UP_VERIFIER = "s" * 43
INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"
MAX_DOWNLOAD_BYTES = 1_100_000_000


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
    # A distinct idempotency key per account keeps multi-user tests independent.
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
    purpose="data_export",
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


def _create_export(client, bearer, token, *, idempotency_key=EXPORT_IDEMPOTENCY_KEY):
    return client.post(
        "/api/v1/privacy/exports",
        json={"step_up_token": token},
        headers={
            "Authorization": f"Bearer {bearer}",
            "Idempotency-Key": idempotency_key,
        },
    )


def _get_export(client, bearer, export_id):
    return client.get(
        f"/api/v1/privacy/exports/{export_id}",
        headers={"Authorization": f"Bearer {bearer}"},
    )


def _download(client, bearer, export_id, *, extra_headers=None):
    headers = {"Authorization": f"Bearer {bearer}"}
    if extra_headers:
        headers.update(extra_headers)
    return client.get(
        f"/api/v1/privacy/exports/{export_id}/download", headers=headers
    )


# -- flag concealment ------------------------------------------------------


def test_flag_off_conceals_export_routes_before_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    app = _make_app(tmp_path, privacy_enabled=False, native_auth=False)
    try:
        with TestClient(app) as client:
            create = client.post(
                "/api/v1/privacy/exports",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
            status = client.get("/api/v1/privacy/exports/whatever")
            download = client.get("/api/v1/privacy/exports/whatever/download")
        assert create.status_code == 404
        assert status.status_code == 404
        assert download.status_code == 404
    finally:
        _close(app)


# -- create ----------------------------------------------------------------


def test_create_requires_bearer(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/privacy/exports",
                json={"step_up_token": "custg_nope.deadbeef"},
                headers={"Idempotency-Key": EXPORT_IDEMPOTENCY_KEY},
            )
        assert response.status_code == 401
    finally:
        _close(app)


def test_create_returns_pending_receipt_bound_to_history_epoch(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            response = _create_export(client, bearer, token)
            assert response.status_code == 202, response.text
            body = response.json()
            assert body["status"] == "pending"
            assert body["max_download_bytes"] == MAX_DOWNLOAD_BYTES
            assert body["byte_size"] is None
            assert body["expires_at"] is None
            assert body["retry_after_seconds"] >= 0
            assert body["export_id"]
            assert response.headers["cache-control"] == "no-store"

            row = app.state.users._conn.execute(
                "SELECT * FROM privacy_export_receipts WHERE export_id = ?",
                (body["export_id"],),
            ).fetchone()
            assert row["status"] == "pending"
            assert row["history_epoch"] == 0
            # The consumed step-up token is claimed exactly once.
            token_row = app.state.users._conn.execute(
                "SELECT claimed_at FROM step_up_tokens"
            ).fetchone()
            assert token_row["claimed_at"] is not None
    finally:
        _close(app)


def test_create_exact_replay_returns_same_receipt(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            first = _create_export(client, bearer, token)
            assert first.status_code == 202, first.text
            replay = _create_export(client, bearer, token)
            assert replay.status_code == 202, replay.text
            assert replay.json()["export_id"] == first.json()["export_id"]
            assert app.state.users._conn.execute(
                "SELECT COUNT(*) FROM privacy_export_receipts"
            ).fetchone()[0] == 1
    finally:
        _close(app)


def test_create_conflicting_idempotency_is_generic_409(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token_a = _mint_step_up_token(
                client, bearer, messages, verifier="a" * 43,
                idempotency_key="1111111111111111aaaaaaaaaaaaaaaa",
            )
            token_b = _mint_step_up_token(
                client, bearer, messages, verifier="b" * 43,
                idempotency_key="2222222222222222bbbbbbbbbbbbbbbb",
            )
            first = _create_export(client, bearer, token_a)
            assert first.status_code == 202
            # Same export Idempotency-Key, different token body → conflict.
            conflict = _create_export(client, bearer, token_b)
            assert conflict.status_code == 409
            assert conflict.json()["code"] == "export_conflict"
    finally:
        _close(app)


def test_create_rejects_wrong_purpose_token(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages, purpose="history_reset")
            response = _create_export(client, bearer, token)
            assert response.status_code == 401
            assert app.state.users._conn.execute(
                "SELECT COUNT(*) FROM privacy_export_receipts"
            ).fetchone()[0] == 0
    finally:
        _close(app)


def test_create_rejects_expired_token(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            app.state.users._conn.execute("UPDATE step_up_tokens SET expires_at = 1")
            app.state.users._conn.commit()
            response = _create_export(client, bearer, token)
            assert response.status_code == 401
    finally:
        _close(app)


def test_create_rejects_already_claimed_token(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            first = _create_export(client, bearer, token)
            assert first.status_code == 202
            # A second, distinct request replaying the same (now claimed) token
            # with a fresh Idempotency-Key cannot mint a second export.
            second = _create_export(
                client, bearer, token, idempotency_key="9999999999999999cccccccccccccccc"
            )
            assert second.status_code == 401
    finally:
        _close(app)


def test_create_rejects_cross_account_token(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            owner = _sign_in(client, messages, email="owner@example.com")
            token = _mint_step_up_token(client, owner, messages)
            attacker = _sign_in(client, messages, email="attacker@example.com")
            response = _create_export(client, attacker, token)
            assert response.status_code == 401
            assert app.state.users._conn.execute(
                "SELECT COUNT(*) FROM privacy_export_receipts"
            ).fetchone()[0] == 0
    finally:
        _close(app)


# -- status + worker -------------------------------------------------------


def test_background_workers_disabled_leaves_receipt_pending(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            export_id = _create_export(client, bearer, token).json()["export_id"]
            # start_background_workers=False means no thread advances the receipt.
            status = _get_export(client, bearer, export_id)
            assert status.status_code == 200
            assert status.json()["status"] == "pending"
    finally:
        _close(app)


def test_worker_builds_ready_receipt_and_status_reports_size(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            export_id = _create_export(client, bearer, token).json()["export_id"]
            assert app.state.privacy_export_worker.drain_once() is True
            status = _get_export(client, bearer, export_id)
            assert status.status_code == 200, status.text
            body = status.json()
            assert body["status"] == "ready"
            assert 1 <= body["byte_size"] <= MAX_DOWNLOAD_BYTES
            assert body["expires_at"] is not None
            assert body["max_download_bytes"] == MAX_DOWNLOAD_BYTES
    finally:
        _close(app)


def test_status_cross_account_is_404(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            owner = _sign_in(client, messages, email="owner@example.com")
            token = _mint_step_up_token(client, owner, messages)
            export_id = _create_export(client, owner, token).json()["export_id"]
            attacker = _sign_in(client, messages, email="attacker@example.com")
            response = _get_export(client, attacker, export_id)
            assert response.status_code == 404
    finally:
        _close(app)


# -- download --------------------------------------------------------------


def test_download_before_ready_is_409_export_pending(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            export_id = _create_export(client, bearer, token).json()["export_id"]
            response = _download(client, bearer, export_id)
            assert response.status_code == 409
            assert response.json()["code"] == "export_pending"
    finally:
        _close(app)


def test_download_ready_streams_same_origin_zip(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            export_id = _create_export(client, bearer, token).json()["export_id"]
            app.state.privacy_export_worker.drain_once()
            receipt = _get_export(client, bearer, export_id).json()
            response = _download(client, bearer, export_id)
            assert response.status_code == 200, response.text
            assert response.headers["content-type"] == "application/zip"
            assert response.headers["cache-control"] == "no-store"
            assert int(response.headers["content-length"]) == receipt["byte_size"]
            assert len(response.content) == receipt["byte_size"]
            # No redirect to object storage or another origin.
            assert response.history == []
            archive = zipfile.ZipFile(io.BytesIO(response.content))
            assert archive.testzip() is None
            names = set(archive.namelist())
            assert "profile.json" in names
            assert "sessions.json" in names
            assert "manifest.json" in names
            blob = b"".join(archive.read(name) for name in names)
            for forbidden in (b"password", b"scrypt", b"token_hash", b"code_hmac"):
                assert forbidden not in blob
    finally:
        _close(app)


def test_lost_lease_after_publish_does_not_delete_ready_zip(tmp_path, messages):
    """A lagging worker that loses record_privacy_export_ready must not unlink
    a ZIP another worker already marked ready at the same path."""
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            export_id = _create_export(client, bearer, token).json()["export_id"]
            assert app.state.privacy_export_worker.drain_once() is True
            path = app.state.privacy_export_service._artifact_path(export_id)
            assert path.exists()
            # Lagging claim cannot steal ready; must not delete the artifact.
            published = app.state.users.record_privacy_export_ready(
                export_id, worker_id="lagging-worker", byte_size=path.stat().st_size
            )
            assert published is False
            assert path.exists()
            status = _get_export(client, bearer, export_id).json()
            assert status["status"] == "ready"
            assert _download(client, bearer, export_id).status_code == 200
    finally:
        _close(app)


def test_download_rejects_range_requests(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            export_id = _create_export(client, bearer, token).json()["export_id"]
            app.state.privacy_export_worker.drain_once()
            response = _download(
                client, bearer, export_id, extra_headers={"Range": "bytes=0-10"}
            )
            assert response.status_code == 416
            assert response.json()["code"] == "range_not_supported"
    finally:
        _close(app)


def test_download_cross_account_is_404(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            owner = _sign_in(client, messages, email="owner@example.com")
            token = _mint_step_up_token(client, owner, messages)
            export_id = _create_export(client, owner, token).json()["export_id"]
            app.state.privacy_export_worker.drain_once()
            attacker = _sign_in(client, messages, email="attacker@example.com")
            response = _download(client, attacker, export_id)
            assert response.status_code == 404
    finally:
        _close(app)


def test_history_epoch_change_fails_the_build(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            export_id = _create_export(client, bearer, token).json()["export_id"]
            # Simulate a history reset bumping the epoch before the build runs.
            app.state.users._conn.execute(
                "UPDATE users SET history_epoch = history_epoch + 1"
            )
            app.state.users._conn.commit()
            app.state.privacy_export_worker.drain_once()
            status = _get_export(client, bearer, export_id).json()
            assert status["status"] == "failed"
            assert status["failure_code"]
            download = _download(client, bearer, export_id)
            assert download.status_code == 409
            assert download.json()["code"] == status["failure_code"]
    finally:
        _close(app)


def test_download_expired_is_410(tmp_path, messages):
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            bearer = _sign_in(client, messages)
            token = _mint_step_up_token(client, bearer, messages)
            export_id = _create_export(client, bearer, token).json()["export_id"]
            app.state.privacy_export_worker.drain_once()
            app.state.users._conn.execute(
                "UPDATE privacy_export_receipts SET expires_at = 1"
                " WHERE export_id = ?",
                (export_id,),
            )
            app.state.users._conn.commit()
            response = _download(client, bearer, export_id)
            assert response.status_code == 410
            assert response.json()["code"] == "export_expired"
    finally:
        _close(app)


# -- OpenAPI ----------------------------------------------------------------


def test_openapi_documents_export_routes_and_max(tmp_path):
    app = _make_app(tmp_path, privacy_enabled=True, native_auth=False)
    try:
        schema = app.openapi()
        assert "/api/v1/privacy/exports" in schema["paths"]
        assert "/api/v1/privacy/exports/{export_id}" in schema["paths"]
        assert "/api/v1/privacy/exports/{export_id}/download" in schema["paths"]
        receipt = schema["components"]["schemas"]["PrivacyExportReceiptResponse"]
        max_field = receipt["properties"]["max_download_bytes"]
        assert max_field.get("const") == MAX_DOWNLOAD_BYTES
        byte_size = receipt["properties"]["byte_size"]
        # ``byte_size`` is an optional constrained integer.
        options = byte_size.get("anyOf", [byte_size])
        integer_option = next(o for o in options if o.get("type") == "integer")
        assert integer_option["maximum"] == MAX_DOWNLOAD_BYTES
        assert integer_option["minimum"] == 1
    finally:
        _close(app)
