"""Task 7 outbox slice: enqueue + drain with a fake Expo provider."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from swinglab.web.jobs import DONE, FAILED, Job
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
from swinglab.web.push_store import MobilePushSettings, PushRegistrationService
from tests.test_mobile_push import (
    EXPO_PROJECT_ID,
    EXPO_TOKEN_A,
    EXPO_TOKEN_B,
    _app,
    _close,
    _identity,
    _issue,
    _put_body,
)


@pytest.fixture
def expo_token(monkeypatch):
    monkeypatch.setenv(EXPO_ACCESS_TOKEN_ENV, "test-expo-access-token")
    return "test-expo-access-token"


def _register(client, raw: str) -> None:
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


def _sign_out_extension(app):
    extensions = [
        ext
        for ext in app.state.sign_out_service._extensions
        if getattr(ext, "extension_id", None)
        == PushRegistrationService.extension_id
    ]
    assert len(extensions) == 1
    return extensions[0]


def test_no_enqueue_without_expo_access_token(tmp_path, monkeypatch):
    monkeypatch.delenv(EXPO_ACCESS_TOKEN_ENV, raising=False)
    assert expo_delivery_configured() is False
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
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
        _register(client, raw)
        provider = FakeExpoPushProvider()
        store = PushOutboxStore(users)
        worker = PushOutboxWorker(users, provider, enabled=True)
        settings = MobilePushSettings(
            enabled=True, expo_project_id=EXPO_PROJECT_ID
        )
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
        assert app.state.jobs.get(job.id).status == DONE
    finally:
        _close(app)


def test_provider_outage_leaves_job_done_and_outbox_retryable(
    tmp_path, expo_token
):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        provider = FakeExpoPushProvider()
        provider.fail_send = True
        store = PushOutboxStore(users)
        worker = PushOutboxWorker(users, provider, enabled=True)
        settings = MobilePushSettings(
            enabled=True, expo_project_id=EXPO_PROJECT_ID
        )
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

        assert worker.drain_once() is True
        assert provider.sent == []
        row = users._conn.execute(
            "SELECT status, lease_owner FROM mobile_push_outbox"
        ).fetchone()
        assert row["status"] == "pending"
        assert row["lease_owner"] is None
        assert app.state.jobs.get(job.id).status == DONE
    finally:
        _close(app)


def test_failed_job_does_not_enqueue(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        settings = MobilePushSettings(
            enabled=True, expo_project_id=EXPO_PROJECT_ID
        )
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
        job.status = FAILED
        app.state.jobs._save(job)
        app.state.jobs._notify_completion_observers(job)

        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
            ).fetchone()[0]
            == 0
        )
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
        _register(client, raw)
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

        _sign_out_extension(app).close_for_sign_out(
            users=users,
            operation_id="op1",
            user_id=user.id,
            selector=token.selector,
        )
        status = users._conn.execute(
            "SELECT status, lease_owner FROM mobile_push_outbox"
        ).fetchone()
        assert status["status"] == "dead"
        assert status["lease_owner"] is None
    finally:
        _close(app)


def test_sign_out_during_leased_send_keeps_outbox_dead(
    tmp_path, expo_token
):
    """Sign-out mid-send must not be revived by the worker completion UPDATE."""
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        job = Job(
            id="jobleasedrace01",
            session_dir=tmp_path / "sessions" / "jobleasedrace01",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment="development",
            expo_project_id=EXPO_PROJECT_ID,
        )

        extension = _sign_out_extension(app)

        class SignOutDuringSend(FakeExpoPushProvider):
            def send(self, messages):
                extension.close_for_sign_out(
                    users=users,
                    operation_id="op-race",
                    user_id=user.id,
                    selector=token.selector,
                )
                mid = users._conn.execute(
                    "SELECT status, lease_owner FROM mobile_push_outbox"
                ).fetchone()
                assert mid["status"] == "dead"
                assert mid["lease_owner"] is None
                return super().send(messages)

        provider = SignOutDuringSend()
        worker = PushOutboxWorker(users, provider, enabled=True)
        assert worker.drain_once() is True
        assert len(provider.sent) == 1
        final = users._conn.execute(
            "SELECT status, provider_ticket_id, lease_owner FROM mobile_push_outbox"
        ).fetchone()
        assert final["status"] == "dead"
        assert final["provider_ticket_id"] is None
        assert final["lease_owner"] is None
    finally:
        _close(app)


def test_expired_pending_outbox_is_marked_dead(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        job = Job(
            id="jobexpired0001",
            session_dir=tmp_path / "sessions" / "jobexpired0001",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment="development",
            expo_project_id=EXPO_PROJECT_ID,
        )
        users._conn.execute(
            "UPDATE mobile_push_outbox SET expires_at = ?",
            (time.time() - 1,),
        )
        users._conn.commit()
        worker = PushOutboxWorker(
            users, FakeExpoPushProvider(), enabled=True
        )
        assert worker.drain_once() is False
        assert (
            users._conn.execute(
                "SELECT status FROM mobile_push_outbox"
            ).fetchone()["status"]
            == "dead"
        )
    finally:
        _close(app)


def test_unregister_then_reregister_does_not_send_stale_token(
    tmp_path, expo_token
):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        job = Job(
            id="jobunreg000001",
            session_dir=tmp_path / "sessions" / "jobunreg000001",
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
            client.delete(
                "/api/v1/devices/push",
                headers={
                    "Authorization": f"Bearer {raw}",
                    **_identity(),
                },
            ).status_code
            == 204
        )
        dead = users._conn.execute(
            "SELECT status, lease_owner FROM mobile_push_outbox"
        ).fetchone()
        assert dead["status"] == "dead"
        assert dead["lease_owner"] is None

        # Re-register with a new token; worker must not revive the dead row.
        assert (
            client.put(
                "/api/v1/devices/push",
                headers={
                    "Authorization": f"Bearer {raw}",
                    **_identity(),
                },
                json=_put_body(token=EXPO_TOKEN_B),
            ).status_code
            == 200
        )
        provider = FakeExpoPushProvider()
        worker = PushOutboxWorker(users, provider, enabled=True)
        assert worker.drain_once() is False
        assert provider.sent == []
        assert (
            users._conn.execute(
                "SELECT status FROM mobile_push_outbox"
            ).fetchone()["status"]
            == "dead"
        )
    finally:
        _close(app)


def test_token_rotation_dead_letters_pending_outbox(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        job = Job(
            id="jobrotate00001",
            session_dir=tmp_path / "sessions" / "jobrotate00001",
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
            client.put(
                "/api/v1/devices/push",
                headers={
                    "Authorization": f"Bearer {raw}",
                    **_identity(),
                },
                json=_put_body(token=EXPO_TOKEN_B),
            ).status_code
            == 200
        )
        row = users._conn.execute(
            "SELECT status, token FROM mobile_push_outbox"
        ).fetchone()
        assert row["status"] == "dead"
        assert row["token"] == EXPO_TOKEN_A

        provider = FakeExpoPushProvider()
        worker = PushOutboxWorker(users, provider, enabled=True)
        assert worker.drain_once() is False
        assert provider.sent == []
    finally:
        _close(app)


def test_schema_generation_is_five(tmp_path):
    app = _app(tmp_path, push_enabled=False)
    try:
        from swinglab.web.mobile_schema import (
            MOBILE_STATE_SCHEMA_GENERATION,
            detect_mobile_state_generation,
        )

        assert MOBILE_STATE_SCHEMA_GENERATION == 5
        assert detect_mobile_state_generation(app.state.users._conn) == 5
        assert (
            app.state.users._conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'mobile_push_outbox'"
            ).fetchone()
            is not None
        )
        assert (
            app.state.users._conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE name = 'mobile_push_environment_fences'"
            ).fetchone()
            is not None
        )
    finally:
        _close(app)


def test_outbox_global_cap_skips_and_counts_drop(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        user, raw, token = _issue(users, "cap@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users, global_cap=1, per_selector_cap=50)
        first = Job(
            id="jobcapfirst001",
            session_dir=tmp_path / "sessions" / "jobcapfirst001",
            status=DONE,
            user_id=user.id,
        )
        second = Job(
            id="jobcapsecond01",
            session_dir=tmp_path / "sessions" / "jobcapsecond01",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            first,
            kind=KIND_ANALYSIS_READY,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
        )
        assert (
            store.enqueue_job_notification(
                second,
                kind=KIND_ANALYSIS_READY,
                environment=environment,
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
        drops = users._conn.execute(
            "SELECT aggregate_drop_count FROM mobile_push_environment_fences"
            " WHERE environment = ? AND expo_project_id = ?",
            (environment, EXPO_PROJECT_ID),
        ).fetchone()[0]
        assert int(drops) >= 1
    finally:
        _close(app)
