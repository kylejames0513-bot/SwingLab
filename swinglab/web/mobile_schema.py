"""Generation-1 native credential schema and protected state HMACs.

This module deliberately owns the closed HMAC domain vocabulary.  Callers
cannot ask for an arbitrary domain string and accidentally make unrelated
secrets comparable in SQLite, logs, backups, or the recovery ledger.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


MOBILE_STATE_KEYRING_ENV = "MOBILE_STATE_HMAC_KEYRING"
MOBILE_STATE_SCHEMA_GENERATION = 1


class MobileStateDomain(str, Enum):
    INSTALLATION_ID = "installation-id"
    AUTH_START_CLIENT_IP = "auth-start-client-ip"
    AUTH_START_NORMALIZED_EMAIL_RATE = "auth-start-normalized-email-rate"
    AUTH_EXCHANGE_CLIENT_IP = "auth-exchange-client-ip"
    AUTH_EXCHANGE_NORMALIZED_EMAIL_RATE = "auth-exchange-normalized-email-rate"
    EMAIL_CODE_VERIFIER = "email-code-verifier"
    AUTH_EXCHANGE_CODE_PROOF = "auth-exchange-code-proof"
    AUTH_EXCHANGE_PKCE_VERIFIER_PROOF = "auth-exchange-pkce-verifier-proof"
    REVIEW_AUTH_ACCOUNT = "review-auth-account"
    REVIEW_AUTH_CLIENT_IP = "review-auth-client-ip"
    REVIEW_AUTH_PASSWORD_PROOF = "review-auth-password-proof"
    REVIEW_AUTH_PKCE_VERIFIER_PROOF = "review-auth-pkce-verifier-proof"
    REVIEW_AUTH_IDEMPOTENCY = "review-auth-idempotency"
    EXCHANGE_IDEMPOTENCY = "exchange-idempotency"
    SIGN_OUT_IDEMPOTENCY = "sign-out-idempotency"
    DEVICE_REVOKE_IDEMPOTENCY = "device-revoke-idempotency"
    PRACTICE_IDEMPOTENCY = "practice-idempotency"
    UPLOAD_IDEMPOTENCY = "upload-idempotency"
    UPLOAD_ABORT_IDEMPOTENCY = "upload-abort-idempotency"
    ANALYSIS_RETRY_IDEMPOTENCY = "analysis-retry-idempotency"
    ANALYSIS_SOURCE_DISCARD_IDEMPOTENCY = "analysis-source-discard-idempotency"
    EXPORT_IDEMPOTENCY = "export-idempotency"
    HISTORY_RESET_IDEMPOTENCY = "history-reset-idempotency"
    ACCOUNT_DELETE_IDEMPOTENCY = "account-delete-idempotency"
    EVENT_IDEMPOTENCY = "event-idempotency"
    RECOVERY_SELECTOR = "recovery-selector"
    RECOVERY_TOKEN_VERIFIER = "recovery-token-verifier"
    ERASURE_STABLE_USER_ID = "erasure-stable-user-id"
    ERASURE_NORMALIZED_EMAIL = "erasure-normalized-email"
    SHOPIFY_ERASURE_SHOP_DOMAIN = "shopify-erasure-shop-domain"
    SHOPIFY_ERASURE_CUSTOMER_ID = "shopify-erasure-customer-id"
    SHOPIFY_ERASURE_NORMALIZED_EMAIL = "shopify-erasure-normalized-email"
    RECOVERY_CHAIN_LINK = "recovery-chain-link"


@dataclass(frozen=True)
class HMACDigest:
    key_id: str
    digest: str


class VersionedHMAC:
    """A strict current+retained keyring with fixed domain separation."""

    _KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

    def __init__(self, current_key_id: str, keys: dict[str, bytes]):
        if not self._KEY_ID.fullmatch(current_key_id) or current_key_id not in keys:
            raise ValueError("The current mobile-state HMAC key is unavailable.")
        if not keys or any(
            not self._KEY_ID.fullmatch(key_id)
            or not isinstance(secret, bytes)
            or len(secret) != 32
            for key_id, secret in keys.items()
        ):
            raise ValueError("Every mobile-state HMAC key must be a named 32-byte key.")
        self._current_key_id = current_key_id
        self._keys = dict(keys)

    @property
    def current_key_id(self) -> str:
        return self._current_key_id

    @property
    def key_ids(self) -> frozenset[str]:
        return frozenset(self._keys)

    @classmethod
    def from_json(cls, value: str) -> "VersionedHMAC":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("MOBILE_STATE_HMAC_KEYRING must be valid JSON.") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "version",
            "current_key_id",
            "keys",
        }:
            raise ValueError("MOBILE_STATE_HMAC_KEYRING has an unsupported shape.")
        if payload["version"] != 1 or not isinstance(payload["keys"], dict):
            raise ValueError("MOBILE_STATE_HMAC_KEYRING version 1 is required.")
        decoded: dict[str, bytes] = {}
        for key_id, encoded in payload["keys"].items():
            if not isinstance(key_id, str) or not isinstance(encoded, str):
                raise ValueError("Mobile-state HMAC keys must be named base64 strings.")
            try:
                secret = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("A mobile-state HMAC key is not valid base64.") from exc
            decoded[key_id] = secret
        current = payload["current_key_id"]
        if not isinstance(current, str):
            raise ValueError("The current mobile-state HMAC key ID is invalid.")
        return cls(current, decoded)

    @classmethod
    def from_env(cls, *, required: bool) -> "VersionedHMAC | None":
        raw = os.environ.get(MOBILE_STATE_KEYRING_ENV, "")
        if not raw.strip():
            if required:
                raise RuntimeError(f"{MOBILE_STATE_KEYRING_ENV} is required.")
            return None
        try:
            return cls.from_json(raw)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    @staticmethod
    def _message(domain: MobileStateDomain, raw: bytes) -> bytes:
        if not isinstance(domain, MobileStateDomain):
            raise ValueError("A closed mobile-state HMAC domain is required.")
        return f"caddieinsight.mobile.v1/{domain.value}\0".encode("ascii") + raw

    @staticmethod
    def _raw_bytes(raw: bytes | str) -> bytes:
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            return raw.encode("utf-8")
        raise TypeError("HMAC input must be bytes or text.")

    def digest(
        self, domain: MobileStateDomain, raw: bytes | str
    ) -> tuple[str, str]:
        material = self._message(domain, self._raw_bytes(raw))
        return self._current_key_id, hmac.new(
            self._keys[self._current_key_id], material, hashlib.sha256
        ).hexdigest()

    def digest_with_key(
        self, key_id: str, domain: MobileStateDomain, raw: bytes | str
    ) -> str:
        try:
            key = self._keys[key_id]
        except KeyError as exc:
            raise KeyError(f"Missing mobile-state HMAC key ID {key_id!r}.") from exc
        return hmac.new(
            key,
            self._message(domain, self._raw_bytes(raw)),
            hashlib.sha256,
        ).hexdigest()

    def candidates(
        self, domain: MobileStateDomain, raw: bytes | str
    ) -> tuple[HMACDigest, ...]:
        ordered = [
            self._current_key_id,
            *(key_id for key_id in sorted(self._keys) if key_id != self._current_key_id),
        ]
        return tuple(
            HMACDigest(key_id, self.digest_with_key(key_id, domain, raw))
            for key_id in ordered
        )

    def require_key_ids(self, key_ids: Iterable[str]) -> None:
        missing = sorted(set(key_ids) - self.key_ids)
        if missing:
            raise RuntimeError(
                "MOBILE_STATE_HMAC_KEYRING is missing referenced key ID(s): "
                + ", ".join(missing)
            )


MOBILE_RATE_LIMIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS mobile_rate_limit_events (
    domain       TEXT NOT NULL,
    key_id       TEXT NOT NULL,
    key_digest   TEXT NOT NULL,
    occurred_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mobile_rate_limit_events_lookup
    ON mobile_rate_limit_events(domain, key_id, key_digest, occurred_at);
CREATE INDEX IF NOT EXISTS mobile_rate_limit_events_purge
    ON mobile_rate_limit_events(occurred_at);
"""


_MOBILE_RATE_LIMIT_REQUIRED_COLUMNS = (
    "domain",
    "key_id",
    "key_digest",
    "occurred_at",
)
_MOBILE_RATE_LIMIT_REQUIRED_INDEXES = {
    "mobile_rate_limit_events_lookup": (
        "domain",
        "key_id",
        "key_digest",
        "occurred_at",
    ),
    "mobile_rate_limit_events_purge": ("occurred_at",),
}


def _index_columns(connection: sqlite3.Connection, index: str) -> tuple[str, ...]:
    return tuple(
        str(row[2])
        for row in connection.execute(f"PRAGMA index_info({index})")
    )


def _validate_index_shape(
    connection: sqlite3.Connection,
    index: str,
    table: str,
    expected_columns: tuple[str, ...],
) -> None:
    owner = connection.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index,),
    ).fetchone()
    metadata = next(
        (
            row
            for row in connection.execute(f"PRAGMA index_list({table})")
            if str(row[1]) == index
        ),
        None,
    )
    actual_columns = _index_columns(connection, index)
    if (
        owner is None
        or str(owner[0]) != table
        or metadata is None
        or bool(metadata[2])
        or bool(metadata[4])
        or actual_columns != expected_columns
    ):
        raise RuntimeError(
            f"Incompatible generation-1 mobile index {index}; expected "
            f"{expected_columns!r}, found {actual_columns!r}."
        )


def validate_mobile_rate_limit_schema(connection: sqlite3.Connection) -> None:
    actual_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(mobile_rate_limit_events)")
    }
    missing = sorted(set(_MOBILE_RATE_LIMIT_REQUIRED_COLUMNS) - actual_columns)
    if missing:
        raise RuntimeError(
            "Incompatible generation-1 mobile schema for mobile_rate_limit_events; "
            f"missing column(s): {', '.join(missing)}."
        )
    for index, expected_columns in _MOBILE_RATE_LIMIT_REQUIRED_INDEXES.items():
        _validate_index_shape(
            connection,
            index,
            "mobile_rate_limit_events",
            expected_columns,
        )


def ensure_mobile_rate_limit_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(MOBILE_RATE_LIMIT_SCHEMA)
    validate_mobile_rate_limit_schema(connection)


_MOBILE_TOKEN_BASE_COLUMNS = {
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

_MOBILE_TOKEN_BASE_DDL = """
CREATE TABLE mobile_api_tokens (
    selector TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    user_id TEXT NOT NULL,
    auth_epoch INTEGER NOT NULL,
    label TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL,
    expires_at REAL NOT NULL,
    revoked_at REAL
)
"""

_MOBILE_TOKEN_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("installation_key", "installation_key TEXT"),
    ("installation_key_version", "installation_key_version TEXT"),
    (
        "state",
        "state TEXT NOT NULL DEFAULT 'active'"
        " CHECK (state IN ('inactive', 'active', 'fenced'))",
    ),
    ("fenced_at", "fenced_at REAL"),
    (
        "review_provider",
        "review_provider TEXT CHECK (review_provider IN ('apple', 'google'))",
    ),
    ("review_build", "review_build TEXT"),
    ("review_expires_at", "review_expires_at REAL"),
    ("review_credential_hmac_key_id", "review_credential_hmac_key_id TEXT"),
    ("review_credential_hmac", "review_credential_hmac TEXT"),
    ("review_lane_revision", "review_lane_revision INTEGER"),
)

_GENERATION_ONE_TABLE_DDL: dict[str, str] = {
    "mobile_rate_limit_events": """
        CREATE TABLE IF NOT EXISTS mobile_rate_limit_events (
            domain TEXT NOT NULL,
            key_id TEXT NOT NULL,
            key_digest TEXT NOT NULL,
            occurred_at REAL NOT NULL
        )
    """,
    "mobile_auth_challenges": """
        CREATE TABLE IF NOT EXISTS mobile_auth_challenges (
            challenge_id TEXT PRIMARY KEY,
            purpose TEXT NOT NULL CHECK (purpose = 'signin'),
            normalized_email TEXT NOT NULL,
            code_hmac_key_id TEXT NOT NULL,
            code_hmac TEXT NOT NULL,
            code_challenge TEXT NOT NULL,
            installation_hmac_key_id TEXT NOT NULL,
            installation_hmac TEXT NOT NULL,
            start_ip_hmac_key_id TEXT NOT NULL,
            start_ip_hmac TEXT NOT NULL,
            device_label TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            last_sent_at REAL NOT NULL,
            consumed_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
            issued_selector TEXT,
            exchange_idempotency_hmac_key_id TEXT,
            exchange_idempotency_hmac TEXT,
            canonical_request_hash TEXT
        )
    """,
    "mobile_review_auth_challenges": """
        CREATE TABLE IF NOT EXISTS mobile_review_auth_challenges (
            challenge_id TEXT PRIMARY KEY,
            purpose TEXT NOT NULL CHECK (purpose = 'review_signin'),
            provider TEXT NOT NULL CHECK (provider IN ('apple', 'google')),
            deployment_environment TEXT NOT NULL,
            platform TEXT NOT NULL CHECK (platform IN ('ios', 'android')),
            app_version TEXT NOT NULL,
            app_build TEXT NOT NULL,
            application_id TEXT NOT NULL,
            matched_user_id TEXT,
            account_hmac_key_id TEXT NOT NULL,
            account_hmac TEXT NOT NULL,
            start_ip_hmac_key_id TEXT NOT NULL,
            start_ip_hmac TEXT NOT NULL,
            password_proof_hmac_key_id TEXT,
            password_proof_hmac TEXT,
            pkce_verifier_proof_hmac_key_id TEXT,
            pkce_verifier_proof_hmac TEXT,
            idempotency_hmac_key_id TEXT,
            idempotency_hmac TEXT,
            code_challenge TEXT NOT NULL,
            installation_hmac_key_id TEXT NOT NULL,
            installation_hmac TEXT NOT NULL,
            device_label TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            consumed_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5)
        )
    """,
    "mobile_auth_exchange_journals": """
        CREATE TABLE IF NOT EXISTS mobile_auth_exchange_journals (
            exchange_id TEXT PRIMARY KEY,
            purpose TEXT NOT NULL CHECK (purpose IN ('email', 'review')),
            challenge_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            auth_epoch INTEGER NOT NULL,
            phase TEXT NOT NULL CHECK (phase IN (
                'prepared', 'prior_recovery_fenced', 'replacement_active', 'complete'
            )),
            installation_hmac_key_id TEXT NOT NULL,
            installation_hmac TEXT NOT NULL,
            prior_selector TEXT,
            replacement_selector TEXT NOT NULL,
            token_verifier_hmac_key_id TEXT NOT NULL,
            token_verifier_hmac TEXT NOT NULL,
            code_proof_hmac_key_id TEXT,
            code_proof_hmac TEXT,
            pkce_verifier_proof_hmac_key_id TEXT NOT NULL,
            pkce_verifier_proof_hmac TEXT NOT NULL,
            idempotency_hmac_key_id TEXT NOT NULL,
            idempotency_hmac TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            review_provider TEXT,
            review_build TEXT,
            review_expires_at REAL,
            review_credential_hmac_key_id TEXT,
            review_credential_hmac TEXT,
            recovery_sequence INTEGER,
            recovery_record_hash TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            UNIQUE(purpose, challenge_id)
        )
    """,
    "mobile_auth_exchange_receipts": """
        CREATE TABLE IF NOT EXISTS mobile_auth_exchange_receipts (
            exchange_id TEXT PRIMARY KEY,
            purpose TEXT NOT NULL CHECK (purpose IN ('email', 'review')),
            challenge_id TEXT NOT NULL,
            replacement_selector TEXT NOT NULL,
            idempotency_hmac_key_id TEXT NOT NULL,
            idempotency_hmac TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            completed_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """,
    "mobile_signout_journals": """
        CREATE TABLE IF NOT EXISTS mobile_signout_journals (
            operation_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            phase TEXT NOT NULL CHECK (phase IN (
                'prepared', 'recovery_fenced', 'extensions_closed',
                'token_revoked', 'complete'
            )),
            selector_hmac_key_id TEXT NOT NULL,
            selector_hmac TEXT NOT NULL,
            token_verifier_hmac_key_id TEXT NOT NULL,
            token_verifier_hmac TEXT NOT NULL,
            idempotency_hmac_key_id TEXT NOT NULL,
            idempotency_hmac TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            recovery_sequence INTEGER,
            recovery_record_hash TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """,
    "mobile_signout_receipts": """
        CREATE TABLE IF NOT EXISTS mobile_signout_receipts (
            operation_id TEXT PRIMARY KEY,
            selector_hmac_key_id TEXT NOT NULL,
            selector_hmac TEXT NOT NULL,
            token_verifier_hmac_key_id TEXT NOT NULL,
            token_verifier_hmac TEXT NOT NULL,
            idempotency_hmac_key_id TEXT NOT NULL,
            idempotency_hmac TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            completed_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """,
    "mobile_device_revoke_journals": """
        CREATE TABLE IF NOT EXISTS mobile_device_revoke_journals (
            operation_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            initiator_selector TEXT,
            target_selector TEXT NOT NULL,
            phase TEXT NOT NULL CHECK (phase IN (
                'prepared', 'recovery_fenced', 'extensions_closed',
                'token_revoked', 'complete'
            )),
            target_selector_hmac_key_id TEXT NOT NULL,
            target_selector_hmac TEXT NOT NULL,
            target_token_verifier_hmac_key_id TEXT NOT NULL,
            target_token_verifier_hmac TEXT NOT NULL,
            idempotency_hmac_key_id TEXT NOT NULL,
            idempotency_hmac TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            recovery_sequence INTEGER,
            recovery_record_hash TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """,
    "mobile_device_revoke_receipts": """
        CREATE TABLE IF NOT EXISTS mobile_device_revoke_receipts (
            operation_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            target_selector_hmac_key_id TEXT NOT NULL,
            target_selector_hmac TEXT NOT NULL,
            idempotency_hmac_key_id TEXT NOT NULL,
            idempotency_hmac TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            completed_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """,
    "mobile_recovery_fence_checkpoints": """
        CREATE TABLE IF NOT EXISTS mobile_recovery_fence_checkpoints (
            checkpoint_id INTEGER PRIMARY KEY CHECK (checkpoint_id = 1),
            lineage_id TEXT NOT NULL,
            baseline_backup_id TEXT NOT NULL,
            schema_generation INTEGER NOT NULL,
            head_sequence INTEGER NOT NULL,
            head_record_key TEXT NOT NULL,
            head_record_hash TEXT NOT NULL,
            head_etag TEXT NOT NULL,
            chain_hmac_key_id TEXT NOT NULL,
            verified_at REAL NOT NULL
        )
    """,
    "mobile_recovery_baseline_journals": """
        CREATE TABLE IF NOT EXISTS mobile_recovery_baseline_journals (
            operation_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL CHECK (phase IN (
                'lineage_prepared', 'backup_verified', 'record_published',
                'head_published', 'scratch_verified', 'accepted'
            )),
            request_hash TEXT NOT NULL,
            lineage_id TEXT NOT NULL,
            backup_id TEXT,
            backup_created_at REAL,
            schema_generation INTEGER,
            manifest_sha256 TEXT,
            baseline_db_checkpoint TEXT,
            record_key TEXT,
            record_hash TEXT,
            head_etag TEXT,
            chain_hmac_key_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """,
    "mobile_recovery_accepted_baselines": """
        CREATE TABLE IF NOT EXISTS mobile_recovery_accepted_baselines (
            lineage_id TEXT PRIMARY KEY,
            baseline_backup_id TEXT NOT NULL UNIQUE,
            minimum_backup_created_at REAL NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            schema_generation INTEGER NOT NULL,
            baseline_db_checkpoint TEXT NOT NULL,
            accepted_at REAL NOT NULL
        )
    """,
    "mobile_restore_credential_reset_markers": """
        CREATE TABLE IF NOT EXISTS mobile_restore_credential_reset_markers (
            marker_id TEXT PRIMARY KEY,
            source_backup_id TEXT NOT NULL,
            source_lineage_id TEXT,
            prepared_at REAL NOT NULL
        )
    """,
}

_GENERATION_ONE_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "mobile_api_tokens": tuple(
        sorted(
            _MOBILE_TOKEN_BASE_COLUMNS
            | {name for name, _ddl in _MOBILE_TOKEN_ADDITIVE_COLUMNS}
        )
    ),
    "mobile_auth_challenges": (
        "challenge_id", "purpose", "normalized_email", "code_hmac_key_id",
        "code_hmac", "code_challenge", "installation_hmac_key_id",
        "installation_hmac", "start_ip_hmac_key_id", "start_ip_hmac",
        "device_label", "created_at", "expires_at", "last_sent_at",
        "consumed_at", "attempts", "issued_selector",
        "exchange_idempotency_hmac_key_id", "exchange_idempotency_hmac",
        "canonical_request_hash",
    ),
    "mobile_review_auth_challenges": (
        "challenge_id", "purpose", "provider", "deployment_environment",
        "platform", "app_version", "app_build", "application_id",
        "matched_user_id", "account_hmac_key_id", "account_hmac",
        "start_ip_hmac_key_id", "start_ip_hmac",
        "password_proof_hmac_key_id", "password_proof_hmac",
        "pkce_verifier_proof_hmac_key_id", "pkce_verifier_proof_hmac",
        "idempotency_hmac_key_id", "idempotency_hmac", "code_challenge",
        "installation_hmac_key_id", "installation_hmac", "device_label",
        "created_at", "expires_at", "consumed_at", "attempts",
    ),
    "mobile_auth_exchange_journals": (
        "exchange_id", "purpose", "challenge_id", "user_id", "auth_epoch",
        "phase", "installation_hmac_key_id", "installation_hmac",
        "prior_selector", "replacement_selector", "token_verifier_hmac_key_id",
        "token_verifier_hmac", "code_proof_hmac_key_id", "code_proof_hmac",
        "pkce_verifier_proof_hmac_key_id", "pkce_verifier_proof_hmac",
        "idempotency_hmac_key_id", "idempotency_hmac", "request_hash",
        "review_provider", "review_build", "review_expires_at",
        "review_credential_hmac_key_id", "review_credential_hmac",
        "recovery_sequence", "recovery_record_hash", "created_at", "updated_at",
        "expires_at",
    ),
    "mobile_auth_exchange_receipts": (
        "exchange_id", "purpose", "challenge_id", "replacement_selector",
        "idempotency_hmac_key_id", "idempotency_hmac", "request_hash",
        "completed_at", "expires_at",
    ),
    "mobile_signout_journals": (
        "operation_id", "user_id", "phase",
        "selector_hmac_key_id", "selector_hmac", "token_verifier_hmac_key_id",
        "token_verifier_hmac", "idempotency_hmac_key_id", "idempotency_hmac",
        "request_hash", "recovery_sequence", "recovery_record_hash",
        "created_at", "updated_at", "expires_at",
    ),
    "mobile_signout_receipts": (
        "operation_id", "selector_hmac_key_id", "selector_hmac",
        "token_verifier_hmac_key_id", "token_verifier_hmac",
        "idempotency_hmac_key_id", "idempotency_hmac", "request_hash",
        "completed_at", "expires_at",
    ),
    "mobile_device_revoke_journals": (
        "operation_id", "owner_user_id", "initiator_selector", "target_selector",
        "phase", "target_selector_hmac_key_id", "target_selector_hmac",
        "target_token_verifier_hmac_key_id", "target_token_verifier_hmac",
        "idempotency_hmac_key_id", "idempotency_hmac", "request_hash",
        "recovery_sequence", "recovery_record_hash", "created_at", "updated_at",
        "expires_at",
    ),
    "mobile_device_revoke_receipts": (
        "operation_id", "owner_user_id", "target_selector_hmac_key_id",
        "target_selector_hmac", "idempotency_hmac_key_id", "idempotency_hmac",
        "request_hash", "completed_at", "expires_at",
    ),
    "mobile_recovery_fence_checkpoints": (
        "checkpoint_id", "lineage_id", "baseline_backup_id", "schema_generation",
        "head_sequence", "head_record_key", "head_record_hash", "head_etag",
        "chain_hmac_key_id", "verified_at",
    ),
    "mobile_recovery_baseline_journals": (
        "operation_id", "phase", "request_hash", "lineage_id", "backup_id",
        "backup_created_at", "schema_generation", "manifest_sha256",
        "baseline_db_checkpoint", "record_key", "record_hash", "head_etag",
        "chain_hmac_key_id", "created_at", "updated_at",
    ),
    "mobile_recovery_accepted_baselines": (
        "lineage_id", "baseline_backup_id", "minimum_backup_created_at",
        "manifest_sha256", "schema_generation", "baseline_db_checkpoint",
        "accepted_at",
    ),
    "mobile_restore_credential_reset_markers": (
        "marker_id", "source_backup_id", "source_lineage_id", "prepared_at",
    ),
    "mobile_rate_limit_events": (
        "domain", "key_id", "key_digest", "occurred_at",
    ),
}

_GENERATION_ONE_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "mobile_api_tokens_user_installation_active": (
        "mobile_api_tokens",
        (
            "user_id", "installation_key_version", "installation_key", "state",
            "expires_at", "revoked_at",
        ),
    ),
    "mobile_api_tokens_review_active": (
        "mobile_api_tokens",
        ("review_provider", "review_build", "review_expires_at", "state"),
    ),
    "mobile_auth_challenges_active_ip": (
        "mobile_auth_challenges",
        ("start_ip_hmac_key_id", "start_ip_hmac", "expires_at", "consumed_at"),
    ),
    "mobile_auth_challenges_active_email": (
        "mobile_auth_challenges",
        ("normalized_email", "expires_at", "consumed_at"),
    ),
    "mobile_review_auth_challenges_active_ip": (
        "mobile_review_auth_challenges",
        ("start_ip_hmac_key_id", "start_ip_hmac", "expires_at", "consumed_at"),
    ),
    "mobile_review_auth_challenges_active_account": (
        "mobile_review_auth_challenges",
        ("account_hmac_key_id", "account_hmac", "expires_at", "consumed_at"),
    ),
    "mobile_auth_exchange_journals_phase": (
        "mobile_auth_exchange_journals", ("phase", "updated_at"),
    ),
    "mobile_auth_exchange_receipts_expiry": (
        "mobile_auth_exchange_receipts", ("expires_at",),
    ),
    "mobile_signout_journals_phase": (
        "mobile_signout_journals", ("phase", "updated_at"),
    ),
    "mobile_signout_journals_replay": (
        "mobile_signout_journals",
        ("idempotency_hmac_key_id", "idempotency_hmac", "expires_at"),
    ),
    "mobile_signout_receipts_replay": (
        "mobile_signout_receipts",
        ("idempotency_hmac_key_id", "idempotency_hmac", "expires_at"),
    ),
    "mobile_device_revoke_journals_phase": (
        "mobile_device_revoke_journals", ("phase", "updated_at"),
    ),
    "mobile_device_revoke_receipts_expiry": (
        "mobile_device_revoke_receipts", ("expires_at",),
    ),
    "mobile_recovery_baseline_journals_phase": (
        "mobile_recovery_baseline_journals", ("phase", "updated_at"),
    ),
    "mobile_rate_limit_events_lookup": (
        "mobile_rate_limit_events", ("domain", "key_id", "key_digest", "occurred_at"),
    ),
    "mobile_rate_limit_events_purge": (
        "mobile_rate_limit_events", ("occurred_at",),
    ),
}

_MOBILE_TOKEN_BASE_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "mobile_api_tokens_user_active": (
        "mobile_api_tokens",
        ("user_id", "auth_epoch", "expires_at", "revoked_at"),
    ),
}

_INDEX_DDL = {
    name: f"CREATE INDEX IF NOT EXISTS {name} ON {table}({', '.join(columns)})"
    for name, (table, columns) in _GENERATION_ONE_INDEXES.items()
}


@dataclass(frozen=True)
class _TableShape:
    columns: tuple[tuple[str, str, bool, str | None, int, int], ...]
    checks: tuple[str, ...]
    unique_indexes: tuple[tuple[str, tuple[str, ...]], ...]


def _sql_without_comments(sql: str) -> str:
    """Replace SQLite comments with whitespace without touching quoted text."""

    result: list[str] = []
    quote_end: str | None = None
    index = 0
    while index < len(sql):
        character = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if quote_end is not None:
            result.append(character)
            if character == quote_end:
                if following == quote_end:
                    result.append(following)
                    index += 1
                else:
                    quote_end = None
        elif character in ("'", '"', "`"):
            quote_end = character
            result.append(character)
        elif character == "[":
            quote_end = "]"
            result.append(character)
        elif character == "-" and following == "-":
            result.append(" ")
            index += 2
            while index < len(sql) and sql[index] not in "\r\n":
                index += 1
            if index < len(sql):
                result.append(sql[index])
        elif character == "/" and following == "*":
            result.append(" ")
            index += 2
            while index < len(sql):
                if (
                    sql[index] == "*"
                    and index + 1 < len(sql)
                    and sql[index + 1] == "/"
                ):
                    index += 1
                    break
                if sql[index] in "\r\n":
                    result.append(sql[index])
                index += 1
        else:
            result.append(character)
        index += 1
    return "".join(result)


def _check_constraints(sql: str) -> tuple[str, ...]:
    """Extract normalized CHECK bodies while respecting nested parentheses."""

    sql = _sql_without_comments(sql)
    constraints: list[str] = []
    search_from = 0
    upper_sql = sql.upper()
    while True:
        check_at = upper_sql.find("CHECK", search_from)
        if check_at < 0:
            break
        opening = check_at + len("CHECK")
        while opening < len(sql) and sql[opening].isspace():
            opening += 1
        if opening >= len(sql) or sql[opening] != "(":
            search_from = opening
            continue
        depth = 0
        quote: str | None = None
        index = opening
        while index < len(sql):
            character = sql[index]
            if quote is not None:
                if character == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 1
                    else:
                        quote = None
            elif character in ("'", '"'):
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    constraints.append(
                        re.sub(r"\s+", "", sql[opening + 1:index])
                    )
                    search_from = index + 1
                    break
            index += 1
        else:
            raise RuntimeError("Malformed generation-1 mobile CHECK constraint.")
    return tuple(sorted(constraints))


def _table_shape(connection: sqlite3.Connection, table: str) -> _TableShape:
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    sql = str(sql_row[0]) if sql_row is not None and sql_row[0] is not None else ""
    unique_indexes = tuple(
        sorted(
            (
                str(row[3]),
                _index_columns(connection, str(row[1])),
            )
            for row in connection.execute(f"PRAGMA index_list({table})")
            if bool(row[2]) and str(row[3]) != "pk"
        )
    )
    return _TableShape(
        columns=tuple(
            (
                str(row[1]),
                str(row[2]).upper(),
                bool(row[3]),
                str(row[4]) if row[4] is not None else None,
                int(row[5]),
                int(row[6]),
            )
            for row in connection.execute(f"PRAGMA table_xinfo({table})")
        ),
        checks=_check_constraints(sql),
        unique_indexes=unique_indexes,
    )


def _canonical_generation_one_table_shapes() -> dict[str, _TableShape]:
    reference = sqlite3.connect(":memory:")
    try:
        reference.execute(_MOBILE_TOKEN_BASE_DDL)
        for _name, ddl in _MOBILE_TOKEN_ADDITIVE_COLUMNS:
            reference.execute(f"ALTER TABLE mobile_api_tokens ADD COLUMN {ddl}")
        for ddl in _GENERATION_ONE_TABLE_DDL.values():
            reference.execute(ddl)
        return {
            table: _table_shape(reference, table)
            for table in _GENERATION_ONE_REQUIRED_COLUMNS
        }
    finally:
        reference.close()


_GENERATION_ONE_TABLE_SHAPES = _canonical_generation_one_table_shapes()


_HMAC_COLUMN_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("mobile_api_tokens", "installation_key_version", "installation_key"),
    (
        "mobile_api_tokens",
        "review_credential_hmac_key_id",
        "review_credential_hmac",
    ),
    ("mobile_auth_challenges", "code_hmac_key_id", "code_hmac"),
    (
        "mobile_auth_challenges",
        "installation_hmac_key_id",
        "installation_hmac",
    ),
    ("mobile_auth_challenges", "start_ip_hmac_key_id", "start_ip_hmac"),
    (
        "mobile_auth_challenges",
        "exchange_idempotency_hmac_key_id",
        "exchange_idempotency_hmac",
    ),
    ("mobile_review_auth_challenges", "account_hmac_key_id", "account_hmac"),
    (
        "mobile_review_auth_challenges",
        "start_ip_hmac_key_id",
        "start_ip_hmac",
    ),
    (
        "mobile_review_auth_challenges",
        "password_proof_hmac_key_id",
        "password_proof_hmac",
    ),
    (
        "mobile_review_auth_challenges",
        "pkce_verifier_proof_hmac_key_id",
        "pkce_verifier_proof_hmac",
    ),
    (
        "mobile_review_auth_challenges",
        "idempotency_hmac_key_id",
        "idempotency_hmac",
    ),
    (
        "mobile_review_auth_challenges",
        "installation_hmac_key_id",
        "installation_hmac",
    ),
    (
        "mobile_auth_exchange_journals",
        "installation_hmac_key_id",
        "installation_hmac",
    ),
    (
        "mobile_auth_exchange_journals",
        "token_verifier_hmac_key_id",
        "token_verifier_hmac",
    ),
    (
        "mobile_auth_exchange_journals",
        "code_proof_hmac_key_id",
        "code_proof_hmac",
    ),
    (
        "mobile_auth_exchange_journals",
        "pkce_verifier_proof_hmac_key_id",
        "pkce_verifier_proof_hmac",
    ),
    (
        "mobile_auth_exchange_journals",
        "idempotency_hmac_key_id",
        "idempotency_hmac",
    ),
    (
        "mobile_auth_exchange_journals",
        "review_credential_hmac_key_id",
        "review_credential_hmac",
    ),
    (
        "mobile_auth_exchange_receipts",
        "idempotency_hmac_key_id",
        "idempotency_hmac",
    ),
    ("mobile_signout_journals", "selector_hmac_key_id", "selector_hmac"),
    (
        "mobile_signout_journals",
        "token_verifier_hmac_key_id",
        "token_verifier_hmac",
    ),
    (
        "mobile_signout_journals",
        "idempotency_hmac_key_id",
        "idempotency_hmac",
    ),
    ("mobile_signout_receipts", "selector_hmac_key_id", "selector_hmac"),
    (
        "mobile_signout_receipts",
        "token_verifier_hmac_key_id",
        "token_verifier_hmac",
    ),
    (
        "mobile_signout_receipts",
        "idempotency_hmac_key_id",
        "idempotency_hmac",
    ),
    (
        "mobile_device_revoke_journals",
        "target_selector_hmac_key_id",
        "target_selector_hmac",
    ),
    (
        "mobile_device_revoke_journals",
        "target_token_verifier_hmac_key_id",
        "target_token_verifier_hmac",
    ),
    (
        "mobile_device_revoke_journals",
        "idempotency_hmac_key_id",
        "idempotency_hmac",
    ),
    (
        "mobile_device_revoke_receipts",
        "target_selector_hmac_key_id",
        "target_selector_hmac",
    ),
    (
        "mobile_device_revoke_receipts",
        "idempotency_hmac_key_id",
        "idempotency_hmac",
    ),
    (
        "mobile_recovery_fence_checkpoints",
        "chain_hmac_key_id",
        "head_record_hash",
    ),
    (
        "mobile_recovery_baseline_journals",
        "chain_hmac_key_id",
        "record_hash",
    ),
    ("mobile_rate_limit_events", "key_id", "key_digest"),
)


@dataclass(frozen=True)
class MobileStateGeneration:
    generation: int
    required_columns: dict[str, tuple[str, ...]]
    required_indexes: dict[str, tuple[str, tuple[str, ...]]]
    required_triggers: dict[str, str]
    required_views: tuple[str, ...]
    restored_credential_tables: tuple[str, ...]

    def __post_init__(self) -> None:
        credential_tables = self.restored_credential_tables
        if (
            len({table.casefold() for table in credential_tables})
            != len(credential_tables)
            or any(
                re.fullmatch(r"[a-z_][a-z0-9_]{0,127}", table) is None
                for table in credential_tables
            )
        ):
            raise ValueError("A mobile-state credential registry is invalid.")
        for table in credential_tables:
            if table in _LEGACY_RESTORED_CREDENTIAL_TABLES:
                continue
            if not table.startswith("mobile_") or table not in self.required_columns:
                raise ValueError(
                    "A restored credential table must be generation-owned."
                )


# Frozen exceptions for shipped pre-mobile credential schemas. New credential
# tables must be generation-owned mobile_* tables, never additions here.
_LEGACY_RESTORED_CREDENTIAL_TABLES = (
    "email_codes",
    "signup_intents",
    "shopify_customer_account_oauth_states",
    "shopify_customer_account_browser_sessions",
)

_GENERATION_ONE_RESTORED_CREDENTIAL_TABLES = (
    "mobile_api_tokens",
    "mobile_auth_challenges",
    "mobile_review_auth_challenges",
    "mobile_auth_exchange_journals",
    "mobile_auth_exchange_receipts",
    "mobile_signout_journals",
    "mobile_signout_receipts",
    "mobile_device_revoke_journals",
    "mobile_device_revoke_receipts",
    *_LEGACY_RESTORED_CREDENTIAL_TABLES,
)


MOBILE_STATE_GENERATIONS: dict[int, MobileStateGeneration] = {
    0: MobileStateGeneration(
        generation=0,
        required_columns={
            "mobile_api_tokens": tuple(sorted(_MOBILE_TOKEN_BASE_COLUMNS))
        },
        required_indexes=_MOBILE_TOKEN_BASE_INDEXES,
        required_triggers={},
        required_views=(),
        restored_credential_tables=(
            "mobile_api_tokens",
            *_LEGACY_RESTORED_CREDENTIAL_TABLES,
        ),
    ),
    1: MobileStateGeneration(
        generation=1,
        required_columns=_GENERATION_ONE_REQUIRED_COLUMNS,
        required_indexes={
            **_MOBILE_TOKEN_BASE_INDEXES,
            **_GENERATION_ONE_INDEXES,
        },
        required_triggers={},
        required_views=(),
        restored_credential_tables=_GENERATION_ONE_RESTORED_CREDENTIAL_TABLES,
    )
}


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))


def _validate_required_columns(connection: sqlite3.Connection) -> None:
    for table, expected in _GENERATION_ONE_TABLE_SHAPES.items():
        actual = _table_shape(connection, table)
        if actual != expected:
            raise RuntimeError(
                f"Incompatible generation-1 mobile schema for {table}; "
                "the exact table shape is required."
            )


def _validate_mobile_schema_object_inventory(
    connection: sqlite3.Connection,
    generation: int,
) -> None:
    """Reject mobile-state objects not registered in the closed generation."""

    contract = MOBILE_STATE_GENERATIONS[generation]
    allowed_tables = {table.casefold() for table in contract.required_columns}
    allowed_indexes = {
        name.casefold(): (table, columns)
        for name, (table, columns) in contract.required_indexes.items()
    }
    allowed_triggers = {
        name.casefold(): table.casefold()
        for name, table in contract.required_triggers.items()
    }
    allowed_views = {name.casefold() for name in contract.required_views}
    seen_indexes: set[str] = set()
    seen_triggers: set[str] = set()
    seen_views: set[str] = set()
    for object_type, name, table_name, sql in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'trigger', 'view')"
    ):
        object_type = str(object_type)
        name = str(name)
        table_name = str(table_name)
        sql_text = str(sql or "")
        name_key = name.casefold()
        table_key = table_name.casefold()
        if name_key.startswith("sqlite_"):
            if (
                object_type == "index"
                and name_key.startswith("sqlite_autoindex_")
                and table_key in allowed_tables
            ):
                continue
            if not name_key.startswith("sqlite_autoindex_mobile_"):
                continue
        references_mobile_state = bool(
            re.search(r"\bmobile_[A-Za-z0-9_]+\b", sql_text, re.IGNORECASE)
        )
        is_mobile_object = (
            name_key.startswith("mobile_")
            or table_key.startswith("mobile_")
            or table_key in allowed_tables
            or (
                object_type in {"trigger", "view"}
                and references_mobile_state
            )
        )
        if not is_mobile_object:
            continue
        if object_type == "table" and name_key in allowed_tables:
            continue
        if object_type == "index" and name_key in allowed_indexes:
            expected_table, expected_columns = allowed_indexes[name_key]
            try:
                _validate_index_shape(
                    connection,
                    name,
                    expected_table,
                    expected_columns,
                )
            except RuntimeError:
                pass
            else:
                seen_indexes.add(name_key)
                continue
        if (
            object_type == "trigger"
            and allowed_triggers.get(name_key) == table_key
        ):
            seen_triggers.add(name_key)
            continue
        if object_type == "view" and name_key in allowed_views:
            seen_views.add(name_key)
            continue
        raise RuntimeError(
            "Unknown or unsupported mobile-state schema object exists."
        )
    if (
        seen_indexes != set(allowed_indexes)
        or seen_triggers != set(allowed_triggers)
        or seen_views != allowed_views
    ):
        raise RuntimeError(
            "A required mobile-state schema object is missing or incompatible."
        )


def validate_mobile_state_schema(connection: sqlite3.Connection) -> None:
    _validate_required_columns(connection)
    for index, (table, expected_columns) in _GENERATION_ONE_INDEXES.items():
        _validate_index_shape(connection, index, table, expected_columns)
    _validate_mobile_schema_object_inventory(
        connection,
        MOBILE_STATE_SCHEMA_GENERATION,
    )


def detect_mobile_state_generation(connection: sqlite3.Connection) -> int:
    """Return the one exact implemented generation or reject a partial footprint."""

    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    indexes = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    token_columns = set(_table_columns(connection, "mobile_api_tokens"))
    if not _MOBILE_TOKEN_BASE_COLUMNS.issubset(token_columns):
        raise RuntimeError(
            "Incompatible generation-0 mobile schema; the exact token base is required."
        )

    additive_names = {name for name, _ddl in _MOBILE_TOKEN_ADDITIVE_COLUMNS}
    generation_one_tables = set(_GENERATION_ONE_REQUIRED_COLUMNS) - {
        "mobile_api_tokens"
    }
    generation_one_indexes = set(_GENERATION_ONE_INDEXES)
    has_generation_one_footprint = bool(
        (token_columns & additive_names)
        or (tables & generation_one_tables)
        or (indexes & generation_one_indexes)
    )
    if not has_generation_one_footprint:
        if token_columns != _MOBILE_TOKEN_BASE_COLUMNS:
            raise RuntimeError(
                "Incompatible generation-0 mobile schema; unknown token columns exist."
            )
        _validate_mobile_schema_object_inventory(connection, 0)
        return 0

    validate_mobile_state_schema(connection)
    return MOBILE_STATE_SCHEMA_GENERATION


def ensure_mobile_state_schema(connection: sqlite3.Connection) -> None:
    """Apply the one valid generation-0 -> generation-1 additive migration.

    A database with none of the token extension columns is an older released
    database.  A database with only some of them is an unknown partial
    migration and fails closed instead of being guessed back into shape.
    """

    token_columns = set(_table_columns(connection, "mobile_api_tokens"))
    if not _MOBILE_TOKEN_BASE_COLUMNS.issubset(token_columns):
        missing = sorted(_MOBILE_TOKEN_BASE_COLUMNS - token_columns)
        raise RuntimeError(
            "Incompatible mobile_api_tokens schema; missing base column(s): "
            + ", ".join(missing)
        )
    additive_names = {name for name, _ddl in _MOBILE_TOKEN_ADDITIVE_COLUMNS}
    present_additive = token_columns & additive_names
    if present_additive and present_additive != additive_names:
        missing = sorted(additive_names - present_additive)
        raise RuntimeError(
            "Incompatible partial generation-1 mobile_api_tokens schema; "
            "missing column(s): " + ", ".join(missing)
        )
    if not present_additive:
        for _name, ddl in _MOBILE_TOKEN_ADDITIVE_COLUMNS:
            connection.execute(f"ALTER TABLE mobile_api_tokens ADD COLUMN {ddl}")

    for ddl in _GENERATION_ONE_TABLE_DDL.values():
        connection.execute(ddl)
    # Validate table shape before index DDL.  This turns a hostile/partial table
    # into one explicit fail-closed error rather than an incidental SQLite
    # "no such column" exception while creating its index.
    _validate_required_columns(connection)
    for ddl in _INDEX_DDL.values():
        connection.execute(ddl)
    validate_mobile_state_schema(connection)


def referenced_mobile_state_key_ids(connection: sqlite3.Connection) -> set[str]:
    referenced: set[str] = set()
    for table, key_column, digest_column in _HMAC_COLUMN_PAIRS:
        mismatched = connection.execute(
            f"SELECT 1 FROM {table} WHERE "
            f"({key_column} IS NULL) != ({digest_column} IS NULL) LIMIT 1"
        ).fetchone()
        if mismatched is not None:
            raise RuntimeError(
                f"Incomplete HMAC key/digest pair in {table}.{key_column}."
            )
        invalid = connection.execute(
            f"SELECT 1 FROM {table} WHERE {key_column} IS NOT NULL AND ("
            f" typeof({key_column}) != 'text'"
            f" OR length({key_column}) NOT BETWEEN 1 AND 64"
            f" OR substr({key_column}, 1, 1) GLOB '[^A-Za-z0-9]'"
            f" OR {key_column} GLOB '*[^A-Za-z0-9._-]*'"
            f" OR typeof({digest_column}) != 'text'"
            f" OR length({digest_column}) != 64"
            f" OR {digest_column} GLOB '*[^0-9a-f]*'"
            ") LIMIT 1"
        ).fetchone()
        if invalid is not None:
            raise RuntimeError(
                f"Persisted invalid HMAC key ID or digest in "
                f"{table}.{key_column}."
            )
        referenced.update(
            str(row[0])
            for row in connection.execute(
                f"SELECT DISTINCT {key_column} FROM {table} "
                f"WHERE {key_column} IS NOT NULL"
            )
        )
    return referenced


def require_mobile_state_key_coverage(
    connection: sqlite3.Connection, keyring: VersionedHMAC | None
) -> set[str]:
    referenced = referenced_mobile_state_key_ids(connection)
    if referenced and keyring is None:
        raise RuntimeError(
            f"{MOBILE_STATE_KEYRING_ENV} is required for referenced key ID(s): "
            + ", ".join(sorted(referenced))
        )
    if keyring is not None:
        keyring.require_key_ids(referenced)
    return referenced


def _schema_digest(connection: sqlite3.Connection, table: str) -> str:
    columns = [
        {
            "name": str(row[1]),
            "type": str(row[2]),
            "not_null": bool(row[3]),
            "default": row[4],
            "primary_key": int(row[5]),
        }
        for row in connection.execute(f"PRAGMA table_info({table})")
    ]
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    indexes = {}
    for row in connection.execute(f"PRAGMA index_list({table})"):
        name = str(row[1])
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (name,),
        ).fetchone()
        indexes[name] = {
            "columns": list(_index_columns(connection, name)),
            "unique": bool(row[2]),
            "origin": str(row[3]),
            "partial": bool(row[4]),
            "sql": sql_row[0] if sql_row is not None else None,
        }
    encoded = json.dumps(
        {
            "table_sql": table_sql_row[0] if table_sql_row is not None else None,
            "columns": columns,
            "indexes": indexes,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _group_counts(
    connection: sqlite3.Connection, table: str, column: str
) -> dict[str, int]:
    return {
        str(row[0]): int(row[1])
        for row in connection.execute(
            f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column} "
            f"ORDER BY {column}"
        )
    }


def mobile_state_summary(connection: sqlite3.Connection) -> dict[str, object]:
    """Return the canonical generation-1 state attestation for backup writers."""

    validate_mobile_state_schema(connection)
    tables = _GENERATION_ONE_REQUIRED_COLUMNS
    phase_tables = (
        "mobile_auth_exchange_journals",
        "mobile_signout_journals",
        "mobile_device_revoke_journals",
        "mobile_recovery_baseline_journals",
    )
    return {
        "generation": MOBILE_STATE_SCHEMA_GENERATION,
        "schema_sha256": {
            table: _schema_digest(connection, table) for table in sorted(tables)
        },
        "table_row_counts": {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in sorted(tables)
        },
        "phase_counts": {
            table: _group_counts(connection, table, "phase")
            for table in phase_tables
        },
        "domain_counts": {
            "mobile_rate_limit_events": _group_counts(
                connection, "mobile_rate_limit_events", "domain"
            )
        },
        "referenced_hmac_key_ids": sorted(
            referenced_mobile_state_key_ids(connection)
        ),
    }
