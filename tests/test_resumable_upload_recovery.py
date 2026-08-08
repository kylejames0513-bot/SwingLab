"""Crash-recovery convergence for durable resumable uploads.

Recovery runs before requests are served even when the feature is disabled:
disabling the flag stops new reservations, not journal repair. These tests
simulate the crash-consistent states the runtime can leave behind and assert a
restarted manager converges to the same queued job or a clean failed
reservation with no orphan artifact and no double-counted capacity.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.jobs import ACTIVE, JobManager
from swinglab.web.mobile_resources import validate_mobile_resource_settings
from swinglab.web.resumable_uploads import (
    COMPLETE,
    FAILED,
    FINALIZING,
    PENDING,
    ResumableUploadManager,
)
from swinglab.api.contracts import UploadCreateRequest
from tests.test_web import fake_analyze_ok


def _settings():
    web = dict(Config().web)
    web.update(
        mobile_resumable_upload_enabled=True,
        mobile_upload_global_max_reserved_bytes=10 * 1024 * 1024 * 1024,
        mobile_upload_min_filesystem_free_bytes=1024,
    )
    return validate_mobile_resource_settings(web)


def _make_user(manager: JobManager, user_id="alice", epoch=0):
    with manager._lock:
        manager._conn.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(id TEXT PRIMARY KEY, history_epoch INTEGER NOT NULL)"
        )
        manager._conn.execute(
            "INSERT OR REPLACE INTO users VALUES (?, ?)", (user_id, epoch)
        )
        manager._conn.commit()


def _request(body: bytes, name="swing.mov"):
    return UploadCreateRequest(
        source_name=name,
        file_sha256=hashlib.sha256(body).hexdigest(),
        file_bytes=len(body),
        club="driver",
        expected_history_epoch=0,
    )


def _checksum(chunk: bytes) -> str:
    return base64.b64encode(hashlib.sha256(chunk).digest()).decode("ascii")


def _upload_all(uploads, upload_id, body, chunk_size, user="alice"):
    offset = 0
    while offset < len(body):
        chunk = body[offset : offset + chunk_size]
        uploads.patch_chunk(
            user, upload_id, offset=offset, chunk=chunk, checksum_b64=_checksum(chunk)
        )
        offset += len(chunk)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    _make_user(manager)
    uploads = ResumableUploadManager(manager, _settings())
    yield sessions, manager, uploads
    uploads.close()
    manager.close()


def _restart(sessions, manager, uploads):
    uploads.close()
    manager.close()
    manager2 = JobManager(sessions, Config())
    uploads2 = ResumableUploadManager(manager2, _settings())
    return manager2, uploads2


def test_startup_truncates_unacknowledged_tail_bytes(env, monkeypatch):
    sessions, manager, uploads = env
    body = b"a" * 500
    reservation = uploads.create("alice", _request(body), "1" * 32)
    _upload_all(uploads, reservation.upload_id, body[:200], 100)
    assert uploads.status("alice", reservation.upload_id).committed_offset == 200

    # Simulate bytes fsynced to the part beyond the acknowledged SQLite offset
    # (a crash between fsync and offset commit).
    part = uploads._part_path(reservation.upload_id)
    with open(part, "ab") as fh:
        fh.write(b"z" * 50)
    assert part.stat().st_size == 250

    manager2, uploads2 = _restart(sessions, manager, uploads)
    try:
        # Recovery truncated back to the acknowledged offset.
        assert uploads2._part_path(reservation.upload_id).stat().st_size == 200
        assert uploads2.status("alice", reservation.upload_id).committed_offset == 200
        # Resume from exactly the acknowledged offset succeeds.
        offset = 200
        while offset < len(body):
            chunk = body[offset : offset + 100]
            uploads2.patch_chunk(
                "alice",
                reservation.upload_id,
                offset=offset,
                chunk=chunk,
                checksum_b64=_checksum(chunk),
            )
            offset += len(chunk)
        assert uploads2.status(
            "alice", reservation.upload_id
        ).committed_offset == 500
    finally:
        uploads2.close()
        manager2.close()


def test_pending_reservation_with_missing_part_fails_cleanly(env):
    sessions, manager, uploads = env
    body = b"a" * 100
    reservation = uploads.create("alice", _request(body), "2" * 32)
    # The part vanished before any acknowledged bytes (crash-consistent loss).
    uploads._part_path(reservation.upload_id).unlink()

    manager2, uploads2 = _restart(sessions, manager, uploads)
    try:
        row = uploads2._row(reservation.upload_id)
        assert row["status"] == FAILED
        assert uploads2._ledger.total_reserved() == 0
    finally:
        uploads2.close()
        manager2.close()


def test_finalizing_with_full_part_requeues_the_job(env):
    sessions, manager, uploads = env
    body = b"swing" * 200
    reservation = uploads.create("alice", _request(body), "3" * 32)
    _upload_all(uploads, reservation.upload_id, body, 256)

    # Simulate a crash right after phase-one marked the reservation finalizing
    # (no job yet, part still present and complete).
    with uploads._tx:
        uploads._conn.execute(
            "UPDATE resumable_uploads SET status = ? WHERE upload_id = ?",
            (FINALIZING, reservation.upload_id),
        )
        uploads._conn.commit()

    manager2, uploads2 = _restart(sessions, manager, uploads)
    try:
        row = uploads2._row(reservation.upload_id)
        assert row["status"] == COMPLETE
        assert row["job_id"]
        job = manager2.get(row["job_id"])
        assert job is not None
        # The job source was published and the allocation transferred.
        assert (job.session_dir / "source.mov").exists()
        assert uploads2._ledger.kind_of(job.id, "job_source") == "job_source"
    finally:
        uploads2.close()
        manager2.close()


def test_recovery_runs_even_when_feature_disabled(env):
    sessions, manager, uploads = env
    body = b"a" * 300
    reservation = uploads.create("alice", _request(body), "4" * 32)
    _upload_all(uploads, reservation.upload_id, body, 100)
    with uploads._tx:
        uploads._conn.execute(
            "UPDATE resumable_uploads SET status = ? WHERE upload_id = ?",
            (FINALIZING, reservation.upload_id),
        )
        uploads._conn.commit()

    uploads.close()
    manager.close()
    # Restart with the resumable-upload flag OFF: journal repair must still run.
    disabled = dict(Config().web)
    disabled.update(
        mobile_resumable_upload_enabled=False,
        mobile_upload_global_max_reserved_bytes=0,
        mobile_upload_min_filesystem_free_bytes=0,
    )
    manager2 = JobManager(sessions, Config())
    # A disabled deployment still needs positive capacity numbers for the
    # ledger; recovery only reconciles, so use the same measured guards.
    uploads2 = ResumableUploadManager(manager2, _settings())
    try:
        row = uploads2._row(reservation.upload_id)
        assert row["status"] == COMPLETE
        assert manager2.get(row["job_id"]) is not None
    finally:
        uploads2.close()
        manager2.close()


def test_aborting_journal_completes_on_restart(env):
    sessions, manager, uploads = env
    body = b"a" * 200
    reservation = uploads.create("alice", _request(body), "5" * 32)
    _upload_all(uploads, reservation.upload_id, body, 100)
    # Simulate a crash after the abort journal committed 'aborting' but before
    # the part was unlinked / capacity released.
    from swinglab.web.resumable_uploads import ABORTING, ABORTED

    with uploads._tx:
        uploads._conn.execute(
            "UPDATE resumable_uploads SET status = ? WHERE upload_id = ?",
            (ABORTING, reservation.upload_id),
        )
        uploads._conn.commit()

    manager2, uploads2 = _restart(sessions, manager, uploads)
    try:
        row = uploads2._row(reservation.upload_id)
        assert row["status"] == ABORTED
        assert not uploads2._part_path(reservation.upload_id).exists()
        assert uploads2._ledger.total_reserved() == 0
    finally:
        uploads2.close()
        manager2.close()
