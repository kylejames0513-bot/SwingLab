"""Dry-run-first, restartable Shopify customer backfill."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote

from ...web.users import (
    SHOPIFY_SYNC_REQUIRES_REVIEW,
    ShopifySyncFencedError,
    User,
    UserStore,
    shopify_remote_privacy_lock,
)
from . import admin
from .customer_sync import CustomerSyncResult, sync_app_user_to_shopify

_CURSOR_RE = re.compile(r"^bf1_[0-9a-f]{32}$")
_STORE_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com$"
)
_SHOP_GID_RE = re.compile(r"^gid://shopify/Shop/[1-9][0-9]*$")
_BACKFILL_BINDING_TABLE = "shopify_customer_backfill_binding"
_REQUIRED_SYNC_COLUMNS = frozenset(
    {
        "id",
        "email",
        "created_at",
        "email_verified_at",
        "shopify_customer_id",
        "shopify_identity_locked",
        "shopify_sync_status",
        "shopify_last_synced_at",
        "shopify_sync_error",
        "shopify_sync_attempts",
        "shopify_sync_next_attempt_at",
        "shopify_sync_attempt_token",
        "shopify_sync_generation",
        "shopify_sync_blocked",
    }
)


class BackfillSafetyError(RuntimeError):
    """A PII-safe preflight or continuation failure."""

    def __init__(self, safe_summary: str):
        self.safe_summary = safe_summary
        super().__init__(safe_summary)


class ShopifyStoreBindingError(BackfillSafetyError):
    """A persisted database/store identity gate failed closed."""

    def __init__(self, status: str, safe_summary: str):
        self.status = status
        super().__init__(safe_summary)


def _user_ref(user_id: str) -> str:
    """Stable operator correlation value that does not expose the local id."""

    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]


def _safe_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _database_ref(db_path: str | Path) -> str:
    resolved = Path(db_path).resolve()
    return _safe_ref(resolved.as_posix().casefold())


def _cursor_scope(client: admin.ShopifyAdminClient) -> str:
    """Bind opaque continuations to the configured store."""

    store_domain = getattr(client, "store_domain", None)
    return str(store_domain or "unscoped-shopify-backfill").strip().lower()


def _opaque_cursor(user_id: str, scope: str) -> str:
    digest = hashlib.sha256(
        f"shopify-backfill-v1\0{scope}\0{user_id}".encode("utf-8")
    ).hexdigest()
    return f"bf1_{digest[:32]}"


def _resolve_cursor(
    users: UserStore | ReadOnlyBackfillStore,
    cursor: str | None,
    *,
    scope: str,
) -> str | None:
    """Resolve an irreversible continuation without exposing a local user id.

    The lookup scans stable user-id pages. This is intentionally a little
    slower than encoding the id in the token: a token copied from operator
    output cannot be decoded into an account identifier.
    """

    if cursor is None:
        return None
    token = str(cursor).strip().lower()
    if _CURSOR_RE.fullmatch(token) is None:
        raise BackfillSafetyError(
            "The Shopify backfill continuation cursor is invalid."
        )
    raw_after: str | None = None
    while True:
        rows, next_raw = users.list_shopify_backfill(
            limit=1000,
            after=raw_after,
        )
        for user in rows:
            if _opaque_cursor(user.id, scope) == token:
                return user.id
        if next_raw is None:
            break
        raw_after = next_raw
    raise BackfillSafetyError(
        "The Shopify backfill continuation cursor does not match this "
        "database and store."
    )


@dataclass(frozen=True)
class BackfillPreflight:
    """Read-only evidence about the selected database and target store."""

    database_ref: str
    store_ref: str
    schema_ready: bool
    missing_columns: tuple[str, ...]
    binding_status: str
    shop_ref: str | None = None
    dry_run_read_only: bool = True
    bound_shop_gid: str | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("bound_shop_gid", None)
        return payload


def _readonly_connection(db_path: str | Path) -> sqlite3.Connection:
    resolved = Path(db_path).resolve()
    uri_path = quote(resolved.as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{uri_path}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


class ReadOnlyBackfillStore:
    """Minimal UserStore-compatible pager backed by SQLite ``mode=ro``."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._connection = _readonly_connection(db_path)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ReadOnlyBackfillStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @contextmanager
    def guard_shopify_sync_remote_read(
        self,
        user_id: str,
        *,
        generation: int,
    ) -> Iterator[str | None]:
        """Order a dry-run lookup against redaction without changing data."""

        # A short read-only transaction validates one consistent fence
        # snapshot. It closes before network I/O, so dry-run works even when
        # database permissions prohibit writes. Only the dedicated
        # provider-vs-redaction advisory lock remains held during the lookup.
        with shopify_remote_privacy_lock(self._db_path):
            connection = _readonly_connection(self._db_path)
            try:
                connection.execute("BEGIN")
                row = connection.execute(
                    "SELECT email, email_verified_at,"
                    " shopify_sync_generation, shopify_sync_blocked"
                    " FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                control = connection.execute(
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
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            yield email

    def list_shopify_backfill(
        self,
        limit: int = 50,
        after: str | None = None,
    ) -> tuple[list[User], str | None]:
        try:
            page_limit = int(limit)
        except (TypeError, ValueError):
            raise ValueError("limit must be a positive integer") from None
        if not 1 <= page_limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        raw_after = str(after or "").strip()
        cursor_clause = " WHERE id > ?" if raw_after else ""
        params: tuple[object, ...] = (
            ((raw_after,) if raw_after else ()) + (page_limit + 1,)
        )
        rows = self._connection.execute(
            "SELECT id, email, created_at, email_verified_at,"
            " shopify_customer_id, shopify_identity_locked,"
            " shopify_sync_status, shopify_last_synced_at,"
            " shopify_sync_error, shopify_sync_attempts,"
            " shopify_sync_next_attempt_at, shopify_sync_attempt_token,"
            " shopify_sync_generation, shopify_sync_blocked"
            f" FROM users{cursor_clause} ORDER BY id LIMIT ?",
            params,
        ).fetchall()
        has_more = len(rows) > page_limit
        page = rows[:page_limit]
        users = [
            User(
                id=str(row["id"]),
                email=str(row["email"]),
                created_at=float(row["created_at"]),
                shopify_customer_id=row["shopify_customer_id"],
                shopify_identity_locked=bool(
                    row["shopify_identity_locked"]
                ),
                email_verified_at=row["email_verified_at"],
                shopify_sync_status=str(
                    row["shopify_sync_status"] or "not_started"
                ),
                shopify_last_synced_at=row["shopify_last_synced_at"],
                shopify_sync_error=row["shopify_sync_error"],
                shopify_sync_attempts=int(
                    row["shopify_sync_attempts"] or 0
                ),
                shopify_sync_next_attempt_at=(
                    row["shopify_sync_next_attempt_at"]
                ),
                shopify_sync_attempt_token=(
                    row["shopify_sync_attempt_token"]
                ),
                shopify_sync_generation=int(
                    row["shopify_sync_generation"] or 0
                ),
                shopify_sync_blocked=bool(row["shopify_sync_blocked"]),
            )
            for row in page
        ]
        next_cursor = users[-1].id if has_more and users else None
        return users, next_cursor


def preflight_backfill_database(
    db_path: str | Path,
    store_domain: str,
) -> BackfillPreflight:
    """Inspect schema and store binding without opening SQLite for writes."""

    path = Path(db_path)
    if not path.is_file():
        raise BackfillSafetyError(
            "The Shopify backfill database was not found."
        )
    expected_store = str(store_domain or "").strip().lower()
    if _STORE_DOMAIN_RE.fullmatch(expected_store) is None:
        raise BackfillSafetyError(
            "The Shopify backfill target store domain is not configured "
            "or invalid."
        )
    try:
        connection = _readonly_connection(path)
        try:
            user_table = connection.execute(
                "SELECT 1 FROM sqlite_master"
                " WHERE type = 'table' AND name = 'users'"
            ).fetchone()
            if user_table is None:
                columns: set[str] = set()
            else:
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(users)"
                    ).fetchall()
                }
            binding_table = connection.execute(
                "SELECT 1 FROM sqlite_master"
                " WHERE type = 'table' AND name = ?",
                (_BACKFILL_BINDING_TABLE,),
            ).fetchone()
            bound_store: str | None = None
            bound_shop_gid: str | None = None
            if binding_table is not None:
                binding_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        f"PRAGMA table_info({_BACKFILL_BINDING_TABLE})"
                    ).fetchall()
                }
                select_columns = "store_domain"
                if "shop_gid" in binding_columns:
                    select_columns += ", shop_gid"
                row = connection.execute(
                    f"SELECT {select_columns}"
                    f" FROM {_BACKFILL_BINDING_TABLE} WHERE id = 1"
                ).fetchone()
                if row is not None:
                    bound_store = str(row["store_domain"]).strip().lower()
                    if "shop_gid" in binding_columns:
                        raw_shop_gid = row["shop_gid"]
                        bound_shop_gid = (
                            str(raw_shop_gid).strip()
                            if raw_shop_gid is not None
                            else None
                        )
        finally:
            connection.close()
    except sqlite3.Error:
        raise BackfillSafetyError(
            "The Shopify backfill database could not be inspected safely."
        ) from None

    missing = tuple(sorted(_REQUIRED_SYNC_COLUMNS - columns))
    if bound_store is None:
        binding_status = "unbound"
    elif bound_store != expected_store:
        binding_status = "mismatch"
    elif (
        bound_shop_gid is None
        or _SHOP_GID_RE.fullmatch(bound_shop_gid) is None
    ):
        binding_status = "incomplete"
    else:
        binding_status = "matched"
    return BackfillPreflight(
        database_ref=_database_ref(path),
        store_ref=_safe_ref(expected_store),
        schema_ready=not missing,
        missing_columns=missing,
        binding_status=binding_status,
        shop_ref=(
            _safe_ref(bound_shop_gid)
            if bound_shop_gid
            and _SHOP_GID_RE.fullmatch(bound_shop_gid) is not None
            else None
        ),
        bound_shop_gid=(
            bound_shop_gid
            if bound_shop_gid
            and _SHOP_GID_RE.fullmatch(bound_shop_gid) is not None
            else None
        ),
    )


def require_matching_shopify_store_binding(
    db_path: str | Path,
    store_domain: str,
    shop_gid: str,
) -> BackfillPreflight:
    """Require an exact persisted domain + authenticated Shop GID match."""

    canonical_shop_gid = str(shop_gid or "").strip()
    if _SHOP_GID_RE.fullmatch(canonical_shop_gid) is None:
        raise ShopifyStoreBindingError(
            "unverifiable",
            "The authenticated Shopify shop identity is invalid.",
        )
    preflight = preflight_backfill_database(db_path, store_domain)
    if preflight.binding_status in {"unbound", "incomplete"}:
        raise ShopifyStoreBindingError(
            "unbound",
            "The database is not explicitly bound to a Shopify store.",
        )
    if preflight.binding_status == "mismatch":
        raise ShopifyStoreBindingError(
            "mismatch",
            "The database is bound to a different Shopify store.",
        )
    if preflight.bound_shop_gid != canonical_shop_gid:
        raise ShopifyStoreBindingError(
            "mismatch",
            "The authenticated Shopify shop does not match the database "
            "binding.",
        )
    return preflight


def _bind_backfill_database_locked(
    db_path: str | Path,
    store_domain: str,
    shop_gid: str,
    *,
    confirmation: str | None,
) -> BackfillPreflight:
    """Persist a store identity while the privacy ordering lock is held."""

    expected_store = str(store_domain or "").strip().lower()
    canonical_shop_gid = str(shop_gid or "").strip()
    if _SHOP_GID_RE.fullmatch(canonical_shop_gid) is None:
        raise BackfillSafetyError(
            "The authenticated Shopify shop identity is invalid."
        )
    preflight = preflight_backfill_database(db_path, expected_store)
    if preflight.binding_status == "mismatch":
        raise BackfillSafetyError(
            "The selected database is bound to a different Shopify store."
        )
    if preflight.binding_status == "matched":
        return require_matching_shopify_store_binding(
            db_path,
            expected_store,
            canonical_shop_gid,
        )
    if str(confirmation or "").strip().lower() != expected_store:
        raise BackfillSafetyError(
            "An unbound database requires an exact --confirm-store value "
            "before Shopify links can be changed."
        )

    try:
        connection = sqlite3.connect(Path(db_path))
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {_BACKFILL_BINDING_TABLE} ("
                "id INTEGER PRIMARY KEY CHECK (id = 1),"
                "store_domain TEXT NOT NULL,"
                "shop_gid TEXT NOT NULL,"
                "bound_at REAL NOT NULL"
                ")"
            )
            binding_columns = {
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({_BACKFILL_BINDING_TABLE})"
                ).fetchall()
            }
            if "shop_gid" not in binding_columns:
                connection.execute(
                    f"ALTER TABLE {_BACKFILL_BINDING_TABLE}"
                    " ADD COLUMN shop_gid TEXT"
                )
            row = connection.execute(
                f"SELECT store_domain, shop_gid"
                f" FROM {_BACKFILL_BINDING_TABLE}"
                " WHERE id = 1"
            ).fetchone()
            if row is not None and str(row[0]).strip().lower() != expected_store:
                connection.rollback()
                raise BackfillSafetyError(
                    "The selected database was concurrently bound to a "
                    "different Shopify store."
                )
            if (
                row is not None
                and row[1] is not None
                and str(row[1]).strip() != canonical_shop_gid
            ):
                connection.rollback()
                raise BackfillSafetyError(
                    "The selected database is bound to a different "
                    "authenticated Shopify shop."
                )
            binding_changed = row is None or row[1] is None
            if row is None:
                connection.execute(
                    f"INSERT INTO {_BACKFILL_BINDING_TABLE}"
                    " (id, store_domain, shop_gid, bound_at)"
                    " VALUES (1, ?, ?, ?)",
                    (expected_store, canonical_shop_gid, time.time()),
                )
            elif row[1] is None:
                connection.execute(
                    f"UPDATE {_BACKFILL_BINDING_TABLE}"
                    " SET shop_gid = ?, bound_at = ? WHERE id = 1",
                    (canonical_shop_gid, time.time()),
                )
            if binding_changed:
                reopened = connection.execute(
                    "UPDATE shopify_sync_control SET"
                    " generation = generation + 1,"
                    " shop_redacted = 0, updated_at = ? WHERE id = 1",
                    (time.time(),),
                )
                if reopened.rowcount != 1:
                    connection.rollback()
                    raise BackfillSafetyError(
                        "The Shopify privacy fence schema is unavailable."
                    )
            connection.commit()
        finally:
            connection.close()
    except BackfillSafetyError:
        raise
    except sqlite3.Error:
        raise BackfillSafetyError(
            "The Shopify store binding could not be persisted."
        ) from None
    return require_matching_shopify_store_binding(
        db_path,
        expected_store,
        canonical_shop_gid,
    )


def bind_backfill_database(
    db_path: str | Path,
    store_domain: str,
    shop_gid: str,
    *,
    confirmation: str | None,
) -> BackfillPreflight:
    """Persist an already-authenticated identity in privacy-safe order."""

    with shopify_remote_privacy_lock(db_path):
        return _bind_backfill_database_locked(
            db_path,
            store_domain,
            shop_gid,
            confirmation=confirmation,
        )


def authenticate_and_bind_backfill_database(
    db_path: str | Path,
    store_domain: str,
    verify_store_access: Callable[[], str],
    *,
    confirmation: str | None,
) -> BackfillPreflight:
    """Authenticate and bind as one ordered action against shop erasure."""

    with shopify_remote_privacy_lock(db_path):
        shop_gid = verify_store_access()
        return _bind_backfill_database_locked(
            db_path,
            store_domain,
            shop_gid,
            confirmation=confirmation,
        )


def authenticate_and_require_backfill_binding(
    db_path: str | Path,
    store_domain: str,
    verify_store_access: Callable[[], str],
) -> BackfillPreflight:
    """Authenticate and require an existing binding without reopening it."""

    with shopify_remote_privacy_lock(db_path):
        shop_gid = verify_store_access()
        return require_matching_shopify_store_binding(
            db_path,
            store_domain,
            shop_gid,
        )


@dataclass(frozen=True)
class BackfillItem:
    user_ref: str
    outcome: str
    status: str
    safe_error: str | None = None


@dataclass
class BackfillSummary:
    dry_run: bool
    scanned: int = 0
    linked: int = 0
    would_link: int = 0
    would_create: int = 0
    skipped: int = 0
    requires_review: int = 0
    failed: int = 0
    next_cursor: str | None = None
    batches: int = 1
    database_ref: str | None = None
    store_ref: str | None = None
    binding_status: str | None = None
    items_truncated: int = 0
    items: list[BackfillItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # ``asdict`` preserves the public field names and converts nested
        # dataclasses, which keeps CLI JSON stable without exposing emails.
        return payload


def _persist_review(users: UserStore, user: User, message: str) -> None:
    """Close one local-only apply attempt in the review state."""

    try:
        current, attempt = users.start_shopify_sync(user.id)
    except (KeyError, ShopifySyncFencedError):
        return
    users.record_shopify_sync_failure(
        current.id,
        attempt,
        SHOPIFY_SYNC_REQUIRES_REVIEW,
        message,
    )


def _review_item(user: User, message: str) -> BackfillItem:
    return BackfillItem(
        user_ref=_user_ref(user.id),
        outcome="requires_review",
        status=SHOPIFY_SYNC_REQUIRES_REVIEW,
        safe_error=message,
    )


def _from_sync_result(user: User, result: CustomerSyncResult) -> BackfillItem:
    return BackfillItem(
        user_ref=_user_ref(user.id),
        outcome=result.action,
        status=result.status,
        safe_error=result.safe_error,
    )


def run_backfill_batch(
    users: UserStore | ReadOnlyBackfillStore,
    client: admin.ShopifyAdminClient,
    *,
    batch_size: int = 50,
    after: str | None = None,
    dry_run: bool = True,
    settings: dict[str, Any] | None = None,
) -> BackfillSummary:
    """Inspect or apply one deterministic batch.

    ``next_cursor`` can be supplied as ``after`` on the next invocation.
    Dry-run performs only ``customerByIdentifier`` Shopify reads and does not
    change user links or sync state. Constructing ``UserStore`` can still
    apply the application's normal additive schema migration before this
    function is called.
    """

    if not 1 <= int(batch_size) <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    if not dry_run and isinstance(users, ReadOnlyBackfillStore):
        raise BackfillSafetyError(
            "A read-only database connection cannot apply Shopify links."
        )
    scope = _cursor_scope(client)
    raw_after = _resolve_cursor(users, after, scope=scope)
    rows, next_cursor = users.list_shopify_backfill(
        limit=int(batch_size), after=raw_after
    )
    summary = BackfillSummary(
        dry_run=bool(dry_run),
        scanned=len(rows),
        next_cursor=(
            _opaque_cursor(next_cursor, scope) if next_cursor else None
        ),
    )

    for user in rows:
        if user.shopify_sync_blocked:
            summary.requires_review += 1
            summary.items.append(
                _review_item(
                    user,
                    "Shopify synchronization is blocked after redaction.",
                )
            )
            continue
        if user.shopify_customer_id:
            summary.skipped += 1
            summary.items.append(
                BackfillItem(
                    user_ref=_user_ref(user.id),
                    outcome="already_linked",
                    status=user.shopify_sync_status,
                )
            )
            continue
        if user.shopify_identity_locked:
            summary.requires_review += 1
            message = "A previous Shopify identity requires manual review."
            if not dry_run:
                _persist_review(users, user, message)
            summary.items.append(
                _review_item(
                    user,
                    message,
                )
            )
            continue
        if not user.email_verified:
            summary.requires_review += 1
            message = (
                "Verified email is required before Shopify linking."
            )
            if not dry_run:
                _persist_review(users, user, message)
            summary.items.append(
                _review_item(
                    user,
                    message,
                )
            )
            continue

        if not dry_run:
            users.mark_shopify_sync_pending(user.id)
            result = sync_app_user_to_shopify(
                users,
                user.id,
                client,
                settings or {},
                expected_generation=user.shopify_sync_generation,
            )
            summary.items.append(_from_sync_result(user, result))
            if result.status == "synced":
                summary.linked += 1
            elif result.status == SHOPIFY_SYNC_REQUIRES_REVIEW:
                summary.requires_review += 1
            else:
                summary.failed += 1
            continue

        lookup_allowed = False
        try:
            with users.guard_shopify_sync_remote_read(
                user.id,
                generation=user.shopify_sync_generation,
            ) as current_email:
                lookup_allowed = current_email is not None
                existing = (
                    client.find_customer_by_email(current_email)
                    if current_email is not None
                    else None
                )
        except admin.ShopifyAdminUserError as exc:
            summary.requires_review += 1
            summary.items.append(
                _review_item(
                    user,
                    str(
                        getattr(
                            exc,
                            "safe_summary",
                            "Shopify rejected the customer lookup.",
                        )
                    )[:500],
                )
            )
        except admin.ShopifyAdminError as exc:
            summary.failed += 1
            summary.items.append(
                BackfillItem(
                    user_ref=_user_ref(user.id),
                    outcome="lookup_failed",
                    status="failed",
                    safe_error=str(
                        getattr(
                            exc,
                            "safe_summary",
                            "Shopify customer lookup failed.",
                        )
                    )[:500],
                )
            )
        else:
            if not lookup_allowed:
                summary.requires_review += 1
                summary.items.append(
                    _review_item(
                        user,
                        "Shopify synchronization was privacy-fenced.",
                    )
                )
                continue
            if existing:
                summary.would_link += 1
                outcome = "would_link_existing"
            else:
                summary.would_create += 1
                outcome = "would_create"
            summary.items.append(
                BackfillItem(
                    user_ref=_user_ref(user.id),
                    outcome=outcome,
                    status="dry_run",
                )
            )
    return summary


def run_backfill_all(
    users: UserStore | ReadOnlyBackfillStore,
    client: admin.ShopifyAdminClient,
    *,
    batch_size: int = 50,
    after: str | None = None,
    dry_run: bool = True,
    settings: dict[str, Any] | None = None,
    max_batches: int = 10_000,
    max_items: int = 1_000,
) -> BackfillSummary:
    """Run every remaining page and return one bounded cumulative summary."""

    if not 1 <= int(max_batches) <= 100_000:
        raise ValueError("max_batches must be between 1 and 100000")
    if not 0 <= int(max_items) <= 100_000:
        raise ValueError("max_items must be between 0 and 100000")

    cumulative = BackfillSummary(
        dry_run=bool(dry_run),
        batches=0,
        next_cursor=after,
    )
    seen: set[str] = set()
    cursor = after
    for _ in range(int(max_batches)):
        batch = run_backfill_batch(
            users,
            client,
            batch_size=batch_size,
            after=cursor,
            dry_run=dry_run,
            settings=settings,
        )
        cumulative.batches += 1
        for field_name in (
            "scanned",
            "linked",
            "would_link",
            "would_create",
            "skipped",
            "requires_review",
            "failed",
        ):
            setattr(
                cumulative,
                field_name,
                getattr(cumulative, field_name) + getattr(batch, field_name),
            )
        available = max(0, int(max_items) - len(cumulative.items))
        cumulative.items.extend(batch.items[:available])
        cumulative.items_truncated += max(
            0, len(batch.items) - available
        )
        cumulative.next_cursor = batch.next_cursor
        if batch.next_cursor is None:
            return cumulative
        if batch.next_cursor in seen:
            raise BackfillSafetyError(
                "The Shopify backfill continuation did not advance."
            )
        seen.add(batch.next_cursor)
        cursor = batch.next_cursor
    raise BackfillSafetyError(
        "The Shopify backfill exceeded its maximum batch safety limit."
    )
