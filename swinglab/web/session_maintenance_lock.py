"""The single cross-process exclusive lock for session-tree maintenance.

Both the running app and the backup CLI take this lock around any filesystem
mutation of the session tree (upload publish, terminal artifact publish,
retention deletion, privacy quarantine) and around a consistent backup snapshot.
It is an advisory OS lock on a fixed byte of ``.session-maintenance.lock`` so
the kernel releases it automatically if a holder crashes.

Lock ordering across the codebase is fixed: maintenance file lock -> per-upload
lock -> UserStore/JobManager lock -> SQLite transaction. Callers must never
acquire these out of order.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import TracebackType

_LOCK_FILENAME = ".session-maintenance.lock"
# We lock a single fixed byte so POSIX and Windows adapters agree on the region.
_LOCK_OFFSET = 0
_LOCK_LENGTH = 1
_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
    import msvcrt
else:
    import fcntl


class SessionMaintenanceLockTimeout(TimeoutError):
    """The maintenance lock could not be acquired within the bounded timeout."""


class _Held:
    """Context-manager handle returned by :meth:`SessionMaintenanceLock.acquire`."""

    def __init__(self, lock: "SessionMaintenanceLock") -> None:
        self._lock = lock

    def __enter__(self) -> "_Held":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._lock.release()


class SessionMaintenanceLock:
    """A bounded-timeout cross-process exclusive lock on one lock file."""

    def __init__(self, sessions_dir: str | Path) -> None:
        self.sessions_dir = Path(sessions_dir)
        self.path = self.sessions_dir / _LOCK_FILENAME
        # In-process reentrancy guard: one process must not fight itself for an
        # OS lock it already owns (flock is per-open-file-description).
        self._local = threading.Lock()
        self._fd: int | None = None
        self.held = False

    def _open_fd(self) -> int:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        return os.open(self.path, flags, 0o600)

    def _try_lock(self, fd: int) -> bool:
        if _IS_WINDOWS:  # pragma: no cover - Windows only
            try:
                os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCK_LENGTH)
                return True
            except OSError:
                return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(self, fd: int) -> None:
        if _IS_WINDOWS:  # pragma: no cover - Windows only
            try:
                os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_LENGTH)
            except OSError:
                pass
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass

    def _acquire_raw(self, *, timeout: float) -> None:
        # The in-process guard is acquired with the same bounded budget so two
        # threads in one process do not spin the OS lock.
        if not self._local.acquire(timeout=max(timeout, 0.0)):
            raise SessionMaintenanceLockTimeout(
                "session maintenance lock is held in-process"
            )
        acquired_local = True
        try:
            fd = self._open_fd()
            deadline = time.monotonic() + max(timeout, 0.0)
            delay = 0.005
            while True:
                if self._try_lock(fd):
                    self._fd = fd
                    self.held = True
                    self._write_diagnostic(fd)
                    acquired_local = False
                    return
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise SessionMaintenanceLockTimeout(
                        f"could not acquire {self.path} within {timeout}s"
                    )
                time.sleep(min(delay, 0.05))
                delay *= 2
        finally:
            if acquired_local:
                self._local.release()

    def _write_diagnostic(self, fd: int) -> None:
        # PID + timestamp are for humans reading the file during an incident;
        # correctness never parses this text back.
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(
                fd,
                f"pid={os.getpid()} acquired_at={time.time():.3f}\n".encode("ascii"),
            )
            os.fsync(fd)
        except OSError:
            pass

    def acquire(self, *, timeout: float) -> _Held:
        self._acquire_raw(timeout=timeout)
        return _Held(self)

    def release(self) -> None:
        if self._fd is None:
            return
        fd = self._fd
        self._unlock(fd)
        try:
            os.close(fd)
        finally:
            self._fd = None
            self.held = False
            try:
                self._local.release()
            except RuntimeError:
                pass
