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
    KIND_REFILM,
    PENDING_MAX_AGE_SECONDS,
    PUSH_TTL_SECONDS,
    PushDeliveryGuard,
    PushOutboxStore,
    PushOutboxWorker,
    TERMINAL_RETENTION_SECONDS,
    attach_job_push_observer,
    expo_delivery_configured,
    purge_terminal_outbox,
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


def _worker(users, provider, **kwargs):
    return PushOutboxWorker(
        users,
        provider,
        enabled=True,
        receipt_delay_seconds=0,
        **kwargs,
    )


def _drain_send_and_receipt(worker, *, send_now=None, receipt_now=None):
    assert worker.drain_once(now=send_now) is True
    assert worker.drain_once(now=receipt_now) is True


def test_terminal_job_enqueues_and_drain_delivers(tmp_path, expo_token, monkeypatch):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        provider = FakeExpoPushProvider()
        store = PushOutboxStore(users)
        worker = _worker(users, provider)
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
        awaiting = users._conn.execute(
            "SELECT status, provider_ticket_id FROM mobile_push_outbox"
        ).fetchone()
        assert awaiting["status"] == "awaiting_receipt"
        assert awaiting["provider_ticket_id"]
        assert worker.drain_once() is True
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
        worker = _worker(users, provider)
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


def test_awaiting_receipt_then_delivered(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        user, raw, token = _issue(users, "receipt@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        provider = FakeExpoPushProvider()
        worker = _worker(users, provider)
        job = Job(
            id="jobreceipt0001",
            session_dir=tmp_path / "sessions" / "jobreceipt0001",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
        )
        assert worker.drain_once(now=10.0) is True
        row = users._conn.execute(
            "SELECT status, provider_ticket_id, receipt_due_at FROM mobile_push_outbox"
        ).fetchone()
        assert row["status"] == "awaiting_receipt"
        assert row["provider_ticket_id"]
        assert worker.drain_once(now=12.0) is True
        assert (
            users._conn.execute(
                "SELECT status FROM mobile_push_outbox"
            ).fetchone()["status"]
            == "delivered"
        )
    finally:
        _close(app)


def test_device_not_registered_dead_letters_and_removes_registration(
    tmp_path, expo_token
):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        user, raw, token = _issue(users, "gone@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        provider = FakeExpoPushProvider()
        worker = _worker(users, provider)
        job = Job(
            id="jobdevicenot01",
            session_dir=tmp_path / "sessions" / "jobdevicenot01",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
        )
        assert worker.drain_once(now=10.0) is True
        ticket_id = users._conn.execute(
            "SELECT provider_ticket_id FROM mobile_push_outbox"
        ).fetchone()[0]
        provider.device_not_registered.add(ticket_id)
        assert worker.drain_once(now=12.0) is True
        assert (
            users._conn.execute(
                "SELECT status FROM mobile_push_outbox"
            ).fetchone()["status"]
            == "dead"
        )
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_registrations"
                " WHERE selector = ?",
                (token.selector,),
            ).fetchone()[0]
            == 0
        )
    finally:
        _close(app)


def test_awaiting_receipt_survives_worker_restart(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        user, raw, token = _issue(users, "restart@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        provider = FakeExpoPushProvider()
        first = _worker(users, provider)
        job = Job(
            id="jobrestart0001",
            session_dir=tmp_path / "sessions" / "jobrestart0001",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
        )
        assert first.drain_once(now=10.0) is True
        assert (
            users._conn.execute(
                "SELECT status FROM mobile_push_outbox"
            ).fetchone()["status"]
            == "awaiting_receipt"
        )
        second = _worker(users, provider)
        assert second.drain_once(now=12.0) is True
        assert (
            users._conn.execute(
                "SELECT status FROM mobile_push_outbox"
            ).fetchone()["status"]
            == "delivered"
        )
    finally:
        _close(app)


def test_practice_reminder_respects_preference_off(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        user, raw, token = _issue(users, "remind@example.com", "Phone")
        client = TestClient(app)
        assert (
            client.put(
                "/api/v1/devices/push",
                headers={"Authorization": f"Bearer {raw}", **_identity()},
                json=_put_body(practice_reminders_enabled=False),
            ).status_code
            == 200
        )
        store = PushOutboxStore(users)
        assert (
            store.enqueue_practice_reminder(
                user_id=user.id,
                selector=token.selector,
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
                source_id="due-1",
            )
            is False
        )
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
            ).fetchone()[0]
            == 0
        )
        assert (
            client.patch(
                "/api/v1/devices/push/preferences",
                headers={"Authorization": f"Bearer {raw}", **_identity()},
                json={"practice_reminders_enabled": True},
            ).status_code
            == 200
        )
        assert store.enqueue_practice_reminder(
            user_id=user.id,
            selector=token.selector,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            source_id="due-1",
        )
        row = users._conn.execute(
            "SELECT kind, source_kind FROM mobile_push_outbox"
        ).fetchone()
        assert row["kind"] == "practice_reminder"
        assert row["source_kind"] == "practice_reminder"
    finally:
        _close(app)


def test_security_notice_on_new_device_not_self(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw_a, token_a = _issue(users, "sec@example.com", "Phone A")
        client = TestClient(app)
        _register(client, raw_a)
        user = users.get(user.id)
        raw_b, token_b = users.issue_mobile_api_token(
            user.id,
            "Phone B",
            expected_auth_epoch=user.auth_epoch,
        )
        assert (
            client.put(
                "/api/v1/devices/push",
                headers={"Authorization": f"Bearer {raw_b}", **_identity()},
                json=_put_body(token=EXPO_TOKEN_B),
            ).status_code
            == 200
        )
        rows = users._conn.execute(
            "SELECT kind, selector, source_kind FROM mobile_push_outbox"
            " ORDER BY selector"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["kind"] == "security_notice"
        assert rows[0]["selector"] == token_a.selector
        assert rows[0]["selector"] != token_b.selector
        # Identical replay must not create another security notice.
        assert (
            client.put(
                "/api/v1/devices/push",
                headers={"Authorization": f"Bearer {raw_b}", **_identity()},
                json=_put_body(token=EXPO_TOKEN_B),
            ).status_code
            == 200
        )
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
                " WHERE kind = 'security_notice'"
            ).fetchone()[0]
            == 1
        )
    finally:
        _close(app)


def test_backfill_missing_for_terminal_jobs(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        user, raw, token = _issue(users, "backfill@example.com", "Phone")
        client = TestClient(app)
        store = PushOutboxStore(users)

        # Job finished before registration — must not backfill.
        early = app.state.jobs.create_session(
            source_name="early.mov", user_id=user.id
        )
        (early.session_dir / "source.mov").write_bytes(b"video")
        early.status = DONE
        app.state.jobs._save(early)
        early_updated = users._conn.execute(
            "SELECT updated_at FROM jobs WHERE id = ?", (early.id,)
        ).fetchone()["updated_at"]
        time.sleep(0.02)
        _register(client, raw)

        assert (
            store.backfill_missing_for_terminal_jobs(
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
                now=time.time(),
            )
            == 0
        )
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
            ).fetchone()[0]
            == 0
        )

        late = app.state.jobs.create_session(
            source_name="late.mov", user_id=user.id
        )
        (late.session_dir / "source.mov").write_bytes(b"video")
        late.status = DONE
        app.state.jobs._save(late)
        # Clear any observer enqueue so backfill is the only writer.
        users._conn.execute("DELETE FROM mobile_push_outbox")
        users._conn.commit()

        assert (
            store.backfill_missing_for_terminal_jobs(
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
                now=time.time(),
            )
            == 1
        )
        row = users._conn.execute(
            "SELECT kind, source_id, selector FROM mobile_push_outbox"
        ).fetchone()
        assert row["kind"] == KIND_ANALYSIS_READY
        assert row["source_id"] == late.id
        assert row["selector"] == token.selector

        # Existing unique key is a no-op.
        assert (
            store.backfill_missing_for_terminal_jobs(
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
                now=time.time(),
            )
            == 0
        )
        assert early_updated < users._conn.execute(
            "SELECT registered_at FROM mobile_push_registrations"
            " WHERE selector = ?",
            (token.selector,),
        ).fetchone()["registered_at"]
    finally:
        _close(app)


def test_pending_older_than_24h_marked_dead_on_drain(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        user, raw, token = _issue(users, "stale@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        job = Job(
            id="jobstale000001",
            session_dir=tmp_path / "sessions" / "jobstale000001",
            status=DONE,
            user_id=user.id,
        )
        now = time.time()
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=now,
        )
        old = now - PENDING_MAX_AGE_SECONDS - 10
        users._conn.execute(
            "UPDATE mobile_push_outbox SET created_at = ?, expires_at = ?",
            (old, old + 900),
        )
        users._conn.commit()
        worker = _worker(users, FakeExpoPushProvider())
        assert worker.drain_once(now=now) is False
        assert (
            users._conn.execute(
                "SELECT status FROM mobile_push_outbox"
            ).fetchone()["status"]
            == "dead"
        )
    finally:
        _close(app)


def test_purge_terminal_outbox_deletes_old_and_respects_limit(
    tmp_path, expo_token
):
    app = _app(tmp_path)
    try:
        users = app.state.users
        now = 10_000_000.0
        old = now - TERMINAL_RETENTION_SECONDS - 100
        for index in range(5):
            users._conn.execute(
                "INSERT INTO mobile_push_outbox ("
                " id, environment, expo_project_id, user_id, selector,"
                " source_kind, source_id, kind, status, token,"
                " attempts, created_at, updated_at, expires_at"
                ") VALUES (?, 'development', ?, 'u', 's',"
                " 'job', ?, 'analysis_ready', 'dead', 'tok',"
                " 0, ?, ?, ?)",
                (
                    f"purge{index}",
                    EXPO_PROJECT_ID,
                    f"job{index}",
                    old,
                    old,
                    old + 900,
                ),
            )
        users._conn.commit()
        deleted = purge_terminal_outbox(users._conn, now=now, limit=3)
        users._conn.commit()
        assert deleted == 3
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
            ).fetchone()[0]
            == 2
        )
        deleted2 = purge_terminal_outbox(users._conn, now=now, limit=1000)
        users._conn.commit()
        assert deleted2 == 2
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
            ).fetchone()[0]
            == 0
        )
    finally:
        _close(app)


def test_failed_capture_enqueues_refilm(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "refilm@example.com", "Phone")
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
        job.failure_code = "capture_no_strike"
        app.state.jobs._save(job)
        app.state.jobs._notify_completion_observers(job)
        row = users._conn.execute(
            "SELECT kind FROM mobile_push_outbox"
        ).fetchone()
        assert row["kind"] == KIND_REFILM
    finally:
        _close(app)


def test_unregister_drain_timeout_returns_202(tmp_path, expo_token):
    app = _app(tmp_path)
    try:
        users = app.state.users
        user, raw, token = _issue(users, "drain202@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        guard = app.state.push_delivery_guard
        assert isinstance(guard, PushDeliveryGuard)
        original_drain = guard.drain_selector

        def _fail_drain(selector, *, timeout_seconds):
            return False

        guard.drain_selector = _fail_drain  # type: ignore[method-assign]
        response = client.delete(
            "/api/v1/devices/push",
            headers={"Authorization": f"Bearer {raw}", **_identity()},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"
        assert body["retry_after_seconds"] == 1
        assert response.headers.get("Retry-After") == "1"
        # Registration must still exist (unregister aborted before delete).
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_registrations"
                " WHERE selector = ?",
                (token.selector,),
            ).fetchone()[0]
            == 1
        )
        guard.drain_selector = original_drain  # type: ignore[method-assign]
    finally:
        _close(app)
