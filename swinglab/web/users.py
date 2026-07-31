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
everywhere. Shopify's customer id remains the durable cross-system link:
an unclaimed store-only stub may follow a changed Shopify email in place,
while a claimed account keeps its verified app login email until a
separate verified email-change flow is completed.

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
_LEGACY_REPLAY_REPAIR_WINDOW_S = 7 * 86400

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
    shopify_identity_locked INTEGER NOT NULL DEFAULT 0,
    shopify_updated_at  REAL,
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
    order_id            TEXT PRIMARY KEY,
    email               TEXT NOT NULL,
    days                REAL NOT NULL,
    applied_at          REAL NOT NULL,
    user_id             TEXT,
    shopify_customer_id TEXT,
    grant_chain         TEXT,
    grant_start         REAL,
    grant_end           REAL,
    pending_days        REAL NOT NULL DEFAULT 0,
    grant_ambiguous     INTEGER NOT NULL DEFAULT 0,
    cancelled_at        REAL
);
CREATE TABLE IF NOT EXISTS shopify_customer_tombstones (
    customer_id   TEXT PRIMARY KEY,
    redacted      INTEGER NOT NULL DEFAULT 0,
    deleted_at    REAL NOT NULL,
    former_user_id TEXT
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
    shopify_identity_locked: bool = False
    shopify_updated_at: float | None = None
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
            try:
                # Serialize schema inspection + ALTER across multiple app
                # processes. A per-instance threading lock cannot prevent
                # two connections from observing the same missing column.
                self._conn.execute("BEGIN IMMEDIATE")
                # Upgrade older databases in place (idempotent — each column
                # is added once): pre-Shopify-billing files lack pro_until,
                # and pre-account-sync files lack the store-account columns.
                columns = {
                    row["name"]
                    for row in self._conn.execute("PRAGMA table_info(users)")
                }
                for name, ddl in (
                    ("pro_until", "pro_until REAL NOT NULL DEFAULT 0"),
                    ("shopify_customer_id", "shopify_customer_id TEXT"),
                    (
                        "shopify_identity_locked",
                        "shopify_identity_locked INTEGER NOT NULL DEFAULT 0",
                    ),
                    ("shopify_updated_at", "shopify_updated_at REAL"),
                    ("source", "source TEXT"),
                    # pre-digest files lack weekly-email consent columns
                    ("digest_opt_in", "digest_opt_in INTEGER NOT NULL DEFAULT 0"),
                    ("digest_last_sent_at", "digest_last_sent_at REAL"),
                    # pre-passwordless files lack the verified stamp
                    ("email_verified_at", "email_verified_at REAL"),
                ):
                    if name not in columns:
                        self._conn.execute(f"ALTER TABLE users ADD COLUMN {ddl}")
                order_columns = {
                    row["name"]
                    for row in self._conn.execute(
                        "PRAGMA table_info(shopify_orders)"
                    )
                }
                pending_days_added = "pending_days" not in order_columns
                for name, ddl in (
                    ("user_id", "user_id TEXT"),
                    ("shopify_customer_id", "shopify_customer_id TEXT"),
                    ("grant_chain", "grant_chain TEXT"),
                    ("grant_start", "grant_start REAL"),
                    ("grant_end", "grant_end REAL"),
                    (
                        "pending_days",
                        "pending_days REAL NOT NULL DEFAULT 0",
                    ),
                    (
                        "grant_ambiguous",
                        "grant_ambiguous INTEGER NOT NULL DEFAULT 0",
                    ),
                    ("cancelled_at", "cancelled_at REAL"),
                ):
                    if name not in order_columns:
                        self._conn.execute(
                            f"ALTER TABLE shopify_orders ADD COLUMN {ddl}"
                        )
                tombstone_columns = {
                    row["name"]
                    for row in self._conn.execute(
                        "PRAGMA table_info(shopify_customer_tombstones)"
                    )
                }
                if "former_user_id" not in tombstone_columns:
                    self._conn.execute(
                        "ALTER TABLE shopify_customer_tombstones"
                        " ADD COLUMN former_user_id TEXT"
                    )
                # A legacy order can be assigned to the matching user only
                # when its days are not still parked. Pending rows remain
                # user_id=NULL until claim_pending_grant moves them.
                self._conn.execute(
                    "UPDATE shopify_orders"
                    " SET user_id = ("
                    "   SELECT users.id FROM users"
                    "   WHERE users.email = shopify_orders.email"
                    " )"
                    " WHERE user_id IS NULL"
                    " AND EXISTS ("
                    "   SELECT 1 FROM users"
                    "   WHERE users.email = shopify_orders.email"
                    " )"
                    " AND ("
                    "   NOT EXISTS ("
                    "     SELECT 1 FROM pro_grants"
                    "     WHERE pro_grants.email = shopify_orders.email"
                    "   )"
                    "   OR applied_at >= ("
                    "     SELECT users.created_at FROM users"
                    "     WHERE users.email = shopify_orders.email"
                    "   )"
                    " )"
                )
                self._conn.execute(
                    "UPDATE shopify_orders"
                    " SET shopify_customer_id = ("
                    "   SELECT users.shopify_customer_id FROM users"
                    "   WHERE users.id = shopify_orders.user_id"
                    " )"
                    " WHERE shopify_customer_id IS NULL AND user_id IS NOT NULL"
                )
                # Once an app account has been associated with Shopify, keep
                # that fact even if a privacy/delete webhook later removes
                # the customer id itself. This non-PII marker prevents a
                # different Shopify customer who happens to reuse the email
                # from taking over the former account.
                self._conn.execute(
                    "UPDATE users SET shopify_identity_locked = 1"
                    " WHERE shopify_customer_id IS NOT NULL"
                    "    OR source = 'shopify'"
                    "    OR EXISTS ("
                    "      SELECT 1 FROM shopify_orders"
                    "      WHERE shopify_orders.user_id = users.id"
                    "        AND shopify_orders.shopify_customer_id IS NOT NULL"
                    "    )"
                )
                # Recover the exact former-account mapping when older order
                # provenance proves it. Never guess from email.
                self._conn.execute(
                    "UPDATE shopify_customer_tombstones"
                    " SET former_user_id = ("
                    "   SELECT shopify_orders.user_id"
                    "   FROM shopify_orders"
                    "   WHERE shopify_orders.shopify_customer_id ="
                    "     shopify_customer_tombstones.customer_id"
                    "     AND shopify_orders.user_id IS NOT NULL"
                    "   ORDER BY shopify_orders.applied_at DESC LIMIT 1"
                    " )"
                    " WHERE redacted = 0 AND former_user_id IS NULL"
                    "   AND EXISTS ("
                    "     SELECT 1 FROM shopify_orders"
                    "     WHERE shopify_orders.shopify_customer_id ="
                    "       shopify_customer_tombstones.customer_id"
                    "       AND shopify_orders.user_id IS NOT NULL"
                    "   )"
                )
                self._backfill_shopify_grant_provenance(
                    backfill_pending=pending_days_added
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _backfill_shopify_grant_provenance(
        self, backfill_pending: bool
    ) -> None:
        """Reconstruct order-level grant ownership for older databases.

        Before grant intervals existed, stacked orders were only represented
        by the user's aggregate ``pro_until`` value. Rebuilding the same
        chronological chains lets a later cancellation remove its own
        surviving interval instead of guessing from ``applied_at`` alone.
        Pending pre-signup grants are attributed newest-first because the
        remaining value in a stacked chain belongs to its later orders.

        The caller holds ``BEGIN IMMEDIATE``; every update therefore lands
        atomically with the schema migration and is safe to repeat.
        """
        user_ids = self._conn.execute(
            "SELECT DISTINCT user_id FROM shopify_orders"
            " WHERE user_id IS NOT NULL AND days > 0"
            "   AND cancelled_at IS NULL"
        ).fetchall()
        for user_row in user_ids:
            user_id = user_row["user_id"]
            user_state = self._conn.execute(
                "SELECT created_at, pro_until FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if user_state is None:
                continue
            account_created_at = float(user_state["created_at"])
            cursor_end = None
            chain = None
            backfilled_orders = []
            all_backfilled_before_account = True
            orders = self._conn.execute(
                "SELECT order_id, days, applied_at, grant_chain,"
                " grant_start, grant_end"
                " FROM shopify_orders"
                " WHERE user_id = ? AND days > 0"
                "   AND cancelled_at IS NULL"
                " ORDER BY applied_at, order_id",
                (user_id,),
            ).fetchall()
            for order in orders:
                complete = (
                    order["grant_chain"] is not None
                    and order["grant_start"] is not None
                    and order["grant_end"] is not None
                )
                if complete:
                    chain = order["grant_chain"]
                    cursor_end = float(order["grant_end"])
                    continue

                paid_at = float(order["applied_at"])
                backfilled_orders.append(order["order_id"])
                all_backfilled_before_account = (
                    all_backfilled_before_account
                    and paid_at < account_created_at
                )
                # Orders bought before an account existed were parked and
                # only began their clock when signup claimed them.
                applied_at = max(paid_at, account_created_at)
                if cursor_end is not None and applied_at <= cursor_end:
                    grant_start = cursor_end
                else:
                    grant_start = applied_at
                    chain = f"legacy:{user_id}:{order['order_id']}"
                grant_end = grant_start + float(order["days"]) * 86400
                self._conn.execute(
                    "UPDATE shopify_orders"
                    " SET grant_chain = ?, grant_start = ?, grant_end = ?,"
                    "     pending_days = 0"
                    " WHERE order_id = ?",
                    (chain, grant_start, grant_end, order["order_id"]),
                )
                cursor_end = grant_end

            latest = self._conn.execute(
                "SELECT grant_chain, grant_end FROM shopify_orders"
                " WHERE user_id = ? AND days > 0"
                "   AND cancelled_at IS NULL"
                "   AND grant_chain IS NOT NULL AND grant_end IS NOT NULL"
                " ORDER BY grant_end DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if (
                latest is not None
                and user_state["pro_until"]
                and backfilled_orders
            ):
                # Normal legacy record_order -> grant_pro_days calls differed
                # by milliseconds. A later aggregate end also means a parked
                # order was claimed after it was paid, so move that final
                # chain forward to the authoritative account expiration.
                # Never move a chain backward by a large gap: that exposes
                # the old crash where the ledger committed but the grant did
                # not, and a paid replay must repair it.
                delta = float(user_state["pro_until"]) - float(
                    latest["grant_end"]
                )
                # A large positive gap is safe only when every order predates
                # the account: that is evidence the parked purchase began
                # when the account was eventually claimed. If the account
                # already existed, the gap can instead be unrelated Stripe,
                # manual, or promotional access—even with one order. Moving
                # the old order onto that tail would let cancellation steal
                # access it never granted.
                ambiguous = (
                    delta > 300 and not all_backfilled_before_account
                )
                if ambiguous:
                    for order_id in backfilled_orders:
                        self._conn.execute(
                            "UPDATE shopify_orders"
                            " SET grant_ambiguous = 1"
                            " WHERE order_id = ?",
                            (order_id,),
                        )
                elif delta > 0 or abs(delta) <= 300:
                    self._conn.execute(
                        "UPDATE shopify_orders"
                        " SET grant_start = grant_start + ?,"
                        "     grant_end = grant_end + ?"
                        " WHERE user_id = ? AND grant_chain = ?"
                        "   AND days > 0 AND cancelled_at IS NULL",
                        (delta, delta, user_id, latest["grant_chain"]),
                    )

        if not backfill_pending:
            return

        pending_grants = self._conn.execute(
            "SELECT email, days FROM pro_grants WHERE days > 0"
        ).fetchall()
        for grant in pending_grants:
            email = grant["email"]
            already_attributed = self._conn.execute(
                "SELECT COALESCE(SUM(pending_days), 0) AS days"
                " FROM shopify_orders"
                " WHERE email = ? AND user_id IS NULL AND days > 0"
                "   AND cancelled_at IS NULL",
                (email,),
            ).fetchone()
            remaining = max(
                0.0,
                float(grant["days"]) - float(already_attributed["days"]),
            )
            if remaining <= 0:
                continue
            orders = self._conn.execute(
                "SELECT order_id, days FROM shopify_orders"
                " WHERE email = ? AND user_id IS NULL AND days > 0"
                "   AND cancelled_at IS NULL AND pending_days <= 0"
                " ORDER BY applied_at DESC, order_id DESC",
                (email,),
            ).fetchall()
            for order in orders:
                allocated = min(float(order["days"]), remaining)
                if allocated <= 0:
                    break
                self._conn.execute(
                    "UPDATE shopify_orders SET pending_days = ?"
                    " WHERE order_id = ?",
                    (allocated, order["order_id"]),
                )
                remaining -= allocated

    def _move_customer_pending_to_email(
        self, customer_id: str, new_email: str
    ) -> None:
        """Move parked orders for one stable customer id to its current email.

        The caller owns the write transaction. Only each matching order's
        ``pending_days`` moves out of the old email aggregate; unrelated
        customers sharing that address stay untouched.
        """
        rows = self._conn.execute(
            "SELECT order_id, email, pending_days FROM shopify_orders"
            " WHERE user_id IS NULL AND shopify_customer_id = ?"
            "   AND days > 0 AND cancelled_at IS NULL AND email != ?"
            " ORDER BY email, applied_at, order_id",
            (customer_id, new_email),
        ).fetchall()
        by_email: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            by_email.setdefault(row["email"], []).append(row)
        for old_email, orders in by_email.items():
            amount = sum(float(row["pending_days"] or 0.0) for row in orders)
            if amount > 0:
                grant = self._conn.execute(
                    "SELECT days FROM pro_grants WHERE email = ?",
                    (old_email,),
                ).fetchone()
                available = float(grant["days"]) if grant is not None else 0.0
                if amount > available + 0.001:
                    raise RuntimeError(
                        "Shopify customer pending value exceeds its"
                        " email aggregate"
                    )
                self._conn.execute(
                    "UPDATE pro_grants SET days = MAX(0, days - ?)"
                    " WHERE email = ?",
                    (amount, old_email),
                )
                self._conn.execute(
                    "DELETE FROM pro_grants"
                    " WHERE email = ? AND days <= 0.000001",
                    (old_email,),
                )
                self._conn.execute(
                    "INSERT INTO pro_grants (email, days) VALUES (?, ?)"
                    " ON CONFLICT(email) DO UPDATE"
                    " SET days = days + excluded.days",
                    (new_email, amount),
                )
            for order in orders:
                self._conn.execute(
                    "UPDATE shopify_orders SET email = ? WHERE order_id = ?",
                    (new_email, order["order_id"]),
                )
                self._conn.execute(
                    "UPDATE gear_orders SET email = ? WHERE order_id = ?",
                    (new_email, order["order_id"]),
                )

    def _erase_customer_pending(
        self, customer_id: str | None, email: str
    ) -> None:
        """Remove only one customer's parked value during redaction."""
        if not customer_id or not email:
            return
        other_customer = self._conn.execute(
            "SELECT 1 FROM shopify_orders"
            " WHERE email = ? AND user_id IS NULL AND pending_days > 0"
            "   AND shopify_customer_id IS NOT NULL"
            "   AND shopify_customer_id != ? LIMIT 1",
            (email, customer_id),
        ).fetchone()
        rows = self._conn.execute(
            "SELECT order_id, pending_days FROM shopify_orders"
            " WHERE email = ? AND user_id IS NULL AND days > 0"
            "   AND cancelled_at IS NULL"
            "   AND (shopify_customer_id = ?"
            + (
                " OR shopify_customer_id IS NULL"
                if other_customer is None
                else ""
            )
            + ")",
            (email, customer_id),
        ).fetchall()
        amount = sum(float(row["pending_days"] or 0.0) for row in rows)
        grant = self._conn.execute(
            "SELECT days FROM pro_grants WHERE email = ?", (email,)
        ).fetchone()
        available = float(grant["days"]) if grant is not None else 0.0
        if amount > available + 0.001:
            raise RuntimeError(
                "Shopify redaction value exceeds its email aggregate"
            )
        if amount > 0:
            self._conn.execute(
                "UPDATE pro_grants SET days = MAX(0, days - ?)"
                " WHERE email = ?",
                (amount, email),
            )
            self._conn.execute(
                "DELETE FROM pro_grants"
                " WHERE email = ? AND days <= 0.000001",
                (email,),
            )
        for row in rows:
            self._conn.execute(
                "UPDATE shopify_orders SET pending_days = 0"
                " WHERE order_id = ?",
                (row["order_id"],),
            )

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

    def upsert_store_customer(
        self,
        email: str,
        customer_id: str | None,
        updated_at: float | None = None,
    ) -> User | None:
        """Mirror a Shopify customer into the app (customers/create|update).

        Shopify's customer id is the durable identity. A known id always
        keeps the same local user row, even when Shopify reports a different
        email. An unclaimed store-only stub may move to the new email in
        place; a claimed account keeps its verified login email until a
        separate verified email-change flow exists. We never auto-merge two
        local users merely because Shopify reports the same email.

        ``None`` means the customer id was previously deleted/redacted and a
        delayed create/update delivery was intentionally ignored.
        """
        email = email.strip().lower()
        with self._lock:
            try:
                # The transaction is the cross-process identity lock. Two
                # web workers cannot both decide that the same Shopify id is
                # new and create separate local users.
                self._conn.execute("BEGIN IMMEDIATE")
                if customer_id:
                    tombstone = self._conn.execute(
                        "SELECT 1 FROM shopify_customer_tombstones"
                        " WHERE customer_id = ?",
                        (customer_id,),
                    ).fetchone()
                    if tombstone is not None:
                        self._conn.rollback()
                        return None

                    linked = self._conn.execute(
                        "SELECT * FROM users WHERE shopify_customer_id = ?",
                        (customer_id,),
                    ).fetchone()
                    if linked is not None:
                        if (
                            linked["shopify_updated_at"] is not None
                            and (
                                (
                                    updated_at is not None
                                    and updated_at
                                    <= linked["shopify_updated_at"]
                                )
                                or (
                                    updated_at is None
                                    and linked["email"] != email
                                )
                            )
                        ):
                            self._conn.rollback()
                            return self._from_row(linked)
                        if linked["email"] != email:
                            target = self._conn.execute(
                                "SELECT id FROM users WHERE email = ?", (email,)
                            ).fetchone()
                            claimed = bool(
                                linked["password_hash"]
                                or linked["email_verified_at"] is not None
                            )
                            if not claimed and target is None:
                                old_email = linked["email"]
                                self._conn.execute(
                                    "UPDATE users SET email = ? WHERE id = ?",
                                    (email, linked["id"]),
                                )
                                # Move only value attributable to this
                                # Shopify identity. An email aggregate may
                                # also contain another customer's purchase;
                                # moving it wholesale would let one customer
                                # claim someone else's Pro time.
                                other_customer = self._conn.execute(
                                    "SELECT 1 FROM shopify_orders"
                                    " WHERE email = ? AND user_id IS NULL"
                                    "   AND pending_days > 0"
                                    "   AND shopify_customer_id IS NOT NULL"
                                    "   AND shopify_customer_id != ? LIMIT 1",
                                    (old_email, customer_id),
                                ).fetchone()
                                move_guest_orders = other_customer is None
                                order_rows = self._conn.execute(
                                    "SELECT order_id, user_id,"
                                    " shopify_customer_id, pending_days"
                                    " FROM shopify_orders WHERE email = ?"
                                    " AND ("
                                    "   user_id = ?"
                                    "   OR (user_id IS NULL AND ("
                                    "     shopify_customer_id = ?"
                                    + (
                                        " OR shopify_customer_id IS NULL"
                                        if move_guest_orders
                                        else ""
                                    )
                                    + "   ))"
                                    " )",
                                    (old_email, linked["id"], customer_id),
                                ).fetchall()
                                pending_to_move = sum(
                                    float(order["pending_days"] or 0.0)
                                    for order in order_rows
                                    if order["user_id"] is None
                                )
                                pending = self._conn.execute(
                                    "SELECT days FROM pro_grants WHERE email = ?",
                                    (old_email,),
                                ).fetchone()
                                available = (
                                    float(pending["days"])
                                    if pending is not None
                                    else 0.0
                                )
                                if pending_to_move > available + 0.001:
                                    raise RuntimeError(
                                        "Shopify pending-grant provenance"
                                        " exceeds its email aggregate"
                                    )
                                if pending_to_move > 0:
                                    self._conn.execute(
                                        "UPDATE pro_grants"
                                        " SET days = MAX(0, days - ?)"
                                        " WHERE email = ?",
                                        (pending_to_move, old_email),
                                    )
                                    self._conn.execute(
                                        "DELETE FROM pro_grants"
                                        " WHERE email = ? AND days <= 0.000001",
                                        (old_email,),
                                    )
                                    self._conn.execute(
                                        "INSERT INTO pro_grants (email, days)"
                                        " VALUES (?, ?)"
                                        " ON CONFLICT(email) DO UPDATE"
                                        " SET days = days + excluded.days",
                                        (email, pending_to_move),
                                    )
                                # An old-address code must not be able to
                                # claim a store-only account after Shopify
                                # moved it.
                                self._conn.execute(
                                    "DELETE FROM email_codes WHERE email = ?",
                                    (old_email,),
                                )
                                # Preserve whether each selected grant is
                                # pending or applied; only its identity email
                                # moves. Gear follows the same order ids.
                                for order in order_rows:
                                    self._conn.execute(
                                        "UPDATE shopify_orders"
                                        " SET email = ?,"
                                        " shopify_customer_id = COALESCE("
                                        "   shopify_customer_id, ?)"
                                        " WHERE order_id = ?",
                                        (
                                            email,
                                            customer_id,
                                            order["order_id"],
                                        ),
                                    )
                                    self._conn.execute(
                                        "UPDATE gear_orders SET email = ?"
                                        " WHERE order_id = ?",
                                        (email, order["order_id"]),
                                    )
                        if updated_at is not None:
                            self._conn.execute(
                                "UPDATE users SET shopify_updated_at = ?"
                                " WHERE id = ?",
                                (updated_at, linked["id"]),
                            )
                        self._conn.execute(
                            "UPDATE users SET shopify_identity_locked = 1"
                            " WHERE id = ?",
                            (linked["id"],),
                        )
                        self._move_customer_pending_to_email(
                            customer_id, email
                        )
                        self._conn.commit()
                        row = self._conn.execute(
                            "SELECT * FROM users WHERE id = ?", (linked["id"],)
                        ).fetchone()
                        return self._from_row(row)

                row = self._conn.execute(
                    "SELECT * FROM users WHERE email = ?", (email,)
                ).fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO users"
                        " (id, email, password_hash, created_at,"
                        "  shopify_customer_id, shopify_identity_locked,"
                        "  shopify_updated_at, source)"
                        " VALUES (?, ?, '', ?, ?, 1, ?, 'shopify')",
                        (
                            uuid.uuid4().hex[:12],
                            email,
                            time.time(),
                            customer_id,
                            updated_at,
                        ),
                    )
                elif (
                    customer_id
                    and row["shopify_customer_id"] is None
                    and not row["shopify_identity_locked"]
                ):
                    self._conn.execute(
                        "UPDATE users SET shopify_customer_id = ?,"
                        " shopify_identity_locked = 1,"
                        " shopify_updated_at = COALESCE(?, shopify_updated_at)"
                        " WHERE id = ?",
                        (customer_id, updated_at, row["id"]),
                    )
                if customer_id:
                    linked_row = self._conn.execute(
                        "SELECT shopify_customer_id FROM users"
                        " WHERE email = ?",
                        (email,),
                    ).fetchone()
                    if (
                        linked_row is not None
                        and linked_row["shopify_customer_id"] == customer_id
                    ):
                        self._move_customer_pending_to_email(
                            customer_id, email
                        )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM users WHERE email = ?", (email,)
                ).fetchone()
                return self._from_row(row)
            except Exception:
                self._conn.rollback()
                raise

    def remove_shopify_customer(
        self,
        customer_id: str | None,
        email: str,
        redact: bool = False,
    ) -> str:
        """Apply customer delete/redact as one serialized transaction.

        The return value is ``"deleted"``, ``"unlinked"``, or ``"unknown"``.
        Tombstoning, the final claimed/activity decision, account mutation,
        and pending-entitlement update commit together. This removes crash
        and concurrent webhook windows that could otherwise lose or
        resurrect paid access.
        """
        customer_id = (customer_id or "").strip() or None
        email = email.strip().lower()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                now = time.time()
                prior_former_user_id = None
                if customer_id:
                    prior_tombstone = self._conn.execute(
                        "SELECT former_user_id"
                        " FROM shopify_customer_tombstones"
                        " WHERE customer_id = ?",
                        (customer_id,),
                    ).fetchone()
                    if prior_tombstone is not None:
                        prior_former_user_id = prior_tombstone[
                            "former_user_id"
                        ]
                    self._conn.execute(
                        "INSERT INTO shopify_customer_tombstones"
                        " (customer_id, redacted, deleted_at, former_user_id)"
                        " VALUES (?, ?, ?, NULL)"
                        " ON CONFLICT(customer_id) DO UPDATE SET"
                        " redacted = MAX("
                        "   shopify_customer_tombstones.redacted,"
                        "   excluded.redacted),"
                        " deleted_at = MAX("
                        "   shopify_customer_tombstones.deleted_at,"
                        "   excluded.deleted_at),"
                        " former_user_id = CASE"
                        "   WHEN excluded.redacted = 1 THEN NULL"
                        "   ELSE shopify_customer_tombstones.former_user_id"
                        " END",
                        (customer_id, int(redact), now),
                    )

                user = None
                if customer_id:
                    user = self._conn.execute(
                        "SELECT * FROM users WHERE shopify_customer_id = ?",
                        (customer_id,),
                    ).fetchone()
                history_bound = bool(prior_former_user_id)
                if user is None and customer_id and prior_former_user_id:
                    user = self._conn.execute(
                        "SELECT * FROM users WHERE id = ?"
                        " AND (shopify_customer_id IS NULL"
                        "      OR shopify_customer_id = ?)",
                        (prior_former_user_id, customer_id),
                    ).fetchone()
                identity_conflict = False
                if user is None and email and not history_bound:
                    fallback = self._conn.execute(
                        "SELECT * FROM users WHERE email = ?", (email,)
                    ).fetchone()
                    if fallback is not None and customer_id:
                        # A customer-id-bearing delete/redact must never
                        # manufacture identity from email. It must match the
                        # active stable id or its exact prior tombstone
                        # mapping; otherwise customer C could bind itself to
                        # former customer D's unlinked account.
                        identity_conflict = True
                        fallback = None
                    user = fallback

                if user is None:
                    if redact and email and not identity_conflict:
                        self._erase_customer_pending(customer_id, email)
                        self._conn.execute(
                            "DELETE FROM email_codes WHERE email = ?", (email,)
                        )
                    self._conn.commit()
                    return "unknown"

                claimed = bool(
                    user["password_hash"] or user["email_verified_at"] is not None
                )
                jobs_table = self._conn.execute(
                    "SELECT 1 FROM sqlite_master"
                    " WHERE type = 'table' AND name = 'jobs'"
                ).fetchone()
                has_activity = False
                if jobs_table is not None:
                    has_activity = (
                        self._conn.execute(
                            "SELECT 1 FROM jobs WHERE user_id = ? LIMIT 1",
                            (user["id"],),
                        ).fetchone()
                        is not None
                    )
                if customer_id and not redact and (claimed or has_activity):
                    # Keep only the internal account id on a plain delete.
                    # The tombstone's stable-customer key can then route a
                    # late paid event back to customer C without allowing a
                    # different customer D at the same email to take over.
                    # A later customers/redact event clears this mapping.
                    self._conn.execute(
                        "UPDATE shopify_customer_tombstones"
                        " SET former_user_id = ?"
                        " WHERE customer_id = ? AND redacted = 0",
                        (user["id"], customer_id),
                    )

                if not claimed and not has_activity:
                    remaining = max(
                        0.0, (float(user["pro_until"] or 0.0) - now) / 86400
                    )
                    # Preserve which paid order owns each still-live slice
                    # before the aggregate is parked. Without this, a later
                    # claim could attach the remaining tail to an already
                    # expired older order and revoke the wrong purchase.
                    order_rows = self._conn.execute(
                        "SELECT order_id, grant_start, grant_end"
                        " FROM shopify_orders"
                        " WHERE user_id = ? AND days > 0"
                        "   AND cancelled_at IS NULL"
                        " ORDER BY COALESCE(grant_start, applied_at), order_id",
                        (user["id"],),
                    ).fetchall()
                    attributable = remaining
                    for order in order_rows:
                        contribution = 0.0
                        if (
                            order["grant_start"] is not None
                            and order["grant_end"] is not None
                        ):
                            contribution = max(
                                0.0,
                                (
                                    float(order["grant_end"])
                                    - max(now, float(order["grant_start"]))
                                )
                                / 86400,
                            )
                        contribution = min(
                            contribution, max(0.0, attributable)
                        )
                        self._conn.execute(
                            "UPDATE shopify_orders"
                            " SET user_id = NULL, pending_days = ?,"
                            "     grant_chain = NULL, grant_start = NULL,"
                            "     grant_end = NULL"
                            " WHERE order_id = ?",
                            (
                                0.0 if redact else contribution,
                                order["order_id"],
                            ),
                        )
                        attributable = max(0.0, attributable - contribution)
                    self._conn.execute(
                        "DELETE FROM users WHERE id = ?", (user["id"],)
                    )
                    if redact:
                        self._erase_customer_pending(
                            customer_id, user["email"]
                        )
                        self._conn.execute(
                            "DELETE FROM email_codes WHERE email = ?",
                            (user["email"],),
                        )
                    elif remaining > 0:
                        self._conn.execute(
                            "INSERT INTO pro_grants (email, days) VALUES (?, ?)"
                            " ON CONFLICT(email) DO UPDATE"
                            " SET days = days + excluded.days",
                            (user["email"], remaining),
                        )
                    self._conn.commit()
                    return "deleted"

                sets = "shopify_customer_id = NULL"
                if redact:
                    self._erase_customer_pending(
                        customer_id, user["email"]
                    )
                    sets += ", source = NULL"
                self._conn.execute(
                    f"UPDATE users SET {sets} WHERE id = ?", (user["id"],)
                )
                self._conn.commit()
                return "unlinked"
            except Exception:
                self._conn.rollback()
                raise

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

    def claim_pending_grant(self, user_id: str, email: str) -> float:
        """Atomically move a parked Shopify grant onto a user.

        The older pop-then-grant sequence had a crash window where the
        pending row was gone but Pro had not been extended yet. This method
        commits both state changes together, so replaying login after an
        interruption is always safe.
        """
        email = email.strip().lower()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                grant = self._conn.execute(
                    "SELECT days FROM pro_grants WHERE email = ?", (email,)
                ).fetchone()
                user = self._conn.execute(
                    "SELECT pro_until, shopify_customer_id,"
                    " shopify_identity_locked FROM users"
                    " WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if grant is None or user is None:
                    self._conn.rollback()
                    return 0.0
                total_days = float(grant["days"])
                pending_orders = self._conn.execute(
                    "SELECT order_id, pending_days, shopify_customer_id"
                    " FROM shopify_orders"
                    " WHERE email = ? AND days > 0 AND user_id IS NULL"
                    "   AND cancelled_at IS NULL"
                    " ORDER BY applied_at, order_id",
                    (email,),
                ).fetchall()
                customer_ids = {
                    order["shopify_customer_id"]
                    for order in pending_orders
                    if order["shopify_customer_id"]
                    and float(order["pending_days"] or 0.0) > 0
                }
                linked_customer_id = user["shopify_customer_id"]
                identity_conflict = False
                if linked_customer_id:
                    identity_conflict = bool(
                        customer_ids - {linked_customer_id}
                    )
                elif user["shopify_identity_locked"]:
                    former_ids = {
                        row["customer_id"]
                        for row in self._conn.execute(
                            "SELECT customer_id"
                            " FROM shopify_customer_tombstones"
                            " WHERE former_user_id = ? AND redacted = 0",
                            (user_id,),
                        )
                        if row["customer_id"] in customer_ids
                    }
                    if len(former_ids) == 1:
                        linked_customer_id = next(iter(former_ids))
                        identity_conflict = bool(
                            customer_ids - {linked_customer_id}
                        )
                    elif customer_ids:
                        identity_conflict = True
                elif len(customer_ids) == 1:
                    candidate = next(iter(customer_ids))
                    existing_link = self._conn.execute(
                        "SELECT 1 FROM users"
                        " WHERE shopify_customer_id = ?"
                        "   AND id != ? LIMIT 1",
                        (candidate, user_id),
                    ).fetchone()
                    tombstone = self._conn.execute(
                        "SELECT redacted, former_user_id"
                        " FROM shopify_customer_tombstones"
                        " WHERE customer_id = ?",
                        (candidate,),
                    ).fetchone()
                    if (
                        existing_link is None
                        and (
                            tombstone is None
                            or (
                                not tombstone["redacted"]
                                and (
                                    tombstone["former_user_id"] is None
                                    or tombstone["former_user_id"] == user_id
                                )
                            )
                        )
                    ):
                        linked_customer_id = candidate
                        if tombstone is None:
                            self._conn.execute(
                                "UPDATE users"
                                " SET shopify_customer_id = ?,"
                                "     shopify_identity_locked = 1"
                                " WHERE id = ?"
                                "   AND shopify_customer_id IS NULL"
                                "   AND shopify_identity_locked = 0",
                                    (candidate, user_id),
                                )
                        else:
                            self._conn.execute(
                                "UPDATE users"
                                " SET shopify_identity_locked = 1"
                                " WHERE id = ?",
                                (user_id,),
                            )
                            self._conn.execute(
                                "UPDATE shopify_customer_tombstones"
                                " SET former_user_id = ?"
                                " WHERE customer_id = ? AND redacted = 0"
                                "   AND (former_user_id IS NULL"
                                "        OR former_user_id = ?)",
                                (user_id, candidate, user_id),
                            )
                    else:
                        identity_conflict = True
                elif len(customer_ids) > 1:
                    identity_conflict = True

                eligible_orders = []
                for order in pending_orders:
                    order_customer_id = order["shopify_customer_id"]
                    eligible = (
                        (
                            linked_customer_id is not None
                            and order_customer_id == linked_customer_id
                        )
                        or (
                            order_customer_id is None
                            and not identity_conflict
                        )
                        or (
                            linked_customer_id is None
                            and not customer_ids
                            and not identity_conflict
                        )
                    )
                    if eligible:
                        eligible_orders.append(order)

                all_attributed = sum(
                    float(order["pending_days"] or 0.0)
                    for order in pending_orders
                )
                eligible_days = sum(
                    float(order["pending_days"] or 0.0)
                    for order in eligible_orders
                )
                if eligible_days > total_days + 0.001:
                    raise RuntimeError(
                        "Eligible Shopify pending value exceeds its"
                        " email aggregate"
                    )
                unattributed = max(0.0, total_days - all_attributed)
                claim_days = min(
                    total_days,
                    eligible_days
                    + (0.0 if identity_conflict else unattributed),
                )
                if claim_days <= 0:
                    self._conn.rollback()
                    return 0.0

                now = time.time()
                base = max(now, user["pro_until"] or 0.0)
                latest = None
                if (user["pro_until"] or 0.0) > now:
                    latest = self._conn.execute(
                        "SELECT grant_chain FROM shopify_orders"
                        " WHERE user_id = ? AND days > 0"
                        "   AND grant_chain IS NOT NULL"
                        " ORDER BY grant_end DESC LIMIT 1",
                        (user_id,),
                    ).fetchone()
                chain = (
                    latest["grant_chain"]
                    if latest is not None
                    else f"claim:{user_id}:{int(now * 1000)}"
                )
                self._conn.execute(
                    "UPDATE users SET pro_until = ? WHERE id = ?",
                    (base + claim_days * 86400, user_id),
                )
                self._conn.execute(
                    "UPDATE pro_grants SET days = MAX(0, days - ?)"
                    " WHERE email = ?",
                    (claim_days, email),
                )
                self._conn.execute(
                    "DELETE FROM pro_grants"
                    " WHERE email = ? AND days <= 0.000001",
                    (email,),
                )
                cursor = base
                for order in eligible_orders:
                    allocated = float(order["pending_days"] or 0.0)
                    grant_end = cursor + allocated * 86400
                    self._conn.execute(
                        "UPDATE shopify_orders"
                        " SET user_id = ?,"
                        "     shopify_customer_id = COALESCE("
                        "       shopify_customer_id, ?),"
                        "     grant_chain = ?, grant_start = ?, grant_end = ?,"
                        "     pending_days = 0, grant_ambiguous = 0"
                        " WHERE order_id = ?",
                        (
                            user_id,
                            linked_customer_id,
                            chain,
                            cursor,
                            grant_end,
                            order["order_id"],
                        ),
                    )
                    cursor = grant_end
                self._conn.commit()
                return claim_days
            except Exception:
                self._conn.rollback()
                raise

    def reduce_pending_grant(self, email: str, days: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pro_grants SET days = MAX(0, days - ?) WHERE email = ?",
                (days, email.strip().lower()),
            )
            self._conn.commit()

    def apply_shopify_order(
        self,
        order_id: str,
        email: str,
        days: float,
        shopify_customer_id: str | None,
        gear: list[tuple[str, str, int]] | None = None,
    ) -> tuple[bool, str, str | None]:
        """Record a paid Shopify order and its effects in one transaction.

        The stable Shopify customer id wins over checkout email. The return
        value is ``(applied, effective_email, user_id)``; ``applied`` is
        false for a replay, an earlier cancellation tombstone, or an order
        that cannot be associated with either a linked user or an email.
        """
        email = email.strip().lower()
        customer_id = (shopify_customer_id or "").strip() or None
        gear = gear or []
        if not order_id or (days <= 0 and not gear):
            return (False, email, None)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                tombstone = None
                if customer_id:
                    tombstone = self._conn.execute(
                        "SELECT redacted, former_user_id"
                        " FROM shopify_customer_tombstones"
                        " WHERE customer_id = ?",
                        (customer_id,),
                    ).fetchone()
                    if tombstone is not None and tombstone["redacted"]:
                        self._conn.rollback()
                        return (False, "", None)
                seen = self._conn.execute(
                    "SELECT * FROM shopify_orders WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
                gear_seen = self._conn.execute(
                    "SELECT email, created_at, cancelled_at FROM gear_orders"
                    " WHERE order_id = ? LIMIT 1",
                    (order_id,),
                ).fetchone()
                now = time.time()
                if seen is not None and (
                    seen["cancelled_at"] is not None or seen["days"] <= 0
                ):
                    self._conn.execute(
                        "UPDATE gear_orders"
                        " SET cancelled_at = COALESCE(cancelled_at, ?)"
                        " WHERE order_id = ?",
                        (seen["cancelled_at"] or now, order_id),
                    )
                    self._conn.commit()
                    return (False, seen["email"], seen["user_id"])
                if (
                    seen is None
                    and gear_seen is not None
                    and gear_seen["cancelled_at"] is not None
                ):
                    # Older code could commit gear, receive a cancellation,
                    # and crash before a Pro ledger existed. Preserve that
                    # cancellation as a tombstone instead of granting on a
                    # later paid replay.
                    self._conn.execute(
                        "INSERT INTO shopify_orders"
                        " (order_id, email, days, applied_at,"
                        "  shopify_customer_id, pending_days, cancelled_at)"
                        " VALUES (?, ?, 0, ?, ?, 0, ?)",
                        (
                            order_id,
                            gear_seen["email"],
                            gear_seen["created_at"],
                            customer_id,
                            gear_seen["cancelled_at"],
                        ),
                    )
                    self._conn.commit()
                    return (False, gear_seen["email"], None)

                needs_pro_ledger = days > 0 and seen is None
                needs_gear = bool(gear) and gear_seen is None

                user = None
                if seen is not None and seen["user_id"]:
                    user = self._conn.execute(
                        "SELECT * FROM users WHERE id = ?",
                        (seen["user_id"],),
                    ).fetchone()
                if user is None and customer_id:
                    user = self._conn.execute(
                        "SELECT * FROM users WHERE shopify_customer_id = ?",
                        (customer_id,),
                    ).fetchone()
                if (
                    user is None
                    and tombstone is not None
                    and not tombstone["redacted"]
                    and tombstone["former_user_id"]
                ):
                    user = self._conn.execute(
                        "SELECT * FROM users"
                        " WHERE id = ? AND shopify_customer_id IS NULL"
                        "   AND shopify_identity_locked = 1",
                        (tombstone["former_user_id"],),
                    ).fetchone()
                lookup_email = (
                    seen["email"]
                    if seen is not None
                    else (
                        gear_seen["email"]
                        if gear_seen is not None
                        else email
                    )
                )
                if user is None and lookup_email:
                    email_user = self._conn.execute(
                        "SELECT * FROM users WHERE email = ?", (lookup_email,)
                    ).fetchone()
                    if customer_id:
                        # A customer-bearing order is direct-granted only to
                        # the account already linked to that exact stable id.
                        # Matching email alone parks the order until a
                        # customer webhook safely establishes the link.
                        if (
                            email_user is not None
                            and email_user["shopify_customer_id"]
                            == customer_id
                        ):
                            user = email_user
                    else:
                        user = email_user

                effective_email = (
                    user["email"] if user is not None else lookup_email
                )
                if needs_pro_ledger and not effective_email:
                    self._conn.rollback()
                    return (False, "", None)

                user_id = (
                    user["id"]
                    if user is not None
                    else (seen["user_id"] if seen is not None else None)
                )
                resolved_customer_id = customer_id or (
                    user["shopify_customer_id"] if user is not None else None
                )
                prior_legacy_cancellation = None
                if seen is not None and user is not None:
                    prior_legacy_cancellation = self._conn.execute(
                        "SELECT 1 FROM shopify_orders"
                        " WHERE user_id = ? AND order_id != ?"
                        "   AND (days <= 0 OR cancelled_at IS NOT NULL)"
                        " LIMIT 1",
                        (user["id"], order_id),
                    ).fetchone()
                ambiguous_order = bool(
                    seen is not None and seen["grant_ambiguous"]
                )
                repair_entitlement = bool(
                    seen is not None
                    and user is not None
                    and seen["days"] > 0
                    and seen["cancelled_at"] is None
                    and seen["grant_end"] is not None
                    and float(seen["grant_end"]) > now
                    and float(user["pro_until"] or 0.0) + 0.001
                    < float(seen["grant_end"])
                    and prior_legacy_cancellation is None
                    and not ambiguous_order
                )
                missing_pending_grant = False
                if (
                    seen is not None
                    and seen["days"] > 0
                    and seen["cancelled_at"] is None
                    and float(seen["pending_days"] or 0.0) <= 0
                    and seen["grant_end"] is None
                    and (
                        user is None
                        or float(user["pro_until"] or 0.0) <= now
                    )
                    and 0
                    <= now - float(seen["applied_at"])
                    <= _LEGACY_REPLAY_REPAIR_WINDOW_S
                ):
                    pending = self._conn.execute(
                        "SELECT 1 FROM pro_grants WHERE email = ?",
                        (seen["email"],),
                    ).fetchone()
                    cancelled = self._conn.execute(
                        "SELECT 1 FROM shopify_orders"
                        " WHERE email = ? AND order_id != ?"
                        "   AND (days <= 0 OR cancelled_at IS NOT NULL)"
                        " LIMIT 1",
                        (seen["email"], order_id),
                    ).fetchone()
                    missing_pending_grant = (
                        pending is None and cancelled is None
                    )
                if (
                    not needs_pro_ledger
                    and not needs_gear
                    and not repair_entitlement
                    and not missing_pending_grant
                ):
                    self._conn.rollback()
                    return (False, effective_email, user_id)

                if needs_pro_ledger:
                    grant_chain = None
                    grant_start = None
                    grant_end = None
                    if user is not None:
                        base = max(now, user["pro_until"] or 0.0)
                        latest = None
                        if (user["pro_until"] or 0.0) > now:
                            latest = self._conn.execute(
                                "SELECT grant_chain FROM shopify_orders"
                                " WHERE user_id = ? AND days > 0"
                                "   AND grant_chain IS NOT NULL"
                                " ORDER BY grant_end DESC LIMIT 1",
                                (user_id,),
                            ).fetchone()
                        grant_chain = (
                            latest["grant_chain"]
                            if latest is not None
                            else f"order:{user_id}:{order_id}"
                        )
                        grant_start = base
                        grant_end = base + days * 86400
                    self._conn.execute(
                        "INSERT INTO shopify_orders"
                        " (order_id, email, days, applied_at, user_id,"
                        "  shopify_customer_id, grant_chain, grant_start,"
                        "  grant_end, pending_days)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            order_id,
                            effective_email,
                            days,
                            now,
                            user_id,
                            resolved_customer_id,
                            grant_chain,
                            grant_start,
                            grant_end,
                            0 if user is not None else days,
                        ),
                    )
                    if user is not None:
                        self._conn.execute(
                            "UPDATE users SET pro_until = ? WHERE id = ?",
                            (grant_end, user_id),
                        )
                    else:
                        self._conn.execute(
                            "INSERT INTO pro_grants (email, days) VALUES (?, ?)"
                            " ON CONFLICT(email) DO UPDATE"
                            " SET days = days + excluded.days",
                            (effective_email, days),
                        )
                elif repair_entitlement:
                    # Heal the origin/main crash window where record_order
                    # committed but grant_pro_days did not. The migrated
                    # interval is an absolute entitlement end, so replay
                    # restores only the still-live remainder and never adds
                    # a second full term. Any earlier cancellation makes the
                    # old aggregate ambiguous, so that case is deliberately
                    # left for a reconciliation report instead of auto-grant.
                    self._conn.execute(
                        "UPDATE users SET pro_until = MAX(pro_until, ?)"
                        " WHERE id = ?",
                        (float(seen["grant_end"]), user_id),
                    )
                elif missing_pending_grant:
                    # Heal the second origin/main crash window: record_order
                    # committed for a buyer with no account, but parking the
                    # grant did not. Limit automatic recovery to recent,
                    # uncancelled evidence; older ambiguity belongs in the
                    # reconciliation report.
                    repaired_days = float(seen["days"])
                    if user is None:
                        self._conn.execute(
                            "UPDATE shopify_orders"
                            " SET pending_days = ?,"
                            " shopify_customer_id = COALESCE("
                            "   shopify_customer_id, ?)"
                            " WHERE order_id = ?",
                            (
                                repaired_days,
                                resolved_customer_id,
                                order_id,
                            ),
                        )
                        self._conn.execute(
                            "INSERT INTO pro_grants (email, days)"
                            " VALUES (?, ?)"
                            " ON CONFLICT(email) DO UPDATE"
                            " SET days = days + excluded.days",
                            (effective_email, repaired_days),
                        )
                    else:
                        base = max(now, float(user["pro_until"] or 0.0))
                        latest = self._conn.execute(
                            "SELECT grant_chain FROM shopify_orders"
                            " WHERE user_id = ? AND days > 0"
                            "   AND grant_chain IS NOT NULL"
                            " ORDER BY grant_end DESC LIMIT 1",
                            (user_id,),
                        ).fetchone()
                        chain = (
                            latest["grant_chain"]
                            if latest is not None
                            else f"repair:{user_id}:{order_id}"
                        )
                        grant_end = base + repaired_days * 86400
                        self._conn.execute(
                            "UPDATE shopify_orders"
                            " SET user_id = ?, pending_days = 0,"
                            " shopify_customer_id = COALESCE("
                            "   shopify_customer_id, ?),"
                            " grant_chain = ?, grant_start = ?,"
                            " grant_end = ?, grant_ambiguous = 0"
                            " WHERE order_id = ?",
                            (
                                user_id,
                                resolved_customer_id,
                                chain,
                                base,
                                grant_end,
                                order_id,
                            ),
                        )
                        self._conn.execute(
                            "UPDATE users SET pro_until = ? WHERE id = ?",
                            (grant_end, user_id),
                        )
                if needs_gear:
                    for sku, title, quantity in gear:
                        self._conn.execute(
                            "INSERT INTO gear_orders"
                            " (order_id, sku, title, quantity, email, created_at)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                order_id,
                                sku,
                                title,
                                quantity,
                                effective_email,
                                now,
                            ),
                        )
                self._conn.commit()
                return (True, effective_email, user_id)
            except Exception:
                self._conn.rollback()
                raise

    def cancel_shopify_order(
        self,
        order_id: str,
        email: str = "",
        shopify_customer_id: str | None = None,
    ) -> tuple[bool, str, float]:
        """Record and revoke a Shopify cancellation in one transaction.

        Unknown cancellations become zero-day tombstones. Shopify can
        deliver webhooks out of order; this prevents a later ``orders/paid``
        delivery for the already-cancelled order from granting access.
        """
        email = email.strip().lower()
        customer_id = (shopify_customer_id or "").strip() or None
        if not order_id:
            return (False, email, 0.0)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                order = self._conn.execute(
                    "SELECT * FROM shopify_orders WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
                now = time.time()
                if order is None:
                    self._conn.execute(
                        "INSERT INTO shopify_orders"
                        " (order_id, email, days, applied_at,"
                        "  shopify_customer_id, cancelled_at)"
                        " VALUES (?, ?, 0, ?, ?, ?)",
                        (order_id, email, now, customer_id, now),
                    )
                    self._conn.execute(
                        "UPDATE gear_orders"
                        " SET cancelled_at = COALESCE(cancelled_at, ?)"
                        " WHERE order_id = ?",
                        (now, order_id),
                    )
                    self._conn.commit()
                    return (False, email, 0.0)
                if order["days"] <= 0 or order["cancelled_at"] is not None:
                    self._conn.execute(
                        "UPDATE gear_orders"
                        " SET cancelled_at = COALESCE(cancelled_at, ?)"
                        " WHERE order_id = ?",
                        (now, order_id),
                    )
                    self._conn.commit()
                    return (False, order["email"], 0.0)

                days = float(order["days"])
                effective_email = order["email"]
                user = None
                if order["user_id"]:
                    user = self._conn.execute(
                        "SELECT id FROM users WHERE id = ?",
                        (order["user_id"],),
                    ).fetchone()
                pending_days = 0.0
                if user is None:
                    order_pending = max(
                        0.0, float(order["pending_days"] or 0.0)
                    )
                    pending = self._conn.execute(
                        "SELECT days FROM pro_grants WHERE email = ?",
                        (effective_email,),
                    ).fetchone()
                    pending_days = min(
                        order_pending,
                        float(pending["days"]) if pending is not None else 0.0,
                    )
                    if pending_days:
                        self._conn.execute(
                            "UPDATE pro_grants SET days = MAX(0, days - ?)"
                            " WHERE email = ?",
                            (pending_days, effective_email),
                        )

                # A detached order may consume only its own parked
                # contribution. Never fall through by email/customer id and
                # accidentally revoke a newly created or unrelated user.
                remaining = days if user is not None else 0.0
                if user is not None:
                    revoke_days = 0.0
                    if order["grant_ambiguous"]:
                        # The legacy aggregate cannot prove which historical
                        # order owns the live tail. Mark the cancellation but
                        # leave access unchanged for explicit reconciliation.
                        revoke_days = 0.0
                    elif (
                        order["grant_start"] is not None
                        and order["grant_end"] is not None
                    ):
                        interval_days = max(
                            0.0,
                            (
                                float(order["grant_end"])
                                - float(order["grant_start"])
                            )
                            / 86400,
                        )
                        later_active = None
                        if order["grant_chain"]:
                            later_active = self._conn.execute(
                                "SELECT 1 FROM shopify_orders"
                                " WHERE user_id = ? AND grant_chain = ?"
                                "   AND days > 0 AND order_id != ?"
                                "   AND grant_start >= ?"
                                "   AND grant_end > ? LIMIT 1",
                                (
                                    user["id"],
                                    order["grant_chain"],
                                    order_id,
                                    float(order["grant_end"]) - 0.001,
                                    now,
                                ),
                            ).fetchone()
                        if float(order["grant_end"]) > now or later_active:
                            revoke_days = min(remaining, interval_days)
                        if revoke_days and order["grant_chain"]:
                            shift = revoke_days * 86400
                            self._conn.execute(
                                "UPDATE shopify_orders"
                                " SET grant_start = grant_start - ?,"
                                "     grant_end = grant_end - ?"
                                " WHERE user_id = ? AND grant_chain = ?"
                                "   AND days > 0 AND order_id != ?"
                                "   AND grant_start >= ?",
                                (
                                    shift,
                                    shift,
                                    user["id"],
                                    order["grant_chain"],
                                    order_id,
                                    float(order["grant_end"]) - 0.001,
                                ),
                            )
                    elif float(order["applied_at"]) + days * 86400 > now:
                        # Legacy rows have no interval metadata. Revoke only
                        # while that individual grant could still be active;
                        # an expired historical cancellation must not wipe a
                        # newer, unrelated purchase.
                        revoke_days = remaining
                    self._conn.execute(
                        "UPDATE users"
                        " SET pro_until = MAX(0, pro_until - ?)"
                        " WHERE id = ?",
                        (revoke_days * 86400, user["id"]),
                    )
                self._conn.execute(
                    "UPDATE shopify_orders"
                    " SET days = 0, pending_days = 0, cancelled_at = ?"
                    " WHERE order_id = ?",
                    (now, order_id),
                )
                self._conn.execute(
                    "UPDATE gear_orders"
                    " SET cancelled_at = COALESCE(cancelled_at, ?)"
                    " WHERE order_id = ?",
                    (now, order_id),
                )
                self._conn.commit()
                return (True, effective_email, days)
            except Exception:
                self._conn.rollback()
                raise

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
            shopify_identity_locked=bool(row["shopify_identity_locked"]),
            shopify_updated_at=row["shopify_updated_at"],
            source=row["source"],
            has_password=bool(row["password_hash"]),
            digest_opt_in=bool(row["digest_opt_in"]),
            digest_last_sent_at=row["digest_last_sent_at"],
            email_verified_at=row["email_verified_at"],
        )
