"""Sliding-window throttling for the auth forms, backed by SQLite.

Why this exists: /login had no rate limiting (scrypt verification is
deliberately expensive — an attacker gets free CPU and unlimited guesses)
and /signup was unthrottled (every throwaway email costs a scrypt hash and
a user row). Uploads are NOT touched here — they already have the monthly
quota and the per-IP active-job limit — and the JSON API's behavior is
unchanged.

Model: one table of (bucket, key, timestamp) attempt rows in the same
SQLite file as jobs/users (its own connection, same pattern as UserStore).
``allow`` counts rows younger than the window; ``record`` adds one. Expired
rows are pruned inline on every allow() call, so the table stays tiny with
no background job. Because the window slides, nobody is ever "locked out"
— the legitimate account owner just waits out the window; there is no
per-account lock bit to reset.

Stdlib only, no new dependencies. Limits and windows come from config
(web.login_attempts_per_15min, web.signups_per_hour_per_ip; 0 = off) and
are wired up in app.py.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_attempts (
    bucket TEXT NOT NULL,
    key    TEXT NOT NULL,
    ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_attempts_lookup ON auth_attempts(bucket, key, ts);
"""


class Throttle:
    def __init__(self, db_path: str | Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def allow(
        self, bucket: str, key: str | None, limit: int, window_s: float,
        now: float | None = None,
    ) -> bool:
        """True while fewer than ``limit`` attempts are on record for
        (bucket, key) within the window. A limit of 0 (feature off) or a
        missing key (no client IP known) always allows — inert, never a
        false lockout."""
        if limit <= 0 or not key:
            return True
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "DELETE FROM auth_attempts WHERE bucket = ? AND key = ? AND ts < ?",
                (bucket, key, now - window_s),
            )
            count = self._conn.execute(
                "SELECT COUNT(*) FROM auth_attempts WHERE bucket = ? AND key = ?",
                (bucket, key),
            ).fetchone()[0]
            self._conn.commit()
        return count < limit

    def record(self, bucket: str, key: str | None, now: float | None = None) -> None:
        """Log one attempt. No-op without a key."""
        if not key:
            return
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "INSERT INTO auth_attempts (bucket, key, ts) VALUES (?, ?, ?)",
                (bucket, key, now),
            )
            self._conn.commit()
