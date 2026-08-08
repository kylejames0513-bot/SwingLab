"""End-to-end HTTP wiring for the durable resumable-upload routes (Task 5).

These exercise the FastAPI surface (auth, Idempotency-Key, the default-off
flag, and the create -> chunk -> complete/abort lifecycle) on top of the
already unit-tested :class:`ResumableUploadManager`.
"""

from __future__ import annotations

import base64
import hashlib
import json

from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.jobs import PROCESSING, QUEUED
from swinglab.web.mobile_schema import VersionedHMAC
from tests.test_mobile_api_tokens import bearer, issue_token

CREATE_KEY = "0123456789abcdef0123456789abcdef"
ABORT_KEY = "fedcba9876543210fedcba9876543210"


def _keyring() -> VersionedHMAC:
    current = base64.b64encode(b"c" * 32).decode("ascii")
    previous = base64.b64encode(b"p" * 32).decode("ascii")
    return VersionedHMAC.from_json(
        json.dumps(
            {
                "version": 1,
                "current_key_id": "current",
                "keys": {"previous": previous, "current": current},
            }
        )
    )


def _app_client(tmp_path, *, resumable_upload_enabled: bool = True):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_resources_enabled"] = True
    cfg.web["mobile_resumable_upload_enabled"] = resumable_upload_enabled
    if resumable_upload_enabled:
        cfg.web["mobile_upload_global_max_reserved_bytes"] = 32 * 1024 * 1024
        cfg.web["mobile_upload_min_filesystem_free_bytes"] = 1
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
    )
    client = TestClient(app)
    user = app.state.users.create(
        "uploader@example.com",
        "longenough",
        email_verified=True,
    )
    login = client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    return app, client, user


def _close(app) -> None:
    app.state.resumable_upload_manager.close()
    for resource in (app.state.jobs, app.state.users, app.state.throttle):
        resource.close()


def _create_body(user, body: bytes, **overrides) -> dict:
    payload = {
        "source_name": "swing.mp4",
        "file_sha256": hashlib.sha256(body).hexdigest(),
        "file_bytes": len(body),
        "club": "iron",
        "hand": "left",
        "angle": "dtl",
        "expected_history_epoch": user.history_epoch,
    }
    payload.update(overrides)
    return payload


def _chunk_headers(token: str, offset: int, chunk: bytes) -> dict:
    digest = base64.b64encode(hashlib.sha256(chunk).digest()).decode("ascii")
    return {
        **bearer(token),
        "Upload-Offset": str(offset),
        "Upload-Checksum": digest,
        "Content-Type": "application/offset+octet-stream",
    }


def test_flag_off_hides_upload_routes(tmp_path):
    app, client, user = _app_client(tmp_path, resumable_upload_enabled=False)
    try:
        token = issue_token(client, "Upload phone")["token"]
        response = client.post(
            "/api/v1/uploads",
            json=_create_body(user, b"0123456789"),
            headers={**bearer(token), "Idempotency-Key": CREATE_KEY},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
    finally:
        _close(app)


def test_missing_idempotency_key_is_rejected_before_work(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Upload phone")["token"]
        response = client.post(
            "/api/v1/uploads",
            json=_create_body(user, b"0123456789"),
            headers=bearer(token),
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_idempotency_key"
    finally:
        _close(app)


def test_full_upload_lifecycle_creates_one_queued_job(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Upload phone")["token"]
        body = b"a-small-swing-video-payload-0123456789"

        created = client.post(
            "/api/v1/uploads",
            json=_create_body(user, body),
            headers={**bearer(token), "Idempotency-Key": CREATE_KEY},
        )
        assert created.status_code == 201, created.text
        reservation = created.json()
        upload_id = reservation["upload_id"]
        assert reservation["status"] == "pending"
        assert reservation["offset"] == 0
        assert reservation["file_bytes"] == len(body)

        # Replaying the exact create returns the same reservation, not a new one.
        replay = client.post(
            "/api/v1/uploads",
            json=_create_body(user, body),
            headers={**bearer(token), "Idempotency-Key": CREATE_KEY},
        )
        assert replay.status_code == 201
        assert replay.json()["upload_id"] == upload_id

        # Send the payload in two chunks, checking the acknowledged offset.
        first, second = body[:20], body[20:]
        patched = client.patch(
            f"/api/v1/uploads/{upload_id}",
            content=first,
            headers=_chunk_headers(token, 0, first),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["offset"] == len(first)

        patched = client.patch(
            f"/api/v1/uploads/{upload_id}",
            content=second,
            headers=_chunk_headers(token, len(first), second),
        )
        assert patched.status_code == 200
        assert patched.json()["offset"] == len(body)

        status = client.get(
            f"/api/v1/uploads/{upload_id}", headers=bearer(token)
        )
        assert status.status_code == 200
        assert status.json()["offset"] == len(body)

        completed = client.post(
            f"/api/v1/uploads/{upload_id}/complete", headers=bearer(token)
        )
        assert completed.status_code == 200, completed.text
        complete_body = completed.json()
        assert complete_body["replayed"] is False
        job = complete_body["job"]
        # A freshly published job is queued and may be picked up immediately.
        assert job["status"] in {QUEUED, PROCESSING}

        # Completion is an idempotent replay once the job exists.
        replayed = client.post(
            f"/api/v1/uploads/{upload_id}/complete", headers=bearer(token)
        )
        assert replayed.status_code == 200
        assert replayed.json()["replayed"] is True
        assert replayed.json()["job"]["id"] == job["id"]

        stored = app.state.jobs.get(job["id"])
        assert stored is not None
        assert stored.user_id == user.id
        assert (stored.session_dir / "source.mp4").exists()
    finally:
        _close(app)


def test_offset_mismatch_reports_acknowledged_offset(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Upload phone")["token"]
        body = b"0123456789abcdef"
        created = client.post(
            "/api/v1/uploads",
            json=_create_body(user, body),
            headers={**bearer(token), "Idempotency-Key": CREATE_KEY},
        )
        upload_id = created.json()["upload_id"]
        bad = client.patch(
            f"/api/v1/uploads/{upload_id}",
            content=body,
            headers=_chunk_headers(token, 5, body),
        )
        assert bad.status_code == 409
        assert bad.json()["code"] == "offset_mismatch"
        assert bad.headers["Upload-Offset"] == "0"
    finally:
        _close(app)


def test_abort_returns_204_and_replays(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Upload phone")["token"]
        body = b"0123456789"
        created = client.post(
            "/api/v1/uploads",
            json=_create_body(user, body),
            headers={**bearer(token), "Idempotency-Key": CREATE_KEY},
        )
        upload_id = created.json()["upload_id"]

        aborted = client.delete(
            f"/api/v1/uploads/{upload_id}",
            headers={**bearer(token), "Idempotency-Key": ABORT_KEY},
        )
        assert aborted.status_code == 204

        replay = client.delete(
            f"/api/v1/uploads/{upload_id}",
            headers={**bearer(token), "Idempotency-Key": ABORT_KEY},
        )
        assert replay.status_code == 204

        conflict = client.delete(
            f"/api/v1/uploads/{upload_id}",
            headers={**bearer(token), "Idempotency-Key": CREATE_KEY},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_conflict"
    finally:
        _close(app)


def test_cross_account_reservation_is_not_found(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Upload phone")["token"]
        body = b"0123456789"
        created = client.post(
            "/api/v1/uploads",
            json=_create_body(user, body),
            headers={**bearer(token), "Idempotency-Key": CREATE_KEY},
        )
        upload_id = created.json()["upload_id"]

        # A second account may never observe the first account's reservation.
        client.post("/logout", follow_redirects=False)
        other = app.state.users.create(
            "intruder@example.com", "longenough", email_verified=True
        )
        client.post(
            "/login",
            data={"email": other.email, "password": "longenough"},
            follow_redirects=False,
        )
        other_token = issue_token(client, "Intruder phone")["token"]
        response = client.get(
            f"/api/v1/uploads/{upload_id}", headers=bearer(other_token)
        )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
    finally:
        _close(app)
