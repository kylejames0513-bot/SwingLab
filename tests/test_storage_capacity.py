"""The single durable capacity authority for upload parts and job sources.

Each ``(kind, object_id)`` row records reserved and materialized bytes.
Admission holds a cross-process lock and checks the configured logical cap and
filesystem free space minus every still-unmaterialized reservation and the
protected floor. Ownership transfers atomically between kinds; terminal purge
releases exactly once.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from swinglab.web.storage_capacity import (
    InsufficientStorageError,
    StorageCapacityLedger,
)


def make_ledger(
    *,
    cap: int = 1000,
    min_free: int = 100,
    free: int = 10_000,
) -> StorageCapacityLedger:
    conn = sqlite3.connect(":memory:")
    holder = {"free": free}
    ledger = StorageCapacityLedger(
        conn,
        sessions_dir=Path("/tmp"),
        logical_cap_bytes=cap,
        min_filesystem_free_bytes=min_free,
        disk_free=lambda: holder["free"],
    )
    ledger._free_holder = holder  # test hook to adjust simulated free space
    return ledger


def test_reserve_then_totals() -> None:
    ledger = make_ledger(cap=1000)
    ledger.reserve("upload_part", "u1", 200)
    assert ledger.total_reserved() == 200
    assert ledger.unmaterialized_bytes() == 200


def test_reserve_is_idempotent_per_object() -> None:
    ledger = make_ledger(cap=1000)
    ledger.reserve("upload_part", "u1", 200)
    ledger.reserve("upload_part", "u1", 200)
    assert ledger.total_reserved() == 200


def test_logical_cap_rejects_overcommit() -> None:
    ledger = make_ledger(cap=500)
    ledger.reserve("upload_part", "u1", 300)
    with pytest.raises(InsufficientStorageError):
        ledger.reserve("upload_part", "u2", 300)
    assert ledger.total_reserved() == 300


def test_exact_limit_admission_succeeds() -> None:
    ledger = make_ledger(cap=500)
    ledger.reserve("upload_part", "u1", 300)
    ledger.reserve("upload_part", "u2", 200)
    assert ledger.total_reserved() == 500


def test_filesystem_floor_rejects_when_free_too_low() -> None:
    ledger = make_ledger(cap=10_000, min_free=100, free=250)
    # free(250) - declared(200) = 50 < floor(100) -> rejected
    with pytest.raises(InsufficientStorageError):
        ledger.reserve("upload_part", "u1", 200)


def test_materialized_update_monotonic_and_bounded() -> None:
    ledger = make_ledger(cap=1000)
    ledger.reserve("upload_part", "u1", 200)
    ledger.update_materialized("upload_part", "u1", 120)
    assert ledger.unmaterialized_bytes() == 80
    with pytest.raises(ValueError):
        ledger.update_materialized("upload_part", "u1", 300)  # exceeds reserved


def test_chunk_admission_uses_current_free_minus_unmaterialized() -> None:
    ledger = make_ledger(cap=10_000, min_free=100, free=1000)
    ledger.reserve("upload_part", "u1", 500)  # 1000 - 500 = 500 >= 100 ok
    ledger.check_chunk_admission(64)  # 1000 - 500 = 500 >= 100 ok
    ledger._free_holder["free"] = 550  # external process ate space
    with pytest.raises(InsufficientStorageError):
        ledger.check_chunk_admission(64)  # 550 - 500 = 50 < 100 -> reject


def test_transfer_is_atomic_no_release_gap() -> None:
    ledger = make_ledger(cap=1000)
    ledger.reserve("upload_part", "u1", 300)
    ledger.update_materialized("upload_part", "u1", 300)
    ledger.transfer("upload_part", "u1", "job_source", "job-42")
    assert ledger.total_reserved() == 300
    assert ledger.kind_of("job-42", "job_source") is not None
    assert ledger.kind_of("u1", "upload_part") is None


def test_release_is_exactly_once() -> None:
    ledger = make_ledger(cap=1000)
    ledger.reserve("upload_part", "u1", 300)
    ledger.release("upload_part", "u1")
    assert ledger.total_reserved() == 0
    ledger.release("upload_part", "u1")  # second release is a no-op
    assert ledger.total_reserved() == 0


def test_release_absent_object_is_noop() -> None:
    ledger = make_ledger(cap=1000)
    ledger.release("upload_part", "missing")
    assert ledger.total_reserved() == 0


def test_health_reports_aggregate_only() -> None:
    ledger = make_ledger(cap=1000, min_free=100, free=5000)
    ledger.reserve("upload_part", "u1", 300)
    health = ledger.health()
    assert health["reserved_bytes"] == 300
    assert health["cap_bytes"] == 1000
    assert "free_headroom_bytes" in health
    # No paths, object ids, or owner data may leak into health.
    text = repr(health)
    assert "u1" not in text
    assert "/tmp" not in text


def test_reconcile_repairs_from_filesystem_truth() -> None:
    ledger = make_ledger(cap=1000)
    ledger.reserve("upload_part", "u1", 300)
    ledger.reserve("upload_part", "u2", 200)
    # u2's part file is gone on disk; reconcile releases it exactly once and
    # corrects u1's materialized bytes to filesystem truth.
    ledger.reconcile({("upload_part", "u1"): 150})
    assert ledger.total_reserved() == 300
    assert ledger.unmaterialized_bytes() == 150
    assert ledger.kind_of("u2", "upload_part") is None
    ledger.reconcile({("upload_part", "u1"): 150})  # stable / no double release
    assert ledger.total_reserved() == 300


def test_two_writers_race_last_space_only_one_admitted() -> None:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    holder = {"free": 10_000}
    guard = threading.Lock()

    class _Lock:
        def acquire(self, *, timeout):
            guard.acquire()
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            guard.release()

    ledger = StorageCapacityLedger(
        conn,
        sessions_dir=Path("/tmp"),
        logical_cap_bytes=300,
        min_filesystem_free_bytes=0,
        disk_free=lambda: holder["free"],
        lock=_Lock(),
    )
    results: list[str] = []

    def attempt(object_id: str) -> None:
        try:
            ledger.reserve("upload_part", object_id, 300)
            results.append(f"ok:{object_id}")
        except InsufficientStorageError:
            results.append(f"reject:{object_id}")

    threads = [threading.Thread(target=attempt, args=(f"u{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for r in results if r.startswith("ok")) == 1
    assert sum(1 for r in results if r.startswith("reject")) == 1
    assert ledger.total_reserved() == 300
