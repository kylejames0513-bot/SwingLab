"""The single durable capacity authority for mobile storage.

One SQLite table (``mobile_storage_allocations``) tracks every byte the server
may be holding on behalf of native clients: in-flight upload parts, the
immutable ``source.<suffix>`` of a queued/processing/retryable-failed job, and
(later) privacy-export temporaries. Each unique ``(kind, object_id)`` row
records how many bytes were *reserved* (the declared/committed size) and how
many are actually *materialized* on disk.

Admission holds a cross-process lock and enforces two independent limits:

* a configured logical cap on total reserved bytes, and
* filesystem free space minus every still-unmaterialized reservation, kept at
  or above the configured floor (which protects the DB/artifact/backup
  headroom).

Ownership transfers atomically between kinds (upload part -> job source) with no
release/re-reserve gap, and terminal purge releases exactly once.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS mobile_storage_allocations (
    kind               TEXT NOT NULL,
    object_id          TEXT NOT NULL,
    reserved_bytes     INTEGER NOT NULL,
    materialized_bytes INTEGER NOT NULL DEFAULT 0,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    PRIMARY KEY (kind, object_id)
);
CREATE INDEX IF NOT EXISTS mobile_storage_allocations_kind
    ON mobile_storage_allocations(kind);
"""


class InsufficientStorageError(RuntimeError):
    """Admission was refused; the caller must surface a retryable 507."""

    def __init__(self, message: str, *, retry_after_seconds: int = 30) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class _NullLock:
    @contextmanager
    def acquire(self, *, timeout: float) -> Iterator[None]:  # noqa: ARG002
        yield


class StorageCapacityLedger:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        sessions_dir: str | Path,
        logical_cap_bytes: int,
        min_filesystem_free_bytes: int,
        protected_floor_bytes: int = 0,
        disk_free: Callable[[], int] | None = None,
        lock: object | None = None,
        clock: Callable[[], float] = time.time,
        lock_timeout: float = 30.0,
    ) -> None:
        self._conn = conn
        self.sessions_dir = Path(sessions_dir)
        self.logical_cap_bytes = int(logical_cap_bytes)
        self.min_filesystem_free_bytes = int(min_filesystem_free_bytes)
        self.protected_floor_bytes = int(protected_floor_bytes)
        self._disk_free = disk_free or self._default_disk_free
        self._lock = lock if lock is not None else _NullLock()
        self._clock = clock
        self._lock_timeout = lock_timeout
        self._tx_guard = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _default_disk_free(self) -> int:
        return shutil.disk_usage(self.sessions_dir).free

    @property
    def _floor(self) -> int:
        return self.min_filesystem_free_bytes + self.protected_floor_bytes

    @contextmanager
    def _admission(self) -> Iterator[sqlite3.Cursor]:
        with self._lock.acquire(timeout=self._lock_timeout):
            with self._tx_guard:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    yield self._conn.cursor()
                    self._conn.commit()
                except BaseException:
                    self._conn.rollback()
                    raise

    def _sum(self, cur: sqlite3.Cursor, expr: str) -> int:
        row = cur.execute(
            f"SELECT COALESCE(SUM({expr}), 0) FROM mobile_storage_allocations"
        ).fetchone()
        return int(row[0])

    def total_reserved(self) -> int:
        with self._tx_guard:
            return self._sum(self._conn.cursor(), "reserved_bytes")

    def materialized_total(self) -> int:
        with self._tx_guard:
            return self._sum(self._conn.cursor(), "materialized_bytes")

    def unmaterialized_bytes(self) -> int:
        with self._tx_guard:
            return self._sum(
                self._conn.cursor(),
                "CASE WHEN reserved_bytes > materialized_bytes "
                "THEN reserved_bytes - materialized_bytes ELSE 0 END",
            )

    def kind_of(self, object_id: str, kind: str) -> str | None:
        with self._tx_guard:
            row = self._conn.execute(
                "SELECT kind FROM mobile_storage_allocations "
                "WHERE kind = ? AND object_id = ?",
                (kind, object_id),
            ).fetchone()
        return None if row is None else row[0]

    def reserve(self, kind: str, object_id: str, declared_bytes: int) -> None:
        if declared_bytes < 0:
            raise ValueError("declared_bytes must be non-negative")
        with self._admission() as cur:
            existing = cur.execute(
                "SELECT reserved_bytes FROM mobile_storage_allocations "
                "WHERE kind = ? AND object_id = ?",
                (kind, object_id),
            ).fetchone()
            if existing is not None:
                # Idempotent: an already-reserved object is not re-admitted or
                # double-counted.
                return
            reserved = self._sum(cur, "reserved_bytes")
            if reserved + declared_bytes > self.logical_cap_bytes:
                raise InsufficientStorageError(
                    "logical storage cap would be exceeded"
                )
            unmaterialized = self._sum(
                cur,
                "CASE WHEN reserved_bytes > materialized_bytes "
                "THEN reserved_bytes - materialized_bytes ELSE 0 END",
            )
            free = self._disk_free()
            if free - unmaterialized - declared_bytes < self._floor:
                raise InsufficientStorageError(
                    "insufficient filesystem free space for reservation"
                )
            now = self._clock()
            cur.execute(
                "INSERT INTO mobile_storage_allocations "
                "(kind, object_id, reserved_bytes, materialized_bytes, "
                " created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)",
                (kind, object_id, declared_bytes, now, now),
            )

    def check_chunk_admission(self, chunk_bytes: int) -> None:
        """Guard a chunk append: current free minus all outstanding
        unmaterialized bytes must stay at or above the floor. The chunk's bytes
        are already inside an existing reservation, so we do not subtract them
        twice; this catches external disk pressure eroding reserved headroom."""
        if chunk_bytes < 0:
            raise ValueError("chunk_bytes must be non-negative")
        with self._admission() as cur:
            unmaterialized = self._sum(
                cur,
                "CASE WHEN reserved_bytes > materialized_bytes "
                "THEN reserved_bytes - materialized_bytes ELSE 0 END",
            )
            free = self._disk_free()
            if free - unmaterialized < self._floor:
                raise InsufficientStorageError(
                    "filesystem free space fell below the reserved floor"
                )

    def update_materialized(
        self, kind: str, object_id: str, materialized_bytes: int
    ) -> None:
        if materialized_bytes < 0:
            raise ValueError("materialized_bytes must be non-negative")
        with self._admission() as cur:
            row = cur.execute(
                "SELECT reserved_bytes, materialized_bytes "
                "FROM mobile_storage_allocations WHERE kind = ? AND object_id = ?",
                (kind, object_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"no allocation for {kind}/{object_id}")
            reserved, current = int(row[0]), int(row[1])
            if materialized_bytes > reserved:
                raise ValueError("materialized bytes exceed reserved bytes")
            # Acknowledged offset only advances; a smaller value is ignored so a
            # stale/retried update cannot un-account committed bytes.
            new_value = max(current, materialized_bytes)
            cur.execute(
                "UPDATE mobile_storage_allocations "
                "SET materialized_bytes = ?, updated_at = ? "
                "WHERE kind = ? AND object_id = ?",
                (new_value, self._clock(), kind, object_id),
            )

    def transfer(
        self,
        from_kind: str,
        from_object_id: str,
        to_kind: str,
        to_object_id: str,
    ) -> None:
        with self._admission() as cur:
            row = cur.execute(
                "SELECT reserved_bytes, materialized_bytes, created_at "
                "FROM mobile_storage_allocations WHERE kind = ? AND object_id = ?",
                (from_kind, from_object_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"no allocation for {from_kind}/{from_object_id}")
            reserved, materialized, created_at = int(row[0]), int(row[1]), row[2]
            # Same transaction: the source row is removed and the destination
            # row inserted with no window in which the bytes are unaccounted.
            cur.execute(
                "DELETE FROM mobile_storage_allocations WHERE kind = ? AND object_id = ?",
                (from_kind, from_object_id),
            )
            cur.execute(
                "INSERT OR REPLACE INTO mobile_storage_allocations "
                "(kind, object_id, reserved_bytes, materialized_bytes, "
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    to_kind,
                    to_object_id,
                    reserved,
                    materialized,
                    created_at,
                    self._clock(),
                ),
            )

    def release(self, kind: str, object_id: str) -> bool:
        """Release a reservation, returning whether a row was actually removed.

        Deleting a non-existent row is a no-op, so a replayed release can never
        double-count against the ledger totals.
        """
        with self._admission() as cur:
            cur.execute(
                "DELETE FROM mobile_storage_allocations WHERE kind = ? AND object_id = ?",
                (kind, object_id),
            )
            return cur.rowcount > 0

    def reconcile(self, present: dict[tuple[str, str], int]) -> None:
        """Repair the ledger from filesystem truth.

        ``present`` maps ``(kind, object_id)`` to the materialized bytes actually
        found on disk. Rows absent from ``present`` are released exactly once;
        rows present have their materialized bytes corrected.
        """
        with self._admission() as cur:
            rows = cur.execute(
                "SELECT kind, object_id, reserved_bytes FROM mobile_storage_allocations"
            ).fetchall()
            now = self._clock()
            for kind, object_id, reserved in rows:
                key = (kind, object_id)
                if key not in present:
                    cur.execute(
                        "DELETE FROM mobile_storage_allocations "
                        "WHERE kind = ? AND object_id = ?",
                        (kind, object_id),
                    )
                    continue
                actual = min(int(present[key]), int(reserved))
                cur.execute(
                    "UPDATE mobile_storage_allocations "
                    "SET materialized_bytes = ?, updated_at = ? "
                    "WHERE kind = ? AND object_id = ?",
                    (actual, now, kind, object_id),
                )

    def health(self) -> dict[str, int]:
        with self._tx_guard:
            cur = self._conn.cursor()
            reserved = self._sum(cur, "reserved_bytes")
            unmaterialized = self._sum(
                cur,
                "CASE WHEN reserved_bytes > materialized_bytes "
                "THEN reserved_bytes - materialized_bytes ELSE 0 END",
            )
            active = int(
                cur.execute(
                    "SELECT COUNT(*) FROM mobile_storage_allocations"
                ).fetchone()[0]
            )
        free = self._disk_free()
        return {
            "reserved_bytes": reserved,
            "cap_bytes": self.logical_cap_bytes,
            "active_allocations": active,
            "free_headroom_bytes": max(free - unmaterialized - self._floor, 0),
        }
