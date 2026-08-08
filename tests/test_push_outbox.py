"""Task 7 outbox slice: enqueue + drain with a fake Expo provider."""

from __future__ import annotations

import os

import pytest

from swinglab.web.jobs import DONE, Job
from swinglab.web.push_delivery import (
    EXPO_ACCESS_TOKEN_ENV,
    FakeExpoPushProvider,
    KIND_ANALYSIS_READY,
    PUSH_TTL_SECONDS,
    PushOutboxStore,
    PushOutboxWorker,
    attach_job_push_observer,
    expo_delivery_configured,
)
from swinglab.web.push_store import MobilePushSettings
from tests.test_mobile_push import (
    EXPO_PROJECT_ID,
    EXPO_TOKEN_A,
    _app,
    _close,
    _identity,
    _issue,
    _put_body,
)
from fastapi.testclient import TestClient


@pytest.fixture
def expo_token(monkeypatch):
    monkeypatch.setenv(EXPO_ACCESS_TOKEN_ENV, "test-expo-access-token")
    return "test-expo-access-token"


def test_no_enqueue_without_expo_access_token(tmp_path, monkeypatch):
    monkeypatch.delenv(EXPO_ACCESS_TOKEN_ENV, raising=False)
    assert expo_delivery_configured() is False
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        assert (
            client.put(
                "/api/v1/devices/push",
                headers={
                    "Authorization": f"Bearer {raw}",
                    **_identity(),
                },
                json=_put_body(),
            ).status_code
            == 200
        )
        store = PushOutboxStore(users)
        job = Job(
            id="jobdeadbeef01",
            session_dir=tmp_path / "sessions" / "jobdeadbeef01",
            status=DONE,
            user_id=user.id,
        )
        assert (
            store.enqueue_job_notification(
                job,
                kind=KIND_ANALYSIS_READY,
                environment="development",
                expo_project_id=EXPO_PROJECT_ID,
            )
            is False
        )
        count = users._conn.execute(
            "SELECT COUNT(*) FROM mobile_push_outbox"
        ).fetchone()[0]
        assert count == 0
    finally:
        _close(app)


def test_terminal_job_enqueues_and_drain_delivers(tmp_path, expo_token, monkeypatch):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        assert (
            client.put(
                "/api/v1/devices/push",
                headers={
                    "Authorization": f"Bearer {raw}",
                    **_identity(),
                },
                json=_put_body(),
            ).status_code
            == 200
        )
        provider = FakeExpoPushProvider()
        store = PushOutboxStore(users)
        worker = PushOutboxWorker(users, provider, enabled=True)
        settings = MobilePushSettings(
            enabled=True, expo_project_id=EXPO_PROJECT_ID
        )
        # Replace any default observer with one bound to our store.
        app.state.jobs._completion_observers.clear()
        attach_job_push_observer(
            app.state.jobs,
            outbox=store,
            settings=settings,
            deployment_environment="development",
        )

        job = app.state.jobs.create_session(
            source_name="clip.mov", user_id=user.id
        )
        (job.session_dir / "source.mov").write_bytes(b"video")
        job.status = DONE
        app.state.jobs._save(job)
        app.state.jobs._notify_completion_observers(job)

        rows = users._conn.execute(
            "SELECT kind, status, selector FROM mobile_push_outbox"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["kind"] == KIND_ANALYSIS_READY
        assert rows[0]["status"] == "pending"
        assert rows[0]["selector"] == token.selector

        assert worker.drain_once() is True
        assert len(provider.sent) == 1
        assert provider.sent[0].ttl == PUSH_TTL_SECONDS
        assert provider.sent[0].to == EXPO_TOKEN_A
        assert "Your swing analysis is ready." in provider.sent[0].body
        delivered = users._conn.execute(
            "SELECT status, provider_ticket_id FROM mobile_push_outbox"
        ).fetchone()
        assert delivered["status"] == "delivered"
        assert delivered["provider_ticket_id"]
        # Provider outage must not change job status.
        assert app.state.jobs.get(job.id).status == DONE
    finally:
        _close(app)


def test_unique_outbox_key_and_sign_out_kills_pending(
    tmp_path, expo_token
):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        assert (
            client.put(
                "/api/v1/devices/push",
                headers={
                    "Authorization": f"Bearer {raw}",
                    **_identity(),
                },
                json=_put_body(),
            ).status_code
            == 200
        )
        store = PushOutboxStore(users)
        job = Job(
            id="jobunique0001",
            session_dir=tmp_path / "sessions" / "jobunique0001",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment="development",
            expo_project_id=EXPO_PROJECT_ID,
        )
        assert (
            store.enqueue_job_notification(
                job,
                kind=KIND_ANALYSIS_READY,
                environment="development",
                expo_project_id=EXPO_PROJECT_ID,
            )
            is False
        )
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
            ).fetchone()[0]
            == 1
        )

        # Sign-out extension clears registration and pending outbox.
        assert app.state.sign_out_service is not None
        from swinglab.web.push_store import PushRegistrationService

        extensions = [
            ext
            for ext in app.state.sign_out_service._extensions
            if getattr(ext, "extension_id", None)
            == PushRegistrationService.extension_id
        ]
        assert len(extensions) == 1
        extensions[0].close_for_sign_out(
            users=users,
            operation_id="op1",
            user_id=user.id,
            selector=token.selector,
        )
        status = users._conn.execute(
            "SELECT status FROM mobile_push_outbox"
        ).fetchone()["status"]
        assert status == "dead"
    finally:
        _close(app)


def test_schema_generation_is_four(tmp_path):
    app = _app(tmp_path, push_enabled=False)
    try:
        from swinglab.web.mobile_schema import (
            MOBILE_STATE_SCHEMA_GENERATION,
            detect_mobile_state_generation,
        )

        assert MOBILE_STATE_SCHEMA_GENERATION == 4
        assert detect_mobile_state_generation(app.state.users._conn) == 4
        assert (
            app.state.users._conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'mobile_push_outbox'"
            ).fetchone()
            is not None
        )
    finally:
        _close(app)
