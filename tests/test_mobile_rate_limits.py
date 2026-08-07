"""Native-auth keyed rate limits never persist identities or debit partially."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from swinglab.web.mobile_schema import (
    MOBILE_STATE_GENERATIONS,
    MobileStateDomain,
    VersionedHMAC,
    mobile_state_summary,
)
from swinglab.web.throttle import KeyedThrottle
from swinglab.web.users import (
    MOBILE_API_TOKEN_ACTIVE_LIMIT,
    MobileAPITokenLimitError,
    UserStore,
)


def _keyring() -> VersionedHMAC:
    current = base64.b64encode(b"c" * 32).decode("ascii")
    previous = base64.b64encode(b"p" * 32).decode("ascii")
    return VersionedHMAC.from_json(
        json.dumps(
            {
                "version": 1,
                "current_key_id": "current",
                "keys": {"previous": previous, "current": current},
            }
        )
    )


def test_versioned_hmac_separates_domains_and_keeps_old_key_candidates():
    """Catches digest reuse across semantic domains or dropped rotation lookup."""

    keyring = _keyring()
    email = b"golfer@example.com"

    current_id, email_digest = keyring.digest(
        MobileStateDomain.AUTH_START_NORMALIZED_EMAIL_RATE, email
    )
    _, code_digest = keyring.digest(
        MobileStateDomain.EMAIL_CODE_VERIFIER, email
    )
    candidates = keyring.candidates(
        MobileStateDomain.AUTH_START_NORMALIZED_EMAIL_RATE, email
    )

    assert current_id == "current"
    assert email_digest != code_digest
    assert [candidate.key_id for candidate in candidates] == [
        "current",
        "previous",
    ]
    assert candidates[0].digest == email_digest


def test_mobile_state_hmac_domain_set_is_closed_and_every_digest_is_distinct():
    """Catches a generic domain or cross-purpose digest comparison being added."""

    expected = {
        "installation-id",
        "auth-start-client-ip",
        "auth-start-normalized-email-rate",
        "auth-exchange-client-ip",
        "auth-exchange-normalized-email-rate",
        "email-code-verifier",
        "auth-exchange-code-proof",
        "auth-exchange-pkce-verifier-proof",
        "review-auth-account",
        "review-auth-client-ip",
        "review-auth-password-proof",
        "review-auth-pkce-verifier-proof",
        "review-auth-idempotency",
        "exchange-idempotency",
        "sign-out-idempotency",
        "device-revoke-idempotency",
        "practice-idempotency",
        "upload-idempotency",
        "upload-abort-idempotency",
        "analysis-retry-idempotency",
        "analysis-source-discard-idempotency",
        "export-idempotency",
        "history-reset-idempotency",
        "account-delete-idempotency",
        "event-idempotency",
        "recovery-selector",
        "recovery-token-verifier",
        "erasure-stable-user-id",
        "erasure-normalized-email",
        "shopify-erasure-shop-domain",
        "shopify-erasure-customer-id",
        "shopify-erasure-normalized-email",
        "recovery-chain-link",
    }
    assert {domain.value for domain in MobileStateDomain} == expected
    digests = {
        _keyring().digest(domain, b"same protected input")[1]
        for domain in MobileStateDomain
    }
    assert len(digests) == len(expected)


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        '{"version":1,"current_key_id":"missing","keys":{}}',
        '{"version":1,"current_key_id":"current","keys":{"current":"c2hvcnQ="}}',
        '{"version":2,"current_key_id":"current","keys":{"current":"Y2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2M="}}',
    ],
)
def test_versioned_hmac_rejects_incomplete_or_weak_keyrings(payload):
    """Catches startup accepting an unversioned, missing, or short HMAC key."""

    with pytest.raises(ValueError):
        VersionedHMAC.from_json(payload)


def test_versioned_hmac_constructor_rejects_text_key_material():
    """Catches a 32-character string being mistaken for a 32-byte secret."""

    with pytest.raises(ValueError, match="32-byte key"):
        VersionedHMAC("current", {"current": "c" * 32})  # type: ignore[dict-item]


def test_keyed_throttle_parallel_consumers_cannot_pass_the_last_slot(tmp_path):
    """Catches the check/insert race that admits more callers than the limit."""

    db_path = tmp_path / "throttle.sqlite"
    keyring = _keyring()
    first = KeyedThrottle(db_path, keyring)
    first.consume("auth-start-ip", "203.0.113.7", limit=4, window_s=900, now=1)
    first.close()

    def consume() -> bool:
        throttle = KeyedThrottle(db_path, keyring)
        try:
            return throttle.consume(
                "auth-start-ip", "203.0.113.7", limit=4, window_s=900, now=2
            ).allowed
        finally:
            throttle.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        allowed = list(executor.map(lambda _: consume(), range(8)))

    assert allowed.count(True) == 3
    assert allowed.count(False) == 5
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM mobile_rate_limit_events"
        ).fetchone()[0] == 4
        stored_dump = "\n".join(connection.iterdump())
    finally:
        connection.close()
    assert "203.0.113.7" not in stored_dump


def test_keyed_throttle_debits_two_keys_all_or_none(tmp_path):
    """Catches an exhausted email bucket still consuming the caller IP bucket."""

    throttle = KeyedThrottle(tmp_path / "throttle.sqlite", _keyring())
    email_entry = ("auth-start-email", "golfer@example.com", 1, 900)
    ip_entry = ("auth-start-ip", "203.0.113.8", 5, 900)
    try:
        first = throttle.consume_many([ip_entry, email_entry], now=10)
        denied = throttle.consume_many([ip_entry, email_entry], now=11)
        other_email = throttle.consume_many(
            [ip_entry, ("auth-start-email", "other@example.com", 5, 900)],
            now=12,
        )
    finally:
        throttle.close()

    assert first.allowed is True
    assert denied.allowed is False
    assert denied.retry_after_seconds == 899
    assert other_email.allowed is True


def test_keyed_throttle_rolls_back_every_debit_on_insert_failure(tmp_path):
    """Catches one key being charged when another debit cannot be persisted."""

    db_path = tmp_path / "throttle.sqlite"
    throttle = KeyedThrottle(db_path, _keyring())
    throttle._conn.execute(
        "CREATE TRIGGER fail_second_mobile_rate_insert "
        "BEFORE INSERT ON mobile_rate_limit_events "
        "WHEN NEW.domain = 'auth-start-email' "
        "BEGIN SELECT RAISE(ABORT, 'synthetic insert failure'); END"
    )
    throttle._conn.commit()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            throttle.consume_many(
                [
                    ("auth-start-ip", "203.0.113.9", 5, 900),
                    ("auth-start-email", "golfer@example.com", 5, 900),
                ],
                now=20,
            )
        assert throttle._conn.execute(
            "SELECT COUNT(*) FROM mobile_rate_limit_events"
        ).fetchone()[0] == 0
    finally:
        throttle.close()


def test_keyed_throttle_counts_old_and_current_keys_and_purges_bounded_rows(tmp_path):
    """Catches key rotation bypass or an unbounded/non-durable 24-hour purge."""

    db_path = tmp_path / "throttle.sqlite"
    keyring = _keyring()
    throttle = KeyedThrottle(db_path, keyring)
    try:
        old = keyring.candidates(
            MobileStateDomain.AUTH_START_CLIENT_IP, b"203.0.113.10"
        )[1]
        throttle._conn.execute(
            "INSERT INTO mobile_rate_limit_events"
            " (domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
            ("auth-start-ip", old.key_id, old.digest, 1.0),
        )
        throttle._conn.commit()
        decision = throttle.consume(
            "auth-start-ip", "203.0.113.10", limit=1, window_s=900, now=2
        )
        assert decision.allowed is False

        for index in range(5):
            throttle._conn.execute(
                "INSERT INTO mobile_rate_limit_events"
                " (domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
                ("stale", "current", "00", -100000.0 - index),
            )
        throttle._conn.commit()
        assert throttle.purge_expired(now=86401, batch_size=2) == 2
        assert throttle._conn.execute(
            "SELECT COUNT(*) FROM mobile_rate_limit_events WHERE domain = 'stale'"
        ).fetchone()[0] == 3
    finally:
        throttle.close()


def test_keyed_throttle_restart_counts_retained_key_without_cross_domain_debit(
    tmp_path,
):
    """Catches restart/rotation bypass or one domain charging another."""

    db_path = tmp_path / "rotated-throttle.sqlite"
    previous_only = VersionedHMAC(
        "previous",
        {"previous": b"p" * 32},
    )
    before_rotation = KeyedThrottle(db_path, previous_only)
    try:
        assert before_rotation.consume(
            "auth-start-ip", "shared-input", limit=1, window_s=900, now=1
        ).allowed is True
    finally:
        before_rotation.close()

    after_rotation = KeyedThrottle(db_path, _keyring())
    try:
        assert after_rotation.consume(
            "auth-start-email", "shared-input", limit=1, window_s=900, now=2
        ).allowed is True
        assert after_rotation.consume(
            "auth-start-ip", "shared-input", limit=1, window_s=900, now=2
        ).allowed is False
    finally:
        after_rotation.close()


def test_keyed_throttle_prunes_expired_window_rows_before_debit(tmp_path):
    """Catches sliding-window cleanup being omitted from the atomic consume."""

    throttle = KeyedThrottle(tmp_path / "throttle.sqlite", _keyring())
    key_id, digest = _keyring().digest(
        MobileStateDomain.AUTH_START_CLIENT_IP, "203.0.113.11"
    )
    throttle._conn.execute(
        "INSERT INTO mobile_rate_limit_events"
        " (domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
        ("auth-start-ip", key_id, digest, 1.0),
    )
    throttle._conn.commit()
    try:
        assert throttle.consume(
            "auth-start-ip", "203.0.113.11", limit=1, window_s=900, now=902
        ).allowed is True
        assert throttle._conn.execute(
            "SELECT COUNT(*) FROM mobile_rate_limit_events"
            " WHERE domain = ? AND key_id = ? AND key_digest = ?",
            ("auth-start-ip", key_id, digest),
        ).fetchone()[0] == 1
    finally:
        throttle.close()


def test_keyed_throttle_rejects_same_named_wrong_lookup_index(tmp_path):
    """Catches standalone throttle startup accepting a weakened lookup index."""

    db_path = tmp_path / "wrong-rate-index.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE mobile_rate_limit_events ("
        "domain TEXT NOT NULL, key_id TEXT NOT NULL, key_digest TEXT NOT NULL,"
        " occurred_at REAL NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX mobile_rate_limit_events_lookup"
        " ON mobile_rate_limit_events(occurred_at)"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="mobile_rate_limit_events_lookup"):
        KeyedThrottle(db_path, _keyring())


def test_user_store_adds_complete_generation_one_schema_to_legacy_database(tmp_path):
    """Catches an additive migration omitting a required table, column, or index."""

    db_path = tmp_path / "users.sqlite"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE mobile_api_tokens ("
        "selector TEXT PRIMARY KEY, token_hash TEXT NOT NULL, user_id TEXT NOT NULL,"
        "auth_epoch INTEGER NOT NULL, label TEXT NOT NULL, created_at REAL NOT NULL,"
        "last_used_at REAL, expires_at REAL NOT NULL, revoked_at REAL)"
    )
    legacy.commit()
    legacy.close()

    users = UserStore(db_path, mobile_state_hmac=_keyring())
    try:
        summary = mobile_state_summary(users._conn)
        assert summary["generation"] == 1
        assert set(summary["schema_sha256"]) == set(
            MOBILE_STATE_GENERATIONS[1].required_columns
        )
        assert summary["referenced_hmac_key_ids"] == []
        token_columns = {
            row["name"]
            for row in users._conn.execute("PRAGMA table_info(mobile_api_tokens)")
        }
        assert {
            "installation_key",
            "installation_key_version",
            "state",
            "review_provider",
            "review_build",
            "review_expires_at",
        } <= token_columns
    finally:
        users.close()


def test_user_store_rejects_partial_generation_one_table(tmp_path):
    """Catches CREATE IF NOT EXISTS silently accepting a weakened challenge table."""

    db_path = tmp_path / "partial.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE mobile_auth_challenges (challenge_id TEXT PRIMARY KEY)"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="mobile_auth_challenges"):
        UserStore(db_path, mobile_state_hmac=_keyring())


def test_user_store_rejects_wrong_generation_one_index_shape(tmp_path):
    """Catches a same-named index that cannot enforce the live-IP lookup contract."""

    db_path = tmp_path / "wrong-index.sqlite"
    users = UserStore(db_path, mobile_state_hmac=_keyring())
    users._conn.execute("DROP INDEX mobile_auth_challenges_active_ip")
    users._conn.execute(
        "CREATE INDEX mobile_auth_challenges_active_ip"
        " ON mobile_auth_challenges(expires_at)"
    )
    users._conn.commit()
    users.close()

    with pytest.raises(RuntimeError, match="mobile_auth_challenges_active_ip"):
        UserStore(db_path, mobile_state_hmac=_keyring())


def test_startup_requires_every_live_mobile_state_hmac_key_even_when_features_off(
    tmp_path,
):
    """Catches feature flags bypassing key coverage for retained protected rows."""

    db_path = tmp_path / "key-coverage.sqlite"
    users = UserStore(db_path, mobile_state_hmac=_keyring())
    users._conn.execute(
        "INSERT INTO mobile_rate_limit_events"
        " (domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
        ("auth-start-ip", "retired", "ab" * 32, 10.0),
    )
    users._conn.commit()
    users.close()

    with pytest.raises(RuntimeError, match="retired"):
        UserStore(db_path, mobile_state_hmac=_keyring())

    current = base64.b64encode(b"c" * 32).decode("ascii")
    retired = base64.b64encode(b"r" * 32).decode("ascii")
    complete = VersionedHMAC.from_json(
        json.dumps(
            {
                "version": 1,
                "current_key_id": "current",
                "keys": {"current": current, "retired": retired},
            }
        )
    )
    reopened = UserStore(db_path, mobile_state_hmac=complete)
    try:
        assert mobile_state_summary(reopened._conn)[
            "referenced_hmac_key_ids"
        ] == ["retired"]
    finally:
        reopened.close()


def test_mobile_state_summary_records_counts_domains_phases_and_schema_digests(tmp_path):
    """Catches a manifest summary unable to attest protected state exactly."""

    db_path = tmp_path / "summary.sqlite"
    users = UserStore(db_path, mobile_state_hmac=_keyring())
    users._conn.execute(
        "INSERT INTO mobile_signout_journals"
        " (operation_id, user_id, selector, phase, selector_hmac_key_id,"
        " selector_hmac, token_verifier_hmac_key_id, token_verifier_hmac,"
        " idempotency_hmac_key_id, idempotency_hmac, request_hash, created_at,"
        " updated_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "operation",
            "user",
            "selector",
            "prepared",
            "k1",
            "01" * 32,
            "k1",
            "02" * 32,
            "k1",
            "03" * 32,
            "04" * 32,
            1.0,
            1.0,
            2.0,
        ),
    )
    users._conn.commit()
    try:
        summary = mobile_state_summary(users._conn)
        assert summary["table_row_counts"]["mobile_signout_journals"] == 1
        assert summary["phase_counts"]["mobile_signout_journals"] == {
            "prepared": 1
        }
        assert summary["referenced_hmac_key_ids"] == ["k1"]
        assert all(
            re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in summary["schema_sha256"].values()
        )
    finally:
        users.close()


def test_mobile_state_schema_digest_covers_actual_indexes(tmp_path):
    """Catches attestation hashing a hard-coded index list instead of SQLite state."""

    users = UserStore(tmp_path / "schema-digest.sqlite", mobile_state_hmac=_keyring())
    try:
        before = mobile_state_summary(users._conn)["schema_sha256"][
            "mobile_rate_limit_events"
        ]
        users._conn.execute(
            "CREATE INDEX mobile_rate_limit_events_domain_time"
            " ON mobile_rate_limit_events(domain, occurred_at)"
        )
        users._conn.commit()
        after = mobile_state_summary(users._conn)["schema_sha256"][
            "mobile_rate_limit_events"
        ]
        assert after != before
    finally:
        users.close()


def test_generation_one_inactive_or_fenced_token_is_never_authenticated(tmp_path):
    """Catches an exchange/sign-out fence column that authentication ignores."""

    users = UserStore(tmp_path / "tokens.sqlite", mobile_state_hmac=_keyring())
    user = users.create("owner@example.com", "longenough")
    raw, token = users.issue_mobile_api_token(
        user.id,
        "Range phone",
        expected_auth_epoch=user.auth_epoch,
        now=10.0,
    )
    assert users.authenticate_mobile_api_principal(raw, now=11.0) is not None

    users._conn.execute(
        "UPDATE mobile_api_tokens SET state = 'inactive' WHERE selector = ?",
        (token.selector,),
    )
    users._conn.commit()
    assert users.authenticate_mobile_api_principal(raw, now=12.0) is None

    users._conn.execute(
        "UPDATE mobile_api_tokens SET state = 'fenced', fenced_at = 13.0"
        " WHERE selector = ?",
        (token.selector,),
    )
    users._conn.commit()
    assert users.authenticate_mobile_api_principal(raw, now=14.0) is None


def test_generation_one_device_cap_counts_only_active_unfenced_tokens(tmp_path):
    """Catches a prepared exchange consuming a slot in the five-device cap."""

    users = UserStore(tmp_path / "device-cap.sqlite", mobile_state_hmac=_keyring())
    try:
        user = users.create("cap-owner@example.com", "longenough")
        issued = [
            users.issue_mobile_api_token(
                user.id,
                f"Device {index}",
                expected_auth_epoch=user.auth_epoch,
                now=10.0,
            )[1]
            for index in range(MOBILE_API_TOKEN_ACTIVE_LIMIT)
        ]
        users._conn.execute(
            "UPDATE mobile_api_tokens SET state = 'fenced', fenced_at = 11.0"
            " WHERE selector = ?",
            (issued[0].selector,),
        )
        users._conn.commit()

        replacement_raw, _replacement = users.issue_mobile_api_token(
            user.id,
            "Replacement",
            expected_auth_epoch=user.auth_epoch,
            now=12.0,
        )
        assert users.authenticate_mobile_api_principal(replacement_raw, now=13.0)
        with pytest.raises(MobileAPITokenLimitError):
            users.issue_mobile_api_token(
                user.id,
                "One too many",
                expected_auth_epoch=user.auth_epoch,
                now=14.0,
            )
    finally:
        users.close()
    users.close()
