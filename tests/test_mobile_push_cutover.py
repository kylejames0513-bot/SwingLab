"""Task 7 cutover slice: environment fences + close/purge operator surface."""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from swinglab.web.mobile_schema import (
    MOBILE_STATE_SCHEMA_GENERATION,
    detect_mobile_state_generation,
)
from swinglab.web.push_cutover import (
    PushFenceClosedError,
    close_fence,
    ensure_open_fence,
    fence_status,
    purge_fence,
    require_open_fence,
)
from swinglab.web.push_delivery import (
    EXPO_ACCESS_TOKEN_ENV,
    KIND_ANALYSIS_READY,
    PushOutboxStore,
)
from swinglab.web.jobs import DONE, Job
from tests.test_mobile_push import (
    EXPO_PROJECT_ID,
    EXPO_TOKEN_A,
    _app,
    _close,
    _identity,
    _issue,
    _put_body,
)


def _request_hash(*, environment: str, project: str, command: str, operation_id: str) -> str:
    material = json.dumps(
        {
            "environment": environment,
            "expo_project_id": project,
            "command": command,
            "operation_id": operation_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _register(client, raw: str) -> None:
    assert (
        client.put(
            "/api/v1/devices/push",
            headers={"Authorization": f"Bearer {raw}", **_identity()},
            json=_put_body(),
        ).status_code
        == 200
    )


def test_schema_generation_is_five(tmp_path):
    app = _app(tmp_path, push_enabled=False)
    try:
        assert MOBILE_STATE_SCHEMA_GENERATION == 5
        assert detect_mobile_state_generation(app.state.users._conn) == 5
        names = {
            str(row[0])
            for row in app.state.users._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "mobile_push_environment_fences" in names
        assert "mobile_push_cutover_operations" in names
    finally:
        _close(app)


def test_ensure_open_creates_revision_one_and_second_call_is_noop(tmp_path):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        now = 1_700_000_000.0
        first = ensure_open_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=now,
        )
        assert first["state"] == "open"
        assert first["activation_revision"] == 1
        assert first["cutoff_revision"] == 1
        second = ensure_open_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=now + 10,
        )
        assert second["activation_revision"] == 1
        assert second["cutoff_revision"] == 1
        count = users._conn.execute(
            "SELECT COUNT(*) FROM mobile_push_environment_fences"
        ).fetchone()[0]
        assert count == 1
    finally:
        _close(app)


def test_closed_fence_blocks_ensure_register_and_enqueue(tmp_path, monkeypatch):
    monkeypatch.setenv(EXPO_ACCESS_TOKEN_ENV, "test-expo-access-token")
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        ensure_open_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=100.0,
        )
        operation_id = str(uuid.uuid4())
        close_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            operation_id=operation_id,
            request_hash=_request_hash(
                environment=environment,
                project=EXPO_PROJECT_ID,
                command="close",
                operation_id=operation_id,
            ),
            apply=True,
            skew_seconds=60,
            now=200.0,
        )
        with pytest.raises(PushFenceClosedError):
            ensure_open_fence(
                users,
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
                now=300.0,
            )
        with pytest.raises(PushFenceClosedError):
            require_open_fence(
                users._conn,
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
            )

        user, raw, _token = _issue(users, "closed@example.com", "Phone")
        client = TestClient(app)
        put = client.put(
            "/api/v1/devices/push",
            headers={"Authorization": f"Bearer {raw}", **_identity()},
            json=_put_body(),
        )
        assert put.status_code in (400, 409, 503)

        store = PushOutboxStore(users)
        job = Job(
            id="jobdeadbeef02",
            session_dir=tmp_path / "sessions" / "jobdeadbeef02",
            status=DONE,
            user_id=user.id,
        )
        assert (
            store.enqueue_job_notification(
                job,
                kind=KIND_ANALYSIS_READY,
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
            )
            is False
        )
    finally:
        _close(app)


def test_close_apply_terminalizes_pending_outbox_and_status_is_aggregate_only(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(EXPO_ACCESS_TOKEN_ENV, "test-expo-access-token")
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        ensure_open_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=10.0,
        )
        user, raw, _token = _issue(users, "golfer@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        job = Job(
            id="jobdeadbeef03",
            session_dir=tmp_path / "sessions" / "jobdeadbeef03",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
        )
        pending = users._conn.execute(
            "SELECT COUNT(*) FROM mobile_push_outbox WHERE status = 'pending'"
        ).fetchone()[0]
        assert pending == 1

        operation_id = str(uuid.uuid4())
        result = close_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            operation_id=operation_id,
            request_hash=_request_hash(
                environment=environment,
                project=EXPO_PROJECT_ID,
                command="close",
                operation_id=operation_id,
            ),
            apply=True,
            skew_seconds=60,
            now=50.0,
        )
        assert result["state"] == "closed"
        dead = users._conn.execute(
            "SELECT COUNT(*) FROM mobile_push_outbox WHERE status = 'dead'"
        ).fetchone()[0]
        assert dead == 1
        status = fence_status(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
        )
        assert status["state"] == "closed"
        assert status["registration_count"] == 1
        assert status["outbox_status_counts"]["dead"] == 1
        assert "token" not in json.dumps(status)
        assert "selector" not in json.dumps(status)
        assert "user_id" not in json.dumps(status)
    finally:
        _close(app)


def test_close_operation_replay_idempotent_and_conflicting_hash_fails(tmp_path):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        ensure_open_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=1.0,
        )
        operation_id = str(uuid.uuid4())
        request_hash = _request_hash(
            environment=environment,
            project=EXPO_PROJECT_ID,
            command="close",
            operation_id=operation_id,
        )
        first = close_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            operation_id=operation_id,
            request_hash=request_hash,
            apply=True,
            skew_seconds=60,
            now=2.0,
        )
        replay = close_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            operation_id=operation_id,
            request_hash=request_hash,
            apply=True,
            skew_seconds=60,
            now=3.0,
        )
        assert first["state"] == replay["state"] == "closed"
        with pytest.raises(Exception, match="request.hash|conflict|hash"):
            close_fence(
                users,
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
                operation_id=operation_id,
                request_hash="0" * 64,
                apply=True,
                skew_seconds=60,
                now=4.0,
            )
    finally:
        _close(app)


def test_purge_refuses_before_safe_after_then_deletes_and_keeps_closed(tmp_path):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        ensure_open_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=10.0,
        )
        user, raw, _token = _issue(users, "purge@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        close_id = str(uuid.uuid4())
        close_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            operation_id=close_id,
            request_hash=_request_hash(
                environment=environment,
                project=EXPO_PROJECT_ID,
                command="close",
                operation_id=close_id,
            ),
            apply=True,
            skew_seconds=60,
            now=100.0,
        )
        fence = users._conn.execute(
            "SELECT provider_safe_after, closed_at, frozen_cutoff_skew_seconds"
            " FROM mobile_push_environment_fences"
            " WHERE environment = ? AND expo_project_id = ?",
            (environment, EXPO_PROJECT_ID),
        ).fetchone()
        safe_after = float(fence["provider_safe_after"])
        # No provider I/O occurred: close time is already safe.
        assert safe_after == float(fence["closed_at"])
        assert float(fence["frozen_cutoff_skew_seconds"]) == 60.0

        purge_id = str(uuid.uuid4())
        with pytest.raises(Exception, match="safe_after|provider.safe|refuse"):
            purge_fence(
                users,
                environment=environment,
                expo_project_id=EXPO_PROJECT_ID,
                operation_id=purge_id,
                request_hash=_request_hash(
                    environment=environment,
                    project=EXPO_PROJECT_ID,
                    command="purge",
                    operation_id=purge_id,
                ),
                apply=True,
                now=safe_after - 1,
            )
        purged = purge_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            operation_id=purge_id,
            request_hash=_request_hash(
                environment=environment,
                project=EXPO_PROJECT_ID,
                command="purge",
                operation_id=purge_id,
            ),
            apply=True,
            now=safe_after,
        )
        assert purged["state"] == "closed"
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_registrations"
                " WHERE environment = ? AND expo_project_id = ?",
                (environment, EXPO_PROJECT_ID),
            ).fetchone()[0]
            == 0
        )
        assert (
            users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
                " WHERE environment = ? AND expo_project_id = ?",
                (environment, EXPO_PROJECT_ID),
            ).fetchone()[0]
            == 0
        )
        assert (
            users._conn.execute(
                "SELECT state FROM mobile_push_environment_fences"
                " WHERE environment = ? AND expo_project_id = ?",
                (environment, EXPO_PROJECT_ID),
            ).fetchone()[0]
            == "closed"
        )
    finally:
        _close(app)


def test_cli_status_json_smoke(tmp_path, monkeypatch, capsys):
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        ensure_open_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=time.time(),
        )
    finally:
        _close(app)

    from swinglab.cli import main

    monkeypatch.setenv("CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", environment)
    monkeypatch.setenv("CADDIEINSIGHT_EXPO_PROJECT_ID", EXPO_PROJECT_ID)
    code = main(
        [
            "mobile-push-cutover",
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--environment",
            environment,
            "--expo-project-id",
            EXPO_PROJECT_ID,
            "status",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "open"
    assert payload["activation_revision"] == 1


def test_cli_rejects_environment_mismatch(tmp_path, monkeypatch, capsys):
    app = _app(tmp_path)
    environment = app.state.mobile_deployment_environment
    _close(app)

    from swinglab.cli import main

    monkeypatch.setenv("CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", environment)
    monkeypatch.setenv("CADDIEINSIGHT_EXPO_PROJECT_ID", EXPO_PROJECT_ID)
    wrong = "production" if environment != "production" else "staging"
    code = main(
        [
            "mobile-push-cutover",
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--environment",
            wrong,
            "--expo-project-id",
            EXPO_PROJECT_ID,
            "status",
            "--json",
        ]
    )
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "environment" in err or "mismatch" in err or "match" in err


def test_worker_stamps_provider_clocks_and_close_uses_ttl_skew(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(EXPO_ACCESS_TOKEN_ENV, "test-expo-access-token")
    app = _app(tmp_path)
    try:
        users = app.state.users
        environment = app.state.mobile_deployment_environment
        ensure_open_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            now=1.0,
        )
        user, raw, token = _issue(users, "stamp@example.com", "Phone")
        client = TestClient(app)
        _register(client, raw)
        store = PushOutboxStore(users)
        job = Job(
            id="jobstamp000001",
            session_dir=tmp_path / "sessions" / "jobstamp000001",
            status=DONE,
            user_id=user.id,
        )
        assert store.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
        )
        from swinglab.web.push_delivery import FakeExpoPushProvider, PushOutboxWorker

        worker = PushOutboxWorker(
            users, FakeExpoPushProvider(), enabled=True, lease_seconds=30,
            receipt_delay_seconds=0,
        )
        assert worker.drain_once(now=50.0) is True
        fence = users._conn.execute(
            "SELECT last_provider_started_at, last_provider_accepted_at,"
            " provider_may_accept_until FROM mobile_push_environment_fences"
            " WHERE environment = ? AND expo_project_id = ?",
            (environment, EXPO_PROJECT_ID),
        ).fetchone()
        assert float(fence["last_provider_started_at"]) == 50.0
        assert float(fence["provider_may_accept_until"]) == 80.0
        assert fence["last_provider_accepted_at"] is not None
        status = users._conn.execute(
            "SELECT status FROM mobile_push_outbox"
        ).fetchone()["status"]
        assert status == "awaiting_receipt"

        close_id = str(uuid.uuid4())
        closed = close_fence(
            users,
            environment=environment,
            expo_project_id=EXPO_PROJECT_ID,
            operation_id=close_id,
            request_hash=_request_hash(
                environment=environment,
                project=EXPO_PROJECT_ID,
                command="close",
                operation_id=close_id,
            ),
            apply=True,
            skew_seconds=60,
            now=100.0,
        )
        after = users._conn.execute(
            "SELECT provider_safe_after, last_provider_accepted_at,"
            " provider_may_accept_until FROM mobile_push_environment_fences"
            " WHERE environment = ? AND expo_project_id = ?",
            (environment, EXPO_PROJECT_ID),
        ).fetchone()
        expected = max(
            float(after["last_provider_accepted_at"]),
            float(after["provider_may_accept_until"]),
        ) + 900.0 + 60.0
        assert float(after["provider_safe_after"]) == expected
        assert closed["provider_safe_after"] == expected
    finally:
        _close(app)
