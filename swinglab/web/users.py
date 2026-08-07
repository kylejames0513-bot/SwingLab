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
nothing). Claiming that row always requires a one-time code delivered to its
inbox. Verified password signup consumes its server-side signup intent and
sets the password on the SAME row; verified email-code sign-in marks the
same row's email verified. Both paths preserve the Shopify link and any Pro
time without creating a duplicate user. An account is *claimed* once it has
a password, a verified email, or a provider-authenticated Shopify Customer
Account; rows with none of those proofs remain unclaimed stubs.
Emails are normalized (trimmed + lowercased) everywhere. Shopify's customer
id remains the durable cross-system link: an unclaimed store-only stub may
follow a changed Shopify email in place, while a claimed account keeps its
verified app login email until a separate verified email-change flow is
completed.

``email_codes`` backs the optional email flows (mailer.py): 6-digit one-time
codes, stored hashed, 10-minute expiry, single-use, rate-limited per email —
used for email-code sign-in, to verify claims at signup, and to reset
passwords when email is configured. ``signup_intents`` keeps the password
signup verification step server-side: it stores only a scrypt hash, expires
with the email code, and is consumed atomically when setup finishes.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from ..integrations.shopify.identity import customer_gid, normalize_customer_id
from ..proof_cycle_practice import (
    PRACTICE_OUTCOMES,
    ProofCyclePracticeEvidence,
    ProofCycleTransferCheck,
    normalize_practice_minutes,
    normalize_practice_outcome,
)
from .mobile_schema import (
    VersionedHMAC,
    ensure_mobile_state_schema,
    require_mobile_state_key_coverage,
)

FREE = "free"
PRO = "pro"

SHOPIFY_SYNC_NOT_STARTED = "not_started"
SHOPIFY_SYNC_PENDING = "pending"
SHOPIFY_SYNC_SYNCED = "synced"
SHOPIFY_SYNC_FAILED = "failed"
SHOPIFY_SYNC_REQUIRES_REVIEW = "requires_review"
SHOPIFY_SYNC_STATUSES = (
    SHOPIFY_SYNC_NOT_STARTED,
    SHOPIFY_SYNC_PENDING,
    SHOPIFY_SYNC_SYNCED,
    SHOPIFY_SYNC_FAILED,
    SHOPIFY_SYNC_REQUIRES_REVIEW,
)

# Subscription statuses that keep Pro features on. past_due keeps access
# during Stripe's retry window instead of cutting a paying customer off over
# one bounced charge.
_PRO_OK_STATUSES = ("active", "trialing", "past_due")

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 16384, 8, 1
_SHOPIFY_SYNC_ERROR_MAX = 500
_SHOPIFY_SYNC_PAGE_MAX = 1000
_SHOPIFY_CUSTOMER_UNIQUE_INDEX = "users_shopify_customer_id_unique"
_SHOPIFY_IDENTITY_CONFLICT_ERROR = (
    "Shopify customer identity conflict requires administrative review."
)
_SHOPIFY_INVALID_ID_ERROR = (
    "Stored Shopify customer identity requires administrative review."
)
_SHOPIFY_LINK_REMOVED_ERROR = (
    "Shopify customer link was removed and requires administrative review."
)
_SHOPIFY_EMAIL_UNVERIFIED_ERROR = (
    "Shopify email match requires inbox verification."
)
_SHOPIFY_REDACTED_ERROR = (
    "Shopify synchronization is blocked after customer redaction."
)

# One-time email codes (claim verification, password reset).
CODE_TTL_S = 600  # codes live 10 minutes
CODE_RESEND_S = 60  # at most one new code per email/purpose per minute
CODE_MAX_ATTEMPTS = 5  # then the code is burned and must be re-requested
SIGNUP_INTENT_TTL_S = CODE_TTL_S
_SIGNUP_INTENT_TOKEN_MAX = 256
_FLOW_SESSION_NONCE_MAX = 256
_LEGACY_REPLAY_REPAIR_WINDOW_S = 7 * 86400
SHOPIFY_PRIVACY_RETENTION_S = 30 * 86400
SHOPIFY_PRIVACY_READY = "ready"
SHOPIFY_PRIVACY_DELIVERED = "delivered"
_SHOPIFY_PRIVACY_REQUEST_PREFIX = "spr_"
_SHOPIFY_PRIVACY_ID_MAX = 255
_SHOPIFY_PRIVACY_ORDER_MAX = 1000
_SHOPIFY_PRIVACY_DATA_TOPIC = "customers/data_request"
_SHOPIFY_PRIVACY_CUSTOMER_REDACT_TOPIC = "customers/redact"
_SHOPIFY_PRIVACY_SHOP_REDACT_TOPIC = "shop/redact"

# The app keeps golf data separate from the commerce identity.  These states
# deliberately describe migration progress instead of inferring a login state
# from an email address.  A future cutover report can therefore account for
# every user without silently treating an unlinked row as migrated.
SHOPIFY_ACCOUNT_LOCAL_ONLY = "local_only"
SHOPIFY_ACCOUNT_LINKED_PENDING_LOGIN = "linked_pending_first_sign_in"
SHOPIFY_ACCOUNT_AUTHENTICATED = "shopify_authenticated"
SHOPIFY_ACCOUNT_REVERIFY = "reverification_required"
SHOPIFY_ACCOUNT_MANUAL_REVIEW = "manual_review"
SHOPIFY_ACCOUNT_REDACTED = "redacted"
SHOPIFY_ACCOUNT_MIGRATION_STATES = (
    SHOPIFY_ACCOUNT_LOCAL_ONLY,
    SHOPIFY_ACCOUNT_LINKED_PENDING_LOGIN,
    SHOPIFY_ACCOUNT_AUTHENTICATED,
    SHOPIFY_ACCOUNT_REVERIFY,
    SHOPIFY_ACCOUNT_MANUAL_REVIEW,
    SHOPIFY_ACCOUNT_REDACTED,
)


def _account_is_claimed(
    password_hash: object,
    email_verified_at: object,
    shopify_account_migration_state: object,
) -> bool:
    """Return whether persisted authentication proves account ownership."""

    return bool(
        password_hash
        or email_verified_at is not None
        or shopify_account_migration_state == SHOPIFY_ACCOUNT_AUTHENTICATED
    )

GOLFER_EXPERIENCE_MODES = ("start", "improve", "compete")
GOLFER_HANDICAP_RANGES = (
    "new_to_golf",
    "30_plus",
    "20_to_29",
    "15_to_19",
    "10_to_14",
    "under_10",
    "prefer_not_to_say",
)
GOLFER_PRIMARY_GOALS = (
    "consistency",
    "tempo",
    "weight_shift",
    "strike_quality",
    "balance",
    "confidence",
)
GOLFER_PRACTICE_MINUTES = (10, 20, 45)
GOLFER_SESSIONS_PER_WEEK = (1, 2, 3)
_GOLFER_DISPLAY_NAME_MAX = 50
_GOLFER_DISPLAY_NAME_OMITTED = object()
PRODUCT_EVENT_NAMES = (
    "landing_view",
    "account_verified",
    "upload_started",
    "upload_completed",
    "brief_viewed",
    "pro_clicked",
    "gear_match_clicked",
    "cart_started",
    "checkout_started",
    "paid_order",
    "fulfillment_updated",
    "repeat_analysis",
)
_PRODUCT_EVENT_ID_MAX = 128
_PRODUCT_EVENT_METADATA_MAX = 512
SHOPIFY_ACCOUNT_OAUTH_TTL_S = 10 * 60
_SHOPIFY_ACCOUNT_OAUTH_MODES = ("login", "link")

# Native clients receive a personal device credential rather than a browser
# session cookie.  The token is selector.secret: the selector makes lookup
# indexable while the 256-bit secret is stored only as a SHA-256 digest.  A
# password reset / ownership recovery advances ``users.auth_epoch`` and
# invalidates every token issued under the former epoch.
MOBILE_API_TOKEN_PREFIX = "ciat_"
MOBILE_API_TOKEN_SELECTOR_BYTES = 18
MOBILE_API_TOKEN_SECRET_BYTES = 32
MOBILE_API_TOKEN_TTL_S = 90 * 86400
MOBILE_API_TOKEN_ACTIVE_LIMIT = 5
_MOBILE_API_TOKEN_LABEL_MAX = 80
_MOBILE_API_TOKEN_MAX_LENGTH = 256
_MOBILE_API_TOKEN_LAST_USED_WRITE_INTERVAL_S = 60
_MOBILE_API_TOKEN_DUMMY_HASH = hashlib.sha256(
    b"caddieinsight-mobile-token-dummy"
).hexdigest()

_SHOPIFY_PRIVACY_LOCKS_GUARD = threading.Lock()
_SHOPIFY_PRIVACY_LOCKS: dict[str, threading.Lock] = {}

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
    email_verified_at   REAL,
    auth_epoch          INTEGER NOT NULL DEFAULT 0,
    history_epoch       INTEGER NOT NULL DEFAULT 0,
    shopify_sync_status TEXT NOT NULL DEFAULT 'not_started',
    shopify_last_synced_at REAL,
    shopify_sync_error  TEXT,
    shopify_sync_attempts INTEGER NOT NULL DEFAULT 0,
    shopify_sync_next_attempt_at REAL,
    shopify_sync_attempt_token TEXT,
    shopify_sync_generation INTEGER NOT NULL DEFAULT 0,
    shopify_sync_blocked INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS shopify_pending_customer_links (
    customer_id TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    updated_at  REAL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS shopify_pending_customer_links_email
    ON shopify_pending_customer_links(email);
CREATE TABLE IF NOT EXISTS gear_orders (
    order_id     TEXT NOT NULL,
    user_id      TEXT,
    sku          TEXT NOT NULL,
    title        TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    email        TEXT NOT NULL,
    created_at   REAL NOT NULL,
    cancelled_at REAL
);
CREATE INDEX IF NOT EXISTS gear_orders_order ON gear_orders(order_id);
CREATE TABLE IF NOT EXISTS lifecycle_email_sends (
    kind        TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    user_id     TEXT,
    sent_at     REAL NOT NULL,
    PRIMARY KEY (kind, subject_key)
);
CREATE TABLE IF NOT EXISTS email_codes (
    email      TEXT NOT NULL,
    purpose    TEXT NOT NULL,
    code_hash  TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    session_nonce_hash TEXT,
    PRIMARY KEY (email, purpose)
);
CREATE TABLE IF NOT EXISTS signup_intents (
    token_hash    TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    digest_opt_in INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    session_nonce_hash TEXT
);
CREATE INDEX IF NOT EXISTS signup_intents_expiry
    ON signup_intents(expires_at);
CREATE TABLE IF NOT EXISTS shopify_privacy_requests (
    request_id      TEXT PRIMARY KEY,
    shop_domain     TEXT NOT NULL,
    status          TEXT NOT NULL,
    snapshot_json   TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    record_count    INTEGER NOT NULL,
    snapshot_bytes  INTEGER NOT NULL,
    created_at      REAL NOT NULL,
    completed_at    REAL NOT NULL,
    expires_at      REAL NOT NULL,
    delivered_at    REAL
);
CREATE TABLE IF NOT EXISTS shopify_sync_control (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    generation     INTEGER NOT NULL DEFAULT 0,
    shop_redacted  INTEGER NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL,
    order_fence_secret TEXT
);
INSERT OR IGNORE INTO shopify_sync_control
    (id, generation, shop_redacted, updated_at)
    VALUES (1, 0, 0, 0);
CREATE TABLE IF NOT EXISTS shopify_privacy_event_fences (
    topic       TEXT NOT NULL,
    event_key   TEXT NOT NULL,
    applied_at  REAL NOT NULL,
    PRIMARY KEY (topic, event_key)
);
CREATE TABLE IF NOT EXISTS shopify_redacted_order_fences (
    order_key    TEXT PRIMARY KEY,
    redacted_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS golfer_profiles (
    user_id                     TEXT PRIMARY KEY,
    display_name                TEXT,
    experience_mode             TEXT NOT NULL DEFAULT 'improve',
    handicap_range              TEXT,
    primary_goal                TEXT,
    practice_minutes            INTEGER NOT NULL DEFAULT 20,
    sessions_per_week           INTEGER NOT NULL DEFAULT 2,
    handedness                  TEXT NOT NULL DEFAULT 'right',
    camera_angle                TEXT NOT NULL DEFAULT 'face-on',
    preferred_club              TEXT,
    reduced_motion              INTEGER NOT NULL DEFAULT 0,
    marketing_email_opt_in      INTEGER NOT NULL DEFAULT 0,
    created_at                  REAL NOT NULL,
    updated_at                  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS practice_checkins (
    user_id       TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    completed_at  REAL NOT NULL,
    PRIMARY KEY (user_id, session_id)
);
CREATE INDEX IF NOT EXISTS practice_checkins_user_completed
    ON practice_checkins(user_id, completed_at DESC);
CREATE TABLE IF NOT EXISTS proof_cycle_practice_evidence (
    user_id             TEXT NOT NULL,
    baseline_session_id TEXT NOT NULL,
    target_fingerprint  TEXT NOT NULL,
    drill_id            TEXT NOT NULL,
    minutes             INTEGER NOT NULL CHECK (minutes IN (10, 20, 45)),
    outcome             TEXT NOT NULL CHECK (outcome IN ('completed', 'still_working')),
    completed_at        REAL NOT NULL,
    completed_day       INTEGER NOT NULL,
    PRIMARY KEY (user_id, baseline_session_id, target_fingerprint, completed_day)
);
CREATE INDEX IF NOT EXISTS proof_cycle_practice_evidence_lookup
    ON proof_cycle_practice_evidence(
        user_id, baseline_session_id, target_fingerprint, completed_at DESC
    );
CREATE TABLE IF NOT EXISTS proof_cycle_transfer_checks (
    session_id          TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    baseline_session_id TEXT NOT NULL,
    target_fingerprint  TEXT NOT NULL,
    drill_id            TEXT NOT NULL,
    club                TEXT NOT NULL,
    hand                TEXT NOT NULL CHECK (hand IN ('left', 'right')),
    angle               TEXT NOT NULL CHECK (angle IN ('face-on', 'dtl')),
    normal_swings       INTEGER NOT NULL CHECK (normal_swings IN (0, 1)),
    declared_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS proof_cycle_transfer_checks_lookup
    ON proof_cycle_transfer_checks(
        user_id, baseline_session_id, target_fingerprint, declared_at DESC
    );
CREATE TABLE IF NOT EXISTS product_events (
    id            TEXT PRIMARY KEY,
    event_name    TEXT NOT NULL,
    user_id       TEXT,
    session_id    TEXT,
    anonymous_id  TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    dedupe_key    TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS product_events_name_created
    ON product_events(event_name, created_at DESC);
CREATE INDEX IF NOT EXISTS product_events_user_created
    ON product_events(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS mobile_api_tokens (
    selector    TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    auth_epoch  INTEGER NOT NULL,
    label       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    last_used_at REAL,
    expires_at  REAL NOT NULL,
    revoked_at  REAL
);
CREATE INDEX IF NOT EXISTS mobile_api_tokens_user_active
    ON mobile_api_tokens(user_id, auth_epoch, expires_at, revoked_at);
CREATE TABLE IF NOT EXISTS shopify_customer_account_oauth_states (
    state_hash  TEXT PRIMARY KEY,
    verifier    TEXT NOT NULL,
    nonce       TEXT NOT NULL,
    user_id     TEXT,
    mode        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS shopify_customer_account_oauth_states_expiry
    ON shopify_customer_account_oauth_states(expires_at);
CREATE TABLE IF NOT EXISTS shopify_customer_account_browser_sessions (
    session_hash TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    id_token     TEXT NOT NULL,
    expires_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS shopify_customer_account_browser_sessions_expiry
    ON shopify_customer_account_browser_sessions(expires_at);
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


def _shopify_privacy_lock_key(db_path: str | Path) -> str:
    """Return one stable in-process key for a shared SQLite database."""

    try:
        resolved = Path(db_path).resolve(strict=False)
    except OSError:
        resolved = Path(db_path).absolute()
    key = str(resolved)
    return key.casefold() if os.name == "nt" else key


def _shopify_privacy_thread_lock(db_path: str | Path) -> threading.Lock:
    key = _shopify_privacy_lock_key(db_path)
    with _SHOPIFY_PRIVACY_LOCKS_GUARD:
        lock = _SHOPIFY_PRIVACY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SHOPIFY_PRIVACY_LOCKS[key] = lock
        return lock


def _lock_shopify_privacy_file(file_descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                os.lseek(file_descriptor, 0, os.SEEK_SET)
                msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if (
                    exc.errno
                    not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                    and getattr(exc, "winerror", None) not in {33, 36}
                ):
                    raise
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_EX)


def _unlock_shopify_privacy_file(file_descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_UN)


@contextmanager
def shopify_remote_privacy_lock(
    db_path: str | Path,
) -> Iterator[None]:
    """Order Shopify provider calls and privacy erasure across processes.

    The persistent sidecar is an advisory lock, not data. The operating
    system releases it if a process exits unexpectedly. A per-database
    threading lock supplies the same ordering between connections in this
    process, where POSIX ``flock`` alone is insufficient.
    """

    database = Path(db_path)
    thread_lock = _shopify_privacy_thread_lock(database)
    with thread_lock:
        if str(db_path) == ":memory:":
            yield
            return
        lock_path = database.with_name(
            f".{database.name}.shopify-privacy.lock"
        )
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        file_descriptor = os.open(lock_path, flags, 0o600)
        acquired = False
        try:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                # Windows ACLs, rather than POSIX mode bits, control access.
                pass
            if os.fstat(file_descriptor).st_size < 1:
                os.write(file_descriptor, b"\0")
                os.fsync(file_descriptor)
            _lock_shopify_privacy_file(file_descriptor)
            acquired = True
            yield
        finally:
            if acquired:
                _unlock_shopify_privacy_file(file_descriptor)
            os.close(file_descriptor)


def _safe_sync_error(value: str | None) -> str | None:
    """Bound a caller-supplied, already-safe operational summary."""

    if value is None:
        return None
    summary = " ".join(str(value).split())
    return summary[:_SHOPIFY_SYNC_ERROR_MAX] or None


def _compatible_customer_id(value: object | None) -> str | None:
    """Normalize real Shopify IDs while preserving legacy opaque fixtures.

    Production Admin and webhook IDs are numeric/GID values. A few older
    white-label databases and compatibility tests used opaque placeholders;
    preserving them here avoids rewriting established PR #28 behavior. New
    Admin-sync writes use :func:`normalize_customer_id` directly and therefore
    cannot introduce another opaque value.
    """

    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return normalize_customer_id(raw)
    except ValueError:
        return raw


def _page_limit(limit: int) -> int:
    if isinstance(limit, bool):
        raise ValueError("limit must be a positive integer")
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        raise ValueError("limit must be a positive integer")
    if parsed <= 0:
        raise ValueError("limit must be a positive integer")
    return min(parsed, _SHOPIFY_SYNC_PAGE_MAX)


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
    auth_epoch: int = 0  # invalidates stateless sessions after ownership changes
    shopify_sync_status: str = SHOPIFY_SYNC_NOT_STARTED
    shopify_last_synced_at: float | None = None
    shopify_sync_error: str | None = None
    shopify_sync_attempts: int = 0
    shopify_sync_next_attempt_at: float | None = None
    # Opaque compare-and-set token for the currently active attempt. It is
    # persisted so a stale worker result cannot overwrite a newer attempt.
    shopify_sync_attempt_token: str | None = None
    # Redaction increments the generation so a worker selected before an
    # erasure cannot start from its stale snapshot. Customer redaction also
    # blocks the account from future automatic writes until an operator
    # deliberately implements a new consent/re-enrollment flow.
    shopify_sync_generation: int = 0
    shopify_sync_blocked: bool = False
    # Customer Account identity is intentionally separate from the Admin API
    # customer id.  It remains unset until a real authorization-code flow
    # proves the subject; this avoids treating a matching email as a login.
    shopify_account_subject: str | None = None
    shopify_account_migration_state: str = SHOPIFY_ACCOUNT_LOCAL_ONLY
    shopify_account_linked_at: float | None = None
    shopify_account_last_login_at: float | None = None
    # Appended to preserve the positional order of the pre-reset User API.
    history_epoch: int = 0  # rejects writes started before a history reset

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
        or by signing in with an emailed code / linked Shopify Customer
        Account. False = an untouched store stub (provisioned by a webhook,
        never used by its owner)."""
        return _account_is_claimed(
            self.has_password,
            self.email_verified_at,
            self.shopify_account_migration_state,
        )


@dataclass(frozen=True)
class GolferProfile:
    """CaddieInsight-owned preferences used to make the next action humane.

    This is not customer-account data and is never included in outbound
    Shopify customer synchronization.  Marketing consent is a separate,
    explicit opt-in; it is not the same as the existing practice digest.
    """

    user_id: str
    display_name: str | None = None
    experience_mode: str = "improve"
    handicap_range: str | None = None
    primary_goal: str | None = None
    practice_minutes: int = 20
    sessions_per_week: int = 2
    handedness: str = "right"
    camera_angle: str = "face-on"
    preferred_club: str | None = None
    reduced_motion: bool = False
    marketing_email_opt_in: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def is_complete(self) -> bool:
        return bool(self.display_name and self.primary_goal and self.preferred_club)


@dataclass(frozen=True)
class PracticeCheckin:
    user_id: str
    session_id: str
    completed_at: float


@dataclass(frozen=True)
class MobileAPIToken:
    """Safe lifecycle metadata for one personal mobile credential.

    The selector is an opaque revocation handle, not a bearer secret.  The
    raw credential and its hash deliberately never appear in this object so
    callers can list and export device records without accidentally leaking
    a reusable token.
    """

    selector: str
    label: str
    created_at: float
    last_used_at: float | None
    expires_at: float
    revoked_at: float | None
    active: bool


@dataclass(frozen=True)
class MobileAPIPrincipal:
    """The authenticated identity and device selector for a mobile token."""

    user: User
    selector: str
    auth_epoch: int
    installation_key: str | None


@dataclass(frozen=True)
class ShopifyCustomerAccountOAuthState:
    """One short-lived, server-side authorization-code transaction."""

    verifier: str
    nonce: str
    user_id: str | None
    mode: str


@dataclass(frozen=True)
class ShopifyCustomerAccountBrowserSession:
    user_id: str
    id_token: str


@dataclass(frozen=True)
class SignupIntent:
    """Public, non-secret metadata for a pending password signup.

    The password hash deliberately never leaves :class:`UserStore`.
    """

    email: str
    digest_opt_in: bool
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class ShopifyPrivacyRequest:
    """PII-free operator metadata for a stored privacy export.

    The customer snapshot is available only through the deliberately named
    :meth:`UserStore.export_shopify_privacy_request` method.
    """

    request_id: str
    status: str
    record_count: int
    snapshot_bytes: int
    created_at: float
    completed_at: float
    expires_at: float
    delivered_at: float | None = None


@dataclass(frozen=True)
class ShopifyShopRedaction:
    """PII-free counts from one transactional shop-wide erasure."""

    applied: bool
    replayed: bool = False
    removed_store_only_users: int = 0
    preserved_accounts: int = 0
    removed_orders: int = 0
    removed_gear_rows: int = 0
    removed_pending_grants: int = 0
    removed_privacy_requests: int = 0


class ShopifySyncFencedError(RuntimeError):
    """An outbound attempt was invalidated or blocked by privacy state."""


class MobileAPITokenLimitError(ValueError):
    """The account already has its bounded set of active device tokens."""


class MobileAPITokenAuthEpochError(RuntimeError):
    """The browser session changed before a device token could be issued."""


class HistoryEpochError(RuntimeError):
    """A history write started before the account's latest reset."""


class HistoryAuthEpochError(HistoryEpochError):
    """Account recovery revoked the session before history deletion."""


class HistoryPrivacyExportConflict(HistoryEpochError):
    """An undelivered compliance export still contains swing history."""


class PasswordAddConflict(RuntimeError):
    """The account changed before an optional password could be added."""


class UserStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        mobile_state_hmac: VersionedHMAC | None = None,
    ):
        self._lock = threading.Lock()
        self._closed = False
        self._db_path = Path(db_path)
        self._mobile_state_hmac = (
            mobile_state_hmac
            if mobile_state_hmac is not None
            else VersionedHMAC.from_env(required=False)
        )
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            try:
                # Serialize schema inspection + ALTER across multiple app
                # processes. A per-instance threading lock cannot prevent
                # two connections from observing the same missing column.
                self._conn.execute("BEGIN IMMEDIATE")
                ensure_mobile_state_schema(self._conn)
                self._conn.execute(
                    "DELETE FROM signup_intents WHERE expires_at <= ?",
                    (time.time(),),
                )
                for table in ("email_codes", "signup_intents"):
                    flow_columns = {
                        row["name"]
                        for row in self._conn.execute(
                            f"PRAGMA table_info({table})"
                        )
                    }
                    if "session_nonce_hash" not in flow_columns:
                        self._conn.execute(
                            f"ALTER TABLE {table}"
                            " ADD COLUMN session_nonce_hash TEXT"
                        )
                privacy_columns = {
                    row["name"]
                    for row in self._conn.execute(
                        "PRAGMA table_info(shopify_privacy_requests)"
                    )
                }
                required_privacy_columns = {
                    "request_id",
                    "shop_domain",
                    "status",
                    "snapshot_json",
                    "snapshot_sha256",
                    "record_count",
                    "snapshot_bytes",
                    "created_at",
                    "completed_at",
                    "expires_at",
                    "delivered_at",
                }
                if not required_privacy_columns.issubset(privacy_columns):
                    # No released build used an earlier shape. Refusing an
                    # unknown/partial table is safer than discarding or
                    # misreading a privacy snapshot created by some other
                    # process.
                    raise RuntimeError(
                        "Incompatible Shopify privacy request schema."
                    )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS"
                    " shopify_privacy_requests_expiry"
                    " ON shopify_privacy_requests(expires_at)"
                )
                control_columns = {
                    row["name"]
                    for row in self._conn.execute(
                        "PRAGMA table_info(shopify_sync_control)"
                    )
                }
                if "order_fence_secret" not in control_columns:
                    self._conn.execute(
                        "ALTER TABLE shopify_sync_control"
                        " ADD COLUMN order_fence_secret TEXT"
                    )
                    control_columns.add("order_fence_secret")
                self._conn.execute(
                    "UPDATE shopify_sync_control"
                    " SET order_fence_secret = ?"
                    " WHERE id = 1"
                    " AND (order_fence_secret IS NULL"
                    "      OR order_fence_secret = '')",
                    (secrets.token_hex(32),),
                )
                required_control_columns = {
                    "id",
                    "generation",
                    "shop_redacted",
                    "updated_at",
                    "order_fence_secret",
                }
                required_event_fence_columns = {
                    "topic",
                    "event_key",
                    "applied_at",
                }
                event_fence_columns = {
                    row["name"]
                    for row in self._conn.execute(
                        "PRAGMA table_info(shopify_privacy_event_fences)"
                    )
                }
                required_order_fence_columns = {
                    "order_key",
                    "redacted_at",
                }
                order_fence_columns = {
                    row["name"]
                    for row in self._conn.execute(
                        "PRAGMA table_info(shopify_redacted_order_fences)"
                    )
                }
                if (
                    not required_control_columns.issubset(control_columns)
                    or not required_event_fence_columns.issubset(
                        event_fence_columns
                    )
                    or not required_order_fence_columns.issubset(
                        order_fence_columns
                    )
                ):
                    raise RuntimeError(
                        "Incompatible Shopify privacy fence schema."
                    )
                # Upgrade older databases in place (idempotent — each column
                # is added once): pre-Shopify-billing files lack pro_until,
                # and pre-account-sync files lack the store-account columns.
                columns = {
                    row["name"]
                    for row in self._conn.execute("PRAGMA table_info(users)")
                }
                sync_status_added = "shopify_sync_status" not in columns
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
                    ("auth_epoch", "auth_epoch INTEGER NOT NULL DEFAULT 0"),
                    (
                        "history_epoch",
                        "history_epoch INTEGER NOT NULL DEFAULT 0",
                    ),
                    (
                        "shopify_sync_status",
                        "shopify_sync_status TEXT NOT NULL DEFAULT 'not_started'",
                    ),
                    ("shopify_last_synced_at", "shopify_last_synced_at REAL"),
                    ("shopify_sync_error", "shopify_sync_error TEXT"),
                    (
                        "shopify_sync_attempts",
                        "shopify_sync_attempts INTEGER NOT NULL DEFAULT 0",
                    ),
                    (
                        "shopify_sync_next_attempt_at",
                        "shopify_sync_next_attempt_at REAL",
                    ),
                    (
                        "shopify_sync_attempt_token",
                        "shopify_sync_attempt_token TEXT",
                    ),
                    (
                        "shopify_sync_generation",
                        "shopify_sync_generation INTEGER NOT NULL DEFAULT 0",
                    ),
                    (
                        "shopify_sync_blocked",
                        "shopify_sync_blocked INTEGER NOT NULL DEFAULT 0",
                    ),
                    (
                        "shopify_account_subject",
                        "shopify_account_subject TEXT",
                    ),
                    (
                        "shopify_account_migration_state",
                        "shopify_account_migration_state TEXT NOT NULL"
                        " DEFAULT 'local_only'",
                    ),
                    (
                        "shopify_account_linked_at",
                        "shopify_account_linked_at REAL",
                    ),
                    (
                        "shopify_account_last_login_at",
                        "shopify_account_last_login_at REAL",
                    ),
                ):
                    if name not in columns:
                        self._conn.execute(f"ALTER TABLE users ADD COLUMN {ddl}")
                self._prepare_shopify_sync_schema(
                    initialize_statuses=sync_status_added
                )
                self._prepare_shopify_customer_account_schema()
                profile_columns = {
                    row["name"]
                    for row in self._conn.execute(
                        "PRAGMA table_info(golfer_profiles)"
                    )
                }
                if "display_name" not in profile_columns:
                    self._conn.execute(
                        "ALTER TABLE golfer_profiles"
                        " ADD COLUMN display_name TEXT"
                    )
                mobile_token_columns = {
                    row["name"]
                    for row in self._conn.execute(
                        "PRAGMA table_info(mobile_api_tokens)"
                    )
                }
                required_mobile_token_columns = {
                    "selector",
                    "token_hash",
                    "user_id",
                    "auth_epoch",
                    "label",
                    "created_at",
                    "last_used_at",
                    "expires_at",
                    "revoked_at",
                }
                if not required_mobile_token_columns.issubset(
                    mobile_token_columns
                ):
                    # A partial credential table could make a migration look
                    # successful while silently weakening authentication.
                    # Refuse it rather than guessing how to repair it.
                    raise RuntimeError("Incompatible mobile token schema.")
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS mobile_api_tokens_user_active"
                    " ON mobile_api_tokens(user_id, auth_epoch, expires_at,"
                    " revoked_at)"
                )
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
                gear_columns = {
                    row["name"]
                    for row in self._conn.execute("PRAGMA table_info(gear_orders)")
                }
                if "user_id" not in gear_columns:
                    self._conn.execute(
                        "ALTER TABLE gear_orders ADD COLUMN user_id TEXT"
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
                # Link old mixed Pro/gear orders only where their own
                # durable order row now proves the same user. Never fill this
                # analytics pointer by matching a checkout email.
                self._conn.execute(
                    "UPDATE gear_orders SET user_id = ("
                    " SELECT shopify_orders.user_id FROM shopify_orders"
                    " WHERE shopify_orders.order_id = gear_orders.order_id"
                    "   AND shopify_orders.user_id IS NOT NULL"
                    ") WHERE user_id IS NULL AND EXISTS ("
                    " SELECT 1 FROM shopify_orders"
                    " WHERE shopify_orders.order_id = gear_orders.order_id"
                    "   AND shopify_orders.user_id IS NOT NULL"
                    ")"
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
                # Protected configuration must cover every durable reference,
                # even when all mobile feature flags are off.  HMAC inputs are
                # intentionally unavailable, so a missing old key cannot be
                # repaired or re-keyed after startup.
                require_mobile_state_key_coverage(
                    self._conn, self._mobile_state_hmac
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        """Release this store's SQLite connection exactly once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def _prepare_shopify_sync_schema(self, initialize_statuses: bool) -> None:
        """Initialize sync state and add the customer-ID uniqueness guard.

        Older databases may already contain the same Shopify identity on more
        than one user because the original column had no unique constraint.
        Startup must never choose a winner or delete account data. Conflicting
        rows are instead marked for review and the partial index is deferred;
        after an operator resolves the rows, the next open creates the index.

        The caller owns ``BEGIN IMMEDIATE`` so canonicalization, preflight, and
        index creation are serialized across app processes.
        """

        if initialize_statuses:
            self._conn.execute(
                "UPDATE users SET shopify_sync_status = CASE"
                " WHEN shopify_customer_id IS NULL THEN ? ELSE ? END,"
                " shopify_sync_error = NULL,"
                " shopify_sync_attempts = COALESCE(shopify_sync_attempts, 0),"
                " shopify_sync_next_attempt_at = NULL,"
                " shopify_sync_attempt_token = NULL",
                (SHOPIFY_SYNC_NOT_STARTED, SHOPIFY_SYNC_SYNCED),
            )
        else:
            # Reconcile only impossible/default states. Real pending, failed,
            # and review states survive every idempotent reopen.
            self._conn.execute(
                "UPDATE users SET shopify_sync_status = ?"
                " WHERE shopify_customer_id IS NOT NULL"
                " AND shopify_sync_status = ?",
                (SHOPIFY_SYNC_SYNCED, SHOPIFY_SYNC_NOT_STARTED),
            )
            self._conn.execute(
                "UPDATE users SET shopify_sync_status = ?"
                " WHERE shopify_customer_id IS NULL"
                " AND shopify_sync_status = ?",
                (SHOPIFY_SYNC_NOT_STARTED, SHOPIFY_SYNC_SYNCED),
            )
            placeholders = ", ".join("?" for _ in SHOPIFY_SYNC_STATUSES)
            self._conn.execute(
                "UPDATE users SET shopify_sync_status = ?,"
                " shopify_sync_error = ?,"
                " shopify_sync_attempt_token = NULL"
                f" WHERE shopify_sync_status NOT IN ({placeholders})",
                (
                    SHOPIFY_SYNC_REQUIRES_REVIEW,
                    _SHOPIFY_INVALID_ID_ERROR,
                    *SHOPIFY_SYNC_STATUSES,
                ),
            )

        rows = self._conn.execute(
            "SELECT id, shopify_customer_id FROM users"
            " WHERE shopify_customer_id IS NOT NULL"
        ).fetchall()
        identities: dict[str, list[sqlite3.Row]] = {}
        invalid_ids: set[str] = set()
        for row in rows:
            try:
                canonical = normalize_customer_id(row["shopify_customer_id"])
            except ValueError:
                invalid_ids.add(row["id"])
                continue
            if canonical is not None:
                identities.setdefault(canonical, []).append(row)

        conflict_ids: set[str] = set()
        for canonical, identity_rows in identities.items():
            if len(identity_rows) > 1:
                conflict_ids.update(row["id"] for row in identity_rows)
                continue
            row = identity_rows[0]
            if row["shopify_customer_id"] != canonical:
                self._conn.execute(
                    "UPDATE users SET shopify_customer_id = ? WHERE id = ?",
                    (canonical, row["id"]),
                )

        # Also catch duplicate opaque legacy IDs without trying to interpret
        # or discard them.
        exact_duplicates = self._conn.execute(
            "SELECT shopify_customer_id FROM users"
            " WHERE shopify_customer_id IS NOT NULL"
            " GROUP BY shopify_customer_id HAVING COUNT(*) > 1"
        ).fetchall()
        for duplicate in exact_duplicates:
            conflict_ids.update(
                row["id"]
                for row in self._conn.execute(
                    "SELECT id FROM users WHERE shopify_customer_id = ?",
                    (duplicate["shopify_customer_id"],),
                ).fetchall()
            )

        if invalid_ids:
            self._conn.executemany(
                "UPDATE users SET shopify_sync_status = ?,"
                " shopify_sync_error = ?,"
                " shopify_sync_next_attempt_at = NULL,"
                " shopify_sync_attempt_token = NULL WHERE id = ?",
                (
                    (
                        SHOPIFY_SYNC_REQUIRES_REVIEW,
                        _SHOPIFY_INVALID_ID_ERROR,
                        user_id,
                    )
                    for user_id in invalid_ids
                ),
            )
        if conflict_ids:
            self._conn.executemany(
                "UPDATE users SET shopify_sync_status = ?,"
                " shopify_sync_error = ?,"
                " shopify_sync_next_attempt_at = NULL,"
                " shopify_sync_attempt_token = NULL WHERE id = ?",
                (
                    (
                        SHOPIFY_SYNC_REQUIRES_REVIEW,
                        _SHOPIFY_IDENTITY_CONFLICT_ERROR,
                        user_id,
                    )
                    for user_id in conflict_ids
                ),
            )
            return

        self._conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS"
            f" {_SHOPIFY_CUSTOMER_UNIQUE_INDEX}"
            " ON users(shopify_customer_id)"
            " WHERE shopify_customer_id IS NOT NULL"
        )

    def _prepare_shopify_customer_account_schema(self) -> None:
        """Guard the future Customer Account subject mapping at startup.

        An old database could have been manually edited before this column
        existed.  Do not pick a winner if that happened: mark every affected
        row for review and defer the unique index until an operator resolves
        the conflict.  New links still reject collisions transactionally.
        """

        valid_placeholders = ", ".join(
            "?" for _ in SHOPIFY_ACCOUNT_MIGRATION_STATES
        )
        self._conn.execute(
            "UPDATE users SET shopify_account_migration_state = ?"
            " WHERE shopify_account_migration_state IS NULL"
            " OR shopify_account_migration_state NOT IN ("
            + valid_placeholders
            + ")",
            (SHOPIFY_ACCOUNT_MANUAL_REVIEW, *SHOPIFY_ACCOUNT_MIGRATION_STATES),
        )
        duplicate_subjects = self._conn.execute(
            "SELECT shopify_account_subject FROM users"
            " WHERE shopify_account_subject IS NOT NULL"
            " GROUP BY shopify_account_subject HAVING COUNT(*) > 1"
        ).fetchall()
        if duplicate_subjects:
            subjects = [str(row["shopify_account_subject"]) for row in duplicate_subjects]
            placeholders = ", ".join("?" for _ in subjects)
            self._conn.execute(
                "UPDATE users SET shopify_account_migration_state = ?"
                f" WHERE shopify_account_subject IN ({placeholders})",
                (SHOPIFY_ACCOUNT_MANUAL_REVIEW, *subjects),
            )
            return
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS"
            " users_shopify_account_subject_unique"
            " ON users(shopify_account_subject)"
            " WHERE shopify_account_subject IS NOT NULL"
        )

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

    @staticmethod
    def _privacy_snapshot_matches_customer(
        snapshot: object,
        *,
        customer_id: str | None,
        order_ids: set[str],
        emails: set[str],
        user_ids: set[str],
    ) -> bool:
        if not isinstance(snapshot, dict):
            return False

        request = snapshot.get("request")
        if isinstance(request, dict):
            if (
                customer_id is not None
                and str(request.get("customer_id") or "") == customer_id
            ):
                return True
            requested_orders = request.get("order_ids")
            if isinstance(requested_orders, list) and order_ids.intersection(
                str(value) for value in requested_orders
            ):
                return True

        collection_fields = {
            "accounts": ("id", "email", "shopify_customer_id"),
            "shopify_orders": (
                "order_id",
                "email",
                "user_id",
                "shopify_customer_id",
            ),
            "gear_orders": ("order_id", "email"),
            "pending_grants": ("email",),
            "pending_customer_links": ("customer_id", "email"),
            "customer_tombstones": ("customer_id", "former_user_id"),
            "analyses": ("user_id",),
            "golfer_profiles": ("user_id",),
            "practice_checkins": ("user_id",),
            "proof_cycle_practice_evidence": ("user_id",),
            "proof_cycle_transfer_checks": ("user_id",),
            "product_events": ("user_id",),
            "mobile_api_tokens": ("user_id",),
        }
        for collection, fields in collection_fields.items():
            rows = snapshot.get(collection)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for field in fields:
                    value = str(row.get(field) or "")
                    if (
                        field == "shopify_customer_id"
                        or field == "customer_id"
                    ):
                        if customer_id is not None and value == customer_id:
                            return True
                    elif field == "order_id" and value in order_ids:
                        return True
                    elif field == "email" and value in emails:
                        return True
                    elif field in {"id", "user_id", "former_user_id"}:
                        if value in user_ids:
                            return True

        authentication = snapshot.get("authentication_metadata")
        if isinstance(authentication, dict):
            for collection in ("email_codes", "signup_intents"):
                rows = authentication.get(collection)
                if not isinstance(rows, list):
                    continue
                if any(
                    isinstance(row, dict)
                    and str(row.get("email") or "") in emails
                    for row in rows
                ):
                    return True
        return False

    def _delete_user_children_locked(
        self, user_ids: Iterable[object]
    ) -> None:
        """Delete non-job rows owned by users while a write lock is held."""

        identifiers = tuple(
            sorted(
                {
                    str(user_id)
                    for user_id in user_ids
                    if str(user_id or "").strip()
                }
            )
        )
        if not identifiers:
            return
        placeholders = ", ".join("?" for _ in identifiers)
        for table in (
            "golfer_profiles",
            "practice_checkins",
            "proof_cycle_practice_evidence",
            "proof_cycle_transfer_checks",
            "product_events",
            "mobile_api_tokens",
            "shopify_customer_account_oauth_states",
            "shopify_customer_account_browser_sessions",
            "lifecycle_email_sends",
        ):
            self._conn.execute(
                f"DELETE FROM {table} WHERE user_id IN ({placeholders})",
                identifiers,
            )

    def _erase_customer_shopify_data(
        self,
        customer_id: str | None,
        email: str,
        *,
        order_ids: tuple[str, ...] = (),
        user_id: str | None = None,
        include_unscoped_email: bool = True,
    ) -> None:
        """Erase one subject's Shopify PII while keeping app entitlements.

        ``users.pro_until`` and analysis history are app-owned aggregates and
        deliberately survive on claimed accounts. The raw customer id remains
        only in the minimal redaction tombstone needed to reject delayed
        Shopify webhooks; that suppression record retains no email or local
        account id.
        """

        email = email.strip().lower()
        explicit_order_ids = set(order_ids)
        clauses: list[str] = []
        params: list[object] = []
        if customer_id:
            clauses.append("shopify_customer_id = ?")
            params.append(customer_id)
        if explicit_order_ids:
            placeholders = ", ".join("?" for _ in explicit_order_ids)
            clauses.append(f"order_id IN ({placeholders})")
            params.extend(sorted(explicit_order_ids))
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)

        other_customer = None
        if email and customer_id:
            other_customer = self._conn.execute(
                "SELECT 1 FROM shopify_orders"
                " WHERE email = ?"
                "   AND shopify_customer_id IS NOT NULL"
                "   AND shopify_customer_id != ? LIMIT 1",
                (email, customer_id),
            ).fetchone()
        if email and include_unscoped_email and other_customer is None:
            clauses.append(
                "(email = ? AND shopify_customer_id IS NULL)"
            )
            params.append(email)

        rows = (
            self._conn.execute(
                "SELECT order_id, email, user_id, pending_days"
                " FROM shopify_orders WHERE " + " OR ".join(clauses),
                tuple(params),
            ).fetchall()
            if clauses
            else []
        )
        matched_order_ids = {
            str(row["order_id"]) for row in rows
        } | explicit_order_ids
        safe_email_scope = bool(
            email and include_unscoped_email and other_customer is None
        )
        matched_emails = (
            {
                str(row["email"]) for row in rows if row["email"]
            }
            if safe_email_scope
            else set()
        )
        if safe_email_scope:
            matched_emails.add(email)
        matched_user_ids = {
            str(row["user_id"]) for row in rows if row["user_id"]
        }
        if user_id:
            matched_user_ids.add(user_id)

        pending_by_email: dict[str, float] = {}
        for row in rows:
            row_email = str(row["email"] or "")
            pending_by_email[row_email] = (
                pending_by_email.get(row_email, 0.0)
                + float(row["pending_days"] or 0.0)
            )
        for row_email, amount in pending_by_email.items():
            if amount <= 0:
                continue
            grant = self._conn.execute(
                "SELECT days FROM pro_grants WHERE email = ?",
                (row_email,),
            ).fetchone()
            available = float(grant["days"]) if grant is not None else 0.0
            if amount > available + 0.001:
                raise RuntimeError(
                    "Shopify redaction value exceeds its email aggregate"
                )
            self._conn.execute(
                "UPDATE pro_grants SET days = MAX(0, days - ?)"
                " WHERE email = ?",
                (amount, row_email),
            )
            self._conn.execute(
                "DELETE FROM pro_grants"
                " WHERE email = ? AND days <= 0.000001",
                (row_email,),
            )
        if safe_email_scope:
            # With no distinct customer at this address, any orphaned
            # email-keyed Shopify aggregate is attributable to the subject
            # even if its original order row predates the current schema.
            self._conn.execute(
                "DELETE FROM pro_grants WHERE email = ?",
                (email,),
            )

        if matched_order_ids:
            self._fence_redacted_orders_locked(
                matched_order_ids,
                now=time.time(),
            )
            placeholders = ", ".join("?" for _ in matched_order_ids)
            parameters = tuple(sorted(matched_order_ids))
            self._conn.execute(
                f"DELETE FROM gear_orders"
                f" WHERE order_id IN ({placeholders})",
                parameters,
            )
            self._conn.execute(
                f"DELETE FROM shopify_orders"
                f" WHERE order_id IN ({placeholders})",
                parameters,
            )
        if safe_email_scope:
            # Legacy gear-only orders had no customer-id column. Email is safe
            # to use only when no different customer is known at the address.
            self._conn.execute(
                "DELETE FROM gear_orders WHERE email = ?",
                (email,),
            )

        privacy_rows = self._conn.execute(
            "SELECT request_id, snapshot_json"
            " FROM shopify_privacy_requests"
        ).fetchall()
        privacy_request_ids: list[str] = []
        for row in privacy_rows:
            try:
                snapshot = json.loads(str(row["snapshot_json"]))
            except (TypeError, ValueError):
                # A malformed export cannot be proven to exclude the subject,
                # so privacy erasure fails safe by purging it.
                privacy_request_ids.append(str(row["request_id"]))
                continue
            if (
                not isinstance(snapshot, dict)
                or snapshot.get("schema_version") != 1
            ):
                privacy_request_ids.append(str(row["request_id"]))
                continue
            if self._privacy_snapshot_matches_customer(
                snapshot,
                customer_id=customer_id,
                order_ids=matched_order_ids,
                emails=matched_emails,
                user_ids=matched_user_ids,
            ):
                privacy_request_ids.append(str(row["request_id"]))
        if privacy_request_ids:
            placeholders = ", ".join("?" for _ in privacy_request_ids)
            self._conn.execute(
                f"DELETE FROM shopify_privacy_requests"
                f" WHERE request_id IN ({placeholders})",
                tuple(privacy_request_ids),
            )
        self._delete_user_children_locked(matched_user_ids)

    # -- Shopify mandatory privacy workflows -----------------------------
    @staticmethod
    def _privacy_shop_domain(value: object) -> str:
        domain = (
            str(value or "")
            .strip()
            .lower()
            .removeprefix("https://")
            .removeprefix("http://")
            .strip("/")
        )
        if not domain or "/" in domain or len(domain) > 255:
            raise ValueError("Invalid Shopify privacy shop domain.")
        return domain

    @staticmethod
    def _privacy_order_ids(values: object) -> tuple[str, ...]:
        if values is None:
            return ()
        if not isinstance(values, (list, tuple)):
            raise ValueError("Invalid Shopify privacy order list.")
        if len(values) > _SHOPIFY_PRIVACY_ORDER_MAX:
            raise ValueError("Shopify privacy order list is too large.")
        normalized: set[str] = set()
        for value in values:
            order_id = str(value or "").strip()
            if not order_id or len(order_id) > _SHOPIFY_PRIVACY_ID_MAX:
                raise ValueError("Invalid Shopify privacy order id.")
            normalized.add(order_id)
        return tuple(sorted(normalized))

    @staticmethod
    def _privacy_customer_id(value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        try:
            return normalize_customer_id(value)
        except ValueError:
            raise ValueError("Invalid Shopify privacy customer id.") from None

    @staticmethod
    def _privacy_request_id(
        shop_domain: str,
        customer_id: str | None,
        order_ids: tuple[str, ...],
        event_id: str | None,
    ) -> str:
        opaque_event = str(event_id or "").strip()
        if len(opaque_event) > _SHOPIFY_PRIVACY_ID_MAX:
            raise ValueError("Invalid Shopify privacy event id.")
        if opaque_event:
            identity = f"event\0{opaque_event}"
        else:
            # Compatibility fallback for direct callers and older routes that
            # do not yet forward X-Shopify-Webhook-Id. The target fingerprint
            # makes exact retries idempotent for the retention window.
            identity = json.dumps(
                {
                    "customer_id": customer_id,
                    "order_ids": order_ids,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        digest = hashlib.sha256(
            f"shopify-privacy-v1\0{shop_domain}\0{identity}".encode("utf-8")
        ).hexdigest()
        return f"{_SHOPIFY_PRIVACY_REQUEST_PREFIX}{digest[:32]}"

    @staticmethod
    def _privacy_event_key(
        topic: str,
        shop_domain: str,
        event_id: str | None,
    ) -> str | None:
        if event_id is None:
            return None
        raw_event_id = str(event_id or "").strip()
        if (
            not raw_event_id
            or len(raw_event_id) > _SHOPIFY_PRIVACY_ID_MAX
        ):
            raise ValueError("Invalid Shopify privacy event id.")
        # Keep no independently dictionary-testable shop-domain digest. The
        # high-entropy delivery id scopes this opaque composite receipt while
        # preventing recovery of the store identifier after shop erasure.
        return hashlib.sha256(
            f"{topic}\0{shop_domain}\0{raw_event_id}".encode("utf-8")
        ).hexdigest()

    def _privacy_event_claimed_locked(
        self,
        topic: str,
        shop_domain: str,
        event_id: str | None,
    ) -> bool:
        event_key = self._privacy_event_key(topic, shop_domain, event_id)
        if event_key is None:
            return False
        return (
            self._conn.execute(
                "SELECT 1 FROM shopify_privacy_event_fences"
                " WHERE topic = ? AND event_key = ?",
                (topic, event_key),
            ).fetchone()
            is not None
        )

    def _claim_privacy_event_locked(
        self,
        topic: str,
        shop_domain: str,
        event_id: str | None,
        *,
        now: float,
    ) -> bool:
        """Claim one compliance delivery inside its mutation transaction.

        ``None`` is reserved for trusted direct/internal callers. The signed
        webhook boundary requires a delivery id before reaching this method.
        Only one-way hashes and the non-PII canonical topic are retained.
        """

        event_key = self._privacy_event_key(topic, shop_domain, event_id)
        if event_key is None:
            return True
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO shopify_privacy_event_fences"
            " (topic, event_key, applied_at) VALUES (?, ?, ?)",
            (topic, event_key, now),
        )
        return cursor.rowcount == 1

    def _redacted_order_key_locked(self, order_id: object) -> str:
        order_value = str(order_id or "").strip()
        secret_row = self._conn.execute(
            "SELECT order_fence_secret FROM shopify_sync_control"
            " WHERE id = 1"
        ).fetchone()
        if (
            not order_value
            or secret_row is None
            or not str(secret_row["order_fence_secret"] or "")
        ):
            raise RuntimeError("Shopify order privacy fence is unavailable.")
        try:
            key = bytes.fromhex(str(secret_row["order_fence_secret"]))
        except ValueError:
            raise RuntimeError(
                "Shopify order privacy fence is unavailable."
            ) from None
        return hmac.new(
            key,
            f"shopify-redacted-order-v1\0{order_value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _shopify_order_is_redacted_locked(self, order_id: object) -> bool:
        order_key = self._redacted_order_key_locked(order_id)
        return (
            self._conn.execute(
                "SELECT 1 FROM shopify_redacted_order_fences"
                " WHERE order_key = ?",
                (order_key,),
            ).fetchone()
            is not None
        )

    def _fence_redacted_orders_locked(
        self,
        order_ids: set[str],
        *,
        now: float,
    ) -> None:
        for order_id in order_ids:
            self._conn.execute(
                "INSERT OR IGNORE INTO shopify_redacted_order_fences"
                " (order_key, redacted_at) VALUES (?, ?)",
                (self._redacted_order_key_locked(order_id), now),
            )

    @staticmethod
    def _privacy_request_from_row(row: sqlite3.Row) -> ShopifyPrivacyRequest:
        return ShopifyPrivacyRequest(
            request_id=str(row["request_id"]),
            status=str(row["status"]),
            record_count=int(row["record_count"]),
            snapshot_bytes=int(row["snapshot_bytes"]),
            created_at=float(row["created_at"]),
            completed_at=float(row["completed_at"]),
            expires_at=float(row["expires_at"]),
            delivered_at=(
                float(row["delivered_at"])
                if row["delivered_at"] is not None
                else None
            ),
        )

    def _privacy_artifact_inventory(
        self, job_ids: set[str]
    ) -> list[dict[str, object]]:
        """Inventory session files without following a forged DB path.

        Binary uploads and reports remain in their existing session folders.
        The durable request snapshot records exactly which files an operator
        must package with the JSON export; it does not duplicate potentially
        large videos inside SQLite.
        """

        if not job_ids or str(self._db_path) == ":memory:":
            return []
        base = self._db_path.parent.resolve()
        inventory: list[dict[str, object]] = []
        for job_id in sorted(job_ids):
            session = (base / job_id).resolve()
            try:
                session.relative_to(base)
            except ValueError:
                inventory.append(
                    {
                        "job_id": job_id,
                        "inventory_complete": False,
                        "files": [],
                    }
                )
                continue
            files: list[dict[str, object]] = []
            complete = True
            if session.is_dir():
                try:
                    candidates = sorted(session.rglob("*"))
                except OSError:
                    candidates = []
                    complete = False
                for candidate in candidates:
                    try:
                        resolved = candidate.resolve()
                        resolved.relative_to(session)
                        if not resolved.is_file():
                            continue
                        stat = resolved.stat()
                        files.append(
                            {
                                "path": resolved.relative_to(session).as_posix(),
                                "bytes": int(stat.st_size),
                                "modified_at": float(stat.st_mtime),
                            }
                        )
                    except (OSError, ValueError):
                        complete = False
            inventory.append(
                {
                    "job_id": job_id,
                    "inventory_complete": complete,
                    "files": files,
                }
            )
        return inventory

    def _build_shopify_privacy_snapshot(
        self,
        *,
        captured_at: float,
        shop_domain: str,
        customer_id: str | None,
        requested_order_ids: tuple[str, ...],
        accounts: list[sqlite3.Row],
        shopify_orders: list[sqlite3.Row],
        gear_rows: list[sqlite3.Row],
        pending_grants: list[sqlite3.Row],
        pending_customer_links: list[sqlite3.Row],
        tombstones: list[sqlite3.Row],
        jobs: list[sqlite3.Row],
        golfer_profiles: list[sqlite3.Row],
        practice_checkins: list[sqlite3.Row],
        proof_cycle_practice_evidence: list[sqlite3.Row],
        proof_cycle_transfer_checks: list[sqlite3.Row],
        product_events: list[sqlite3.Row],
        mobile_api_tokens: list[sqlite3.Row],
        email_codes: list[sqlite3.Row],
        signup_intents: list[sqlite3.Row],
    ) -> tuple[str, str, int, int]:
        """Inventory files and encode a captured DB view without DB locks."""

        account_exports = []
        for row in accounts:
            account_exports.append(
                {
                    key: row[key]
                    for key in row.keys()
                    if key
                    not in {
                        "password_hash",
                        "shopify_sync_attempt_token",
                    }
                }
                | {
                    "password_configured": bool(row["password_hash"]),
                    "shopify_sync_attempt_active": bool(
                        row["shopify_sync_attempt_token"]
                    ),
                }
            )
        job_ids = {str(row["id"]) for row in jobs}
        artifact_inventory = self._privacy_artifact_inventory(job_ids)
        snapshot = {
            "schema_version": 1,
            "captured_at": captured_at,
            "request": {
                "shop_domain": shop_domain,
                "customer_id": customer_id,
                "order_ids": list(requested_order_ids),
            },
            "accounts": account_exports,
            "shopify_orders": [dict(row) for row in shopify_orders],
            "gear_orders": [dict(row) for row in gear_rows],
            "pending_grants": [dict(row) for row in pending_grants],
            "pending_customer_links": [
                dict(row) for row in pending_customer_links
            ],
            "customer_tombstones": [dict(row) for row in tombstones],
            "analyses": [dict(row) for row in jobs],
            "golfer_profiles": [dict(row) for row in golfer_profiles],
            "practice_checkins": [dict(row) for row in practice_checkins],
            "proof_cycle_practice_evidence": [
                dict(row) for row in proof_cycle_practice_evidence
            ],
            "proof_cycle_transfer_checks": [
                dict(row) for row in proof_cycle_transfer_checks
            ],
            "product_events": [dict(row) for row in product_events],
            # The exported device inventory intentionally omits both the
            # credential digest and auth epoch.  It is enough for a customer
            # to identify a device record without turning a privacy archive
            # into an authentication or correlation dataset.
            "mobile_api_tokens": [
                {
                    "selector": row["selector"],
                    "user_id": row["user_id"],
                    "label": row["label"],
                    "created_at": row["created_at"],
                    "last_used_at": row["last_used_at"],
                    "expires_at": row["expires_at"],
                    "revoked_at": row["revoked_at"],
                }
                for row in mobile_api_tokens
            ],
            "session_artifacts": artifact_inventory,
            # Credential hashes and one-time secrets are never copied into a
            # customer-facing export. Lifecycle metadata is sufficient.
            "authentication_metadata": {
                "email_codes": [dict(row) for row in email_codes],
                "signup_intents": [dict(row) for row in signup_intents],
            },
        }
        snapshot_json = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = snapshot_json.encode("utf-8")
        record_count = sum(
            len(records)
            for records in (
                accounts,
                shopify_orders,
                gear_rows,
                pending_grants,
                pending_customer_links,
                tombstones,
                jobs,
                golfer_profiles,
                practice_checkins,
                proof_cycle_practice_evidence,
                proof_cycle_transfer_checks,
                product_events,
                mobile_api_tokens,
                email_codes,
                signup_intents,
            )
        ) + sum(
            len(item["files"])
            for item in artifact_inventory
        )
        return (
            snapshot_json,
            hashlib.sha256(encoded).hexdigest(),
            len(encoded),
            record_count,
        )

    def capture_shopify_data_request(
        self,
        *,
        shop_domain: str,
        configured_shop_domain: str,
        customer_id: object | None,
        order_ids: object,
        event_id: str | None = None,
        now: float | None = None,
        retention_s: float = SHOPIFY_PRIVACY_RETENTION_S,
        include_replay_status: bool = False,
    ) -> (
        ShopifyPrivacyRequest
        | None
        | tuple[ShopifyPrivacyRequest | None, bool]
    ):
        """Capture a replay-idempotent customer data snapshot.

        ``None`` means the signed payload named a shop other than the exact
        configured store. Database rows come from one stable read transaction.
        Filesystem inventory and JSON encoding happen after that transaction;
        a short final transaction atomically claims the delivery and publishes
        the ready snapshot. The privacy ordering lock prevents redaction from
        interleaving with those phases. The returned object contains no
        customer data.
        """

        def capture_result(
            request: ShopifyPrivacyRequest | None,
            *,
            replayed: bool,
        ):
            return (
                (request, replayed)
                if include_replay_status
                else request
            )

        supplied_shop = self._privacy_shop_domain(shop_domain)
        configured_shop = self._privacy_shop_domain(configured_shop_domain)
        if not hmac.compare_digest(supplied_shop, configured_shop):
            return capture_result(None, replayed=False)
        normalized_customer_id = self._privacy_customer_id(customer_id)
        normalized_order_ids = self._privacy_order_ids(order_ids)
        request_id = self._privacy_request_id(
            configured_shop,
            normalized_customer_id,
            normalized_order_ids,
            event_id,
        )
        now = time.time() if now is None else float(now)
        retention_s = float(retention_s)
        if retention_s <= 0:
            raise ValueError("Shopify privacy retention must be positive.")

        with shopify_remote_privacy_lock(self._db_path), self._lock:
            try:
                self._conn.execute("BEGIN")
                event_already_claimed = (
                    self._privacy_event_claimed_locked(
                        _SHOPIFY_PRIVACY_DATA_TOPIC,
                        configured_shop,
                        event_id,
                    )
                )
                existing = self._conn.execute(
                    "SELECT * FROM shopify_privacy_requests"
                    " WHERE request_id = ? AND expires_at > ?",
                    (request_id, now),
                ).fetchone()
                if existing is not None:
                    self._conn.commit()
                    return capture_result(
                        self._privacy_request_from_row(existing),
                        replayed=event_already_claimed,
                    )
                if event_already_claimed:
                    # The preserved receipt proves this delivery completed
                    # before; shop/customer erasure may intentionally have
                    # removed its export snapshot. Never recapture later
                    # state after that erasure.
                    self._conn.commit()
                    self._conn.execute("BEGIN IMMEDIATE")
                    self._conn.execute(
                        "DELETE FROM shopify_privacy_requests"
                        " WHERE expires_at <= ?",
                        (now,),
                    )
                    self._conn.commit()
                    return capture_result(None, replayed=True)

                # Do not claim the delivery until the snapshot is ready. If
                # the process exits during filesystem inventory, a retry can
                # safely rebuild rather than finding a receipt with no export.

                target_user_ids: set[str] = set()
                if normalized_customer_id is not None:
                    target_user_ids.update(
                        str(row["id"])
                        for row in self._conn.execute(
                            "SELECT id FROM users"
                            " WHERE shopify_customer_id = ?",
                            (normalized_customer_id,),
                        ).fetchall()
                    )
                    target_user_ids.update(
                        str(row["former_user_id"])
                        for row in self._conn.execute(
                            "SELECT former_user_id"
                            " FROM shopify_customer_tombstones"
                            " WHERE customer_id = ?"
                            " AND former_user_id IS NOT NULL",
                            (normalized_customer_id,),
                        ).fetchall()
                    )

                order_clauses: list[str] = []
                order_params: list[object] = []
                if normalized_customer_id is not None:
                    order_clauses.append("shopify_customer_id = ?")
                    order_params.append(normalized_customer_id)
                if normalized_order_ids:
                    placeholders = ", ".join("?" for _ in normalized_order_ids)
                    order_clauses.append(f"order_id IN ({placeholders})")
                    order_params.extend(normalized_order_ids)
                initial_orders = (
                    self._conn.execute(
                        "SELECT * FROM shopify_orders WHERE "
                        + " OR ".join(order_clauses)
                        + " ORDER BY applied_at, order_id",
                        tuple(order_params),
                    ).fetchall()
                    if order_clauses
                    else []
                )
                target_user_ids.update(
                    str(row["user_id"])
                    for row in initial_orders
                    if row["user_id"] is not None
                )

                all_order_clauses = list(order_clauses)
                all_order_params = list(order_params)
                if target_user_ids:
                    placeholders = ", ".join("?" for _ in target_user_ids)
                    all_order_clauses.append(f"user_id IN ({placeholders})")
                    all_order_params.extend(sorted(target_user_ids))
                shopify_orders = (
                    self._conn.execute(
                        "SELECT * FROM shopify_orders WHERE "
                        + " OR ".join(all_order_clauses)
                        + " ORDER BY applied_at, order_id",
                        tuple(all_order_params),
                    ).fetchall()
                    if all_order_clauses
                    else []
                )
                relevant_order_ids = {
                    str(row["order_id"]) for row in shopify_orders
                } | set(normalized_order_ids)

                accounts: list[sqlite3.Row] = []
                if target_user_ids:
                    placeholders = ", ".join("?" for _ in target_user_ids)
                    accounts = self._conn.execute(
                        f"SELECT * FROM users WHERE id IN ({placeholders})"
                        " ORDER BY created_at, id",
                        tuple(sorted(target_user_ids)),
                    ).fetchall()

                gear_rows: list[sqlite3.Row] = []
                if relevant_order_ids:
                    placeholders = ", ".join("?" for _ in relevant_order_ids)
                    gear_rows = self._conn.execute(
                        f"SELECT * FROM gear_orders"
                        f" WHERE order_id IN ({placeholders})"
                        " ORDER BY created_at, order_id, sku, title",
                        tuple(sorted(relevant_order_ids)),
                    ).fetchall()

                emails = {
                    str(row["email"]) for row in accounts
                } | {
                    str(row["email"]) for row in shopify_orders
                } | {
                    str(row["email"]) for row in gear_rows
                }
                pending_grants: list[sqlite3.Row] = []
                email_codes: list[sqlite3.Row] = []
                signup_intents: list[sqlite3.Row] = []
                if emails:
                    placeholders = ", ".join("?" for _ in emails)
                    params = tuple(sorted(emails))
                    pending_grants = self._conn.execute(
                        f"SELECT email, days FROM pro_grants"
                        f" WHERE email IN ({placeholders}) ORDER BY email",
                        params,
                    ).fetchall()
                    email_codes = self._conn.execute(
                        "SELECT email, purpose, created_at, expires_at,"
                        f" attempts FROM email_codes"
                        f" WHERE email IN ({placeholders})"
                        " ORDER BY email, purpose",
                        params,
                    ).fetchall()
                    signup_intents = self._conn.execute(
                        "SELECT email, digest_opt_in, created_at, expires_at"
                        f" FROM signup_intents"
                        f" WHERE email IN ({placeholders}) ORDER BY email",
                        params,
                    ).fetchall()

                tombstone_customer_ids = {
                    str(row["shopify_customer_id"])
                    for row in shopify_orders
                    if row["shopify_customer_id"] is not None
                }
                if normalized_customer_id is not None:
                    tombstone_customer_ids.add(normalized_customer_id)
                tombstones: list[sqlite3.Row] = []
                pending_customer_links: list[sqlite3.Row] = []
                if tombstone_customer_ids:
                    placeholders = ", ".join(
                        "?" for _ in tombstone_customer_ids
                    )
                    tombstones = self._conn.execute(
                        "SELECT * FROM shopify_customer_tombstones"
                        f" WHERE customer_id IN ({placeholders})"
                        " ORDER BY deleted_at, customer_id",
                        tuple(sorted(tombstone_customer_ids)),
                    ).fetchall()
                    pending_customer_links = self._conn.execute(
                        "SELECT * FROM shopify_pending_customer_links"
                        f" WHERE customer_id IN ({placeholders})"
                        " ORDER BY created_at, customer_id",
                        tuple(sorted(tombstone_customer_ids)),
                    ).fetchall()
                if emails:
                    placeholders = ", ".join("?" for _ in emails)
                    by_email = self._conn.execute(
                        "SELECT * FROM shopify_pending_customer_links"
                        f" WHERE email IN ({placeholders})"
                        " ORDER BY created_at, customer_id",
                        tuple(sorted(emails)),
                    ).fetchall()
                    seen_pending_ids = {
                        str(row["customer_id"])
                        for row in pending_customer_links
                    }
                    pending_customer_links.extend(
                        row
                        for row in by_email
                        if str(row["customer_id"]) not in seen_pending_ids
                    )

                jobs: list[sqlite3.Row] = []
                golfer_profiles: list[sqlite3.Row] = []
                practice_checkins: list[sqlite3.Row] = []
                proof_cycle_practice_evidence: list[sqlite3.Row] = []
                proof_cycle_transfer_checks: list[sqlite3.Row] = []
                product_events: list[sqlite3.Row] = []
                mobile_api_tokens: list[sqlite3.Row] = []
                jobs_table = self._conn.execute(
                    "SELECT 1 FROM sqlite_master"
                    " WHERE type = 'table' AND name = 'jobs'"
                ).fetchone()
                if jobs_table is not None and target_user_ids:
                    placeholders = ", ".join("?" for _ in target_user_ids)
                    jobs = self._conn.execute(
                        f"SELECT * FROM jobs WHERE user_id IN ({placeholders})"
                        " ORDER BY created_at, id",
                        tuple(sorted(target_user_ids)),
                    ).fetchall()
                if target_user_ids:
                    placeholders = ", ".join("?" for _ in target_user_ids)
                    identifiers = tuple(sorted(target_user_ids))
                    golfer_profiles = self._conn.execute(
                        f"SELECT * FROM golfer_profiles WHERE user_id IN ({placeholders})"
                        " ORDER BY user_id",
                        identifiers,
                    ).fetchall()
                    practice_checkins = self._conn.execute(
                        f"SELECT * FROM practice_checkins WHERE user_id IN ({placeholders})"
                        " ORDER BY completed_at, session_id",
                        identifiers,
                    ).fetchall()
                    proof_cycle_practice_evidence = self._conn.execute(
                        "SELECT * FROM proof_cycle_practice_evidence"
                        f" WHERE user_id IN ({placeholders})"
                        " ORDER BY completed_at, baseline_session_id",
                        identifiers,
                    ).fetchall()
                    proof_cycle_transfer_checks = self._conn.execute(
                        "SELECT * FROM proof_cycle_transfer_checks"
                        f" WHERE user_id IN ({placeholders})"
                        " ORDER BY declared_at, session_id",
                        identifiers,
                    ).fetchall()
                    product_events = self._conn.execute(
                        f"SELECT * FROM product_events WHERE user_id IN ({placeholders})"
                        " ORDER BY created_at, id",
                        identifiers,
                    ).fetchall()
                    mobile_api_tokens = self._conn.execute(
                        "SELECT selector, user_id, label, created_at,"
                        " last_used_at, expires_at, revoked_at"
                        f" FROM mobile_api_tokens WHERE user_id IN ({placeholders})"
                        " ORDER BY created_at, selector",
                        identifiers,
                    ).fetchall()

                self._conn.commit()
                self._lock.release()
                try:
                    (
                        snapshot_json,
                        snapshot_sha256,
                        snapshot_bytes,
                        record_count,
                    ) = self._build_shopify_privacy_snapshot(
                        captured_at=now,
                        shop_domain=configured_shop,
                        customer_id=normalized_customer_id,
                        requested_order_ids=normalized_order_ids,
                        accounts=accounts,
                        shopify_orders=shopify_orders,
                        gear_rows=gear_rows,
                        pending_grants=pending_grants,
                        pending_customer_links=pending_customer_links,
                        tombstones=tombstones,
                        jobs=jobs,
                        golfer_profiles=golfer_profiles,
                        practice_checkins=practice_checkins,
                        proof_cycle_practice_evidence=proof_cycle_practice_evidence,
                        proof_cycle_transfer_checks=proof_cycle_transfer_checks,
                        product_events=product_events,
                        mobile_api_tokens=mobile_api_tokens,
                        email_codes=email_codes,
                        signup_intents=signup_intents,
                    )
                finally:
                    self._lock.acquire()

                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "DELETE FROM shopify_privacy_requests"
                    " WHERE expires_at <= ?",
                    (now,),
                )
                existing = self._conn.execute(
                    "SELECT * FROM shopify_privacy_requests"
                    " WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    self._conn.commit()
                    return capture_result(
                        self._privacy_request_from_row(existing),
                        replayed=True,
                    )
                claimed_event = self._claim_privacy_event_locked(
                    _SHOPIFY_PRIVACY_DATA_TOPIC,
                    configured_shop,
                    event_id,
                    now=now,
                )
                if not claimed_event:
                    self._conn.commit()
                    return capture_result(None, replayed=True)
                self._conn.execute(
                    "INSERT INTO shopify_privacy_requests"
                    " (request_id, shop_domain, status, snapshot_json,"
                    "  snapshot_sha256, record_count, snapshot_bytes,"
                    "  created_at, completed_at, expires_at, delivered_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        request_id,
                        configured_shop,
                        SHOPIFY_PRIVACY_READY,
                        snapshot_json,
                        snapshot_sha256,
                        record_count,
                        snapshot_bytes,
                        now,
                        now,
                        now + retention_s,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM shopify_privacy_requests"
                    " WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                self._conn.commit()
                return capture_result(
                    self._privacy_request_from_row(row),
                    replayed=False,
                )
            except Exception:
                self._conn.rollback()
                raise

    def get_shopify_privacy_request(
        self, request_id: str, *, now: float | None = None
    ) -> ShopifyPrivacyRequest | None:
        """Return PII-free status metadata, removing an expired snapshot."""

        request_id = str(request_id or "").strip()
        if not request_id.startswith(_SHOPIFY_PRIVACY_REQUEST_PREFIX):
            return None
        now = time.time() if now is None else float(now)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM shopify_privacy_requests"
                " WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= now:
                self._conn.execute(
                    "DELETE FROM shopify_privacy_requests"
                    " WHERE request_id = ?",
                    (request_id,),
                )
                self._conn.commit()
                return None
            return self._privacy_request_from_row(row)

    def list_shopify_privacy_requests(
        self, limit: int = 100, *, now: float | None = None
    ) -> list[ShopifyPrivacyRequest]:
        """List pending/delivered request metadata without customer fields."""

        limit = _page_limit(limit)
        now = time.time() if now is None else float(now)
        with self._lock:
            self._conn.execute(
                "DELETE FROM shopify_privacy_requests WHERE expires_at <= ?",
                (now,),
            )
            rows = self._conn.execute(
                "SELECT * FROM shopify_privacy_requests"
                " ORDER BY created_at, request_id LIMIT ?",
                (limit,),
            ).fetchall()
            self._conn.commit()
        return [self._privacy_request_from_row(row) for row in rows]

    def export_shopify_privacy_request(
        self, request_id: str, *, now: float | None = None
    ) -> dict[str, object] | None:
        """Return the integrity-checked PII snapshot for operator delivery."""

        request_id = str(request_id or "").strip()
        if not request_id.startswith(_SHOPIFY_PRIVACY_REQUEST_PREFIX):
            return None
        now = time.time() if now is None else float(now)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM shopify_privacy_requests"
                " WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= now:
                self._conn.execute(
                    "DELETE FROM shopify_privacy_requests"
                    " WHERE request_id = ?",
                    (request_id,),
                )
                self._conn.commit()
                return None
            snapshot_json = str(row["snapshot_json"])
            digest = hashlib.sha256(
                snapshot_json.encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(digest, str(row["snapshot_sha256"])):
                raise RuntimeError(
                    "Shopify privacy snapshot integrity check failed."
                )
            snapshot = json.loads(snapshot_json)
        if not isinstance(snapshot, dict):
            raise RuntimeError("Shopify privacy snapshot is invalid.")
        return snapshot

    def mark_shopify_privacy_request_delivered(
        self, request_id: str, *, now: float | None = None
    ) -> ShopifyPrivacyRequest | None:
        """Idempotently stamp operator delivery without extending retention."""

        request_id = str(request_id or "").strip()
        if not request_id.startswith(_SHOPIFY_PRIVACY_REQUEST_PREFIX):
            return None
        now = time.time() if now is None else float(now)
        with self._lock:
            self._conn.execute(
                "UPDATE shopify_privacy_requests"
                " SET status = ?, delivered_at = COALESCE(delivered_at, ?)"
                " WHERE request_id = ? AND expires_at > ?",
                (
                    SHOPIFY_PRIVACY_DELIVERED,
                    now,
                    request_id,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM shopify_privacy_requests"
                " WHERE request_id = ? AND expires_at > ?",
                (request_id, now),
            ).fetchone()
            self._conn.commit()
        return self._privacy_request_from_row(row) if row is not None else None

    def purge_expired_shopify_privacy_requests(
        self, *, now: float | None = None
    ) -> int:
        """Delete snapshots at their explicit retention deadline."""

        now = time.time() if now is None else float(now)
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM shopify_privacy_requests WHERE expires_at <= ?",
                (now,),
            )
            self._conn.commit()
        return int(cursor.rowcount)

    def redact_shopify_store(
        self,
        shop_domain: str,
        configured_shop_domain: str,
        *,
        event_id: str | None,
    ) -> ShopifyShopRedaction:
        """Erase one configured store's local integration state atomically.

        The deployment is intentionally single-store, so an exact shop match
        authorizes clearing the Shopify ledgers and sync metadata. Independent
        CaddieInsight credentials, Stripe state, digest choices, Pro expiry,
        and swing-analysis jobs/files survive. Store-only passwordless users
        with no analysis history are removed because their email originated
        solely from Shopify. Entitlement expiry on retained accounts is not
        guessed from a now-erased mixed grant chain.

        The opaque Shopify delivery id is hashed into a replay fence that is
        deliberately outside the erased integration tables. Therefore the
        same delivery cannot erase state created after a reinstall, while a
        distinct authenticated redaction remains effective.
        """

        supplied_shop = self._privacy_shop_domain(shop_domain)
        configured_shop = self._privacy_shop_domain(configured_shop_domain)
        if not hmac.compare_digest(supplied_shop, configured_shop):
            return ShopifyShopRedaction(applied=False)
        with shopify_remote_privacy_lock(self._db_path), self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                claimed_event = self._claim_privacy_event_locked(
                    _SHOPIFY_PRIVACY_SHOP_REDACT_TOPIC,
                    supplied_shop,
                    event_id,
                    now=time.time(),
                )
                if not claimed_event:
                    self._conn.commit()
                    return ShopifyShopRedaction(
                        applied=False,
                        replayed=True,
                    )
                # Serialize erasure against every outbound remote-write guard,
                # invalidate selected/active attempts, and keep the global
                # fence closed until a newly authenticated explicit rebind.
                now = time.time()
                self._conn.execute(
                    "UPDATE shopify_sync_control SET"
                    " generation = generation + 1,"
                    " shop_redacted = 1, updated_at = ? WHERE id = 1",
                    (now,),
                )
                self._conn.execute(
                    "UPDATE users SET"
                    " shopify_sync_generation ="
                    "   shopify_sync_generation + 1,"
                    " shopify_sync_attempt_token = NULL"
                )
                jobs_table = self._conn.execute(
                    "SELECT 1 FROM sqlite_master"
                    " WHERE type = 'table' AND name = 'jobs'"
                ).fetchone()
                active_user_ids = (
                    {
                        str(row["user_id"])
                        for row in self._conn.execute(
                            "SELECT DISTINCT user_id FROM jobs"
                            " WHERE user_id IS NOT NULL"
                        ).fetchall()
                    }
                    if jobs_table is not None
                    else set()
                )
                store_state = (
                    "(source = 'shopify'"
                    " OR shopify_customer_id IS NOT NULL"
                    " OR shopify_identity_locked = 1"
                    " OR shopify_sync_status != 'not_started'"
                    " OR EXISTS (SELECT 1 FROM shopify_orders"
                    "            WHERE shopify_orders.user_id = users.id)"
                    " OR EXISTS (SELECT 1"
                    "   FROM shopify_pending_customer_links"
                    "  WHERE shopify_pending_customer_links.email"
                    "        = users.email))"
                )
                store_rows = self._conn.execute(
                    "SELECT * FROM users WHERE " + store_state
                ).fetchall()
                store_only_rows = [
                    row
                    for row in store_rows
                    if not self._user_row_is_claimed(row)
                    and str(row["id"]) not in active_user_ids
                ]
                store_only_ids = [str(row["id"]) for row in store_only_rows]
                store_only_emails = [
                    str(row["email"]) for row in store_only_rows
                ]
                preserved_accounts = len(store_rows) - len(store_only_ids)
                if store_only_emails:
                    placeholders = ", ".join("?" for _ in store_only_emails)
                    self._conn.execute(
                        f"DELETE FROM email_codes"
                        f" WHERE email IN ({placeholders})",
                        tuple(store_only_emails),
                    )
                if store_only_ids:
                    placeholders = ", ".join("?" for _ in store_only_ids)
                    self._delete_user_children_locked(store_only_ids)
                    self._conn.execute(
                        f"DELETE FROM users WHERE id IN ({placeholders})",
                        tuple(store_only_ids),
                    )

                # Reset every user's store-specific fields. This includes
                # outbound backfill state on accounts that were never linked.
                self._conn.execute(
                    "UPDATE users SET"
                    " shopify_customer_id = NULL,"
                    " shopify_identity_locked = 0,"
                    " shopify_updated_at = NULL,"
                    " source = CASE WHEN source = 'shopify'"
                    "               THEN NULL ELSE source END,"
                    " shopify_sync_status = ?,"
                    " shopify_last_synced_at = NULL,"
                    " shopify_sync_error = NULL,"
                    " shopify_sync_attempts = 0,"
                    " shopify_sync_next_attempt_at = NULL,"
                    " shopify_sync_attempt_token = NULL,"
                    " shopify_account_migration_state = CASE"
                    "   WHEN shopify_account_subject IS NOT NULL"
                    "     OR shopify_account_migration_state != ?"
                    "   THEN ? ELSE shopify_account_migration_state END,"
                    " shopify_account_subject = NULL,"
                    " shopify_account_linked_at = NULL,"
                    " shopify_account_last_login_at = NULL",
                    (
                        SHOPIFY_SYNC_NOT_STARTED,
                        SHOPIFY_ACCOUNT_LOCAL_ONLY,
                        SHOPIFY_ACCOUNT_REDACTED,
                    ),
                )
                removed_orders = int(
                    self._conn.execute(
                        "SELECT COUNT(*) FROM shopify_orders"
                    ).fetchone()[0]
                )
                removed_gear_rows = int(
                    self._conn.execute(
                        "SELECT COUNT(*) FROM gear_orders"
                    ).fetchone()[0]
                )
                removed_pending_grants = int(
                    self._conn.execute(
                        "SELECT COUNT(*) FROM pro_grants"
                    ).fetchone()[0]
                )
                removed_privacy_requests = int(
                    self._conn.execute(
                        "SELECT COUNT(*) FROM shopify_privacy_requests"
                    ).fetchone()[0]
                )
                redacted_order_ids = {
                    str(row["order_id"])
                    for row in self._conn.execute(
                        "SELECT order_id FROM shopify_orders"
                        " UNION SELECT order_id FROM gear_orders"
                    ).fetchall()
                }
                self._fence_redacted_orders_locked(
                    redacted_order_ids,
                    now=now,
                )
                self._conn.execute("DELETE FROM shopify_orders")
                self._conn.execute("DELETE FROM gear_orders")
                self._conn.execute("DELETE FROM pro_grants")
                # Order-keyed send markers are Shopify-derived data too.
                self._conn.execute(
                    "DELETE FROM lifecycle_email_sends"
                    " WHERE kind LIKE 'shopify\\_%' ESCAPE '\\'"
                )
                self._conn.execute("DELETE FROM shopify_customer_tombstones")
                self._conn.execute(
                    "DELETE FROM shopify_pending_customer_links"
                )
                self._conn.execute("DELETE FROM signup_intents")
                self._conn.execute("DELETE FROM shopify_privacy_requests")
                self._conn.execute(
                    "DELETE FROM shopify_customer_account_oauth_states"
                )
                self._conn.execute(
                    "DELETE FROM shopify_customer_account_browser_sessions"
                )
                binding_table = self._conn.execute(
                    "SELECT 1 FROM sqlite_master"
                    " WHERE type = 'table'"
                    " AND name = 'shopify_customer_backfill_binding'"
                ).fetchone()
                if binding_table is not None:
                    self._conn.execute(
                        "DELETE FROM shopify_customer_backfill_binding"
                    )
                self._conn.commit()
                return ShopifyShopRedaction(
                    applied=True,
                    removed_store_only_users=len(store_only_ids),
                    preserved_accounts=max(0, preserved_accounts),
                    removed_orders=removed_orders,
                    removed_gear_rows=removed_gear_rows,
                    removed_pending_grants=removed_pending_grants,
                    removed_privacy_requests=removed_privacy_requests,
                )
            except Exception:
                self._conn.rollback()
                raise

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

    @staticmethod
    def _hash_signup_intent_token(token: str) -> str | None:
        token = str(token or "").strip()
        if not token or len(token) > _SIGNUP_INTENT_TOKEN_MAX:
            return None
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_flow_session_nonce(nonce: str | None) -> str | None:
        if nonce is None:
            return None
        raw = str(nonce).strip()
        if not raw or len(raw) > _FLOW_SESSION_NONCE_MAX:
            return None
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _flow_session_nonce_matches(
        stored_hash: object | None, supplied_hash: str | None
    ) -> bool:
        if stored_hash is None:
            return supplied_hash is None
        if supplied_hash is None:
            return False
        return hmac.compare_digest(str(stored_hash), supplied_hash)

    def issue_signup_intent(
        self,
        email: str,
        password: str,
        *,
        digest_opt_in: bool = False,
        session_nonce: str | None = None,
        now: float | None = None,
    ) -> str:
        """Store a short-lived password signup without retaining plaintext.

        A fresh intent for the same normalized email revokes the older browser
        token. The returned opaque token is the only value placed in the
        verification form; the scrypt hash stays in SQLite.
        """

        email = self.validate_signup(email, password)
        now = time.time() if now is None else float(now)
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_signup_intent_token(token)
        assert token_hash is not None  # token_urlsafe always returns a value
        password_hash = hash_password(password)
        nonce_hash = self._hash_flow_session_nonce(session_nonce)
        if session_nonce is not None and nonce_hash is None:
            raise ValueError("Invalid signup verification session.")
        with self._lock:
            self._conn.execute(
                "DELETE FROM signup_intents WHERE expires_at <= ?", (now,)
            )
            self._conn.execute(
                "DELETE FROM signup_intents WHERE email = ?", (email,)
            )
            self._conn.execute(
                "INSERT INTO signup_intents"
                " (token_hash, email, password_hash, digest_opt_in,"
                "  created_at, expires_at, session_nonce_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    token_hash,
                    email,
                    password_hash,
                    int(bool(digest_opt_in)),
                    now,
                    now + SIGNUP_INTENT_TTL_S,
                    nonce_hash,
                ),
            )
            self._conn.commit()
        return token

    def get_signup_intent(
        self,
        token: str,
        *,
        session_nonce: str | None = None,
        now: float | None = None,
    ) -> SignupIntent | None:
        """Return non-secret metadata for one live browser-bound intent."""

        token_hash = self._hash_signup_intent_token(token)
        if token_hash is None:
            return None
        now = time.time() if now is None else float(now)
        nonce_hash = self._hash_flow_session_nonce(session_nonce)
        if session_nonce is not None and nonce_hash is None:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT email, digest_opt_in, created_at, expires_at,"
                " session_nonce_hash"
                " FROM signup_intents WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is not None and row["expires_at"] <= now:
                self._conn.execute(
                    "DELETE FROM signup_intents WHERE token_hash = ?",
                    (token_hash,),
                )
                self._conn.commit()
                row = None
            if row is not None and not self._flow_session_nonce_matches(
                row["session_nonce_hash"], nonce_hash
            ):
                row = None
        if row is None:
            return None
        return SignupIntent(
            email=row["email"],
            digest_opt_in=bool(row["digest_opt_in"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
        )

    def discard_signup_intent(self, token: str) -> bool:
        """Revoke one exact signup intent after definitive delivery failure."""

        token_hash = self._hash_signup_intent_token(token)
        if token_hash is None:
            return False
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM signup_intents WHERE token_hash = ?",
                (token_hash,),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def _create_with_password_hash_locked(
        self,
        email: str,
        password_hash: str,
        *,
        email_verified: bool,
        shopify_sync_pending: bool,
        digest_opt_in: bool = False,
    ) -> User:
        """Create/claim a user while ``self._lock`` and a write txn are held."""

        row = self._conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        verified_at = time.time() if email_verified else None
        if row is not None:
            if row["password_hash"]:
                raise ValueError(
                    "An account with that email already exists — log in."
                )
            sets = [
                "password_hash = ?",
                "email_verified_at = COALESCE(email_verified_at, ?)",
            ]
            values: list[object] = [password_hash, verified_at]
            if digest_opt_in:
                sets.append("digest_opt_in = 1")
            if shopify_sync_pending and row["shopify_customer_id"] is None:
                sets.extend(
                    (
                        "shopify_sync_status = ?",
                        "shopify_sync_next_attempt_at = NULL",
                        "shopify_sync_attempt_token = NULL",
                    )
                )
                values.append(SHOPIFY_SYNC_PENDING)
            values.append(row["id"])
            self._conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                tuple(values),
            )
            if email_verified:
                self._resolve_verified_shopify_identity_locked(
                    str(row["id"]), email
                )
            updated = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (row["id"],)
            ).fetchone()
            return self._from_row(updated)

        user = User(
            id=uuid.uuid4().hex[:12],
            email=email,
            created_at=time.time(),
            shopify_sync_status=(
                SHOPIFY_SYNC_PENDING
                if shopify_sync_pending
                else SHOPIFY_SYNC_NOT_STARTED
            ),
            email_verified_at=verified_at,
            digest_opt_in=bool(digest_opt_in),
        )
        self._conn.execute(
            "INSERT INTO users"
            " (id, email, password_hash, created_at, digest_opt_in,"
            "  email_verified_at, shopify_sync_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user.id,
                email,
                password_hash,
                user.created_at,
                int(user.digest_opt_in),
                user.email_verified_at,
                user.shopify_sync_status,
            ),
        )
        if email_verified:
            self._resolve_verified_shopify_identity_locked(user.id, email)
            created = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user.id,)
            ).fetchone()
            return self._from_row(created)
        return user

    def create(
        self,
        email: str,
        password: str,
        *,
        shopify_sync_pending: bool = False,
        email_verified: bool = False,
    ) -> User:
        """Create an account — or, when the email belongs to a row with no
        password yet (an unclaimed store stub from upsert_store_customer,
        or a code-only passwordless account), set the password on the SAME
        row, so its Shopify link and any Pro time already granted by order
        webhooks stay with the user. No duplicates. The web layer requires
        an emailed code first and refuses identity/value claims while email
        delivery is unavailable — see app.py."""
        email = self.validate_signup(email, password)
        try:
            with self._lock:
                user = self._create_with_password_hash_locked(
                    email,
                    hash_password(password),
                    email_verified=email_verified,
                    shopify_sync_pending=shopify_sync_pending,
                )
                self._conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("An account with that email already exists — log in.")
        return user

    def complete_signup_intent(
        self,
        token: str,
        *,
        session_nonce: str | None = None,
        now: float | None = None,
    ) -> User:
        """Atomically consume a live intent and create/claim its account.

        The intent is deleted before account mutation in the same serialized
        transaction, so two requests cannot reuse it. Expired or unknown
        tokens are intentionally indistinguishable.
        """

        token_hash = self._hash_signup_intent_token(token)
        if token_hash is None:
            raise ValueError("That signup verification expired — start again.")
        now = time.time() if now is None else float(now)
        nonce_hash = self._hash_flow_session_nonce(session_nonce)
        if session_nonce is not None and nonce_hash is None:
            raise ValueError("That signup verification expired — start again.")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT * FROM signup_intents WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if row is None or row["expires_at"] <= now:
                    if row is not None:
                        self._conn.execute(
                            "DELETE FROM signup_intents WHERE token_hash = ?",
                            (token_hash,),
                        )
                    self._conn.commit()
                    raise ValueError(
                        "That signup verification expired — start again."
                    )
                if not self._flow_session_nonce_matches(
                    row["session_nonce_hash"], nonce_hash
                ):
                    self._conn.rollback()
                    raise ValueError(
                        "That signup verification expired — start again."
                    )
                self._conn.execute(
                    "DELETE FROM signup_intents WHERE token_hash = ?",
                    (token_hash,),
                )
                try:
                    user = self._create_with_password_hash_locked(
                        row["email"],
                        row["password_hash"],
                        email_verified=True,
                        shopify_sync_pending=False,
                        digest_opt_in=bool(row["digest_opt_in"]),
                    )
                except ValueError:
                    # A competing account creation still consumes this
                    # browser-bound secret; the visitor must start over.
                    self._conn.commit()
                    raise
                self._conn.commit()
                return user
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def complete_signup_intent_with_code(
        self,
        token: str,
        code: str,
        *,
        shopify_sync_pending: bool = False,
        session_nonce: str | None = None,
        now: float | None = None,
    ) -> User:
        """Verify and consume signup state in one durable transaction.

        The one-time code, signup intent, password-hash account creation,
        inbox-verification stamp, and optional durable Shopify outbox state
        either all commit or none do. A worker wake happens in the web layer
        only after this method returns; if the process dies first, the
        persisted ``pending`` row remains discoverable by coordinator polling.
        """

        token_hash = self._hash_signup_intent_token(token)
        if token_hash is None:
            raise ValueError("That signup verification expired — start again.")
        now = time.time() if now is None else float(now)
        supplied_code = str(code or "").strip()
        nonce_hash = self._hash_flow_session_nonce(session_nonce)
        if session_nonce is not None and nonce_hash is None:
            raise ValueError("That signup verification expired — start again.")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                intent = self._conn.execute(
                    "SELECT * FROM signup_intents WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if intent is None or float(intent["expires_at"]) <= now:
                    if intent is not None:
                        self._conn.execute(
                            "DELETE FROM signup_intents"
                            " WHERE token_hash = ?",
                            (token_hash,),
                        )
                    self._conn.commit()
                    raise ValueError(
                        "That signup verification expired — start again."
                    )
                if not self._flow_session_nonce_matches(
                    intent["session_nonce_hash"], nonce_hash
                ):
                    self._conn.rollback()
                    raise ValueError(
                        "That signup verification expired — start again."
                    )
                code_row = self._conn.execute(
                    "SELECT code_hash, expires_at, attempts,"
                    " session_nonce_hash"
                    " FROM email_codes"
                    " WHERE email = ? AND purpose = 'claim'",
                    (intent["email"],),
                ).fetchone()
                expected = self._hash_code(
                    str(intent["email"]), "claim", supplied_code
                )
                if code_row is not None and not (
                    self._flow_session_nonce_matches(
                        code_row["session_nonce_hash"], nonce_hash
                    )
                ):
                    self._conn.rollback()
                    raise ValueError(
                        "That signup verification expired — start again."
                    )
                code_ok = bool(
                    code_row is not None
                    and float(code_row["expires_at"]) > now
                    and hmac.compare_digest(
                        expected, str(code_row["code_hash"])
                    )
                )
                if not code_ok:
                    if code_row is not None:
                        if (
                            float(code_row["expires_at"]) <= now
                            or int(code_row["attempts"]) + 1
                            >= CODE_MAX_ATTEMPTS
                        ):
                            self._conn.execute(
                                "DELETE FROM email_codes"
                                " WHERE email = ? AND purpose = 'claim'",
                                (intent["email"],),
                            )
                        else:
                            self._conn.execute(
                                "UPDATE email_codes"
                                " SET attempts = attempts + 1"
                                " WHERE email = ? AND purpose = 'claim'",
                                (intent["email"],),
                            )
                    self._conn.commit()
                    raise ValueError(
                        "That code didn't match (or expired) — check the "
                        "email, or start again for a fresh code."
                    )
                self._conn.execute(
                    "DELETE FROM email_codes"
                    " WHERE email = ? AND purpose = 'claim'",
                    (intent["email"],),
                )
                self._conn.execute(
                    "DELETE FROM signup_intents WHERE token_hash = ?",
                    (token_hash,),
                )
                user = self._create_with_password_hash_locked(
                    str(intent["email"]),
                    str(intent["password_hash"]),
                    email_verified=True,
                    shopify_sync_pending=bool(shopify_sync_pending),
                    digest_opt_in=bool(intent["digest_opt_in"]),
                )
                self._conn.commit()
                return user
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def set_password(self, user_id: str, password: str) -> None:
        """Reset/replace a password (used by the emailed-code reset flow)."""
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        with self._lock:
            self._conn.execute(
                "UPDATE users SET password_hash = ?,"
                " auth_epoch = auth_epoch + 1 WHERE id = ?",
                (hash_password(password), user_id),
            )
            self._conn.commit()

    def add_password(
        self,
        user_id: str,
        password: str,
        *,
        expected_auth_epoch: int,
        expected_history_epoch: int,
    ) -> User:
        """Add the first password with auth/history compare-and-set guards.

        Passwordless browser ownership was proved earlier, but hashing leaves
        a race window. The final credential mutation therefore shares one
        ``BEGIN IMMEDIATE`` transaction with both epoch checks and the
        no-existing-password condition. A reset or ownership change wins in
        full; it can never race with creation of a persistent credential.
        """

        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        password_hash = hash_password(password)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT password_hash, auth_epoch, history_epoch"
                    " FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if (
                    row is None
                    or bool(row["password_hash"])
                    or int(row["auth_epoch"] or 0) != int(expected_auth_epoch)
                    or int(row["history_epoch"] or 0)
                    != int(expected_history_epoch)
                ):
                    raise PasswordAddConflict(
                        "The account changed before the password was added."
                    )
                changed = self._conn.execute(
                    "UPDATE users SET password_hash = ?,"
                    " auth_epoch = auth_epoch + 1"
                    " WHERE id = ? AND password_hash = ''"
                    " AND auth_epoch = ? AND history_epoch = ?",
                    (
                        password_hash,
                        user_id,
                        int(expected_auth_epoch),
                        int(expected_history_epoch),
                    ),
                ).rowcount
                if changed != 1:
                    raise PasswordAddConflict(
                        "The account changed before the password was added."
                    )
                updated = self._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if updated is None:
                    raise PasswordAddConflict(
                        "The account changed before the password was added."
                    )
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        return self._from_row(updated)

    def authenticate(self, email: str, password: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        return self._from_row(row)

    def _resolve_verified_shopify_identity_locked(
        self, user_id: str, email: str
    ) -> bool:
        """Resolve one parked store identity after inbox proof.

        The caller owns ``self._lock`` and the surrounding write transaction.
        A single unambiguous customer id may attach; multiple ids, an existing
        owner, or a conflicting/redacted tombstone stays in review.
        """

        user = self._conn.execute(
            "SELECT * FROM users WHERE id = ? AND email = ?",
            (user_id, email),
        ).fetchone()
        if user is None or user["email_verified_at"] is None:
            return False
        pending_rows = self._conn.execute(
            "SELECT customer_id, updated_at"
            " FROM shopify_pending_customer_links WHERE email = ?"
            " ORDER BY created_at, customer_id",
            (email,),
        ).fetchall()
        order_customer_ids = {
            str(row["shopify_customer_id"])
            for row in self._conn.execute(
                "SELECT DISTINCT shopify_customer_id FROM shopify_orders"
                " WHERE email = ? AND user_id IS NULL"
                "   AND pending_days > 0"
                "   AND shopify_customer_id IS NOT NULL",
                (email,),
            ).fetchall()
        }
        candidates = {
            str(row["customer_id"]) for row in pending_rows
        } | order_customer_ids
        current_customer_id = user["shopify_customer_id"]
        if current_customer_id:
            if candidates and candidates - {current_customer_id}:
                self._conn.execute(
                    "UPDATE users SET shopify_sync_status = ?,"
                    " shopify_sync_error = ?,"
                    " shopify_sync_next_attempt_at = NULL,"
                    " shopify_sync_attempt_token = NULL WHERE id = ?",
                    (
                        SHOPIFY_SYNC_REQUIRES_REVIEW,
                        _SHOPIFY_IDENTITY_CONFLICT_ERROR,
                        user_id,
                    ),
                )
                return False
            self._conn.execute(
                "DELETE FROM shopify_pending_customer_links"
                " WHERE customer_id = ?",
                (current_customer_id,),
            )
            return True
        if not candidates:
            return False
        if len(candidates) != 1:
            self._conn.execute(
                "UPDATE users SET shopify_sync_status = ?,"
                " shopify_sync_error = ?,"
                " shopify_sync_next_attempt_at = NULL,"
                " shopify_sync_attempt_token = NULL WHERE id = ?",
                (
                    SHOPIFY_SYNC_REQUIRES_REVIEW,
                    _SHOPIFY_IDENTITY_CONFLICT_ERROR,
                    user_id,
                ),
            )
            return False
        customer_id = next(iter(candidates))
        other_owner = self._conn.execute(
            "SELECT 1 FROM users WHERE shopify_customer_id = ?"
            " AND id != ? LIMIT 1",
            (customer_id, user_id),
        ).fetchone()
        tombstone = self._conn.execute(
            "SELECT redacted, former_user_id"
            " FROM shopify_customer_tombstones WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        tombstone_conflict = bool(
            tombstone is not None
            and (
                tombstone["redacted"]
                or (
                    tombstone["former_user_id"] is not None
                    and tombstone["former_user_id"] != user_id
                )
            )
        )
        if (
            tombstone is not None
            and not tombstone["redacted"]
            and (
                tombstone["former_user_id"] is None
                or tombstone["former_user_id"] == user_id
            )
        ):
            # A deleted Shopify customer stays deliberately unlinked. Inbox
            # proof may recover that customer's parked purchase through the
            # exact former-account tombstone, but must not resurrect the
            # deleted store link.
            self._conn.execute(
                "UPDATE users SET shopify_identity_locked = 1,"
                " shopify_sync_status = ?, shopify_sync_error = ?,"
                " shopify_sync_next_attempt_at = NULL,"
                " shopify_sync_attempt_token = NULL WHERE id = ?",
                (
                    SHOPIFY_SYNC_REQUIRES_REVIEW,
                    _SHOPIFY_LINK_REMOVED_ERROR,
                    user_id,
                ),
            )
            self._conn.execute(
                "UPDATE shopify_customer_tombstones"
                " SET former_user_id = ?"
                " WHERE customer_id = ? AND redacted = 0"
                " AND (former_user_id IS NULL OR former_user_id = ?)",
                (user_id, customer_id, user_id),
            )
            self._conn.execute(
                "DELETE FROM shopify_pending_customer_links"
                " WHERE customer_id = ?",
                (customer_id,),
            )
            self._move_customer_pending_to_email(customer_id, email)
            return True
        locked_conflict = bool(
            user["shopify_identity_locked"]
            and not (
                tombstone is not None
                and not tombstone["redacted"]
                and tombstone["former_user_id"] == user_id
            )
        )
        if other_owner is not None or tombstone_conflict or locked_conflict:
            self._conn.execute(
                "UPDATE users SET shopify_sync_status = ?,"
                " shopify_sync_error = ?,"
                " shopify_sync_next_attempt_at = NULL,"
                " shopify_sync_attempt_token = NULL WHERE id = ?",
                (
                    SHOPIFY_SYNC_REQUIRES_REVIEW,
                    _SHOPIFY_IDENTITY_CONFLICT_ERROR,
                    user_id,
                ),
            )
            return False
        updated_at = next(
            (
                row["updated_at"]
                for row in reversed(pending_rows)
                if row["customer_id"] == customer_id
                and row["updated_at"] is not None
            ),
            None,
        )
        self._conn.execute(
            "UPDATE users SET shopify_customer_id = ?,"
            " shopify_identity_locked = 1,"
            " shopify_updated_at = COALESCE(?, shopify_updated_at),"
            " shopify_sync_status = ?, shopify_last_synced_at = ?,"
            " shopify_sync_error = NULL,"
            " shopify_sync_next_attempt_at = NULL,"
            " shopify_sync_attempt_token = NULL WHERE id = ?"
            " AND shopify_customer_id IS NULL",
            (
                customer_id,
                updated_at,
                SHOPIFY_SYNC_SYNCED,
                time.time(),
                user_id,
            ),
        )
        self._conn.execute(
            "DELETE FROM shopify_pending_customer_links"
            " WHERE customer_id = ?",
            (customer_id,),
        )
        self._move_customer_pending_to_email(customer_id, email)
        return True

    def verify_email_signin(
        self, email: str, *, shopify_sync_pending: bool = False
    ) -> User:
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
                    " email_verified_at, shopify_sync_status)"
                    " VALUES (?, ?, '', ?, ?, ?)",
                    (
                        uuid.uuid4().hex[:12],
                        email,
                        now,
                        now,
                        (
                            SHOPIFY_SYNC_PENDING
                            if shopify_sync_pending
                            else SHOPIFY_SYNC_NOT_STARTED
                        ),
                    ),
                )
            else:
                sets = []
                values: list[object] = []
                if row["email_verified_at"] is None:
                    sets.append("email_verified_at = ?")
                    values.append(now)
                    if row["password_hash"]:
                        # A password created before inbox proof may belong to
                        # a pre-registration attacker. The first successful
                        # code establishes the actual owner: revoke that
                        # password and every earlier stateless session before
                        # any parked Shopify identity/value can attach.
                        sets.extend(
                            (
                                "password_hash = ''",
                                "auth_epoch = auth_epoch + 1",
                            )
                        )
                if (
                    shopify_sync_pending
                    and row["shopify_customer_id"] is None
                ):
                    sets.extend(
                        (
                            "shopify_sync_status = ?",
                            "shopify_sync_next_attempt_at = NULL",
                            "shopify_sync_attempt_token = NULL",
                        )
                    )
                    values.append(SHOPIFY_SYNC_PENDING)
                if sets:
                    values.append(row["id"])
                    self._conn.execute(
                        f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                        values,
                    )
            resolved = self._conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if resolved is not None:
                self._resolve_verified_shopify_identity_locked(
                    str(resolved["id"]), email
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

    # -- personal mobile API tokens -------------------------------------
    @staticmethod
    def _normalize_mobile_api_token_label(value: object) -> str:
        """Validate a short, display-only device label.

        Labels are never used as an authentication factor.  Keeping them
        bounded and control-character-free prevents a future management UI or
        privacy export from becoming a generic unbounded metadata sink.
        """

        if not isinstance(value, str):
            raise ValueError("A mobile device name is required.")
        label = " ".join(value.split())
        if not label or len(label) > _MOBILE_API_TOKEN_LABEL_MAX:
            raise ValueError(
                "A mobile device name must be 1 to "
                f"{_MOBILE_API_TOKEN_LABEL_MAX} characters."
            )
        if any(ord(character) < 32 for character in label):
            raise ValueError("A mobile device name contains invalid characters.")
        return label

    @staticmethod
    def _valid_mobile_api_token_component(
        value: object, *, minimum: int, maximum: int
    ) -> bool:
        return (
            isinstance(value, str)
            and minimum <= len(value) <= maximum
            and value.isascii()
            and all(character.isalnum() or character in "-_" for character in value)
        )

    @classmethod
    def _parse_mobile_api_token(cls, value: object) -> tuple[str, str] | None:
        """Parse one canonical selector.secret credential without normalizing it."""

        if (
            not isinstance(value, str)
            or len(value) > _MOBILE_API_TOKEN_MAX_LENGTH
            or not value.startswith(MOBILE_API_TOKEN_PREFIX)
        ):
            return None
        selector, separator, secret = value[
            len(MOBILE_API_TOKEN_PREFIX):
        ].partition(".")
        if (
            separator != "."
            or "." in secret
            or not cls._valid_mobile_api_token_component(
                selector, minimum=16, maximum=64
            )
            or not cls._valid_mobile_api_token_component(
                secret, minimum=43, maximum=128
            )
        ):
            return None
        return selector, secret

    @classmethod
    def _mobile_api_token_hash(cls, selector: str, secret: str) -> str:
        """Hash the complete opaque credential before it reaches SQLite."""

        return hashlib.sha256(
            f"{MOBILE_API_TOKEN_PREFIX}{selector}.{secret}".encode("utf-8")
        ).hexdigest()

    @classmethod
    def _mobile_api_token_metadata_from_row(
        cls,
        row: sqlite3.Row,
        *,
        current_auth_epoch: int,
        now: float,
    ) -> MobileAPIToken:
        revoked_at = row["revoked_at"]
        return MobileAPIToken(
            selector=str(row["selector"]),
            label=str(row["label"]),
            created_at=float(row["created_at"]),
            last_used_at=(
                float(row["last_used_at"])
                if row["last_used_at"] is not None
                else None
            ),
            expires_at=float(row["expires_at"]),
            revoked_at=float(revoked_at) if revoked_at is not None else None,
            active=(
                revoked_at is None
                and float(row["expires_at"]) > now
                and int(row["auth_epoch"]) == current_auth_epoch
                and str(row["state"]) == "active"
                and row["fenced_at"] is None
            ),
        )

    def issue_mobile_api_token(
        self,
        user_id: str,
        label: object,
        *,
        expected_auth_epoch: int,
        now: float | None = None,
    ) -> tuple[str, MobileAPIToken]:
        """Issue one personal token and return its raw value exactly once.

        The caller must present the returned raw token to the device at issue
        time.  Only a selector, digest, lifecycle timestamps, and the user
        epoch are durable.  The transaction holds a SQLite write lock across
        the active-token count and insert so concurrent browser tabs cannot
        exceed the cap.
        """

        normalized_label = self._normalize_mobile_api_token_label(label)
        issued_at = time.time() if now is None else float(now)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                user = self._conn.execute(
                    "SELECT id, auth_epoch FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if user is None:
                    raise ValueError("Account is unavailable.")
                current_auth_epoch = int(user["auth_epoch"] or 0)
                if current_auth_epoch != int(expected_auth_epoch):
                    # Session authentication happens before the request body is
                    # consumed. Re-check the captured epoch inside this write
                    # transaction so a password reset or ownership recovery
                    # cannot race a slow token-issuance request.
                    raise MobileAPITokenAuthEpochError(
                        "The authenticated account session changed."
                    )
                active_count = int(
                    self._conn.execute(
                        "SELECT COUNT(*) FROM mobile_api_tokens"
                        " WHERE user_id = ? AND auth_epoch = ?"
                        " AND revoked_at IS NULL AND expires_at > ?"
                        " AND state = 'active' AND fenced_at IS NULL",
                        (user_id, current_auth_epoch, issued_at),
                    ).fetchone()[0]
                )
                if active_count >= MOBILE_API_TOKEN_ACTIVE_LIMIT:
                    raise MobileAPITokenLimitError(
                        "You already have "
                        f"{MOBILE_API_TOKEN_ACTIVE_LIMIT} active mobile devices. "
                        "Revoke one before adding another."
                    )

                # token_urlsafe returns URL/header-safe values; selector
                # collisions are astronomically unlikely, but a bounded retry
                # preserves the database primary-key guarantee without ever
                # accepting a caller-selected selector.
                token = ""
                metadata = None
                for _ in range(5):
                    selector = secrets.token_urlsafe(MOBILE_API_TOKEN_SELECTOR_BYTES)
                    secret = secrets.token_urlsafe(MOBILE_API_TOKEN_SECRET_BYTES)
                    if self._conn.execute(
                        "SELECT 1 FROM mobile_api_tokens WHERE selector = ?",
                        (selector,),
                    ).fetchone() is not None:
                        continue
                    token_hash = self._mobile_api_token_hash(selector, secret)
                    expires_at = issued_at + MOBILE_API_TOKEN_TTL_S
                    self._conn.execute(
                        "INSERT INTO mobile_api_tokens"
                        " (selector, token_hash, user_id, auth_epoch, label,"
                        "  created_at, last_used_at, expires_at, revoked_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)",
                        (
                            selector,
                            token_hash,
                            user_id,
                            current_auth_epoch,
                            normalized_label,
                            issued_at,
                            expires_at,
                        ),
                    )
                    token = f"{MOBILE_API_TOKEN_PREFIX}{selector}.{secret}"
                    metadata = MobileAPIToken(
                        selector=selector,
                        label=normalized_label,
                        created_at=issued_at,
                        last_used_at=None,
                        expires_at=expires_at,
                        revoked_at=None,
                        active=True,
                    )
                    break
                if metadata is None:
                    raise RuntimeError("Could not allocate a mobile token selector.")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return token, metadata

    def list_mobile_api_tokens(
        self, user_id: str, *, now: float | None = None
    ) -> list[MobileAPIToken]:
        """List non-secret lifecycle metadata for one account's devices."""

        observed_at = time.time() if now is None else float(now)
        with self._lock:
            user = self._conn.execute(
                "SELECT auth_epoch FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                return []
            rows = self._conn.execute(
                "SELECT selector, user_id, auth_epoch, label, created_at,"
                " last_used_at, expires_at, revoked_at, state, fenced_at"
                " FROM mobile_api_tokens WHERE user_id = ?"
                " ORDER BY created_at DESC, selector",
                (user_id,),
            ).fetchall()
        return [
            self._mobile_api_token_metadata_from_row(
                row,
                current_auth_epoch=int(user["auth_epoch"] or 0),
                now=observed_at,
            )
            for row in rows
        ]

    def revoke_mobile_api_token(
        self,
        user_id: str,
        selector: object,
        *,
        now: float | None = None,
    ) -> bool:
        """Revoke one owned device token without exposing its secret."""

        if not self._valid_mobile_api_token_component(
            selector, minimum=16, maximum=64
        ):
            return False
        revoked_at = time.time() if now is None else float(now)
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE mobile_api_tokens"
                " SET revoked_at = COALESCE(revoked_at, ?)"
                " WHERE selector = ? AND user_id = ?",
                (revoked_at, selector, user_id),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def authenticate_mobile_api_principal(
        self, token: object, *, now: float | None = None
    ) -> MobileAPIPrincipal | None:
        """Authenticate a valid, unrevoked device token and record its use.

        A malformed, unknown, expired, revoked, or epoch-stale credential has
        one intentionally indistinguishable ``None`` outcome.  The candidate
        digest is compared even for an unknown selector, avoiding a simple
        selector-existence timing branch.
        """

        parsed = self._parse_mobile_api_token(token)
        if parsed is None:
            return None
        selector, secret = parsed
        candidate_hash = self._mobile_api_token_hash(selector, secret)
        observed_at = time.time() if now is None else float(now)
        with self._lock:
            # Unknown or invalid credentials stay read-only. A random bearer
            # flood must not reserve SQLite's single writer before a secret has
            # even authenticated.
            token_row = self._conn.execute(
                "SELECT * FROM mobile_api_tokens WHERE selector = ?",
                (selector,),
            ).fetchone()
            expected_hash = (
                str(token_row["token_hash"])
                if token_row is not None
                else _MOBILE_API_TOKEN_DUMMY_HASH
            )
            if not hmac.compare_digest(candidate_hash, expected_hash):
                return None
            if token_row is None:
                return None
            user = self._conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (token_row["user_id"],),
            ).fetchone()
            if (
                user is None
                or token_row["revoked_at"] is not None
                or float(token_row["expires_at"]) <= observed_at
                or int(token_row["auth_epoch"]) != int(user["auth_epoch"] or 0)
                or str(token_row["state"]) != "active"
                or token_row["fenced_at"] is not None
            ):
                return None

            last_used_at = token_row["last_used_at"]
            touch_before = (
                observed_at - _MOBILE_API_TOKEN_LAST_USED_WRITE_INTERVAL_S
            )
            if last_used_at is None or float(last_used_at) <= touch_before:
                try:
                    # Device-management timestamps are operational metadata,
                    # not an audit log. Sample them at most once per minute so
                    # polling does not turn every authenticated read into a
                    # SQLite write.
                    self._conn.execute(
                        "UPDATE mobile_api_tokens SET last_used_at = ?"
                        " WHERE selector = ? AND user_id = ?"
                        " AND revoked_at IS NULL AND expires_at > ?"
                        " AND auth_epoch = ?"
                        " AND EXISTS (SELECT 1 FROM users AS current_user"
                        " WHERE current_user.id = mobile_api_tokens.user_id"
                        " AND COALESCE(current_user.auth_epoch, 0) = ?)"
                        " AND (last_used_at IS NULL OR last_used_at <= ?)",
                        (
                            observed_at,
                            selector,
                            user["id"],
                            observed_at,
                            int(user["auth_epoch"] or 0),
                            int(user["auth_epoch"] or 0),
                            touch_before,
                        ),
                    )
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
        authenticated_user = self._from_row(user)
        return MobileAPIPrincipal(
            user=authenticated_user,
            selector=selector,
            auth_epoch=authenticated_user.auth_epoch,
            installation_key=(
                str(token_row["installation_key"])
                if token_row["installation_key"] is not None
                else None
            ),
        )

    def authenticate_mobile_api_token(self, token: object, *, now=None) -> User | None:
        principal = self.authenticate_mobile_api_principal(token, now=now)
        return principal.user if principal is not None else None

    def _shopify_identity_rows(
        self,
        customer_id: object,
        *,
        exclude_user_id: str | None = None,
    ) -> list[sqlite3.Row]:
        """Find logical owners across canonical and legacy GID storage.

        The caller must hold ``self._lock``. A legacy database with a deferred
        unique index can contain both ``7001`` and a Customer GID for the same
        identity, so raw SQL equality against only one representation is not a
        sufficient guard.
        """

        canonical_id = _compatible_customer_id(customer_id)
        if canonical_id is None:
            return []
        candidates = [canonical_id]
        try:
            gid = customer_gid(canonical_id)
        except ValueError:
            gid = None
        if gid and gid != canonical_id:
            candidates.append(gid)
        placeholders = ", ".join("?" for _ in candidates)
        rows = self._conn.execute(
            f"SELECT * FROM users WHERE shopify_customer_id IN ({placeholders})",
            candidates,
        ).fetchall()
        return [
            row
            for row in rows
            if row["id"] != exclude_user_id
            and _compatible_customer_id(row["shopify_customer_id"])
            == canonical_id
        ]

    # -- outbound Shopify customer synchronization -----------------------
    def mark_shopify_sync_pending(
        self, user_id: str, safe_error: str | None = None
    ) -> User | None:
        """Queue a user unless a durable privacy fence blocks the write."""

        with self._lock:
            error_assignment = (
                ", shopify_sync_error = ?" if safe_error is not None else ""
            )
            params: list[object] = [SHOPIFY_SYNC_PENDING]
            if safe_error is not None:
                params.append(_safe_sync_error(safe_error))
            params.append(user_id)
            cursor = self._conn.execute(
                "UPDATE users SET shopify_sync_status = ?"
                f"{error_assignment},"
                " shopify_sync_next_attempt_at = NULL,"
                " shopify_sync_attempt_token = NULL"
                " WHERE id = ? AND shopify_sync_blocked = 0"
                " AND EXISTS ("
                "   SELECT 1 FROM shopify_sync_control"
                "   WHERE id = 1 AND shop_redacted = 0"
                " )",
                params,
            )
            self._conn.commit()
            if cursor.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._from_row(row)

    def start_shopify_sync(
        self,
        user_id: str,
        *,
        expected_generation: int | None = None,
    ) -> tuple[User, str]:
        """Start one compare-and-set-protected sync attempt.

        The returned token is deliberately opaque. A newer call replaces it,
        so success/failure from a slow or crashed older worker becomes a no-op.
        A caller that selected a due row earlier can pass its generation;
        redaction increments that value, making the stale selection fail
        before any remote operation begins.
        """

        attempt = uuid.uuid4().hex
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    raise KeyError(user_id)
                control = self._conn.execute(
                    "SELECT shop_redacted FROM shopify_sync_control"
                    " WHERE id = 1"
                ).fetchone()
                if (
                    control is None
                    or bool(control["shop_redacted"])
                    or bool(row["shopify_sync_blocked"])
                    or (
                        expected_generation is not None
                        and int(row["shopify_sync_generation"])
                        != int(expected_generation)
                    )
                ):
                    self._conn.rollback()
                    raise ShopifySyncFencedError(
                        "Shopify synchronization was privacy-fenced."
                    )
                self._conn.execute(
                    "UPDATE users SET shopify_sync_status = ?,"
                    " shopify_sync_attempts = shopify_sync_attempts + 1,"
                    " shopify_sync_next_attempt_at = NULL,"
                    " shopify_sync_attempt_token = ? WHERE id = ?",
                    (SHOPIFY_SYNC_PENDING, attempt, user_id),
                )
                row = self._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        return self._from_row(row), attempt

    @contextmanager
    def guard_shopify_sync_remote_write(
        self,
        user_id: str,
        attempt: str,
        *,
        generation: int,
    ) -> Iterator[bool]:
        """Serialize one remote customer mutation against privacy erasure.

        A dedicated cross-process advisory lock remains held around the
        provider call. The application SQLite lock and a short write
        transaction are used only to validate the persisted fence first.
        Consequently unrelated accounts and app work remain responsive while
        redaction still either commits first (this yields ``False``) or waits
        for an already-authorized call and then invalidates its local result.
        """

        token = str(attempt or "").strip()
        if not token:
            yield False
            return
        with shopify_remote_privacy_lock(self._db_path):
            with self._lock:
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    row = self._conn.execute(
                        "SELECT shopify_sync_attempt_token,"
                        " shopify_sync_generation, shopify_sync_blocked"
                        " FROM users WHERE id = ?",
                        (user_id,),
                    ).fetchone()
                    control = self._conn.execute(
                        "SELECT shop_redacted FROM shopify_sync_control"
                        " WHERE id = 1"
                    ).fetchone()
                    allowed = bool(
                        row is not None
                        and control is not None
                        and not bool(control["shop_redacted"])
                        and not bool(row["shopify_sync_blocked"])
                        and int(row["shopify_sync_generation"])
                        == int(generation)
                        and hmac.compare_digest(
                            str(row["shopify_sync_attempt_token"] or ""),
                            token,
                        )
                    )
                    self._conn.commit()
                except Exception:
                    if self._conn.in_transaction:
                        self._conn.rollback()
                    raise
            yield allowed

    @contextmanager
    def guard_shopify_sync_remote_read(
        self,
        user_id: str,
        *,
        generation: int,
    ) -> Iterator[str | None]:
        """Serialize an email-based provider lookup against redaction.

        No local row is mutated. A brief SQLite transaction validates the
        selected generation; only the dedicated advisory lock remains held
        while the provider lookup is running.
        """

        with shopify_remote_privacy_lock(self._db_path):
            with self._lock:
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    row = self._conn.execute(
                        "SELECT email, email_verified_at,"
                        " shopify_sync_generation, shopify_sync_blocked"
                        " FROM users WHERE id = ?",
                        (user_id,),
                    ).fetchone()
                    control = self._conn.execute(
                        "SELECT shop_redacted FROM shopify_sync_control"
                        " WHERE id = 1"
                    ).fetchone()
                    email = (
                        str(row["email"])
                        if (
                            row is not None
                            and row["email_verified_at"] is not None
                            and control is not None
                            and not bool(control["shop_redacted"])
                            and not bool(row["shopify_sync_blocked"])
                            and int(row["shopify_sync_generation"])
                            == int(generation)
                        )
                        else None
                    )
                    self._conn.commit()
                except Exception:
                    if self._conn.in_transaction:
                        self._conn.rollback()
                    raise
            yield email

    def record_shopify_sync_success(
        self, user_id: str, attempt: str, customer_id: object
    ) -> bool:
        """Commit a linked customer only for the current attempt token.

        Durable Shopify identity is immutable once linked. A different linked
        ID or an ID already owned by another user is retained for operator
        review rather than guessed or overwritten.
        """

        canonical_id = normalize_customer_id(customer_id)
        if canonical_id is None:
            raise ValueError("A Shopify customer ID is required.")
        attempt = str(attempt or "").strip()
        if not attempt:
            return False
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT shopify_customer_id FROM users"
                    " WHERE id = ? AND shopify_sync_attempt_token = ?"
                    " AND shopify_sync_blocked = 0"
                    " AND EXISTS ("
                    "   SELECT 1 FROM shopify_sync_control"
                    "   WHERE id = 1 AND shop_redacted = 0"
                    " )",
                    (user_id, attempt),
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    return False
                current_id = _compatible_customer_id(row["shopify_customer_id"])
                owners = self._shopify_identity_rows(
                    canonical_id,
                    exclude_user_id=user_id,
                )
                if (
                    (current_id is not None and current_id != canonical_id)
                    or owners
                ):
                    self._conn.execute(
                        "UPDATE users SET shopify_sync_status = ?,"
                        " shopify_sync_error = ?,"
                        " shopify_sync_next_attempt_at = NULL,"
                        " shopify_sync_attempt_token = NULL"
                        " WHERE id = ? AND shopify_sync_attempt_token = ?",
                        (
                            SHOPIFY_SYNC_REQUIRES_REVIEW,
                            _SHOPIFY_IDENTITY_CONFLICT_ERROR,
                            user_id,
                            attempt,
                        ),
                    )
                    self._conn.commit()
                    return False
                cursor = self._conn.execute(
                    "UPDATE users SET shopify_customer_id = ?,"
                    " shopify_identity_locked = 1,"
                    " shopify_sync_status = ?,"
                    " shopify_last_synced_at = ?,"
                    " shopify_sync_error = NULL,"
                    " shopify_sync_next_attempt_at = NULL,"
                    " shopify_sync_attempt_token = NULL"
                    " WHERE id = ? AND shopify_sync_attempt_token = ?",
                    (
                        canonical_id,
                        SHOPIFY_SYNC_SYNCED,
                        time.time(),
                        user_id,
                        attempt,
                    ),
                )
                self._conn.commit()
                return cursor.rowcount == 1
            except sqlite3.IntegrityError:
                self._conn.rollback()
                # A legacy database may gain a conflicting row while its
                # unique index is intentionally deferred. Preserve both rows
                # and make the active attempt visible for review.
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "UPDATE users SET shopify_sync_status = ?,"
                    " shopify_sync_error = ?,"
                    " shopify_sync_next_attempt_at = NULL,"
                    " shopify_sync_attempt_token = NULL"
                    " WHERE id = ? AND shopify_sync_attempt_token = ?",
                    (
                        SHOPIFY_SYNC_REQUIRES_REVIEW,
                        _SHOPIFY_IDENTITY_CONFLICT_ERROR,
                        user_id,
                        attempt,
                    ),
                )
                self._conn.commit()
                return False
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def record_shopify_sync_failure(
        self,
        user_id: str,
        attempt: str,
        status: str,
        safe_error: str,
        next_attempt_at: float | None = None,
    ) -> bool:
        """Record a terminal attempt result if its token is still current."""

        if status not in (SHOPIFY_SYNC_FAILED, SHOPIFY_SYNC_REQUIRES_REVIEW):
            raise ValueError("Shopify sync failure status is invalid.")
        if status == SHOPIFY_SYNC_REQUIRES_REVIEW:
            next_attempt_at = None
        elif next_attempt_at is not None:
            next_attempt_at = float(next_attempt_at)
        attempt = str(attempt or "").strip()
        if not attempt:
            return False
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE users SET shopify_sync_status = ?,"
                " shopify_sync_error = ?,"
                " shopify_sync_next_attempt_at = ?,"
                " shopify_sync_attempt_token = NULL"
                " WHERE id = ? AND shopify_sync_attempt_token = ?",
                (
                    status,
                    _safe_sync_error(safe_error),
                    next_attempt_at,
                    user_id,
                    attempt,
                ),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def list_due_shopify_syncs(
        self, now: float | None = None, limit: int = 20
    ) -> list[User]:
        """Pending work plus retryable failures, including crashed attempts."""

        now = time.time() if now is None else float(now)
        limit = _page_limit(limit)
        with self._lock:
            control = self._conn.execute(
                "SELECT shop_redacted FROM shopify_sync_control WHERE id = 1"
            ).fetchone()
            if control is None or bool(control["shop_redacted"]):
                return []
            rows = self._conn.execute(
                "SELECT * FROM users"
                " WHERE shopify_sync_blocked = 0 AND ("
                " shopify_sync_status = ?"
                " OR (shopify_sync_status = ?"
                "     AND shopify_sync_next_attempt_at IS NOT NULL"
                "     AND shopify_sync_next_attempt_at <= ?))"
                " ORDER BY"
                " CASE shopify_sync_status WHEN ? THEN 0 ELSE 1 END,"
                " COALESCE(shopify_sync_next_attempt_at, created_at),"
                " created_at, id LIMIT ?",
                (
                    SHOPIFY_SYNC_PENDING,
                    SHOPIFY_SYNC_FAILED,
                    now,
                    SHOPIFY_SYNC_PENDING,
                    limit,
                ),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def _page_shopify_users(
        self,
        where: str,
        params: tuple[object, ...],
        limit: int,
        after: str | None,
    ) -> tuple[list[User], str | None]:
        limit = _page_limit(limit)
        cursor = str(after or "").strip()
        cursor_clause = " AND id > ?" if cursor else ""
        query_params = params + ((cursor,) if cursor else ()) + (limit + 1,)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM users WHERE {where}{cursor_clause}"
                " ORDER BY id LIMIT ?",
                query_params,
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        users = [self._from_row(row) for row in page]
        next_cursor = users[-1].id if has_more and users else None
        return users, next_cursor

    def shopify_sync_health_counts(
        self, now: float | None = None
    ) -> dict[str, int | float | None]:
        """Aggregate PII-free worker/backlog health in one bounded query."""

        now = time.time() if now is None else float(now)
        with self._lock:
            row = self._conn.execute(
                "SELECT"
                " COUNT(*) AS total,"
                " SUM(CASE WHEN shopify_sync_status = ? THEN 1 ELSE 0 END)"
                "   AS pending,"
                " SUM(CASE WHEN shopify_sync_status = ? THEN 1 ELSE 0 END)"
                "   AS failed,"
                " SUM(CASE WHEN shopify_sync_status = ? THEN 1 ELSE 0 END)"
                "   AS requires_review,"
                " SUM(CASE"
                "   WHEN shopify_sync_status = ? THEN 1"
                "   WHEN shopify_sync_status = ?"
                "    AND shopify_sync_next_attempt_at IS NOT NULL"
                "    AND shopify_sync_next_attempt_at <= ? THEN 1"
                "   ELSE 0 END) AS due,"
                " MIN(CASE"
                "   WHEN shopify_sync_status = ?"
                "     THEN COALESCE(shopify_sync_next_attempt_at, created_at)"
                "   WHEN shopify_sync_status = ?"
                "    AND shopify_sync_next_attempt_at IS NOT NULL"
                "    AND shopify_sync_next_attempt_at <= ?"
                "     THEN shopify_sync_next_attempt_at"
                "   ELSE NULL END) AS oldest_due_at"
                " FROM users",
                (
                    SHOPIFY_SYNC_PENDING,
                    SHOPIFY_SYNC_FAILED,
                    SHOPIFY_SYNC_REQUIRES_REVIEW,
                    SHOPIFY_SYNC_PENDING,
                    SHOPIFY_SYNC_FAILED,
                    now,
                    SHOPIFY_SYNC_PENDING,
                    SHOPIFY_SYNC_FAILED,
                    now,
                ),
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "pending": int(row["pending"] or 0),
            "failed": int(row["failed"] or 0),
            "requires_review": int(row["requires_review"] or 0),
            "due": int(row["due"] or 0),
            "oldest_due_at": (
                float(row["oldest_due_at"])
                if row["oldest_due_at"] is not None
                else None
            ),
        }

    def list_shopify_sync_health(
        self, limit: int = 50, after: str | None = None
    ) -> tuple[list[User], str | None]:
        """Deterministically page users for a protected admin surface."""

        return self._page_shopify_users("1 = 1", (), limit, after)

    def list_shopify_backfill(
        self, limit: int = 50, after: str | None = None
    ) -> tuple[list[User], str | None]:
        """Page every row so dry-runs explicitly report linked-row skips."""

        return self._page_shopify_users("1 = 1", (), limit, after)

    # -- store accounts (called by the Shopify customer webhooks) ---------
    def get_by_shopify_account_subject(self, subject: object) -> User | None:
        try:
            normalized_subject = self._shopify_account_subject(subject)
        except ValueError:
            return None
        return self._one("shopify_account_subject", normalized_subject)

    def get_by_shopify(self, customer_id: str) -> User | None:
        return self._one(
            "shopify_customer_id", _compatible_customer_id(customer_id)
        )

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
        customer_id = _compatible_customer_id(customer_id)
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

                    pending_identity = self._conn.execute(
                        "SELECT customer_id, email, updated_at"
                        " FROM shopify_pending_customer_links"
                        " WHERE customer_id = ?",
                        (customer_id,),
                    ).fetchone()
                    if pending_identity is not None:
                        # Once an identity/email collision is quarantined, a
                        # later event must not bypass it by creating a fresh
                        # stub at another address. Only a timestamped event at
                        # least as new may move the parked address; a replay
                        # with no timestamp is never allowed to roll it back.
                        if (
                            updated_at is not None
                            and (
                                pending_identity["updated_at"] is None
                                or updated_at
                                >= float(pending_identity["updated_at"])
                            )
                        ):
                            self._conn.execute(
                                "UPDATE shopify_pending_customer_links"
                                " SET email = ?, updated_at = ?"
                                " WHERE customer_id = ?",
                                (email, updated_at, customer_id),
                            )
                        pending_identity = self._conn.execute(
                            "SELECT customer_id, email, updated_at"
                            " FROM shopify_pending_customer_links"
                            " WHERE customer_id = ?",
                            (customer_id,),
                        ).fetchone()
                        pending_user = self._conn.execute(
                            "SELECT * FROM users WHERE email = ?",
                            (pending_identity["email"],),
                        ).fetchone()
                        if pending_user is not None:
                            self._conn.execute(
                                "UPDATE users SET shopify_sync_status = ?,"
                                " shopify_sync_error = ?,"
                                " shopify_sync_next_attempt_at = NULL,"
                                " shopify_sync_attempt_token = NULL"
                                " WHERE id = ?",
                                (
                                    SHOPIFY_SYNC_REQUIRES_REVIEW,
                                    _SHOPIFY_EMAIL_UNVERIFIED_ERROR,
                                    pending_user["id"],
                                ),
                            )
                        self._conn.commit()
                        if pending_user is None:
                            return None
                        refreshed = self._conn.execute(
                            "SELECT * FROM users WHERE id = ?",
                            (pending_user["id"],),
                        ).fetchone()
                        return self._from_row(refreshed)

                    identity_rows = self._shopify_identity_rows(customer_id)
                    if len(identity_rows) > 1:
                        self._conn.executemany(
                            "UPDATE users SET shopify_sync_status = ?,"
                            " shopify_sync_error = ?,"
                            " shopify_sync_next_attempt_at = NULL,"
                            " shopify_sync_attempt_token = NULL WHERE id = ?",
                            (
                                (
                                    SHOPIFY_SYNC_REQUIRES_REVIEW,
                                    _SHOPIFY_IDENTITY_CONFLICT_ERROR,
                                    identity_row["id"],
                                )
                                for identity_row in identity_rows
                            ),
                        )
                        self._conn.commit()
                        return self._from_row(identity_rows[0])
                    linked = identity_rows[0] if identity_rows else None
                    if linked is not None:
                        if linked["shopify_customer_id"] != customer_id:
                            self._conn.execute(
                                "UPDATE users SET shopify_customer_id = ?"
                                " WHERE id = ?",
                                (customer_id, linked["id"]),
                            )
                            linked = self._conn.execute(
                                "SELECT * FROM users WHERE id = ?",
                                (linked["id"],),
                            ).fetchone()
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
                            claimed = self._user_row_is_claimed(linked)
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
                                self._conn.execute(
                                    "DELETE FROM signup_intents WHERE email = ?",
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
                            "UPDATE users SET shopify_identity_locked = 1,"
                            " shopify_sync_status = ?,"
                            " shopify_last_synced_at = ?,"
                            " shopify_sync_error = NULL,"
                            " shopify_sync_next_attempt_at = NULL,"
                            " shopify_sync_attempt_token = NULL"
                            " WHERE id = ?",
                            (
                                SHOPIFY_SYNC_SYNCED,
                                time.time(),
                                linked["id"],
                            ),
                        )
                        self._move_customer_pending_to_email(
                            customer_id, email
                        )
                        self._conn.execute(
                            "DELETE FROM shopify_pending_customer_links"
                            " WHERE customer_id = ?",
                            (customer_id,),
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
                        "  shopify_updated_at, source, shopify_sync_status,"
                        "  shopify_last_synced_at)"
                        " VALUES (?, ?, '', ?, ?, 1, ?, 'shopify', ?, ?)",
                        (
                            uuid.uuid4().hex[:12],
                            email,
                            time.time(),
                            customer_id,
                            updated_at,
                            (
                                SHOPIFY_SYNC_SYNCED
                                if customer_id
                                else SHOPIFY_SYNC_NOT_STARTED
                            ),
                            time.time() if customer_id else None,
                        ),
                    )
                    if customer_id:
                        self._conn.execute(
                            "DELETE FROM shopify_pending_customer_links"
                            " WHERE customer_id = ?",
                            (customer_id,),
                        )
                elif (
                    customer_id
                    and row["shopify_customer_id"] is None
                    and not row["shopify_identity_locked"]
                ):
                    if row["email_verified_at"] is None:
                        # A password proves only that someone registered first;
                        # it does not prove ownership of this inbox. Park the
                        # stable store identity until an emailed code verifies
                        # the address instead of handing the Shopify account to
                        # a pre-registration attacker.
                        self._conn.execute(
                            "INSERT INTO shopify_pending_customer_links"
                            " (customer_id, email, updated_at, created_at)"
                            " VALUES (?, ?, ?, ?)"
                            " ON CONFLICT(customer_id) DO UPDATE SET"
                            " email = CASE"
                            "   WHEN excluded.updated_at IS NOT NULL"
                            "    AND (shopify_pending_customer_links.updated_at"
                            "         IS NULL"
                            "     OR excluded.updated_at >="
                            "        shopify_pending_customer_links.updated_at"
                            "    )"
                            "   THEN excluded.email ELSE"
                            "        shopify_pending_customer_links.email END,"
                            " updated_at = CASE"
                            "   WHEN excluded.updated_at IS NOT NULL"
                            "    AND (shopify_pending_customer_links.updated_at"
                            "         IS NULL"
                            "     OR excluded.updated_at >="
                            "        shopify_pending_customer_links.updated_at"
                            "    )"
                            "   THEN excluded.updated_at ELSE"
                            "        shopify_pending_customer_links.updated_at"
                            " END",
                            (customer_id, email, updated_at, time.time()),
                        )
                        self._conn.execute(
                            "UPDATE users SET shopify_sync_status = ?,"
                            " shopify_sync_error = ?,"
                            " shopify_sync_next_attempt_at = NULL,"
                            " shopify_sync_attempt_token = NULL"
                            " WHERE id = ?",
                            (
                                SHOPIFY_SYNC_REQUIRES_REVIEW,
                                _SHOPIFY_EMAIL_UNVERIFIED_ERROR,
                                row["id"],
                            ),
                        )
                    else:
                        self._conn.execute(
                            "UPDATE users SET shopify_customer_id = ?,"
                            " shopify_identity_locked = 1,"
                            " shopify_updated_at = COALESCE("
                            "   ?, shopify_updated_at),"
                            " shopify_sync_status = ?,"
                            " shopify_last_synced_at = ?,"
                            " shopify_sync_error = NULL,"
                            " shopify_sync_next_attempt_at = NULL,"
                            " shopify_sync_attempt_token = NULL"
                            " WHERE id = ?",
                            (
                                customer_id,
                                updated_at,
                                SHOPIFY_SYNC_SYNCED,
                                time.time(),
                                row["id"],
                            ),
                        )
                        self._conn.execute(
                            "DELETE FROM shopify_pending_customer_links"
                            " WHERE customer_id = ?",
                            (customer_id,),
                        )
                elif customer_id and row["shopify_customer_id"] is None:
                    # A prior identity lock is intentionally not bypassed,
                    # even after inbox verification. Preserve the candidate
                    # for explicit review instead of losing the stable id.
                    self._conn.execute(
                        "INSERT INTO shopify_pending_customer_links"
                        " (customer_id, email, updated_at, created_at)"
                        " VALUES (?, ?, ?, ?)"
                        " ON CONFLICT(customer_id) DO UPDATE SET"
                        " email = CASE"
                        "   WHEN excluded.updated_at IS NOT NULL"
                        "    AND (shopify_pending_customer_links.updated_at"
                        "         IS NULL"
                        "     OR excluded.updated_at >="
                        "        shopify_pending_customer_links.updated_at)"
                        "   THEN excluded.email ELSE"
                        "        shopify_pending_customer_links.email END,"
                        " updated_at = CASE"
                        "   WHEN excluded.updated_at IS NOT NULL"
                        "    AND (shopify_pending_customer_links.updated_at"
                        "         IS NULL"
                        "     OR excluded.updated_at >="
                        "        shopify_pending_customer_links.updated_at)"
                        "   THEN excluded.updated_at ELSE"
                        "        shopify_pending_customer_links.updated_at END",
                        (customer_id, email, updated_at, time.time()),
                    )
                    self._conn.execute(
                        "UPDATE users SET shopify_sync_status = ?,"
                        " shopify_sync_error = ?,"
                        " shopify_sync_next_attempt_at = NULL,"
                        " shopify_sync_attempt_token = NULL WHERE id = ?",
                        (
                            SHOPIFY_SYNC_REQUIRES_REVIEW,
                            _SHOPIFY_IDENTITY_CONFLICT_ERROR,
                            row["id"],
                        ),
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
        *,
        privacy_event_id: str | None = None,
        privacy_shop_domain: str | None = None,
        order_ids: object = None,
    ) -> str:
        """Apply customer delete/redact as one serialized transaction.

        The return value is ``"deleted"``, ``"unlinked"``, or ``"unknown"``.
        Tombstoning, the final claimed/activity decision, account mutation,
        and pending-entitlement update commit together. This removes crash
        and concurrent webhook windows that could otherwise lose or
        resurrect paid access.
        """
        customer_id = _compatible_customer_id(customer_id)
        email = email.strip().lower()
        normalized_order_ids = (
            self._privacy_order_ids(order_ids) if redact else ()
        )
        privacy_guard = (
            shopify_remote_privacy_lock(self._db_path)
            if redact
            else nullcontext()
        )
        with privacy_guard, self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                now = time.time()
                if redact and privacy_event_id is not None:
                    if privacy_shop_domain is None:
                        raise ValueError(
                            "Shopify privacy shop domain is required."
                        )
                    if not self._claim_privacy_event_locked(
                        _SHOPIFY_PRIVACY_CUSTOMER_REDACT_TOPIC,
                        self._privacy_shop_domain(privacy_shop_domain),
                        privacy_event_id,
                        now=now,
                    ):
                        self._conn.commit()
                        return "replayed"
                if redact and email:
                    # This is a privacy fence, not an identity merge: an
                    # unlinked independent account at the redacted address
                    # keeps its local data, but any selected/active outbound
                    # attempt is invalidated and automatic Shopify writes
                    # stay blocked.
                    self._conn.execute(
                        "UPDATE users SET"
                        " shopify_sync_generation ="
                        "   shopify_sync_generation + 1,"
                        " shopify_sync_blocked = 1,"
                        " shopify_sync_status = ?,"
                        " shopify_sync_error = ?,"
                        " shopify_sync_next_attempt_at = NULL,"
                        " shopify_sync_attempt_token = NULL"
                        " WHERE email = ?"
                        " AND (? IS NULL"
                        "      OR shopify_customer_id IS NULL"
                        "      OR shopify_customer_id = ?)",
                        (
                            SHOPIFY_SYNC_REQUIRES_REVIEW,
                            _SHOPIFY_REDACTED_ERROR,
                            email,
                            customer_id,
                            customer_id,
                        ),
                    )
                prior_former_user_id = None
                if customer_id:
                    self._conn.execute(
                        "DELETE FROM shopify_pending_customer_links"
                        " WHERE customer_id = ?",
                        (customer_id,),
                    )
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

                if (
                    redact
                    and user is not None
                    and (not email or str(user["email"]) != email)
                ):
                    self._conn.execute(
                        "UPDATE users SET"
                        " shopify_sync_generation ="
                        "   shopify_sync_generation + 1,"
                        " shopify_sync_blocked = 1,"
                        " shopify_sync_status = ?,"
                        " shopify_sync_error = ?,"
                        " shopify_sync_next_attempt_at = NULL,"
                        " shopify_sync_attempt_token = NULL"
                        " WHERE id = ?",
                        (
                            SHOPIFY_SYNC_REQUIRES_REVIEW,
                            _SHOPIFY_REDACTED_ERROR,
                            user["id"],
                        ),
                    )

                if user is None:
                    if redact:
                        self._erase_customer_shopify_data(
                            customer_id,
                            email,
                            order_ids=normalized_order_ids,
                            include_unscoped_email=not identity_conflict,
                        )
                    if redact and email and not identity_conflict:
                        self._conn.execute(
                            "DELETE FROM email_codes WHERE email = ?", (email,)
                        )
                        self._conn.execute(
                            "DELETE FROM signup_intents WHERE email = ?", (email,)
                        )
                    self._conn.commit()
                    return "unknown"

                claimed = self._user_row_is_claimed(user)
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
                    order_rows = (
                        []
                        if redact
                        else self._conn.execute(
                            "SELECT order_id, grant_start, grant_end"
                            " FROM shopify_orders"
                            " WHERE user_id = ? AND days > 0"
                            "   AND cancelled_at IS NULL"
                            " ORDER BY COALESCE(grant_start, applied_at),"
                            " order_id",
                            (user["id"],),
                        ).fetchall()
                    )
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
                    if redact:
                        self._erase_customer_shopify_data(
                            customer_id,
                            str(user["email"]),
                            order_ids=normalized_order_ids,
                            user_id=str(user["id"]),
                        )
                        self._conn.execute(
                            "DELETE FROM email_codes WHERE email = ?",
                            (user["email"],),
                        )
                        self._conn.execute(
                            "DELETE FROM signup_intents WHERE email = ?",
                            (user["email"],),
                        )
                    else:
                        self._delete_user_children_locked((user["id"],))
                        if remaining > 0:
                            self._conn.execute(
                                "INSERT INTO pro_grants (email, days)"
                                " VALUES (?, ?)"
                                " ON CONFLICT(email) DO UPDATE"
                                " SET days = days + excluded.days",
                                (user["email"], remaining),
                            )
                    self._conn.execute(
                        "DELETE FROM users WHERE id = ?", (user["id"],)
                    )
                    self._conn.commit()
                    return "deleted"

                sets = (
                    "shopify_customer_id = NULL, shopify_sync_status = ?,"
                    " shopify_sync_error = ?,"
                    " shopify_sync_next_attempt_at = NULL,"
                    " shopify_sync_attempt_token = NULL"
                )
                if redact:
                    self._erase_customer_shopify_data(
                        customer_id,
                        str(user["email"]),
                        order_ids=normalized_order_ids,
                        user_id=str(user["id"]),
                    )
                    self._conn.execute(
                        "DELETE FROM signup_intents WHERE email = ?",
                        (user["email"],),
                    )
                    sets += (
                        ", source = NULL, shopify_account_subject = NULL,"
                        " shopify_account_migration_state = 'redacted',"
                        " shopify_account_linked_at = NULL,"
                        " shopify_account_last_login_at = NULL"
                    )
                self._conn.execute(
                    f"UPDATE users SET {sets} WHERE id = ?",
                    (
                        SHOPIFY_SYNC_REQUIRES_REVIEW,
                        _SHOPIFY_LINK_REMOVED_ERROR,
                        user["id"],
                    ),
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
        sets = (
            "shopify_customer_id = NULL, shopify_sync_status = ?,"
            " shopify_sync_error = ?,"
            " shopify_sync_next_attempt_at = NULL,"
            " shopify_sync_attempt_token = NULL"
        )
        if clear_source:
            sets += (
                ", source = NULL, shopify_account_subject = NULL,"
                " shopify_account_migration_state = 'redacted',"
                " shopify_account_linked_at = NULL,"
                " shopify_account_last_login_at = NULL"
            )
        with self._lock:
            self._conn.execute(
                f"UPDATE users SET {sets} WHERE id = ?",
                (
                    SHOPIFY_SYNC_REQUIRES_REVIEW,
                    _SHOPIFY_LINK_REMOVED_ERROR,
                    user_id,
                ),
            )
            self._conn.commit()

    def delete_user(self, user_id: str) -> None:
        """Remove a user row outright. Callers must only do this for
        unclaimed stubs — see shopify_billing for the guard rails."""
        with self._lock:
            self._delete_user_children_locked((user_id,))
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

    # -- CaddieInsight profile, practice loop, and first-party events -----
    @staticmethod
    def _profile_choice(
        value: object,
        allowed: tuple[str, ...],
        label: str,
        *,
        optional: bool = False,
    ) -> str | None:
        normalized = str(value or "").strip().lower()
        if not normalized and optional:
            return None
        if normalized not in allowed:
            raise ValueError(f"Choose a valid {label}.")
        return normalized

    @staticmethod
    def _profile_integer(
        value: object,
        allowed: tuple[int, ...],
        label: str,
    ) -> int:
        try:
            normalized = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"Choose a valid {label}.") from None
        if normalized not in allowed:
            raise ValueError(f"Choose a valid {label}.")
        return normalized

    @staticmethod
    def _profile_club(value: object) -> str | None:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if normalized not in {
            "driver",
            "fairway-wood",
            "hybrid",
            "iron",
            "wedge",
        }:
            raise ValueError("Choose a valid preferred club.")
        return normalized

    @staticmethod
    def _user_row_is_claimed(row: sqlite3.Row) -> bool:
        return _account_is_claimed(
            row["password_hash"],
            row["email_verified_at"],
            row["shopify_account_migration_state"],
        )

    @staticmethod
    def _profile_display_name(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Enter the name you want CaddieInsight to use.")
        if any(
            character in "\r\n"
            or unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in value
        ):
            raise ValueError("Your display name contains invalid characters.")
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized:
            raise ValueError("Enter the name you want CaddieInsight to use.")
        if len(normalized) > _GOLFER_DISPLAY_NAME_MAX:
            raise ValueError(
                "Your display name must be 50 characters or fewer."
            )
        if any(
            character in "\r\n"
            or unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in normalized
        ):
            raise ValueError("Your display name contains invalid characters.")
        return normalized

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> GolferProfile:
        return GolferProfile(
            user_id=str(row["user_id"]),
            display_name=row["display_name"],
            experience_mode=str(row["experience_mode"]),
            handicap_range=row["handicap_range"],
            primary_goal=row["primary_goal"],
            practice_minutes=int(row["practice_minutes"]),
            sessions_per_week=int(row["sessions_per_week"]),
            handedness=str(row["handedness"]),
            camera_angle=str(row["camera_angle"]),
            preferred_club=row["preferred_club"],
            reduced_motion=bool(row["reduced_motion"]),
            marketing_email_opt_in=bool(row["marketing_email_opt_in"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def get_golfer_profile(self, user_id: str) -> GolferProfile | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM golfer_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._profile_from_row(row) if row is not None else None

    def ensure_golfer_profile(
        self,
        user_id: str,
        *,
        now: float | None = None,
    ) -> GolferProfile:
        """Create one incomplete profile shell for a claimed account.

        Shopify customer webhooks create unclaimed local stubs. Those rows
        must not gain CaddieInsight-owned profile data until the customer has
        proved account ownership with a password or verified email code.
        """

        timestamp = time.time() if now is None else float(now)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                user_row = self._conn.execute(
                    "SELECT password_hash, email_verified_at,"
                    " shopify_account_migration_state"
                    " FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if user_row is None:
                    raise ValueError("Your account is no longer available.")
                if not self._user_row_is_claimed(user_row):
                    raise ValueError(
                        "Verify your account before creating a golfer profile."
                    )
                self._conn.execute(
                    "INSERT INTO golfer_profiles"
                    " (user_id, created_at, updated_at) VALUES (?, ?, ?)"
                    " ON CONFLICT(user_id) DO NOTHING",
                    (user_id, timestamp, timestamp),
                )
                row = self._conn.execute(
                    "SELECT * FROM golfer_profiles WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        assert row is not None
        return self._profile_from_row(row)

    def upsert_golfer_profile(
        self,
        user_id: str,
        *,
        display_name: object = _GOLFER_DISPLAY_NAME_OMITTED,
        experience_mode: object,
        handicap_range: object,
        primary_goal: object,
        practice_minutes: object,
        sessions_per_week: object,
        handedness: object,
        camera_angle: object,
        preferred_club: object,
        reduced_motion: bool = False,
        marketing_email_opt_in: bool = False,
        now: float | None = None,
    ) -> GolferProfile:
        """Validate and atomically save Caddie-owned preferences.

        The caller has to provide every established product-facing
        preference. ``display_name`` is optional only for compatibility with
        older clients; omitting it preserves a previously saved name.
        """

        mode = self._profile_choice(
            experience_mode, GOLFER_EXPERIENCE_MODES, "experience mode"
        )
        normalized_display_name = (
            _GOLFER_DISPLAY_NAME_OMITTED
            if display_name is _GOLFER_DISPLAY_NAME_OMITTED
            else self._profile_display_name(display_name)
        )
        handicap = self._profile_choice(
            handicap_range,
            GOLFER_HANDICAP_RANGES,
            "handicap range",
            optional=True,
        )
        goal = self._profile_choice(
            primary_goal,
            GOLFER_PRIMARY_GOALS,
            "main goal",
            optional=True,
        )
        minutes = self._profile_integer(
            practice_minutes, GOLFER_PRACTICE_MINUTES, "practice time"
        )
        sessions = self._profile_integer(
            sessions_per_week,
            GOLFER_SESSIONS_PER_WEEK,
            "weekly practice frequency",
        )
        hand = self._profile_choice(
            handedness, ("right", "left"), "handedness"
        )
        angle = self._profile_choice(
            camera_angle, ("face-on", "dtl"), "camera angle"
        )
        club = self._profile_club(preferred_club)
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                owner = self._conn.execute(
                    "SELECT password_hash, email_verified_at,"
                    " shopify_account_migration_state"
                    " FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if owner is None:
                    raise ValueError("Your account is no longer available.")
                if not self._user_row_is_claimed(owner):
                    raise ValueError(
                        "Verify your account before creating a golfer profile."
                    )
                existing_profile = self._conn.execute(
                    "SELECT display_name FROM golfer_profiles"
                    " WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                stored_display_name = (
                    (
                        existing_profile["display_name"]
                        if existing_profile is not None
                        else None
                    )
                    if normalized_display_name is _GOLFER_DISPLAY_NAME_OMITTED
                    else normalized_display_name
                )
                self._conn.execute(
                    "INSERT INTO golfer_profiles"
                    " (user_id, display_name, experience_mode, handicap_range,"
                    "  primary_goal,"
                    "  practice_minutes, sessions_per_week, handedness,"
                    "  camera_angle, preferred_club, reduced_motion,"
                    "  marketing_email_opt_in, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(user_id) DO UPDATE SET"
                    " display_name = excluded.display_name,"
                    " experience_mode = excluded.experience_mode,"
                    " handicap_range = excluded.handicap_range,"
                    " primary_goal = excluded.primary_goal,"
                    " practice_minutes = excluded.practice_minutes,"
                    " sessions_per_week = excluded.sessions_per_week,"
                    " handedness = excluded.handedness,"
                    " camera_angle = excluded.camera_angle,"
                    " preferred_club = excluded.preferred_club,"
                    " reduced_motion = excluded.reduced_motion,"
                    " marketing_email_opt_in = excluded.marketing_email_opt_in,"
                    " updated_at = excluded.updated_at",
                    (
                        user_id,
                        stored_display_name,
                        mode,
                        handicap,
                        goal,
                        minutes,
                        sessions,
                        hand,
                        angle,
                        club,
                        int(bool(reduced_motion)),
                        int(bool(marketing_email_opt_in)),
                        timestamp,
                        timestamp,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM golfer_profiles WHERE user_id = ?", (user_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        assert row is not None
        return self._profile_from_row(row)

    def _assert_history_epoch_locked(
        self,
        user_id: str,
        expected_history_epoch: int,
        *,
        session_ids: Iterable[str] = (),
    ) -> None:
        """Fence a write against a concurrent history reset.

        Callers start ``BEGIN IMMEDIATE`` before this check.  That ordering
        makes the epoch read and the related write one serialized operation
        with :meth:`delete_swing_history_related`, even though the job manager
        and user store use separate SQLite connections.
        """

        row = self._conn.execute(
            "SELECT history_epoch FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None or int(row["history_epoch"] or 0) != int(
            expected_history_epoch
        ):
            raise HistoryEpochError(
                "Swing history changed while this request was in progress."
            )
        for session_id in dict.fromkeys(session_ids):
            owner = self._conn.execute(
                "SELECT user_id FROM jobs WHERE id = ?", (session_id,)
            ).fetchone()
            if owner is None or str(owner["user_id"] or "") != user_id:
                raise HistoryEpochError(
                    "Swing history changed while this request was in progress."
                )

    @staticmethod
    def delete_swing_history_related(
        connection: sqlite3.Connection,
        user_id: str,
        *,
        expected_auth_epoch: int | None = None,
        expected_history_epoch: int | None = None,
    ) -> int:
        """Delete non-job swing history inside a JobManager reset transaction.

        The manager invokes this callback after it has frozen and validated
        the user's owned-job set but before deleting those job rows.  Keeping
        the work on the manager's connection gives the reset one atomic SQLite
        commit while deliberately preserving the account, golfer profile,
        entitlements, purchases, credentials, device tokens, and quota
        receipts.
        """

        row = connection.execute(
            "SELECT auth_epoch, history_epoch FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HistoryEpochError("The account is no longer available.")
        if expected_auth_epoch is not None and int(
            row["auth_epoch"] or 0
        ) != int(expected_auth_epoch):
            raise HistoryAuthEpochError(
                "Account authentication changed while this request was in progress."
            )
        if expected_history_epoch is not None and int(
            row["history_epoch"] or 0
        ) != int(expected_history_epoch):
            raise HistoryEpochError(
                "Swing history changed after reset confirmation."
            )

        for table in (
            "practice_checkins",
            "proof_cycle_practice_evidence",
            "proof_cycle_transfer_checks",
        ):
            connection.execute(
                f'DELETE FROM "{table}" WHERE user_id = ?', (user_id,)
            )
        connection.execute(
            "DELETE FROM product_events WHERE user_id = ?"
            " AND session_id IS NOT NULL",
            (user_id,),
        )

        # A cached Shopify privacy export may contain the reports or practice
        # rows being reset. Block while a matching/unreadable export is still
        # awaiting delivery; after delivery, purge stale matching cache so a
        # later download cannot resurrect old swing history.
        request_ids: list[str] = []
        for privacy_row in connection.execute(
            "SELECT request_id, snapshot_json, delivered_at"
            " FROM shopify_privacy_requests"
        ).fetchall():
            delivered = privacy_row["delivered_at"] is not None
            try:
                snapshot = json.loads(str(privacy_row["snapshot_json"]))
            except (TypeError, ValueError):
                if not delivered:
                    raise HistoryPrivacyExportConflict(
                        "An undelivered privacy export requires operator review."
                    )
                request_ids.append(str(privacy_row["request_id"]))
                continue
            if not isinstance(snapshot, dict) or snapshot.get(
                "schema_version"
            ) != 1:
                if not delivered:
                    raise HistoryPrivacyExportConflict(
                        "An undelivered privacy export requires operator review."
                    )
                request_ids.append(str(privacy_row["request_id"]))
                continue
            if UserStore._privacy_snapshot_matches_customer(
                snapshot,
                customer_id=None,
                order_ids=set(),
                emails=set(),
                user_ids={user_id},
            ):
                if not delivered:
                    raise HistoryPrivacyExportConflict(
                        "Deliver the pending privacy export before resetting history."
                    )
                request_ids.append(str(privacy_row["request_id"]))
        if request_ids:
            placeholders = ", ".join("?" for _ in request_ids)
            connection.execute(
                f"DELETE FROM shopify_privacy_requests"
                f" WHERE request_id IN ({placeholders})",
                tuple(request_ids),
            )

        connection.execute(
            "UPDATE users SET history_epoch = history_epoch + 1 WHERE id = ?",
            (user_id,),
        )
        updated = connection.execute(
            "SELECT history_epoch FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if updated is None:
            raise HistoryEpochError("The account is no longer available.")
        return int(updated["history_epoch"] or 0)

    def record_practice_checkin(
        self,
        user_id: str,
        session_id: str,
        *,
        expected_history_epoch: int | None = None,
        now: float | None = None,
    ) -> PracticeCheckin:
        """Mark one prescribed session practiced, replay-safely."""

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id or len(normalized_session_id) > 128:
            raise ValueError("Invalid practice session.")
        completed_at = time.time() if now is None else float(now)
        with self._lock:
            try:
                if expected_history_epoch is not None:
                    self._conn.execute("BEGIN IMMEDIATE")
                    self._assert_history_epoch_locked(
                        user_id,
                        expected_history_epoch,
                        session_ids=(normalized_session_id,),
                    )
                self._conn.execute(
                    "INSERT INTO practice_checkins"
                    " (user_id, session_id, completed_at) VALUES (?, ?, ?)"
                    " ON CONFLICT(user_id, session_id) DO UPDATE SET"
                    " completed_at = excluded.completed_at",
                    (user_id, normalized_session_id, completed_at),
                )
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        return PracticeCheckin(
            user_id=user_id,
            session_id=normalized_session_id,
            completed_at=completed_at,
        )

    def list_practice_checkins(
        self, user_id: str, *, limit: int = 50
    ) -> list[PracticeCheckin]:
        try:
            bounded_limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            raise ValueError("Invalid practice check-in limit.") from None
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, session_id, completed_at"
                " FROM practice_checkins WHERE user_id = ?"
                " ORDER BY completed_at DESC LIMIT ?",
                (user_id, bounded_limit),
            ).fetchall()
        return [
            PracticeCheckin(
                user_id=str(row["user_id"]),
                session_id=str(row["session_id"]),
                completed_at=float(row["completed_at"]),
            )
            for row in rows
        ]

    # The existing ``practice_checkins`` table is intentionally left as a
    # small, replay-safe compatibility surface.  Proof Cycle practice receipts
    # live separately because they need an immutable target context and must
    # never change what the legacy API means.
    @staticmethod
    def _proof_cycle_token(
        value: object,
        label: str,
        *,
        maximum: int = 128,
    ) -> str:
        token = str(value or "").strip()
        if (
            not token
            or len(token) > maximum
            or any(not (char.isalnum() or char in "._:-") for char in token)
        ):
            raise ValueError(f"Invalid Proof Cycle {label}.")
        return token

    @staticmethod
    def _proof_cycle_target_fingerprint(value: object) -> str:
        fingerprint = str(value or "").strip()
        if (
            len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise ValueError("Invalid Proof Cycle target.")
        return fingerprint

    @staticmethod
    def _proof_cycle_timestamp(value: float | None, label: str) -> float:
        try:
            timestamp = time.time() if value is None else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid Proof Cycle {label}.") from None
        if timestamp != timestamp or timestamp in (float("inf"), float("-inf")):
            raise ValueError(f"Invalid Proof Cycle {label}.")
        return timestamp

    @staticmethod
    def _proof_cycle_evidence_from_row(
        row: sqlite3.Row,
    ) -> ProofCyclePracticeEvidence:
        outcome = str(row["outcome"])
        if outcome not in PRACTICE_OUTCOMES:
            raise ValueError("Stored Proof Cycle practice outcome is invalid.")
        minutes = normalize_practice_minutes(row["minutes"])
        return ProofCyclePracticeEvidence(
            user_id=str(row["user_id"]),
            baseline_session_id=str(row["baseline_session_id"]),
            target_fingerprint=str(row["target_fingerprint"]),
            drill_id=str(row["drill_id"]),
            minutes=minutes,
            outcome=outcome,  # type: ignore[arg-type]
            completed_at=float(row["completed_at"]),
            completed_day=int(row["completed_day"]),
        )

    @staticmethod
    def _proof_cycle_transfer_from_row(
        row: sqlite3.Row,
    ) -> ProofCycleTransferCheck:
        if row["normal_swings"] != 1:
            raise ValueError("Stored Proof Cycle transfer check is invalid.")
        return ProofCycleTransferCheck(
            session_id=str(row["session_id"]),
            user_id=str(row["user_id"]),
            baseline_session_id=str(row["baseline_session_id"]),
            target_fingerprint=str(row["target_fingerprint"]),
            drill_id=str(row["drill_id"]),
            club=str(row["club"]),
            hand=str(row["hand"]),
            angle=str(row["angle"]),
            normal_swings=True,
            declared_at=float(row["declared_at"]),
        )

    def record_proof_cycle_practice_evidence(
        self,
        user_id: str,
        *,
        baseline_session_id: object,
        target_fingerprint: object,
        drill_id: object,
        minutes: object,
        outcome: object,
        expected_history_epoch: int | None = None,
        now: float | None = None,
    ) -> ProofCyclePracticeEvidence:
        """Upsert one structured, self-reported receipt per target per day.

        Updating the day's receipt makes a double-submit harmless while still
        allowing a golfer to correct the duration/outcome after finishing the
        selected block.  It does not update a Proof Cycle sidecar or verdict.
        """

        normalized_user = self._proof_cycle_token(user_id, "user")
        baseline = self._proof_cycle_token(
            baseline_session_id, "baseline session"
        )
        fingerprint = self._proof_cycle_target_fingerprint(target_fingerprint)
        drill = self._proof_cycle_token(drill_id, "drill")
        duration = normalize_practice_minutes(minutes)
        normalized_outcome = normalize_practice_outcome(outcome)
        completed_at = self._proof_cycle_timestamp(now, "practice time")
        completed_day = int(completed_at // 86400)
        with self._lock:
            try:
                if expected_history_epoch is not None:
                    self._conn.execute("BEGIN IMMEDIATE")
                    self._assert_history_epoch_locked(
                        normalized_user,
                        expected_history_epoch,
                        session_ids=(baseline,),
                    )
                self._conn.execute(
                    "INSERT INTO proof_cycle_practice_evidence"
                    " (user_id, baseline_session_id, target_fingerprint, drill_id,"
                    "  minutes, outcome, completed_at, completed_day)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(user_id, baseline_session_id, target_fingerprint,"
                    "             completed_day) DO UPDATE SET"
                    " drill_id = excluded.drill_id, minutes = excluded.minutes,"
                    " outcome = excluded.outcome, completed_at = excluded.completed_at",
                    (
                        normalized_user,
                        baseline,
                        fingerprint,
                        drill,
                        duration,
                        normalized_outcome,
                        completed_at,
                        completed_day,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM proof_cycle_practice_evidence"
                    " WHERE user_id = ? AND baseline_session_id = ?"
                    " AND target_fingerprint = ? AND completed_day = ?",
                    (normalized_user, baseline, fingerprint, completed_day),
                ).fetchone()
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        assert row is not None
        return self._proof_cycle_evidence_from_row(row)

    def list_proof_cycle_practice_evidence(
        self,
        user_id: str,
        *,
        baseline_session_id: object | None = None,
        target_fingerprint: object | None = None,
        limit: int = 100,
    ) -> list[ProofCyclePracticeEvidence]:
        """Read only the authenticated user's bounded structured receipts."""

        normalized_user = self._proof_cycle_token(user_id, "user")
        try:
            bounded_limit = max(1, min(int(limit), 365))
        except (TypeError, ValueError):
            raise ValueError("Invalid Proof Cycle practice limit.") from None
        clauses = ["user_id = ?"]
        params: list[object] = [normalized_user]
        if baseline_session_id is not None:
            clauses.append("baseline_session_id = ?")
            params.append(
                self._proof_cycle_token(
                    baseline_session_id, "baseline session"
                )
            )
        if target_fingerprint is not None:
            clauses.append("target_fingerprint = ?")
            params.append(
                self._proof_cycle_target_fingerprint(target_fingerprint)
            )
        params.append(bounded_limit)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM proof_cycle_practice_evidence WHERE "
                + " AND ".join(clauses)
                + " ORDER BY completed_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._proof_cycle_evidence_from_row(row) for row in rows]

    def record_proof_cycle_transfer_check(
        self,
        user_id: str,
        *,
        session_id: object,
        baseline_session_id: object,
        target_fingerprint: object,
        drill_id: object,
        club: object,
        hand: object,
        angle: object,
        normal_swings: object,
        expected_history_epoch: int | None = None,
        now: float | None = None,
    ) -> ProofCycleTransferCheck:
        """Persist one server-validated normal-swing declaration per upload."""

        normalized_user = self._proof_cycle_token(user_id, "user")
        session = self._proof_cycle_token(session_id, "session")
        baseline = self._proof_cycle_token(
            baseline_session_id, "baseline session"
        )
        fingerprint = self._proof_cycle_target_fingerprint(target_fingerprint)
        drill = self._proof_cycle_token(drill_id, "drill")
        normalized_club = self._proof_cycle_token(club, "club")
        normalized_hand = str(hand or "").strip().lower()
        normalized_angle = str(angle or "").strip().lower()
        if normalized_hand not in {"left", "right"}:
            raise ValueError("Invalid Proof Cycle handedness.")
        if normalized_angle not in {"face-on", "dtl"}:
            raise ValueError("Invalid Proof Cycle camera angle.")
        if normal_swings is not True:
            raise ValueError("A normal-swing declaration is required.")
        declared_at = self._proof_cycle_timestamp(now, "transfer declaration time")
        values = (
            session,
            normalized_user,
            baseline,
            fingerprint,
            drill,
            normalized_club,
            normalized_hand,
            normalized_angle,
            1,
            declared_at,
        )
        with self._lock:
            try:
                if expected_history_epoch is not None:
                    self._conn.execute("BEGIN IMMEDIATE")
                    self._assert_history_epoch_locked(
                        normalized_user,
                        expected_history_epoch,
                        session_ids=(session, baseline),
                    )
                self._conn.execute(
                    "INSERT INTO proof_cycle_transfer_checks"
                    " (session_id, user_id, baseline_session_id, target_fingerprint,"
                    "  drill_id, club, hand, angle, normal_swings, declared_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(session_id) DO NOTHING",
                    values,
                )
                row = self._conn.execute(
                    "SELECT * FROM proof_cycle_transfer_checks WHERE session_id = ?",
                    (session,),
                ).fetchone()
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        assert row is not None
        stored = self._proof_cycle_transfer_from_row(row)
        if stored != ProofCycleTransferCheck(
            session_id=session,
            user_id=normalized_user,
            baseline_session_id=baseline,
            target_fingerprint=fingerprint,
            drill_id=drill,
            club=normalized_club,
            hand=normalized_hand,
            angle=normalized_angle,
            normal_swings=True,
            declared_at=stored.declared_at,
        ):
            raise ValueError("This upload already has a different transfer check.")
        return stored

    def get_proof_cycle_transfer_check(
        self, user_id: str, session_id: object
    ) -> ProofCycleTransferCheck | None:
        """Return a transfer declaration only within the authenticated owner."""

        normalized_user = self._proof_cycle_token(user_id, "user")
        session = self._proof_cycle_token(session_id, "session")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM proof_cycle_transfer_checks"
                " WHERE user_id = ? AND session_id = ?",
                (normalized_user, session),
            ).fetchone()
        return self._proof_cycle_transfer_from_row(row) if row else None

    @staticmethod
    def _product_event_token(value: object, label: str, *, optional: bool) -> str | None:
        token = str(value or "").strip()
        if not token and optional:
            return None
        if (
            not token
            or len(token) > _PRODUCT_EVENT_ID_MAX
            or any(not (char.isalnum() or char in "._:-") for char in token)
        ):
            raise ValueError(f"Invalid {label}.")
        return token

    def record_product_event(
        self,
        event_name: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        anonymous_id: str | None = None,
        metadata: dict[str, str] | None = None,
        dedupe_key: str | None = None,
        expected_history_epoch: int | None = None,
        now: float | None = None,
    ) -> bool:
        """Append a minimal, first-party product event.

        There is no request body, IP address, email, or free-form note in the
        ledger.  The only supported metadata is a small internal string map,
        and callers should use it for product surfaces—not personal data.
        """

        normalized_name = str(event_name or "").strip()
        if normalized_name not in PRODUCT_EVENT_NAMES:
            raise ValueError("Unsupported product event.")
        session = self._product_event_token(
            session_id, "session id", optional=True
        )
        anonymous = self._product_event_token(
            anonymous_id, "anonymous id", optional=True
        )
        key = self._product_event_token(
            dedupe_key, "event dedupe key", optional=True
        )
        if user_id is not None:
            user_id = self._product_event_token(
                user_id, "user id", optional=False
            )
        if not user_id and not anonymous:
            raise ValueError("A product event needs an account or anonymous id.")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict) or len(metadata) > 8:
            raise ValueError("Invalid product event metadata.")
        cleaned_metadata: dict[str, str] = {}
        for raw_key, raw_value in metadata.items():
            metadata_key = self._product_event_token(
                raw_key, "event metadata key", optional=False
            )
            metadata_value = str(raw_value or "").strip()
            if len(metadata_value) > 80:
                raise ValueError("Invalid product event metadata.")
            cleaned_metadata[str(metadata_key)] = metadata_value
        metadata_json = json.dumps(
            cleaned_metadata, sort_keys=True, separators=(",", ":")
        )
        if len(metadata_json.encode("utf-8")) > _PRODUCT_EVENT_METADATA_MAX:
            raise ValueError("Invalid product event metadata.")
        event_id = uuid.uuid4().hex
        created_at = time.time() if now is None else float(now)
        with self._lock:
            try:
                if expected_history_epoch is not None:
                    if user_id is None:
                        raise HistoryEpochError(
                            "History fencing requires an account."
                        )
                    self._conn.execute("BEGIN IMMEDIATE")
                    self._assert_history_epoch_locked(
                        user_id,
                        expected_history_epoch,
                        session_ids=((session,) if session is not None else ()),
                    )
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO product_events"
                    " (id, event_name, user_id, session_id, anonymous_id,"
                    "  metadata_json, created_at, dedupe_key)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        normalized_name,
                        user_id,
                        session,
                        anonymous,
                        metadata_json,
                        created_at,
                        key,
                    ),
                )
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        return cursor.rowcount == 1

    def product_event_counts(self, *, since: float | None = None) -> dict[str, int]:
        params: tuple[object, ...] = ()
        query = "SELECT event_name, COUNT(*) AS count FROM product_events"
        if since is not None:
            query += " WHERE created_at >= ?"
            params = (float(since),)
        query += " GROUP BY event_name"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        counts = {name: 0 for name in PRODUCT_EVENT_NAMES}
        counts.update({str(row["event_name"]): int(row["count"]) for row in rows})
        return counts

    def user_id_for_shopify_order(self, order_id: object) -> str | None:
        """Return the one durable app identity attached to a paid order.

        A fulfillment webhook often carries only an order ID.  The paid-order
        transaction records the app user alongside both Pro and gear rows, so
        fulfillment telemetry can remain identity-bound without falling back
        to an email lookup.  Ambiguous or legacy rows deliberately produce no
        event rather than guessing an account.
        """

        normalized_order_id = str(order_id or "").strip()
        if not normalized_order_id or len(normalized_order_id) > _SHOPIFY_PRIVACY_ID_MAX:
            return None
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT user_id FROM ("
                " SELECT user_id FROM shopify_orders WHERE order_id = ?"
                " UNION"
                " SELECT user_id FROM gear_orders WHERE order_id = ?"
                ") WHERE user_id IS NOT NULL LIMIT 2",
                (normalized_order_id, normalized_order_id),
            ).fetchall()
        if len(rows) != 1:
            return None
        user_id = rows[0]["user_id"]
        return str(user_id) if isinstance(user_id, str) and user_id else None

    # -- Shopify Customer Account migration state ------------------------
    @staticmethod
    def _oauth_secret(value: object, label: str) -> str:
        secret = str(value or "").strip()
        if not 32 <= len(secret) <= 512:
            raise ValueError(f"Invalid Shopify Customer Account {label}.")
        return secret

    def issue_shopify_customer_account_oauth_state(
        self,
        *,
        state: object,
        verifier: object,
        nonce: object,
        user_id: str | None,
        mode: str,
        now: float | None = None,
    ) -> None:
        """Persist the PKCE verifier server-side for one authorization flow.

        Starlette sessions are signed cookies, not a server-side session
        store.  Keeping the verifier here avoids putting the OAuth secret in
        a browser-readable cookie.  The row is one-use and expires in ten
        minutes even if the callback never arrives.
        """

        state_value = self._oauth_secret(state, "state")
        verifier_value = self._oauth_secret(verifier, "code verifier")
        nonce_value = self._oauth_secret(nonce, "nonce")
        if mode not in _SHOPIFY_ACCOUNT_OAUTH_MODES:
            raise ValueError("Invalid Shopify Customer Account flow.")
        if user_id is not None and self._product_event_token(
            user_id, "user id", optional=False
        ) is None:
            raise ValueError("Invalid Shopify Customer Account user.")
        timestamp = time.time() if now is None else float(now)
        state_hash = hashlib.sha256(state_value.encode("utf-8")).hexdigest()
        with self._lock:
            self._conn.execute(
                "DELETE FROM shopify_customer_account_oauth_states"
                " WHERE expires_at <= ?",
                (timestamp,),
            )
            self._conn.execute(
                "INSERT INTO shopify_customer_account_oauth_states"
                " (state_hash, verifier, nonce, user_id, mode, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    state_hash,
                    verifier_value,
                    nonce_value,
                    user_id,
                    mode,
                    timestamp,
                    timestamp + SHOPIFY_ACCOUNT_OAUTH_TTL_S,
                ),
            )
            self._conn.commit()

    def consume_shopify_customer_account_oauth_state(
        self,
        state: object,
        *,
        now: float | None = None,
    ) -> ShopifyCustomerAccountOAuthState | None:
        """Consume an OAuth state exactly once, regardless of its outcome."""

        try:
            state_value = self._oauth_secret(state, "state")
        except ValueError:
            return None
        timestamp = time.time() if now is None else float(now)
        state_hash = hashlib.sha256(state_value.encode("utf-8")).hexdigest()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT verifier, nonce, user_id, mode, expires_at"
                    " FROM shopify_customer_account_oauth_states"
                    " WHERE state_hash = ?",
                    (state_hash,),
                ).fetchone()
                self._conn.execute(
                    "DELETE FROM shopify_customer_account_oauth_states"
                    " WHERE state_hash = ? OR expires_at <= ?",
                    (state_hash, timestamp),
                )
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        if row is None or float(row["expires_at"]) <= timestamp:
            return None
        return ShopifyCustomerAccountOAuthState(
            verifier=str(row["verifier"]),
            nonce=str(row["nonce"]),
            user_id=(str(row["user_id"]) if row["user_id"] is not None else None),
            mode=str(row["mode"]),
        )

    def issue_shopify_customer_account_browser_session(
        self,
        user_id: str,
        id_token: object,
        *,
        expires_at: float,
    ) -> str:
        """Keep a Shopify logout token server-side for one browser session."""

        token = str(id_token or "").strip()
        if not token or len(token) > 16_384:
            raise ValueError("Invalid Shopify Customer Account logout token.")
        if self.get(user_id) is None:
            raise ValueError("Your account is no longer available.")
        session_id = secrets.token_urlsafe(32)
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        with self._lock:
            self._conn.execute(
                "DELETE FROM shopify_customer_account_browser_sessions"
                " WHERE expires_at <= ?",
                (time.time(),),
            )
            self._conn.execute(
                "INSERT INTO shopify_customer_account_browser_sessions"
                " (session_hash, user_id, id_token, expires_at) VALUES (?, ?, ?, ?)",
                (session_hash, user_id, token, float(expires_at)),
            )
            self._conn.commit()
        return session_id

    def consume_shopify_customer_account_browser_session(
        self, session_id: object, *, now: float | None = None
    ) -> ShopifyCustomerAccountBrowserSession | None:
        raw_session_id = str(session_id or "").strip()
        if not 32 <= len(raw_session_id) <= 512:
            return None
        timestamp = time.time() if now is None else float(now)
        session_hash = hashlib.sha256(raw_session_id.encode("utf-8")).hexdigest()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT user_id, id_token, expires_at"
                    " FROM shopify_customer_account_browser_sessions"
                    " WHERE session_hash = ?",
                    (session_hash,),
                ).fetchone()
                self._conn.execute(
                    "DELETE FROM shopify_customer_account_browser_sessions"
                    " WHERE session_hash = ? OR expires_at <= ?",
                    (session_hash, timestamp),
                )
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        if row is None or float(row["expires_at"]) <= timestamp:
            return None
        return ShopifyCustomerAccountBrowserSession(
            user_id=str(row["user_id"]), id_token=str(row["id_token"])
        )

    @staticmethod
    def _shopify_account_subject(value: object) -> str:
        subject = str(value or "").strip()
        if not subject or len(subject) > 255:
            raise ValueError("Invalid Shopify Customer Account subject.")
        return subject

    def link_shopify_customer_account(
        self,
        user_id: str,
        *,
        subject: object,
        customer_id: object,
        authenticated: bool = False,
        now: float | None = None,
    ) -> User:
        """Bind a locally authenticated account to a proven Shopify subject.

        This method intentionally never looks up a local user by email.  The
        authorization callback supplies a provider-proven subject plus the
        durable Shopify customer id, and both mappings must be unique.
        """

        normalized_subject = self._shopify_account_subject(subject)
        canonical_customer_id = _compatible_customer_id(customer_id)
        if canonical_customer_id is None:
            raise ValueError("Invalid Shopify customer identity.")
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                user = self._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if user is None:
                    raise ValueError("Your account is no longer available.")
                subject_owner = self._conn.execute(
                    "SELECT id FROM users WHERE shopify_account_subject = ?"
                    " AND id != ?",
                    (normalized_subject, user_id),
                ).fetchone()
                identity_owners = self._shopify_identity_rows(
                    canonical_customer_id, exclude_user_id=user_id
                )
                conflicting_customer = bool(identity_owners)
                incompatible_existing = bool(
                    user["shopify_customer_id"] is not None
                    and _compatible_customer_id(user["shopify_customer_id"])
                    != canonical_customer_id
                )
                if subject_owner is not None or conflicting_customer or incompatible_existing:
                    affected_ids = {user_id}
                    if subject_owner is not None:
                        affected_ids.add(str(subject_owner["id"]))
                    affected_ids.update(str(row["id"]) for row in identity_owners)
                    placeholders = ", ".join("?" for _ in affected_ids)
                    self._conn.execute(
                        "UPDATE users SET shopify_account_migration_state = ?"
                        f" WHERE id IN ({placeholders})",
                        (SHOPIFY_ACCOUNT_MANUAL_REVIEW, *sorted(affected_ids)),
                    )
                    self._conn.commit()
                    raise ValueError(
                        "This Shopify identity needs manual account review."
                    )
                state = (
                    SHOPIFY_ACCOUNT_AUTHENTICATED
                    if authenticated
                    else SHOPIFY_ACCOUNT_LINKED_PENDING_LOGIN
                )
                self._conn.execute(
                    "UPDATE users SET shopify_account_subject = ?,"
                    " shopify_customer_id = ?, shopify_identity_locked = 1,"
                    " shopify_sync_status = ?, shopify_last_synced_at = ?,"
                    " shopify_sync_error = NULL,"
                    " shopify_sync_next_attempt_at = NULL,"
                    " shopify_sync_attempt_token = NULL,"
                    " shopify_account_migration_state = ?,"
                    " shopify_account_linked_at = COALESCE("
                    "   shopify_account_linked_at, ?),"
                    " shopify_account_last_login_at = CASE WHEN ? THEN ?"
                    "   ELSE shopify_account_last_login_at END"
                    " WHERE id = ?",
                    (
                        normalized_subject,
                        canonical_customer_id,
                        SHOPIFY_SYNC_SYNCED,
                        timestamp,
                        state,
                        timestamp,
                        int(authenticated),
                        timestamp,
                        user_id,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
        assert row is not None
        return self._from_row(row)

    def shopify_account_migration_counts(self) -> dict[str, int]:
        """Return a PII-free migration reconciliation snapshot."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT shopify_account_migration_state, COUNT(*) AS count"
                " FROM users GROUP BY shopify_account_migration_state"
            ).fetchall()
        counts = {state: 0 for state in SHOPIFY_ACCOUNT_MIGRATION_STATES}
        unclassified = 0
        for row in rows:
            state = str(row["shopify_account_migration_state"] or "")
            count = int(row["count"])
            if state in counts:
                counts[state] += count
            else:
                unclassified += count
        counts["unclassified"] = unclassified
        counts["total"] = sum(
            count for state, count in counts.items() if state != "total"
        )
        return counts

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
                    " shopify_identity_locked, email_verified_at FROM users"
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
                email_verified = user["email_verified_at"] is not None
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
                elif not email_verified and customer_ids:
                    # An unlinked password account may have been registered
                    # by someone who does not control the inbox. Keep stable
                    # customer-bearing value parked until code verification.
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
                            and email_verified
                        )
                        or (
                            linked_customer_id is None
                            and not customer_ids
                            and not identity_conflict
                            and email_verified
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
                    + (
                        unattributed
                        if email_verified and not identity_conflict
                        else 0.0
                    ),
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
        customer_id = _compatible_customer_id(shopify_customer_id)
        gear = gear or []
        if not order_id or (days <= 0 and not gear):
            return (False, email, None)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if self._shopify_order_is_redacted_locked(order_id):
                    self._conn.rollback()
                    return (False, "", None)
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
                    if (
                        user is not None
                        and seen["shopify_customer_id"] is None
                        and user["email_verified_at"] is None
                    ):
                        # Legacy email-only attribution is not enough proof
                        # to repair or extend an unverified account.
                        user = None
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
                        if (
                            email_user is not None
                            and email_user["email_verified_at"] is not None
                        ):
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
                    else None
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
                            " (order_id, user_id, sku, title, quantity, email, created_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                order_id,
                                user_id,
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
        customer_id = _compatible_customer_id(shopify_customer_id)
        if not order_id:
            return (False, email, 0.0)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if self._shopify_order_is_redacted_locked(order_id):
                    self._conn.rollback()
                    return (False, "", 0.0)
                if customer_id:
                    redacted = self._conn.execute(
                        "SELECT 1 FROM shopify_customer_tombstones"
                        " WHERE customer_id = ? AND redacted = 1",
                        (customer_id,),
                    ).fetchone()
                    if redacted is not None:
                        self._conn.rollback()
                        return (False, "", 0.0)
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

    # -- one-shot transactional (lifecycle) emails ------------------------
    def claim_lifecycle_email(
        self,
        kind: str,
        subject_key: str,
        user_id: str | None = None,
    ) -> bool:
        """Atomically claim a one-shot transactional email BEFORE it goes
        out. True = this caller owns the send. The insert-first order is the
        same rule as claim_digest_send: a webhook replay or a crash mid-send
        can never double-email for one (kind, subject_key). ``user_id``, when
        the send belongs to an account, lets account deletion take the
        markers with it."""
        if not kind or not subject_key:
            return False
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO lifecycle_email_sends"
                " (kind, subject_key, user_id, sent_at)"
                " VALUES (?, ?, ?, ?)",
                (kind, subject_key, user_id, time.time()),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def pro_expiring_between(self, start: float, end: float) -> list[User]:
        """Accounts whose time-boxed Pro lapses in (start, end], oldest
        first. Subscription-managed Pro (Stripe plan with a live status) is
        excluded — it renews on its own, so only the day-grant entitlement
        can quietly run out. A lifetime grant sits ~100 years out and never
        lands inside a reminder window."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users WHERE pro_until > ? AND pro_until <= ?"
                " AND NOT (plan = ? AND subscription_status IN (?, ?, ?))"
                " ORDER BY created_at",
                (start, end, PRO, *_PRO_OK_STATUSES),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    # -- one-time email codes (claim verification, password reset) --------
    @staticmethod
    def _hash_code(email: str, purpose: str, code: str) -> str:
        # Tied to email+purpose so a code only works where it was issued.
        return hashlib.sha256(f"{email}|{purpose}|{code}".encode()).hexdigest()

    def issue_email_code(
        self,
        email: str,
        purpose: str,
        *,
        session_nonce: str | None = None,
    ) -> str | None:
        """Mint a fresh 6-digit code (the caller emails it). Returns None —
        and keeps the outstanding code valid — when one was already issued
        in the last CODE_RESEND_S seconds, so an email can't be flooded."""
        email = email.strip().lower()
        now = time.time()
        nonce_hash = self._hash_flow_session_nonce(session_nonce)
        if session_nonce is not None and nonce_hash is None:
            raise ValueError("Invalid email verification session.")
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
                " (email, purpose, code_hash, created_at, expires_at, attempts,"
                "  session_nonce_hash)"
                " VALUES (?, ?, ?, ?, ?, 0, ?)",
                (email, purpose, self._hash_code(email, purpose, code),
                 now, now + CODE_TTL_S, nonce_hash),
            )
            self._conn.commit()
        return code

    def check_email_code(
        self,
        email: str,
        purpose: str,
        code: str,
        *,
        session_nonce: str | None = None,
    ) -> bool:
        """Verify and consume a code: single-use (deleted on success),
        expired codes never match, and CODE_MAX_ATTEMPTS wrong guesses
        burn the code so it can't be brute-forced."""
        email = email.strip().lower()
        now = time.time()
        nonce_hash = self._hash_flow_session_nonce(session_nonce)
        if session_nonce is not None and nonce_hash is None:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT code_hash, expires_at, attempts, session_nonce_hash"
                " FROM email_codes"
                " WHERE email = ? AND purpose = ?",
                (email, purpose),
            ).fetchone()
            if row is None:
                return False
            if not self._flow_session_nonce_matches(
                row["session_nonce_hash"], nonce_hash
            ):
                # A copied code submitted from another browser must neither
                # consume nor age the initiating browser's verification.
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
            auth_epoch=int(row["auth_epoch"] or 0),
            history_epoch=int(row["history_epoch"] or 0),
            shopify_sync_status=row["shopify_sync_status"],
            shopify_last_synced_at=row["shopify_last_synced_at"],
            shopify_sync_error=row["shopify_sync_error"],
            shopify_sync_attempts=int(row["shopify_sync_attempts"] or 0),
            shopify_sync_next_attempt_at=row["shopify_sync_next_attempt_at"],
            shopify_sync_attempt_token=row["shopify_sync_attempt_token"],
            shopify_sync_generation=int(
                row["shopify_sync_generation"] or 0
            ),
            shopify_sync_blocked=bool(row["shopify_sync_blocked"]),
            shopify_account_subject=row["shopify_account_subject"],
            shopify_account_migration_state=(
                row["shopify_account_migration_state"]
                or SHOPIFY_ACCOUNT_LOCAL_ONLY
            ),
            shopify_account_linked_at=row["shopify_account_linked_at"],
            shopify_account_last_login_at=row[
                "shopify_account_last_login_at"
            ],
        )
