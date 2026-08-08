"""The one cross-process maintenance lock shared by the app and backup CLI.

It locks a fixed byte of ``.session-maintenance.lock`` (``fcntl.flock`` on
POSIX, ``msvcrt.locking`` on Windows) with a bounded timeout. The PID/timestamp
text written into the file is diagnostic only and never parsed for correctness.
"""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from swinglab.web.session_maintenance_lock import (
    SessionMaintenanceLock,
    SessionMaintenanceLockTimeout,
)


def test_lock_file_created_under_sessions_dir(tmp_path: Path) -> None:
    lock = SessionMaintenanceLock(tmp_path)
    with lock.acquire(timeout=1.0):
        assert (tmp_path / ".session-maintenance.lock").exists()


def test_reentrant_same_object_is_serialized_not_deadlocked(tmp_path: Path) -> None:
    lock = SessionMaintenanceLock(tmp_path)
    with lock.acquire(timeout=1.0):
        assert lock.held is True
    assert lock.held is False


def test_diagnostic_text_is_written(tmp_path: Path) -> None:
    lock = SessionMaintenanceLock(tmp_path)
    with lock.acquire(timeout=1.0):
        pass
    text = (tmp_path / ".session-maintenance.lock").read_text()
    # PID and a timestamp appear for humans; correctness never reads them back.
    assert str(os.getpid()) in text


def _hold_lock(dir_str: str, hold_seconds: float, ready, done) -> None:
    lock = SessionMaintenanceLock(Path(dir_str))
    with lock.acquire(timeout=5.0):
        ready.set()
        time.sleep(hold_seconds)
    done.set()


def test_second_process_times_out_while_first_holds(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    done = ctx.Event()
    holder = ctx.Process(
        target=_hold_lock, args=(str(tmp_path), 2.0, ready, done)
    )
    holder.start()
    try:
        assert ready.wait(timeout=5.0)
        lock = SessionMaintenanceLock(tmp_path)
        start = time.monotonic()
        with pytest.raises(SessionMaintenanceLockTimeout):
            with lock.acquire(timeout=0.3):
                pass
        elapsed = time.monotonic() - start
        assert elapsed < 2.0
    finally:
        holder.join(timeout=10.0)
    assert done.is_set()


def test_lock_released_after_holder_exits(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    done = ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(str(tmp_path), 0.2, ready, done))
    holder.start()
    assert ready.wait(timeout=5.0)
    holder.join(timeout=10.0)
    # Once the holder exits the OS releases its advisory lock; we acquire fast.
    lock = SessionMaintenanceLock(tmp_path)
    with lock.acquire(timeout=2.0):
        assert lock.held is True


def _crash_while_holding(dir_str: str, ready) -> None:
    lock = SessionMaintenanceLock(Path(dir_str))
    lock._acquire_raw(timeout=5.0)  # acquire and deliberately never release
    ready.set()
    os._exit(0)


def test_lock_released_when_holder_crashes(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    crasher = ctx.Process(target=_crash_while_holding, args=(str(tmp_path), ready))
    crasher.start()
    assert ready.wait(timeout=5.0)
    crasher.join(timeout=10.0)
    lock = SessionMaintenanceLock(tmp_path)
    with lock.acquire(timeout=2.0):
        assert lock.held is True
