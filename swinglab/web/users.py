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
``gear_orders`` is the same idea for everything that ISN'T Pro: each paid
order's gear line items are recorded once (replay-idempotent per order,
cancellations mark rows rather than lose the audit trail) so the
gear-attach KPI (swinglab.kpis) has real numbers to stand on.

Accounts can also *start on the Shopify store*: customer webhooks
(shopify_billing.py) upsert a passwordless "store account" — a stub row with
``shopify_customer_id`` set, ``source='shopify'``, and an empty password
hash. A stub can never log in with a password (an empty hash verifies
nothing). It is claimed in one of two ways, both landing on the SAME row so
the Shopify link and any Pro time granted by order webhooks carry over with
no duplicate user: signing up with its email sets a password in place
(:meth:`create`), or — with email delivery configured — signing in with an emailed
one-time code marks the email verified (:meth:`verify_email_signin`), which
is strictly stronger proof of ownership than the password path. An account
is *claimed* once it has a password OR a verified email; only rows with
neither are unclaimed stubs. Emails are normalized (trimmed + lowercased)
everywhere so store and app spellings always match.

``email_codes`` backs the optional email flows (mailer.py): 6-digit one-time
codes, stored hashed, 10-minute expiry, single-use, rate-limited per email —
used for email-code sign-in, to verify claims at signup, and to reset
passwords when email is configured.
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

# One-time email codes (claim verification, password reset).
CODE_TTL_S = 600  # codes live 10 minutes
CODE_RESEND_S = 60  # at most one new code per email/purpose per minute
CODE_MAX_ATTEMPTS = 5  # then the code is burned and must be re-requested

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    created_at          REAL NOT NULL,
    stripe_customer_id  TEXT,
    plan                TEXT NOT NULL DEFAULT 'free',
    subscription_status TEXT NOT NULL DEFAULT 'none',
    pro_until           REAL NOT NULL DEFAULT 0,
    shopify_customer_id TEXT,
    source              TEXT,
    digest_opt_in       INTEGER NOT NULL DEFAULT 0,
    digest_last_sent_at REAL,
    email_verified_at   REAL
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
CREATE TABLE IF NOT EXISTS gear_orders (
    order_id     TEXT NOT NULL,
    sku          TEXT NOT NULL,
    title        TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    email        TEXT NOT NULL,
    created_at   REAL NOT NULL,
    cancelled_at REAL
);
CREATE INDEX IF NOT EXISTS gear_orders_order ON gear_orders(order_id);
CREATE TABLE IF NOT EXISTS email_codes (
    email      TEXT NOT NULL,
    purpose    TEXT NOT NULL,
    code_hash  TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (email, purpose)
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
    shopify_customer_id: str | None = None
    source: str | None = None  # 'shopify' for accounts born from a customer webhook
    has_password: bool = True  # False = no password set (stub, or code sign-in only)
    digest_opt_in: bool = False  # weekly practice-plan email consent (off by default)
    digest_last_sent_at: float | None = None  # last digest send (claimed pre-send)
    email_verified_at: float | None = None  # first successful code sign-in/claim

    @property
    def is_pro(self) -> bool:
        if self.plan == PRO and self.subscription_status in _PRO_OK_STATUSES:
            return True
        return self.pro_until > time.time()

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def claimed(self) -> bool:
        """Someone has proven this account is theirs — by setting a password
        or by signing in with an emailed code. False = an untouched store
        stub (provisioned by a webhook, never used by its owner)."""
        return self.has_password or self.email_verified


class UserStore:
    def __init__(self, db_path: str | Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # Upgrade older databases in place (idempotent — each column is
            # added once): pre-Shopify-billing files lack pro_until, and
            # pre-account-sync files lack the store-account columns.
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(users)")
            }
            for name, ddl in (
                ("pro_until", "pro_until REAL NOT NULL DEFAULT 0"),
                ("shopify_customer_id", "shopify_customer_id TEXT"),
                ("source", "source TEXT"),
                # pre-digest files lack the weekly-email consent columns
                ("digest_opt_in", "digest_opt_in INTEGER NOT NULL DEFAULT 0"),
                ("digest_last_sent_at", "digest_last_sent_at REAL"),
                # pre-passwordless files lack the email-verified stamp
                ("email_verified_at", "email_verified_at REAL"),
            ):
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE users ADD COLUMN {ddl}")
                    self._conn.commit()

    # -- signup / login ---------------------------------------------------
    @staticmethod
    def validate_email(email: str) -> str:
        """Check an email looks like one, returning it normalized."""
        email = email.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise ValueError("That doesn't look like an email address.")
        return email

    @classmethod
    def validate_signup(cls, email: str, password: str) -> str:
        """Check signup input, returning the normalized email."""
        email = cls.validate_email(email)
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return email

    def create(self, email: str, password: str) -> User:
        """Create an account — or, when the email belongs to a row with no
        password yet (an unclaimed store stub from upsert_store_customer,
        or a code-only passwordless account), set the password on the SAME
        row, so its Shopify link and any Pro time already granted by order
        webhooks stay with the user. No duplicates. The web layer requires
        an emailed code first when email delivery is configured — see app.py."""
        email = self.validate_signup(email, password)
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM users WHERE email = ?", (email,)
                ).fetchone()
                if row is not None:
                    if row["password_hash"]:
                        raise ValueError(
                            "An account with that email already exists — log in."
                        )
                    self._conn.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(password), row["id"]),
                    )
                    self._conn.commit()
                    row = self._conn.execute(
                        "SELECT * FROM users WHERE id = ?", (row["id"],)
                    ).fetchone()
                    return self._from_row(row)
                user = User(
                    id=uuid.uuid4().hex[:12], email=email, created_at=time.time()
                )
                self._conn.execute(
                    "INSERT INTO users (id, email, password_hash, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (user.id, email, hash_password(password), user.created_at),
                )
                self._conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("An account with that email already exists — log in.")
        return user

    def set_password(self, user_id: str, password: str) -> None:
        """Reset/replace a password (used by the emailed-code reset flow)."""
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        with self._lock:
            self._conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user_id),
            )
            self._conn.commit()

    def authenticate(self, email: str, password: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        return self._from_row(row)

    def verify_email_signin(self, email: str) -> User:
        """A sign-in code for this email was just entered correctly — the
        one moment all three account states converge. Existing account:
        returned as-is. Unclaimed store stub: claimed in place (the code is
        proof of inbox ownership, so the Shopify link and anything bought
        stay put). No account at all: a passwordless one is created. Either
        way the email is stamped verified (first proof wins — the stamp is
        never moved)."""
        email = self.validate_email(email)
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO users (id, email, password_hash, created_at,"
                    " email_verified_at) VALUES (?, ?, '', ?, ?)",
                    (uuid.uuid4().hex[:12], email, now, now),
                )
            elif row["email_verified_at"] is None:
                self._conn.execute(
                    "UPDATE users SET email_verified_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
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

    # -- store accounts (called by the Shopify customer webhooks) ---------
    def get_by_shopify(self, customer_id: str) -> User | None:
        return self._one("shopify_customer_id", customer_id)

    def upsert_store_customer(self, email: str, customer_id: str | None) -> User:
        """Mirror a Shopify customer into the app (customers/create|update).

        No account for the email -> create a passwordless stub with
        source='shopify'. Account exists -> link/refresh its
        shopify_customer_id and touch NOTHING else — an existing password
        or email is never overwritten. Replays are naturally idempotent:
        re-applying the same customer lands on the same row.
        """
        email = email.strip().lower()
        with self._lock:
            if customer_id:
                # The store link is one-to-one and follows the store
                # customer — if their email changed store-side, detach the
                # id from whichever row held it before.
                self._conn.execute(
                    "UPDATE users SET shopify_customer_id = NULL"
                    " WHERE shopify_customer_id = ? AND email != ?",
                    (customer_id, email),
                )
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO users (id, email, password_hash, created_at,"
                    " shopify_customer_id, source)"
                    " VALUES (?, ?, '', ?, ?, 'shopify')",
                    (uuid.uuid4().hex[:12], email, time.time(), customer_id),
                )
            elif customer_id and row["shopify_customer_id"] != customer_id:
                self._conn.execute(
                    "UPDATE users SET shopify_customer_id = ? WHERE id = ?",
                    (customer_id, row["id"]),
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
        return self._from_row(row)

    def unlink_shopify(self, user_id: str, clear_source: bool = False) -> None:
        """Drop the store link (customers/delete on a claimed account);
        clear_source additionally erases the shopify-sourced profile field
        for customers/redact."""
        sets = "shopify_customer_id = NULL"
        if clear_source:
            sets += ", source = NULL"
        with self._lock:
            self._conn.execute(
                f"UPDATE users SET {sets} WHERE id = ?", (user_id,)
            )
            self._conn.commit()

    def delete_user(self, user_id: str) -> None:
        """Remove a user row outright. Callers must only do this for
        unclaimed stubs — see shopify_billing for the guard rails."""
        with self._lock:
            self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self._conn.commit()

    def has_activity(self, user_id: str) -> bool:
        """Any analyses on this account? The jobs table lives in the same
        SQLite file when running under the web app; a standalone user DB
        (no jobs table) trivially has none."""
        with self._lock:
            table = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).fetchone()
            if table is None:
                return False
            row = self._conn.execute(
                "SELECT 1 FROM jobs WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
        return row is not None

    def has_unclaimed_value(self, email: str) -> bool:
        """Does signup with this email set a password on an EXISTING row —
        an unclaimed store stub, a code-only passwordless account, or a Pro
        purchase parked before signup? With email delivery on, such signups must
        prove control of the inbox first. (An account that already has a
        password returns False: signup against it fails outright, no code
        needed.)"""
        email = email.strip().lower()
        user = self.get_by_email(email)
        if user is not None:
            return not user.has_password
        return self.pending_grant_days(email) > 0

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

    def pending_grant_days(self, email: str) -> float:
        """Peek at parked days without claiming them."""
        with self._lock:
            row = self._conn.execute(
                "SELECT days FROM pro_grants WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
        return row["days"] if row else 0.0

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

    # -- gear order ledger (called by the Shopify webhook) -----------------
    def record_gear_order(
        self, order_id: str, email: str, items: list[tuple[str, str, int]]
    ) -> bool:
        """Remember an order's non-Pro (gear) line items — the raw material
        for the gear-attach KPI (swinglab.kpis). ``items`` is a list of
        (sku, title, quantity). Same replay rule as record_order, keyed per
        order: False (and no writes) when the order was already recorded,
        so a re-delivered webhook can never double-count a sale."""
        now = time.time()
        with self._lock:
            seen = self._conn.execute(
                "SELECT 1 FROM gear_orders WHERE order_id = ? LIMIT 1",
                (order_id,),
            ).fetchone()
            if seen is not None:
                return False
            for sku, title, quantity in items:
                self._conn.execute(
                    "INSERT INTO gear_orders"
                    " (order_id, sku, title, quantity, email, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (order_id, str(sku), str(title), int(quantity),
                     email.strip().lower(), now),
                )
            self._conn.commit()
        return True

    def cancel_gear_order(self, order_id: str) -> int:
        """Mark a cancelled order's gear rows (kept for audit, excluded
        from the attach KPI). Idempotent — a replayed cancellation, or one
        for an order with no gear, changes nothing. Returns rows newly
        marked."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE gear_orders SET cancelled_at = ?"
                " WHERE order_id = ? AND cancelled_at IS NULL",
                (time.time(), order_id),
            )
            self._conn.commit()
        return cursor.rowcount

    # -- weekly digest consent (see swinglab.web.digest) ------------------
    def set_digest_opt_in(self, user_id: str, opt_in: bool) -> None:
        """Flip the weekly practice-plan email consent (signup checkbox,
        account toggle, and the one-click unsubscribe link)."""
        with self._lock:
            self._conn.execute(
                "UPDATE users SET digest_opt_in = ? WHERE id = ?",
                (1 if opt_in else 0, user_id),
            )
            self._conn.commit()

    def digest_optins(self) -> list[User]:
        """Everyone who asked for the weekly email, oldest account first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users WHERE digest_opt_in = 1 ORDER BY created_at"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def claim_digest_send(self, user_id: str, now: float, interval_s: float) -> bool:
        """Atomically claim this week's digest send by stamping
        digest_last_sent_at BEFORE any email goes out. True = this caller
        owns the send. The stamp-first order means a crash between claim
        and send skips a week rather than ever double-sending within one."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE users SET digest_last_sent_at = ?"
                " WHERE id = ? AND digest_opt_in = 1"
                " AND (digest_last_sent_at IS NULL OR digest_last_sent_at <= ?)",
                (now, user_id, now - interval_s),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    # -- one-time email codes (claim verification, password reset) --------
    @staticmethod
    def _hash_code(email: str, purpose: str, code: str) -> str:
        # Tied to email+purpose so a code only works where it was issued.
        return hashlib.sha256(f"{email}|{purpose}|{code}".encode()).hexdigest()

    def issue_email_code(self, email: str, purpose: str) -> str | None:
        """Mint a fresh 6-digit code (the caller emails it). Returns None —
        and keeps the outstanding code valid — when one was already issued
        in the last CODE_RESEND_S seconds, so an email can't be flooded."""
        email = email.strip().lower()
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at, expires_at FROM email_codes"
                " WHERE email = ? AND purpose = ?",
                (email, purpose),
            ).fetchone()
            if (
                row is not None
                and now - row["created_at"] < CODE_RESEND_S
                and row["expires_at"] > now
            ):
                return None
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._conn.execute(
                "INSERT OR REPLACE INTO email_codes"
                " (email, purpose, code_hash, created_at, expires_at, attempts)"
                " VALUES (?, ?, ?, ?, ?, 0)",
                (email, purpose, self._hash_code(email, purpose, code),
                 now, now + CODE_TTL_S),
            )
            self._conn.commit()
        return code

    def check_email_code(self, email: str, purpose: str, code: str) -> bool:
        """Verify and consume a code: single-use (deleted on success),
        expired codes never match, and CODE_MAX_ATTEMPTS wrong guesses
        burn the code so it can't be brute-forced."""
        email = email.strip().lower()
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT code_hash, expires_at, attempts FROM email_codes"
                " WHERE email = ? AND purpose = ?",
                (email, purpose),
            ).fetchone()
            if row is None:
                return False
            expected = self._hash_code(email, purpose, code.strip())
            ok = (
                row["expires_at"] > now
                and hmac.compare_digest(expected, row["code_hash"])
            )
            if ok or row["expires_at"] <= now or row["attempts"] + 1 >= CODE_MAX_ATTEMPTS:
                self._conn.execute(
                    "DELETE FROM email_codes WHERE email = ? AND purpose = ?",
                    (email, purpose),
                )
            else:
                self._conn.execute(
                    "UPDATE email_codes SET attempts = attempts + 1"
                    " WHERE email = ? AND purpose = ?",
                    (email, purpose),
                )
            self._conn.commit()
        return ok

    def discard_email_code(self, email: str, purpose: str, code: str) -> bool:
        """Remove exactly the code a caller failed to deliver.

        Matching the hash prevents a slow failed sender from deleting a newer
        replacement code issued by another request.
        """
        email = email.strip().lower()
        code_hash = self._hash_code(email, purpose, code)
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM email_codes"
                " WHERE email = ? AND purpose = ? AND code_hash = ?",
                (email, purpose, code_hash),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def _from_row(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            created_at=row["created_at"],
            stripe_customer_id=row["stripe_customer_id"],
            plan=row["plan"],
            subscription_status=row["subscription_status"],
            pro_until=row["pro_until"] or 0.0,
            shopify_customer_id=row["shopify_customer_id"],
            source=row["source"],
            has_password=bool(row["password_hash"]),
            digest_opt_in=bool(row["digest_opt_in"]),
            digest_last_sent_at=row["digest_last_sent_at"],
            email_verified_at=row["email_verified_at"],
        )
