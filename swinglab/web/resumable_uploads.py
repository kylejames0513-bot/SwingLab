"""Durable, resumable mobile uploads with atomic job completion.

The reservation row in ``mobile_uploads`` (next to the session tree, in the same
``swinglab.db``) is the source of truth for an in-flight upload; the part file
under ``sessions_dir/.uploads/<upload_id>.part`` is its bytes. SQLite always
holds the *acknowledged* offset: bytes are fsynced to the part file first, then
the offset is committed, so a crash can only ever leave extra unacknowledged
bytes (truncated on the next touch), never a phantom-acknowledged short file.

Serialization under the preserved one-replica topology is an in-process
per-upload keyed lock; capacity admission is delegated to the durable
:class:`StorageCapacityLedger` (which holds the cross-process maintenance lock).
Completion is a recoverable journal: mark ``finalizing`` -> create the job
directory and atomically move the part to ``source.<suffix>`` -> mark the job
queued and the reservation ``complete`` -> submit. Abort is a separate journal
with its own idempotency key and a seven-day 204 receipt.

Only the happy path plus idempotent replay is exposed to callers; the internal
``finalizing`` row is never observable as a job.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..api.contracts import UploadCreateRequest
from .jobs import ACTIVE, JobManager, Job
from .mobile_schema import MobileStateDomain, VersionedHMAC
from .storage_capacity import InsufficientStorageError, StorageCapacityLedger

PENDING = "pending"
FINALIZING = "finalizing"
COMPLETE = "complete"
ABORTING = "aborting"
ABORTED = "aborted"
FAILED = "failed"
REPAIR_REQUIRED = "repair_required"
SOURCE_UNAVAILABLE = "source_unavailable_after_restore"

_ACTIVE_STATUSES = (PENDING, FINALIZING)
_ALLOWED_SUFFIXES = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4"})
_ABORT_RECEIPT_TTL_SECONDS = 7 * 24 * 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS mobile_uploads (
    upload_id           TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    status              TEXT NOT NULL,
    source_name         TEXT NOT NULL,
    suffix              TEXT NOT NULL,
    file_bytes          INTEGER NOT NULL,
    file_sha256         TEXT NOT NULL,
    club                TEXT NOT NULL,
    hand                TEXT NOT NULL,
    angle               TEXT NOT NULL,
    level               TEXT,
    history_epoch       INTEGER NOT NULL,
    idempotency_key_id  TEXT NOT NULL,
    idempotency_hmac    TEXT NOT NULL,
    request_hash        TEXT NOT NULL,
    committed_offset    INTEGER NOT NULL DEFAULT 0,
    comparison_mode     TEXT,
    baseline_session_id TEXT,
    target_fingerprint  TEXT,
    drill_id            TEXT,
    job_id              TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    expires_at          REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS mobile_uploads_idem
    ON mobile_uploads(user_id, idempotency_hmac);
CREATE INDEX IF NOT EXISTS mobile_uploads_user_status
    ON mobile_uploads(user_id, status);

CREATE TABLE IF NOT EXISTS mobile_upload_abort_receipts (
    upload_id           TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    idempotency_key_id  TEXT NOT NULL,
    idempotency_hmac    TEXT NOT NULL,
    request_hash        TEXT NOT NULL,
    created_at          REAL NOT NULL,
    expires_at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mobile_upload_abort_receipts_expiry
    ON mobile_upload_abort_receipts(expires_at);
"""


class UploadError(RuntimeError):
    """Base for every resumable-upload failure surfaced to the route layer."""


class UploadNotFound(UploadError):
    """No such owned reservation (missing or cross-account)."""


class UploadIdempotencyConflict(UploadError):
    """An idempotency key was reused with a different request."""


class UploadStateConflict(UploadError):
    """The reservation is in a state incompatible with the request."""


class UploadOffsetMismatch(UploadError):
    def __init__(self, message: str, *, acknowledged_offset: int) -> None:
        super().__init__(message)
        self.acknowledged_offset = acknowledged_offset


class UploadChunkTooLarge(UploadError):
    """A chunk exceeded the configured chunk size or the declared file size."""


class UploadChecksumMismatch(UploadError):
    """A chunk or full-file digest did not match."""


class UploadExpired(UploadError):
    """The reservation's TTL elapsed."""


class UploadBusy(UploadError):
    """Another operation holds the per-upload lock."""


class UploadCapacityError(UploadError):
    def __init__(self, message: str, *, retry_after_seconds: int = 30) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class UploadComparisonConflict(UploadError):
    """The comparison triple is stale or does not match the current assignment."""


class UploadHistoryConflict(UploadError):
    """The client's expected history epoch is no longer current."""


class UploadRepairRequired(UploadError):
    """The reservation needs same-lock repair before it can be touched."""


@dataclass(frozen=True)
class Reservation:
    upload_id: str
    user_id: str
    status: str
    file_bytes: int
    committed_offset: int
    chunk_bytes: int
    expires_at: float
    job_id: str | None
    comparison_mode: str | None


class _KeyedLocks:
    """A registry of per-upload locks acquired without blocking (409 on busy)."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def get(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock


class ResumableUploadManager:
    def __init__(
        self,
        jobs: JobManager,
        settings,
        *,
        state_hmac: VersionedHMAC | None = None,
        ledger: StorageCapacityLedger | None = None,
        maintenance_lock=None,
        comparison_resolver: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._jobs = jobs
        self.settings = settings
        self.sessions_dir = jobs.sessions_dir
        self.uploads_dir = self.sessions_dir / ".uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._state_hmac = state_hmac
        self._comparison_resolver = comparison_resolver
        self._locks = _KeyedLocks()
        import sqlite3

        self._conn = sqlite3.connect(
            self.sessions_dir / "swinglab.db", check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._tx = threading.Lock()
        with self._tx:
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        if maintenance_lock is None:
            from .session_maintenance_lock import SessionMaintenanceLock

            maintenance_lock = SessionMaintenanceLock(self.sessions_dir)
        self._maintenance = maintenance_lock
        if ledger is None:
            import sqlite3 as _sqlite3

            ledger_conn = _sqlite3.connect(
                self.sessions_dir / "swinglab.db", check_same_thread=False
            )
            ledger = StorageCapacityLedger(
                ledger_conn,
                sessions_dir=self.sessions_dir,
                logical_cap_bytes=settings.upload_global_max_reserved_bytes,
                min_filesystem_free_bytes=settings.upload_min_filesystem_free_bytes,
                lock=maintenance_lock,
            )
        self._ledger = ledger
        self.recover()

    def close(self) -> None:
        with self._tx:
            self._conn.close()

    # -- helpers ----------------------------------------------------------
    def _part_path(self, upload_id: str) -> Path:
        return self.uploads_dir / f"{upload_id}.part"

    def _idem_digest(
        self, domain: MobileStateDomain, key: str
    ) -> tuple[str, str]:
        if self._state_hmac is not None:
            return self._state_hmac.digest(domain, key)
        # Deterministic local fallback for embedders without a keyring: still
        # binds the domain so distinct routes never collide on one key.
        material = f"{domain.value}\0{key}".encode("utf-8")
        return "_local", hashlib.sha256(material).hexdigest()

    def _request_hash(self, user_id: str, request: UploadCreateRequest) -> str:
        comparison = None
        if request.comparison is not None:
            comparison = {
                "mode": request.comparison.mode,
                "baseline_session_id": request.comparison.baseline_session_id,
                "target_fingerprint": request.comparison.target_fingerprint,
                "drill_id": request.comparison.drill_id,
            }
        canonical = json.dumps(
            {
                "user_id": user_id,
                "source_name": request.source_name,
                "file_sha256": request.file_sha256,
                "file_bytes": request.file_bytes,
                "club": request.club,
                "hand": request.hand,
                "angle": request.angle,
                "level": request.level,
                "comparison": comparison,
                "history_epoch": request.expected_history_epoch,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _reservation_from_row(self, row) -> Reservation:
        return Reservation(
            upload_id=row["upload_id"],
            user_id=row["user_id"],
            status=row["status"],
            file_bytes=int(row["file_bytes"]),
            committed_offset=int(row["committed_offset"]),
            chunk_bytes=self.settings.upload_chunk_bytes,
            expires_at=float(row["expires_at"]),
            job_id=row["job_id"],
            comparison_mode=row["comparison_mode"],
        )

    def _row(self, upload_id: str):
        with self._tx:
            return self._conn.execute(
                "SELECT * FROM mobile_uploads WHERE upload_id = ?", (upload_id,)
            ).fetchone()

    def _owned_row(self, upload_id: str, user_id: str):
        row = self._row(upload_id)
        if row is None or row["user_id"] != user_id:
            raise UploadNotFound("No such upload.")
        return row

    def _current_history_epoch(self, user_id: str) -> int | None:
        with self._jobs._lock:
            row = self._jobs._conn.execute(
                "SELECT history_epoch FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return int(row["history_epoch"])
        except (TypeError, ValueError, KeyError):
            return None

    # -- comparison -------------------------------------------------------
    def _validate_comparison(self, user_id: str, request: UploadCreateRequest) -> None:
        comparison = request.comparison
        if comparison is None:
            return
        if self._comparison_resolver is None:
            # No assignment authority wired: a comparison claim cannot be proven.
            raise UploadComparisonConflict("No Proof Cycle assignment is available.")
        target = self._comparison_resolver(
            user_id=user_id,
            club=request.club,
            hand=request.hand,
            angle=request.angle,
            before=self._clock(),
        )
        if target is None:
            raise UploadComparisonConflict("No current Proof Cycle assignment.")
        if (
            comparison.baseline_session_id != target.baseline_session_id
            or comparison.target_fingerprint != target.target_fingerprint
            or comparison.drill_id != target.drill_id
        ):
            raise UploadComparisonConflict(
                "The comparison target is no longer current."
            )
        if comparison.mode == "matched" and (
            request.club != target.club
            or request.hand != target.hand
            or request.angle != target.angle
        ):
            raise UploadComparisonConflict(
                "A matched re-film must preserve baseline club, hand, and angle."
            )

    # -- create -----------------------------------------------------------
    def create(
        self, user_id: str, request: UploadCreateRequest, idempotency_key: str
    ) -> Reservation:
        suffix = Path(request.source_name).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise UploadStateConflict("Unsupported upload file type.")
        max_bytes = int(self._jobs.cfg.web["max_upload_mb"]) * 1024 * 1024
        if request.file_bytes > max_bytes:
            raise UploadChunkTooLarge("The declared file is larger than allowed.")

        key_id, key_hmac = self._idem_digest(
            MobileStateDomain.UPLOAD_IDEMPOTENCY, idempotency_key
        )
        request_hash = self._request_hash(user_id, request)

        # Idempotent replay / conflict decision (own connection transaction).
        with self._tx:
            existing = self._conn.execute(
                "SELECT * FROM mobile_uploads "
                "WHERE user_id = ? AND idempotency_key_id = ? AND idempotency_hmac = ?",
                (user_id, key_id, key_hmac),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise UploadIdempotencyConflict(
                        "This idempotency key was used for a different upload."
                    )
                return self._reservation_from_row(existing)
            active = self._conn.execute(
                "SELECT COUNT(*) FROM mobile_uploads "
                "WHERE user_id = ? AND status IN (?, ?)",
                (user_id, PENDING, FINALIZING),
            ).fetchone()[0]
            if int(active) >= int(self.settings.active_uploads_per_user):
                raise UploadStateConflict("Too many active uploads.")

        current_epoch = self._current_history_epoch(user_id)
        if current_epoch is None or current_epoch != request.expected_history_epoch:
            raise UploadHistoryConflict("Swing history changed before upload creation.")

        self._validate_comparison(user_id, request)

        upload_id = uuid.uuid4().hex
        # Reserve durable capacity first; if we crash before the row lands,
        # recovery reconciles the allocation from filesystem truth (no part).
        try:
            self._ledger.reserve("upload_part", upload_id, request.file_bytes)
        except InsufficientStorageError as exc:
            raise UploadCapacityError(
                str(exc), retry_after_seconds=exc.retry_after_seconds
            ) from exc

        now = self._clock()
        expires_at = now + int(self.settings.upload_ttl_seconds)
        comparison = request.comparison
        try:
            with self._tx:
                self._conn.execute(
                    "INSERT INTO mobile_uploads "
                    "(upload_id, user_id, status, source_name, suffix, file_bytes, "
                    " file_sha256, club, hand, angle, level, history_epoch, "
                    " idempotency_key_id, idempotency_hmac, request_hash, "
                    " committed_offset, comparison_mode, baseline_session_id, "
                    " target_fingerprint, drill_id, job_id, created_at, updated_at, "
                    " expires_at) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, "
                    " NULL, ?, ?, ?)",
                    (
                        upload_id,
                        user_id,
                        PENDING,
                        request.source_name,
                        suffix,
                        request.file_bytes,
                        request.file_sha256,
                        request.club,
                        request.hand,
                        request.angle,
                        request.level,
                        request.expected_history_epoch,
                        key_id,
                        key_hmac,
                        request_hash,
                        comparison.mode if comparison else None,
                        comparison.baseline_session_id if comparison else None,
                        comparison.target_fingerprint if comparison else None,
                        comparison.drill_id if comparison else None,
                        now,
                        now,
                        expires_at,
                    ),
                )
                self._conn.commit()
        except Exception:
            with self._tx:
                if self._conn.in_transaction:
                    self._conn.rollback()
            self._ledger.release("upload_part", upload_id)
            raise
        # Create the (empty) part file and durably record its directory entry.
        part = self._part_path(upload_id)
        fd = os.open(part, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self._fsync_dir(self.uploads_dir)
        return self._reservation_from_row(self._owned_row(upload_id, user_id))

    # -- status -----------------------------------------------------------
    def status(self, user_id: str, upload_id: str) -> Reservation:
        row = self._owned_row(upload_id, user_id)
        reservation = self._reservation_from_row(row)
        if reservation.status == REPAIR_REQUIRED:
            raise UploadRepairRequired("This upload is being repaired.")
        return reservation

    # -- chunk ------------------------------------------------------------
    def patch_chunk(
        self,
        user_id: str,
        upload_id: str,
        *,
        offset: int,
        chunk: bytes,
        checksum_b64: str,
    ) -> Reservation:
        lock = self._locks.get(upload_id)
        if not lock.acquire(blocking=False):
            raise UploadBusy("This upload is busy.")
        try:
            row = self._owned_row(upload_id, user_id)
            reservation = self._reservation_from_row(row)
            if reservation.status == REPAIR_REQUIRED:
                self._repair_locked(upload_id)
                row = self._owned_row(upload_id, user_id)
                reservation = self._reservation_from_row(row)
            if reservation.status != PENDING:
                raise UploadStateConflict("This upload no longer accepts chunks.")
            if self._clock() > reservation.expires_at:
                raise UploadExpired("This upload reservation expired.")
            if offset != reservation.committed_offset:
                raise UploadOffsetMismatch(
                    "Unexpected upload offset.",
                    acknowledged_offset=reservation.committed_offset,
                )
            if len(chunk) == 0:
                raise UploadChunkTooLarge("An empty chunk is not allowed.")
            if len(chunk) > self.settings.upload_chunk_bytes:
                raise UploadChunkTooLarge("The chunk exceeds the configured size.")
            if offset + len(chunk) > reservation.file_bytes:
                raise UploadChunkTooLarge("The chunk exceeds the declared file size.")
            # Verify the chunk digest before it ever touches the part file.
            try:
                expected = base64.b64decode(checksum_b64, validate=True)
            except ValueError as exc:
                raise UploadChecksumMismatch("Malformed chunk checksum.") from exc
            if hashlib.sha256(chunk).digest() != expected:
                raise UploadChecksumMismatch("Chunk checksum mismatch.")
            # Capacity guard against external disk pressure eroding reserved
            # headroom; never append an uncommitted chunk on failure.
            try:
                self._ledger.check_chunk_admission(len(chunk))
            except InsufficientStorageError as exc:
                raise UploadCapacityError(
                    str(exc), retry_after_seconds=exc.retry_after_seconds
                ) from exc

            part = self._part_path(upload_id)
            acknowledged = reservation.committed_offset
            fd = os.open(part, os.O_WRONLY)
            try:
                os.lseek(fd, acknowledged, os.SEEK_SET)
                written = 0
                view = memoryview(chunk)
                while written < len(chunk):
                    written += os.write(fd, view[written:])
                os.fsync(fd)
            except OSError:
                self._truncate_or_mark_repair(fd, part, upload_id, acknowledged)
                raise
            new_offset = acknowledged + len(chunk)
            try:
                with self._tx:
                    self._conn.execute(
                        "UPDATE mobile_uploads SET committed_offset = ?, updated_at = ? "
                        "WHERE upload_id = ? AND committed_offset = ? AND status = ?",
                        (new_offset, self._clock(), upload_id, acknowledged, PENDING),
                    )
                    self._conn.commit()
                self._ledger.update_materialized("upload_part", upload_id, new_offset)
            except Exception:
                # DB failure after the bytes were fsynced: truncate back to the
                # acknowledged offset so no unacknowledged bytes survive.
                self._truncate_or_mark_repair(fd, part, upload_id, acknowledged)
                os.close(fd)
                raise
            os.close(fd)
            return self._reservation_from_row(self._owned_row(upload_id, user_id))
        finally:
            lock.release()

    def _truncate_or_mark_repair(
        self, fd: int, part: Path, upload_id: str, acknowledged: int
    ) -> None:
        try:
            os.ftruncate(fd, acknowledged)
            os.fsync(fd)
        except OSError:
            with self._tx:
                self._conn.execute(
                    "UPDATE mobile_uploads SET status = ?, updated_at = ? "
                    "WHERE upload_id = ?",
                    (REPAIR_REQUIRED, self._clock(), upload_id),
                )
                self._conn.commit()

    def _repair_locked(self, upload_id: str) -> None:
        """Truncate the part to the acknowledged offset and clear repair state."""
        row = self._row(upload_id)
        if row is None:
            return
        acknowledged = int(row["committed_offset"])
        part = self._part_path(upload_id)
        if part.exists():
            fd = os.open(part, os.O_WRONLY)
            try:
                os.ftruncate(fd, acknowledged)
                os.fsync(fd)
            finally:
                os.close(fd)
        with self._tx:
            self._conn.execute(
                "UPDATE mobile_uploads SET status = ?, updated_at = ? WHERE upload_id = ?",
                (PENDING, self._clock(), upload_id),
            )
            self._conn.commit()

    # -- completion -------------------------------------------------------
    def complete_mobile_upload(
        self, user_id: str, upload_id: str
    ) -> tuple[Job, bool]:
        lock = self._locks.get(upload_id)
        if not lock.acquire(blocking=False):
            raise UploadBusy("This upload is busy.")
        try:
            row = self._owned_row(upload_id, user_id)
            if row["status"] == COMPLETE and row["job_id"]:
                job = self._jobs.get(row["job_id"])
                if job is not None:
                    return job, True
            if row["status"] in (ABORTING, ABORTED):
                raise UploadStateConflict("This upload was aborted.")
            if row["status"] == REPAIR_REQUIRED:
                self._repair_locked(upload_id)
                row = self._owned_row(upload_id, user_id)
            if row["status"] != PENDING:
                raise UploadStateConflict("This upload cannot be completed.")
            if self._clock() > float(row["expires_at"]):
                raise UploadExpired("This upload reservation expired.")
            if int(row["committed_offset"]) != int(row["file_bytes"]):
                raise UploadStateConflict("The upload is not fully received.")

            part = self._part_path(upload_id)
            if not self._verify_full_digest(part, row["file_sha256"], row["file_bytes"]):
                self._mark_failed(upload_id)
                raise UploadChecksumMismatch("The uploaded file digest did not match.")

            # Phase one: mark finalizing (recoverable) before any job exists.
            with self._tx:
                self._conn.execute(
                    "UPDATE mobile_uploads SET status = ?, updated_at = ? "
                    "WHERE upload_id = ? AND status = ?",
                    (FINALIZING, self._clock(), upload_id, PENDING),
                )
                self._conn.commit()

            job = self._publish_job(row)

            # Phase two: bind the job and mark the reservation complete.
            with self._tx:
                self._conn.execute(
                    "UPDATE mobile_uploads SET status = ?, job_id = ?, updated_at = ? "
                    "WHERE upload_id = ?",
                    (COMPLETE, job.id, self._clock(), upload_id),
                )
                self._conn.commit()
            # The bytes now belong to the job source; transfer the allocation
            # with no release/re-reserve gap.
            try:
                self._ledger.transfer(
                    "upload_part", upload_id, "job_source", job.id
                )
            except KeyError:
                pass
            self._jobs.submit(job, job.session_dir / f"source{row['suffix']}")
            return job, False
        finally:
            lock.release()

    def _verify_full_digest(self, part: Path, expected: str, file_bytes: int) -> bool:
        if not part.exists() or part.stat().st_size != int(file_bytes):
            return False
        digest = hashlib.sha256()
        with open(part, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == expected

    def _mark_failed(self, upload_id: str) -> None:
        with self._tx:
            self._conn.execute(
                "UPDATE mobile_uploads SET status = ?, updated_at = ? WHERE upload_id = ?",
                (FAILED, self._clock(), upload_id),
            )
            self._conn.commit()
        self._ledger.release("upload_part", upload_id)
        self._part_path(upload_id).unlink(missing_ok=True)

    def _publish_job(self, row) -> Job:
        # Reuse the JobManager's owned session creation (dir + row + epoch
        # fence); the atomic part->source move is serialized by the per-upload
        # lock and durably published with a directory fsync.
        try:
            job = self._jobs.create_session(
                source_name=row["source_name"],
                hand=row["hand"],
                fast=False,
                user_id=row["user_id"],
                angle=row["angle"],
                club=row["club"],
                level=row["level"],
                expected_history_epoch=int(row["history_epoch"]),
            )
        except Exception as exc:  # HistoryResetConflict and friends
            raise UploadHistoryConflict(str(exc)) from exc
        source = job.session_dir / f"source{row['suffix']}"
        part = self._part_path(row["upload_id"])
        with self._maintenance.acquire(timeout=30.0):
            os.replace(part, source)
            self._fsync_dir(job.session_dir)
        return job

    # -- abort ------------------------------------------------------------
    def abort(self, user_id: str, upload_id: str, idempotency_key: str) -> None:
        key_id, key_hmac = self._idem_digest(
            MobileStateDomain.UPLOAD_ABORT_IDEMPOTENCY, idempotency_key
        )
        lock = self._locks.get(upload_id)
        if not lock.acquire(blocking=False):
            raise UploadBusy("This upload is busy.")
        try:
            with self._tx:
                receipt = self._conn.execute(
                    "SELECT * FROM mobile_upload_abort_receipts WHERE upload_id = ?",
                    (upload_id,),
                ).fetchone()
            if receipt is not None:
                if (
                    receipt["user_id"] != user_id
                    or receipt["idempotency_key_id"] != key_id
                    or receipt["idempotency_hmac"] != key_hmac
                ):
                    raise UploadIdempotencyConflict(
                        "This upload was already aborted with a different key."
                    )
                return  # exact replay -> 204
            row = self._row(upload_id)
            if row is None or row["user_id"] != user_id:
                raise UploadNotFound("No such upload.")
            if row["status"] == COMPLETE:
                raise UploadStateConflict("A completed upload cannot be aborted.")

            # Journal the aborting intent before any filesystem mutation.
            with self._tx:
                self._conn.execute(
                    "UPDATE mobile_uploads SET status = ?, updated_at = ? "
                    "WHERE upload_id = ?",
                    (ABORTING, self._clock(), upload_id),
                )
                self._conn.commit()
            part = self._part_path(upload_id)
            with self._maintenance.acquire(timeout=30.0):
                part.unlink(missing_ok=True)
                self._fsync_dir(self.uploads_dir)
            self._ledger.release("upload_part", upload_id)
            now = self._clock()
            with self._tx:
                self._conn.execute(
                    "UPDATE mobile_uploads SET status = ?, updated_at = ? "
                    "WHERE upload_id = ?",
                    (ABORTED, now, upload_id),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO mobile_upload_abort_receipts "
                    "(upload_id, user_id, idempotency_key_id, idempotency_hmac, "
                    " request_hash, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        upload_id,
                        user_id,
                        key_id,
                        key_hmac,
                        upload_id,
                        now,
                        now + _ABORT_RECEIPT_TTL_SECONDS,
                    ),
                )
                self._conn.commit()
        finally:
            lock.release()

    # -- maintenance / recovery ------------------------------------------
    def expire_stale(self) -> int:
        """Release reservations whose TTL elapsed and their orphaned parts."""
        now = self._clock()
        with self._tx:
            rows = self._conn.execute(
                "SELECT upload_id FROM mobile_uploads "
                "WHERE status = ? AND expires_at < ?",
                (PENDING, now),
            ).fetchall()
        released = 0
        for row in rows:
            upload_id = row["upload_id"]
            lock = self._locks.get(upload_id)
            if not lock.acquire(blocking=False):
                continue
            try:
                with self._maintenance.acquire(timeout=30.0):
                    self._part_path(upload_id).unlink(missing_ok=True)
                    self._fsync_dir(self.uploads_dir)
                self._ledger.release("upload_part", upload_id)
                with self._tx:
                    self._conn.execute(
                        "UPDATE mobile_uploads SET status = ?, updated_at = ? "
                        "WHERE upload_id = ?",
                        (FAILED, self._clock(), upload_id),
                    )
                    self._conn.commit()
                released += 1
            finally:
                lock.release()
        return released

    def discard_for_user(self, user_id: str) -> None:
        """History reset / account deletion discards active reservations."""
        with self._tx:
            rows = self._conn.execute(
                "SELECT upload_id FROM mobile_uploads "
                "WHERE user_id = ? AND status IN (?, ?)",
                (user_id, PENDING, FINALIZING),
            ).fetchall()
        for row in rows:
            upload_id = row["upload_id"]
            self._part_path(upload_id).unlink(missing_ok=True)
            self._ledger.release("upload_part", upload_id)
            with self._tx:
                self._conn.execute(
                    "UPDATE mobile_uploads SET status = ?, updated_at = ? "
                    "WHERE upload_id = ?",
                    (FAILED, self._clock(), upload_id),
                )
                self._conn.commit()

    def recover(self) -> None:
        """Converge every nonterminal reservation before requests are served.

        * ``finalizing`` rows resume from the part (republish) or, if the source
          already moved to the job dir, from the job; unrecoverable ones fail.
        * ``pending`` rows are truncated to their acknowledged offset so no
          unacknowledged tail bytes survive a crash.
        * ``repair_required`` rows are truncated and reopened.
        * ``aborting`` rows finish their abort.
        * the capacity ledger is reconciled from filesystem truth.
        """
        with self._tx:
            rows = self._conn.execute(
                "SELECT * FROM mobile_uploads WHERE status IN (?, ?, ?, ?)",
                (PENDING, FINALIZING, ABORTING, REPAIR_REQUIRED),
            ).fetchall()
        for row in rows:
            upload_id = row["upload_id"]
            status = row["status"]
            part = self._part_path(upload_id)
            if status in (PENDING, REPAIR_REQUIRED):
                acknowledged = int(row["committed_offset"])
                if part.exists():
                    try:
                        fd = os.open(part, os.O_WRONLY)
                        try:
                            if part.stat().st_size > acknowledged:
                                os.ftruncate(fd, acknowledged)
                                os.fsync(fd)
                        finally:
                            os.close(fd)
                    except OSError:
                        continue
                    if status == REPAIR_REQUIRED:
                        with self._tx:
                            self._conn.execute(
                                "UPDATE mobile_uploads SET status = ? WHERE upload_id = ?",
                                (PENDING, upload_id),
                            )
                            self._conn.commit()
                else:
                    # A pending reservation whose part vanished cannot resume.
                    self._fail_and_release(upload_id)
            elif status == ABORTING:
                part.unlink(missing_ok=True)
                self._ledger.release("upload_part", upload_id)
                with self._tx:
                    self._conn.execute(
                        "UPDATE mobile_uploads SET status = ? WHERE upload_id = ?",
                        (ABORTED, upload_id),
                    )
                    self._conn.commit()
            elif status == FINALIZING:
                self._recover_finalizing(row)
        self._reconcile_ledger()

    def _recover_finalizing(self, row) -> None:
        upload_id = row["upload_id"]
        part = self._part_path(upload_id)
        if part.exists() and part.stat().st_size == int(row["file_bytes"]):
            try:
                job = self._publish_job(row)
            except UploadError:
                self._fail_and_release(upload_id)
                return
            with self._tx:
                self._conn.execute(
                    "UPDATE mobile_uploads SET status = ?, job_id = ? WHERE upload_id = ?",
                    (COMPLETE, job.id, upload_id),
                )
                self._conn.commit()
            try:
                self._ledger.transfer("upload_part", upload_id, "job_source", job.id)
            except KeyError:
                pass
            self._jobs.submit(job, job.session_dir / f"source{row['suffix']}")
        else:
            # No recoverable part; a committed job (if any) is left intact,
            # otherwise the reservation fails cleanly.
            if row["job_id"]:
                with self._tx:
                    self._conn.execute(
                        "UPDATE mobile_uploads SET status = ? WHERE upload_id = ?",
                        (COMPLETE, upload_id),
                    )
                    self._conn.commit()
            else:
                self._fail_and_release(upload_id)

    def _fail_and_release(self, upload_id: str) -> None:
        self._ledger.release("upload_part", upload_id)
        self._part_path(upload_id).unlink(missing_ok=True)
        with self._tx:
            self._conn.execute(
                "UPDATE mobile_uploads SET status = ? WHERE upload_id = ?",
                (FAILED, upload_id),
            )
            self._conn.commit()

    def _reconcile_ledger(self) -> None:
        present: dict[tuple[str, str], int] = {}
        with self._tx:
            rows = self._conn.execute(
                "SELECT upload_id, status, committed_offset FROM mobile_uploads "
                "WHERE status IN (?, ?)",
                (PENDING, FINALIZING),
            ).fetchall()
        for row in rows:
            part = self._part_path(row["upload_id"])
            if part.exists():
                present[("upload_part", row["upload_id"])] = min(
                    part.stat().st_size, int(row["committed_offset"])
                )
        # Job-source allocations are owned by the job lifecycle, not upload
        # recovery: preserve them at their current materialized bytes so
        # reconcile never releases a live queued/processing source.
        for kind, object_id, materialized in self._non_upload_ledger_rows():
            present[(kind, object_id)] = materialized
        self._ledger.reconcile(present)

    def _non_upload_ledger_rows(self) -> list[tuple[str, str, int]]:
        rows = self._ledger._conn.execute(
            "SELECT kind, object_id, materialized_bytes "
            "FROM mobile_storage_allocations WHERE kind != 'upload_part'"
        ).fetchall()
        return [(r[0], r[1], int(r[2])) for r in rows]
