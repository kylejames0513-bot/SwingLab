"""User accounts and plans.

Same SQLite file as the jobs table (its own connection). Passwords are hashed
with stdlib scrypt — no external auth service, so accounts work anywhere the
app runs. Plan state lives here and only webhooks change it: a Stripe
subscription (billing.py) sets ``plan``/``subscription_status``, and Shopify
purchases (shopify_billing.py) extend the time-boxed ``pro_until`` — either
one makes ``is_pro`` true. Shopify purchases made before the buyer has an
account wait in ``pro_grants`` until that email signs up or logs in, and
processed orders are remembered in ``shopify_orders`` so replayed webhooks
can't double-grant (and cancellations know how much to take back).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

FREE = "free"
PRO = "pro"

# Subscription statuses that keep Pro features on. past_due keeps access
# during Stripe's retry window instead of cutting a paying customer off over
# one bounced charge.
_PRO_OK_STATUSES = ("active", "trialing", "past_due")

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 16384, 8, 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    created_at          REAL NOT NULL,
    stripe_customer_id  TEXT,
    plan                TEXT NOT NULL DEFAULT 'free',
    subscription_status TEXT NOT NULL DEFAULT 'none',
    pro_until           REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pro_grants (
    email TEXT PRIMARY KEY,
    days  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS shopify_orders (
    order_id   TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    days       REAL NOT NULL,
    applied_at REAL NOT NULL
);
"""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


@dataclass
class User:
    id: str
    email: str
    created_at: float
    stripe_customer_id: str | None = None
    plan: str = FREE
    subscription_status: str = "none"
    pro_until: float = 0.0

    @property
    def is_pro(self) -> bool:
        if self.plan == PRO and self.subscription_status in _PRO_OK_STATUSES:
            return True
        return self.pro_until > time.time()


class UserStore:
    def __init__(self, db_path: str | Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # Databases created before Shopify billing lack pro_until.
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(users)")
            }
            if "pro_until" not in columns:
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN pro_until REAL NOT NULL DEFAULT 0"
                )
                self._conn.commit()

    # -- signup / login ---------------------------------------------------
    def create(self, email: str, password: str) -> User:
        email = email.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise ValueError("That doesn't look like an email address.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        user = User(id=uuid.uuid4().hex[:12], email=email, created_at=time.time())
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO users (id, email, password_hash, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (user.id, email, hash_password(password), user.created_at),
                )
                self._conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("An account with that email already exists — log in.")
        return user

    def authenticate(self, email: str, password: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        return self._from_row(row)

    # -- lookup -----------------------------------------------------------
    def get(self, user_id: str) -> User | None:
        return self._one("id", user_id)

    def get_by_email(self, email: str) -> User | None:
        return self._one("email", email.strip().lower())

    def get_by_customer(self, customer_id: str) -> User | None:
        return self._one("stripe_customer_id", customer_id)

    def _one(self, column: str, value: str | None) -> User | None:
        if not value:
            return None
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM users WHERE {column} = ?", (value,)
            ).fetchone()
        return self._from_row(row) if row else None

    # -- plan updates (called by the Stripe webhook) ----------------------
    def set_customer(self, user_id: str, customer_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
                (customer_id, user_id),
            )
            self._conn.commit()

    def set_plan(self, user_id: str, plan: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET plan = ?, subscription_status = ? WHERE id = ?",
                (plan, status, user_id),
            )
            self._conn.commit()

    # -- time-boxed Pro (called by the Shopify webhook) --------------------
    def grant_pro_days(self, user_id: str, days: float) -> None:
        """Extend Pro: from now for lapsed/free accounts, stacked on top of
        the remaining time for active ones (buying early never loses days)."""
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT pro_until FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if row is None:
                return
            base = max(now, row["pro_until"] or 0.0)
            self._conn.execute(
                "UPDATE users SET pro_until = ? WHERE id = ?",
                (base + days * 86400, user_id),
            )
            self._conn.commit()

    def revoke_pro_days(self, user_id: str, days: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET pro_until = MAX(0, pro_until - ?) WHERE id = ?",
                (days * 86400, user_id),
            )
            self._conn.commit()

    def add_pending_grant(self, email: str, days: float) -> None:
        """Park purchased days for an email with no account yet."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO pro_grants (email, days) VALUES (?, ?)"
                " ON CONFLICT(email) DO UPDATE SET days = days + excluded.days",
                (email.strip().lower(), days),
            )
            self._conn.commit()

    def pop_pending_grant(self, email: str) -> float:
        """Claim (and clear) any parked days for this email."""
        email = email.strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT days FROM pro_grants WHERE email = ?", (email,)
            ).fetchone()
            if row is None:
                return 0.0
            self._conn.execute("DELETE FROM pro_grants WHERE email = ?", (email,))
            self._conn.commit()
        return row["days"]

    def reduce_pending_grant(self, email: str, days: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pro_grants SET days = MAX(0, days - ?) WHERE email = ?",
                (days, email.strip().lower()),
            )
            self._conn.commit()

    def record_order(self, order_id: str, email: str, days: float) -> bool:
        """Remember a granted order. False when already recorded — the
        webhook was replayed and must not grant twice."""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO shopify_orders"
                " (order_id, email, days, applied_at) VALUES (?, ?, ?, ?)",
                (order_id, email.strip().lower(), days, time.time()),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def void_order(self, order_id: str) -> tuple[str, float]:
        """Zero out a cancelled order, returning what it had granted (the
        email and days) so the caller can take the days back. ("", 0) for
        unknown or already-voided orders — cancellations replay too."""
        with self._lock:
            row = self._conn.execute(
                "SELECT email, days FROM shopify_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if row is None or row["days"] <= 0:
                return ("", 0.0)
            self._conn.execute(
                "UPDATE shopify_orders SET days = 0 WHERE order_id = ?", (order_id,)
            )
            self._conn.commit()
        return (row["email"], row["days"])

    def _from_row(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            created_at=row["created_at"],
            stripe_customer_id=row["stripe_customer_id"],
            plan=row["plan"],
            subscription_status=row["subscription_status"],
            pro_until=row["pro_until"] or 0.0,
        )
