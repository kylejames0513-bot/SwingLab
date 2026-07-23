"""User accounts and plans.

Same SQLite file as the jobs table (its own connection). Passwords are hashed
with stdlib scrypt — no external auth service, so accounts work anywhere the
app runs. Plan state ("free"/"pro" + Stripe subscription status) lives here
and is updated by the Stripe webhook in billing.py.
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
    subscription_status TEXT NOT NULL DEFAULT 'none'
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

    @property
    def is_pro(self) -> bool:
        return self.plan == PRO and self.subscription_status in _PRO_OK_STATUSES


class UserStore:
    def __init__(self, db_path: str | Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)

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

    def _from_row(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            created_at=row["created_at"],
            stripe_customer_id=row["stripe_customer_id"],
            plan=row["plan"],
            subscription_status=row["subscription_status"],
        )
