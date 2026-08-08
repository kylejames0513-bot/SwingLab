"""Durable resumable mobile uploads.

This suite starts with the server-owned upload/retry policy bounds (validated
strictly at app composition time) and grows into the create/chunk/complete/abort
contract. Capacity guards ship at 0 while the feature is off and must be
measured, strictly-positive values before ``mobile_resumable_upload_enabled``
turns on.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.jobs import ACTIVE, JobManager, QUEUED
from swinglab.web.mobile_resources import validate_mobile_resource_settings
from swinglab.web.resumable_uploads import (
    ResumableUploadManager,
    UploadCapacityError,
    UploadChecksumMismatch,
    UploadChunkTooLarge,
    UploadComparisonConflict,
    UploadHistoryConflict,
    UploadIdempotencyConflict,
    UploadNotFound,
    UploadOffsetMismatch,
    UploadStateConflict,
)
from swinglab.api.contracts import UploadCreateRequest
from tests.test_web import fake_analyze_ok


def _web(**overrides):
    web = dict(Config().web)
    web.update(overrides)
    return web


def _enabled_settings(**overrides):
    base = dict(
        mobile_resumable_upload_enabled=True,
        mobile_upload_global_max_reserved_bytes=10 * 1024 * 1024 * 1024,
        mobile_upload_min_filesystem_free_bytes=1024,
    )
    base.update(overrides)
    return validate_mobile_resource_settings(_web(**base))


def _make_user(manager: JobManager, user_id: str = "alice", epoch: int = 0) -> None:
    with manager._lock:
        manager._conn.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(id TEXT PRIMARY KEY, history_epoch INTEGER NOT NULL)"
        )
        manager._conn.execute(
            "INSERT OR REPLACE INTO users VALUES (?, ?)", (user_id, epoch)
        )
        manager._conn.commit()


@pytest.fixture
def upload_env(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    manager = JobManager(tmp_path / "sessions", Config())
    _make_user(manager)
    settings = _enabled_settings()
    uploads = ResumableUploadManager(manager, settings)
    try:
        yield manager, uploads
    finally:
        uploads.close()
        manager.close()


def _request(body: bytes, *, source_name="swing.mov", epoch=0, comparison=None):
    return UploadCreateRequest(
        source_name=source_name,
        file_sha256=hashlib.sha256(body).hexdigest(),
        file_bytes=len(body),
        club="driver",
        hand="right",
        angle="face-on",
        expected_history_epoch=epoch,
        comparison=comparison,
    )


def _checksum(chunk: bytes) -> str:
    return base64.b64encode(hashlib.sha256(chunk).digest()).decode("ascii")


def _upload_all(uploads, user_id, upload_id, body, chunk_size):
    offset = 0
    while offset < len(body):
        chunk = body[offset : offset + chunk_size]
        uploads.patch_chunk(
            user_id,
            upload_id,
            offset=offset,
            chunk=chunk,
            checksum_b64=_checksum(chunk),
        )
        offset += len(chunk)


def test_shipped_defaults_are_off_with_zero_capacity_guards() -> None:
    settings = validate_mobile_resource_settings(_web())
    assert settings.resumable_upload_enabled is False
    assert settings.analysis_retry_window_seconds == 86400
    assert settings.analysis_retry_max_attempts == 2
    assert settings.upload_global_max_reserved_bytes == 0
    assert settings.upload_min_filesystem_free_bytes == 0


def test_enabling_upload_requires_positive_capacity_guards() -> None:
    # Global reserved bytes still 0 -> rejected.
    with pytest.raises(ValueError):
        validate_mobile_resource_settings(
            _web(
                mobile_resumable_upload_enabled=True,
                mobile_upload_min_filesystem_free_bytes=1024,
            )
        )
    # Min filesystem free still 0 -> rejected.
    with pytest.raises(ValueError):
        validate_mobile_resource_settings(
            _web(
                mobile_resumable_upload_enabled=True,
                mobile_upload_global_max_reserved_bytes=1024,
            )
        )


def test_enabling_upload_with_positive_guards_succeeds() -> None:
    settings = validate_mobile_resource_settings(
        _web(
            mobile_resumable_upload_enabled=True,
            mobile_upload_global_max_reserved_bytes=10 * 1024 * 1024 * 1024,
            mobile_upload_min_filesystem_free_bytes=1 * 1024 * 1024 * 1024,
        )
    )
    assert settings.resumable_upload_enabled is True
    assert settings.upload_global_max_reserved_bytes == 10 * 1024 * 1024 * 1024


@pytest.mark.parametrize(
    "overrides",
    [
        {"mobile_analysis_retry_window_seconds": 0},
        {"mobile_analysis_retry_window_seconds": -1},
        {"mobile_analysis_retry_max_attempts": 0},
        {"mobile_analysis_retry_max_attempts": 11},
        {"mobile_upload_global_max_reserved_bytes": -1},
        {"mobile_upload_min_filesystem_free_bytes": (1 << 43) + 1},
        {"mobile_analysis_retry_max_attempts": True},
    ],
)
def test_out_of_range_or_wrong_type_values_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        validate_mobile_resource_settings(_web(**overrides))


def test_shipped_config_yaml_composes() -> None:
    cfg = Config.load("config.yaml")
    settings = validate_mobile_resource_settings(cfg.web)
    assert settings.resumable_upload_enabled is False
    assert settings.analysis_retry_max_attempts == 2


# --- reservation lifecycle -------------------------------------------------


def test_create_reserves_capacity_and_starts_at_zero_offset(upload_env):
    manager, uploads = upload_env
    body = b"x" * 4096
    reservation = uploads.create("alice", _request(body), "a" * 32)
    assert reservation.status == "pending"
    assert reservation.committed_offset == 0
    assert reservation.file_bytes == 4096
    assert uploads._ledger.total_reserved() == 4096


def test_create_replay_same_key_returns_same_reservation(upload_env):
    manager, uploads = upload_env
    body = b"y" * 100
    first = uploads.create("alice", _request(body), "b" * 32)
    second = uploads.create("alice", _request(body), "b" * 32)
    assert first.upload_id == second.upload_id
    assert uploads._ledger.total_reserved() == 100  # not double counted


def test_create_conflicting_key_is_rejected(upload_env):
    manager, uploads = upload_env
    uploads.create("alice", _request(b"a" * 10), "c" * 32)
    with pytest.raises(UploadIdempotencyConflict):
        uploads.create("alice", _request(b"b" * 20), "c" * 32)


def test_active_upload_cap_enforced(upload_env):
    manager, uploads = upload_env
    uploads.create("alice", _request(b"a" * 10, source_name="one.mov"), "1" * 32)
    uploads.create("alice", _request(b"b" * 10, source_name="two.mov"), "2" * 32)
    with pytest.raises(UploadStateConflict):
        uploads.create("alice", _request(b"c" * 10, source_name="tre.mov"), "3" * 32)


def test_stale_history_epoch_conflicts(upload_env):
    manager, uploads = upload_env
    with pytest.raises(UploadHistoryConflict):
        uploads.create("alice", _request(b"a" * 10, epoch=5), "d" * 32)


def test_chunk_offset_mismatch(upload_env):
    manager, uploads = upload_env
    body = b"z" * 100
    reservation = uploads.create("alice", _request(body), "e" * 32)
    with pytest.raises(UploadOffsetMismatch) as info:
        uploads.patch_chunk(
            "alice",
            reservation.upload_id,
            offset=10,
            chunk=body[:10],
            checksum_b64=_checksum(body[:10]),
        )
    assert info.value.acknowledged_offset == 0


def test_chunk_checksum_mismatch_does_not_advance(upload_env):
    manager, uploads = upload_env
    body = b"z" * 100
    reservation = uploads.create("alice", _request(body), "f" * 32)
    with pytest.raises(UploadChecksumMismatch):
        uploads.patch_chunk(
            "alice",
            reservation.upload_id,
            offset=0,
            chunk=body[:10],
            checksum_b64=_checksum(b"different"),
        )
    assert uploads.status("alice", reservation.upload_id).committed_offset == 0


def test_oversized_chunk_rejected(upload_env):
    manager, uploads = upload_env
    body = b"z" * 100
    reservation = uploads.create("alice", _request(body), "g" * 32)
    huge = b"q" * (uploads.settings.upload_chunk_bytes + 1)
    with pytest.raises(UploadChunkTooLarge):
        uploads.patch_chunk(
            "alice",
            reservation.upload_id,
            offset=0,
            chunk=huge,
            checksum_b64=_checksum(huge),
        )


def test_chunk_beyond_declared_size_rejected(upload_env):
    manager, uploads = upload_env
    body = b"z" * 10
    reservation = uploads.create("alice", _request(body), "h" * 32)
    over = b"z" * 20
    with pytest.raises(UploadChunkTooLarge):
        uploads.patch_chunk(
            "alice",
            reservation.upload_id,
            offset=0,
            chunk=over,
            checksum_b64=_checksum(over),
        )


def test_full_upload_then_complete_creates_one_queued_job(upload_env):
    manager, uploads = upload_env
    body = b"swingbytes" * 500
    reservation = uploads.create("alice", _request(body), "i" * 32)
    _upload_all(uploads, "alice", reservation.upload_id, body, 1024)
    assert uploads.status("alice", reservation.upload_id).committed_offset == len(body)
    job, replayed = uploads.complete_mobile_upload("alice", reservation.upload_id)
    assert replayed is False
    assert job.status in ACTIVE
    # The source moved into the job directory as source.<suffix>.
    assert (job.session_dir / "source.mov").exists()
    # Capacity transferred from upload part to the job source (no gap).
    assert uploads._ledger.kind_of(job.id, "job_source") == "job_source"
    assert uploads._ledger.kind_of(reservation.upload_id, "upload_part") is None


def test_complete_is_idempotent_replay(upload_env):
    manager, uploads = upload_env
    body = b"a" * 2048
    reservation = uploads.create("alice", _request(body), "j" * 32)
    _upload_all(uploads, "alice", reservation.upload_id, body, 512)
    job1, replayed1 = uploads.complete_mobile_upload("alice", reservation.upload_id)
    job2, replayed2 = uploads.complete_mobile_upload("alice", reservation.upload_id)
    assert replayed1 is False
    assert replayed2 is True
    assert job1.id == job2.id


def test_complete_with_bad_full_digest_fails_no_job(upload_env):
    manager, uploads = upload_env
    body = b"a" * 100
    request = _request(body)
    # Corrupt the declared digest so the assembled file cannot match.
    request = UploadCreateRequest(
        source_name=request.source_name,
        file_sha256="0" * 64,
        file_bytes=len(body),
        club="driver",
        expected_history_epoch=0,
    )
    reservation = uploads.create("alice", request, "k" * 32)
    _upload_all(uploads, "alice", reservation.upload_id, body, 50)
    with pytest.raises(UploadChecksumMismatch):
        uploads.complete_mobile_upload("alice", reservation.upload_id)
    assert manager.list_recent(user_id="alice") == []
    assert uploads._ledger.total_reserved() == 0


def test_cross_account_reservation_is_not_found(upload_env):
    manager, uploads = upload_env
    _make_user(manager, "mallory", 0)
    reservation = uploads.create("alice", _request(b"a" * 10), "l" * 32)
    with pytest.raises(UploadNotFound):
        uploads.status("mallory", reservation.upload_id)


def test_capacity_overcommit_returns_507(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    manager = JobManager(tmp_path / "sessions", Config())
    _make_user(manager)
    settings = _enabled_settings(mobile_upload_global_max_reserved_bytes=1000)
    uploads = ResumableUploadManager(manager, settings)
    try:
        uploads.create("alice", _request(b"a" * 800, source_name="a.mov"), "m" * 32)
        with pytest.raises(UploadCapacityError):
            uploads.create("alice", _request(b"b" * 800, source_name="b.mov"), "n" * 32)
    finally:
        uploads.close()
        manager.close()


# --- abort -----------------------------------------------------------------


def test_abort_releases_capacity_and_replays_204(upload_env):
    manager, uploads = upload_env
    body = b"a" * 300
    reservation = uploads.create("alice", _request(body), "o" * 32)
    _upload_all(uploads, "alice", reservation.upload_id, body, 100)
    uploads.abort("alice", reservation.upload_id, "1" * 32)
    assert uploads._ledger.total_reserved() == 0
    assert not (uploads.uploads_dir / f"{reservation.upload_id}.part").exists()
    # Exact same-key replay is a no-op 204 even after the part/file is gone.
    uploads.abort("alice", reservation.upload_id, "1" * 32)


def test_abort_conflicting_key_after_tombstone(upload_env):
    manager, uploads = upload_env
    reservation = uploads.create("alice", _request(b"a" * 10), "p" * 32)
    uploads.abort("alice", reservation.upload_id, "1" * 32)
    with pytest.raises(UploadIdempotencyConflict):
        uploads.abort("alice", reservation.upload_id, "9" * 32)


def test_abort_completed_upload_conflicts(upload_env):
    manager, uploads = upload_env
    body = b"a" * 256
    reservation = uploads.create("alice", _request(body), "q" * 32)
    _upload_all(uploads, "alice", reservation.upload_id, body, 128)
    uploads.complete_mobile_upload("alice", reservation.upload_id)
    with pytest.raises(UploadStateConflict):
        uploads.abort("alice", reservation.upload_id, "1" * 32)


# --- comparison ------------------------------------------------------------


class _FakeTarget:
    def __init__(self):
        self.baseline_session_id = "base-1"
        self.target_fingerprint = "f" * 64
        self.drill_id = "drill-1"
        self.club = "driver"
        self.hand = "right"
        self.angle = "face-on"


def test_matched_comparison_requires_current_assignment(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    manager = JobManager(tmp_path / "sessions", Config())
    _make_user(manager)
    target = _FakeTarget()
    uploads = ResumableUploadManager(
        manager,
        _enabled_settings(),
        comparison_resolver=lambda **_: target,
    )
    try:
        good = {
            "mode": "matched",
            "baseline_session_id": "base-1",
            "target_fingerprint": "f" * 64,
            "drill_id": "drill-1",
        }
        reservation = uploads.create(
            "alice", _request(b"a" * 10, comparison=good), "r" * 32
        )
        assert reservation.comparison_mode == "matched"
        stale = {
            "mode": "matched",
            "baseline_session_id": "base-1",
            "target_fingerprint": "e" * 64,
            "drill_id": "drill-1",
        }
        with pytest.raises(UploadComparisonConflict):
            uploads.create("alice", _request(b"b" * 10, comparison=stale), "s" * 32)
    finally:
        uploads.close()
        manager.close()
