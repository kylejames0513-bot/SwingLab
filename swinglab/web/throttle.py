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

import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .mobile_schema import (
    HMACDigest,
    MobileStateDomain,
    VersionedHMAC,
    ensure_mobile_rate_limit_schema,
)

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
        self._closed = False
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        """Release this throttle's SQLite connection exactly once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

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


@dataclass(frozen=True)
class MultiRateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


_KEYED_RATE_DOMAINS: dict[str, MobileStateDomain] = {
    "auth-start-ip": MobileStateDomain.AUTH_START_CLIENT_IP,
    "auth-start-email": MobileStateDomain.AUTH_START_NORMALIZED_EMAIL_RATE,
    "auth-exchange-ip": MobileStateDomain.AUTH_EXCHANGE_CLIENT_IP,
    "auth-exchange-email": MobileStateDomain.AUTH_EXCHANGE_NORMALIZED_EMAIL_RATE,
    "review-auth-ip": MobileStateDomain.REVIEW_AUTH_CLIENT_IP,
    "review-auth-start-ip": MobileStateDomain.REVIEW_AUTH_CLIENT_IP,
    "review-auth-exchange-ip": MobileStateDomain.REVIEW_AUTH_CLIENT_IP,
}
_PROTECTED_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_PROTECTED_DIGEST = re.compile(r"[0-9a-f]{64}")
_PreparedRateEntry = tuple[
    str, int, float, tuple[tuple[str, str], ...], str, str
]


class KeyedThrottle:
    """Atomic multi-key sliding windows with no raw identity persistence."""

    def __init__(self, db_path: str | Path, keyring: VersionedHMAC):
        self._lock = threading.Lock()
        self._closed = False
        self._keyring = keyring
        self._conn = sqlite3.connect(
            db_path, check_same_thread=False, timeout=30.0
        )
        with self._lock:
            ensure_mobile_rate_limit_schema(self._conn)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def consume(
        self,
        domain: str,
        raw_key: str | None,
        limit: int,
        window_s: float,
        now: float | None = None,
    ) -> MultiRateLimitDecision:
        return self.consume_many([(domain, raw_key, limit, window_s)], now=now)

    def consume_many(
        self,
        entries: list[tuple[str, str | None, int, float]],
        now: float | None = None,
    ) -> MultiRateLimitDecision:
        if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
            raise ValueError("Between one and eight keyed rate entries are required.")
        observed_at = time.time() if now is None else float(now)
        prepared: list[_PreparedRateEntry] = []
        seen_domains: set[str] = set()
        for domain, raw_key, limit, window_s in entries:
            if domain in seen_domains:
                raise ValueError("A keyed rate domain may appear only once per debit.")
            seen_domains.add(domain)
            try:
                hmac_domain = _KEYED_RATE_DOMAINS[domain]
            except KeyError as exc:
                raise ValueError("Unsupported keyed rate-limit domain.") from exc
            if not isinstance(raw_key, str) or not raw_key:
                raise ValueError("A bounded positive keyed rate key is required.")
            if not isinstance(limit, int) or not 1 <= limit <= 100_000:
                raise ValueError("A bounded positive keyed rate limit is required.")
            if not 1 <= float(window_s) <= 86400:
                raise ValueError("A bounded keyed rate window is required.")
            candidates = self._keyring.candidates(hmac_domain, raw_key)
            current_id, current_digest = self._keyring.digest(hmac_domain, raw_key)
            prepared.append(
                (
                    domain,
                    limit,
                    float(window_s),
                    tuple((item.key_id, item.digest) for item in candidates),
                    current_id,
                    current_digest,
                )
            )

        return self._consume_prepared(prepared, observed_at=observed_at)

    def consume_review_account(
        self,
        *,
        phase: str,
        client_ip: str,
        account_candidates: tuple[HMACDigest, ...],
        account_current: HMACDigest,
        ip_limit: int,
        account_limit: int,
        window_s: float,
        now: float | None = None,
    ) -> MultiRateLimitDecision:
        """Atomically debit one raw review IP and one protected account key.

        This deliberately narrow entry point is the only path that accepts an
        already-protected key. It prevents review-account digests persisted in
        challenges from being HMACed a second time while keeping the general
        throttle API raw-key-only.
        """

        try:
            ip_domain, account_domain = {
                "start": ("review-auth-start-ip", "review-auth-start-account"),
                "exchange": (
                    "review-auth-exchange-ip",
                    "review-auth-exchange-account",
                ),
            }[phase]
        except (KeyError, TypeError) as exc:
            raise ValueError("A closed review rate-limit phase is required.") from exc
        if not isinstance(client_ip, str) or not client_ip:
            raise ValueError("A bounded positive keyed rate key is required.")
        for limit in (ip_limit, account_limit):
            if not isinstance(limit, int) or not 1 <= limit <= 100_000:
                raise ValueError("A bounded positive keyed rate limit is required.")
        if not 1 <= float(window_s) <= 86400:
            raise ValueError("A bounded keyed rate window is required.")
        if not isinstance(account_candidates, tuple) or not account_candidates:
            raise ValueError("Protected review-account candidates are required.")
        if (
            any(
                not isinstance(item, HMACDigest)
                for item in account_candidates
            )
            or not isinstance(account_current, HMACDigest)
        ):
            raise ValueError("Protected review-account candidates are invalid.")
        protected = tuple((item.key_id, item.digest) for item in account_candidates)
        if (
            any(
                _PROTECTED_KEY_ID.fullmatch(item.key_id) is None
                or _PROTECTED_DIGEST.fullmatch(item.digest) is None
                for item in account_candidates
            )
            or len({item.key_id for item in account_candidates})
            != len(account_candidates)
            or (account_current.key_id, account_current.digest) not in protected
        ):
            raise ValueError("Protected review-account candidates are invalid.")
        self._keyring.require_key_ids(item.key_id for item in account_candidates)
        ip_hmac_domain = _KEYED_RATE_DOMAINS[ip_domain]
        ip_candidates = self._keyring.candidates(ip_hmac_domain, client_ip)
        ip_current_id, ip_current_digest = self._keyring.digest(
            ip_hmac_domain, client_ip
        )
        prepared: list[_PreparedRateEntry] = [
            (
                ip_domain,
                ip_limit,
                float(window_s),
                tuple((item.key_id, item.digest) for item in ip_candidates),
                ip_current_id,
                ip_current_digest,
            ),
            (
                account_domain,
                account_limit,
                float(window_s),
                protected,
                account_current.key_id,
                account_current.digest,
            ),
        ]
        observed_at = time.time() if now is None else float(now)
        return self._consume_prepared(prepared, observed_at=observed_at)

    def _consume_prepared(
        self, prepared: list[_PreparedRateEntry], *, observed_at: float
    ) -> MultiRateLimitDecision:

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                denied_for: list[int] = []
                for (
                    domain,
                    limit,
                    window_s,
                    candidates,
                    _current_id,
                    _current_digest,
                ) in prepared:
                    cutoff = observed_at - window_s
                    clauses = " OR ".join(
                        "(key_id = ? AND key_digest = ?)" for _ in candidates
                    )
                    parameters: list[object] = [domain, cutoff]
                    for key_id, digest in candidates:
                        parameters.extend((key_id, digest))
                    self._conn.execute(
                        "DELETE FROM mobile_rate_limit_events"
                        " WHERE domain = ? AND occurred_at <= ? AND ("
                        + clauses
                        + ")",
                        parameters,
                    )
                    rows = self._conn.execute(
                        "SELECT occurred_at FROM mobile_rate_limit_events"
                        " WHERE domain = ? AND occurred_at > ? AND ("
                        + clauses
                        + ") ORDER BY occurred_at",
                        parameters,
                    ).fetchall()
                    if len(rows) >= limit:
                        oldest_relevant = float(rows[len(rows) - limit][0])
                        denied_for.append(
                            max(
                                1,
                                min(
                                    int(math.ceil(window_s)),
                                    int(
                                        math.ceil(
                                            oldest_relevant + window_s - observed_at
                                        )
                                    ),
                                ),
                            )
                        )
                if denied_for:
                    self._conn.commit()
                    return MultiRateLimitDecision(False, max(denied_for))
                self._conn.executemany(
                    "INSERT INTO mobile_rate_limit_events"
                    " (domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
                    [
                        (domain, current_id, current_digest, observed_at)
                        for (
                            domain,
                            _limit,
                            _window_s,
                            _candidates,
                            current_id,
                            current_digest,
                        ) in prepared
                    ],
                )
                self._conn.commit()
                return MultiRateLimitDecision(True)
            except Exception:
                self._conn.rollback()
                raise

    def purge_expired(
        self, *, now: float | None = None, batch_size: int = 500
    ) -> int:
        if not 1 <= int(batch_size) <= 10_000:
            raise ValueError("A bounded purge batch is required.")
        observed_at = time.time() if now is None else float(now)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                cursor = self._conn.execute(
                    "DELETE FROM mobile_rate_limit_events WHERE rowid IN ("
                    " SELECT rowid FROM mobile_rate_limit_events"
                    " WHERE occurred_at <= ? ORDER BY occurred_at LIMIT ?)",
                    (observed_at - 86400, int(batch_size)),
                )
                self._conn.commit()
                return int(cursor.rowcount)
            except Exception:
                self._conn.rollback()
                raise
