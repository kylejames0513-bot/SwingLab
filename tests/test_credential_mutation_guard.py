"""Selector-scoped admission and final-commit fencing for bearer mutations."""

from __future__ import annotations

import importlib

import pytest

from swinglab.api.auth import MobileAuthContext
from swinglab.web.users import UserStore


@pytest.fixture
def mobile_credential(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = users.create("lease-owner@example.com", "longenough")
    raw_token, token = users.issue_mobile_api_token(
        user.id,
        "Lease phone",
        expected_auth_epoch=user.auth_epoch,
        now=1_000.0,
    )
    context = MobileAuthContext(
        user=user,
        via_bearer=True,
        selector=token.selector,
        auth_epoch=user.auth_epoch,
    )
    try:
        yield users, user, raw_token, token.selector, context
    finally:
        users.close()


@pytest.fixture
def review_mobile_credential(tmp_path):
    users = UserStore(tmp_path / "review-users.db")
    user = users.create("review-lease-owner@example.com", "longenough")
    raw_token, token = users.issue_mobile_api_token(
        user.id,
        "Review lease phone",
        expected_auth_epoch=user.auth_epoch,
        now=1_000.0,
    )
    users._conn.execute(
        "UPDATE mobile_api_tokens SET expires_at = 2000,"
        " review_provider = 'apple', review_build = '42',"
        " review_expires_at = 1100,"
        " review_credential_hmac_key_id = 'entitlements-k1',"
        " review_credential_hmac = ?, review_lane_revision = 7"
        " WHERE selector = ?",
        ("a" * 64, token.selector),
    )
    users._conn.commit()
    context = MobileAuthContext(
        user=user,
        via_bearer=True,
        selector=token.selector,
        auth_epoch=user.auth_epoch,
        review_provider="apple",
        review_build="42",
        review_expires_at=1100.0,
        review_credential_hmac_key_id="entitlements-k1",
        review_credential_hmac="a" * 64,
        review_lane_revision=7,
    )
    try:
        yield users, user, raw_token, token.selector, context
    finally:
        users.close()


def _guard_api():
    module = importlib.import_module("swinglab.web.credential_mutations")
    return (
        module.CredentialMutationGuard,
        module.CredentialMutationRejected,
    )


@pytest.mark.parametrize("invalidate", ["selector", "auth_epoch"])
def test_final_locked_validation_rechecks_selector_and_captured_epoch(
    mobile_credential, invalidate
):
    """A lease admitted earlier cannot commit after revoke or recovery."""

    CredentialMutationGuard, CredentialMutationRejected = _guard_api()
    users, user, _raw_token, selector, context = mobile_credential
    guard = CredentialMutationGuard()
    lease = guard.admit(context)

    if invalidate == "selector":
        assert users.revoke_mobile_api_token(user.id, selector, now=1_001.0)
    else:
        users.set_password(user.id, "replacement-password")

    with users._lock:
        users._conn.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(CredentialMutationRejected):
                lease.validate_locked(users, now=1_002.0)
        finally:
            users._conn.rollback()
    lease.release()


def test_validate_and_close_caller_converts_its_lease_without_self_wait(
    mobile_credential,
):
    """Self sign-out releases the caller before waiting for older work."""

    CredentialMutationGuard, _CredentialMutationRejected = _guard_api()
    users, _user, raw_token, _selector, context = mobile_credential
    guard = CredentialMutationGuard()
    caller = guard.admit(context)

    close = guard.validate_and_close_caller(caller, users, now=1_001.0)

    assert caller.released is True
    assert close.drain(timeout_seconds=0.0) is True
    assert users.authenticate_mobile_api_principal(raw_token, now=1_002.0) is None


def test_close_cancels_other_leases_and_reports_pending_until_they_drain(
    mobile_credential,
):
    """A bounded close is pending while an earlier cooperative request exists."""

    CredentialMutationGuard, _CredentialMutationRejected = _guard_api()
    users, _user, _raw_token, _selector, context = mobile_credential
    guard = CredentialMutationGuard()
    earlier = guard.admit(context)
    caller = guard.admit(context)

    close = guard.validate_and_close_caller(caller, users, now=1_001.0)

    assert earlier.cancellation_requested is True
    assert close.drain(timeout_seconds=0.0) is False
    earlier.release()
    assert close.drain(timeout_seconds=0.05) is True


def test_closed_selector_rejects_new_admission(mobile_credential):
    CredentialMutationGuard, CredentialMutationRejected = _guard_api()
    users, _user, _raw_token, _selector, context = mobile_credential
    guard = CredentialMutationGuard()
    caller = guard.admit(context)
    guard.validate_and_close_caller(caller, users, now=1_001.0)

    with pytest.raises(CredentialMutationRejected):
        guard.admit(context)


@pytest.mark.parametrize(
    ("column", "changed"),
    (
        ("review_provider", "google"),
        ("review_build", "43"),
        ("review_expires_at", 1200.0),
        ("review_credential_hmac_key_id", "entitlements-k2"),
        ("review_credential_hmac", "b" * 64),
        ("review_lane_revision", 8),
    ),
)
def test_review_lease_final_validation_rechecks_every_captured_scope_field(
    review_mobile_credential, column, changed
):
    CredentialMutationGuard, CredentialMutationRejected = _guard_api()
    users, _user, _raw_token, selector, context = review_mobile_credential
    guard = CredentialMutationGuard()
    lease = guard.admit(context)
    users._conn.execute(
        f"UPDATE mobile_api_tokens SET {column} = ? WHERE selector = ?",
        (changed, selector),
    )
    users._conn.commit()

    with users._lock:
        users._conn.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(CredentialMutationRejected):
                lease.validate_locked(users, now=1_050.0)
        finally:
            users._conn.rollback()
    lease.release()


def test_review_lease_rejects_final_commit_after_authoritative_review_expiry(
    review_mobile_credential,
):
    CredentialMutationGuard, CredentialMutationRejected = _guard_api()
    users, _user, _raw_token, _selector, context = review_mobile_credential
    guard = CredentialMutationGuard()
    lease = guard.admit(context)

    with users._lock:
        users._conn.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(CredentialMutationRejected):
                lease.validate_locked(users, now=1_101.0)
        finally:
            users._conn.rollback()
    lease.release()


def test_review_lane_close_cancels_existing_lease_before_its_final_commit(
    review_mobile_credential,
):
    CredentialMutationGuard, CredentialMutationRejected = _guard_api()
    users, _user, _raw_token, selector, context = review_mobile_credential
    guard = CredentialMutationGuard()
    lease = guard.admit(context)

    close = guard.close_selector(selector)
    assert lease.cancellation_requested is True
    assert close.drain(timeout_seconds=0.0) is False
    with users._lock:
        users._conn.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(CredentialMutationRejected):
                lease.validate_locked(users, now=1_050.0)
        finally:
            users._conn.rollback()
    lease.release()
    assert close.drain(timeout_seconds=0.05) is True


def test_ordinary_lease_final_validation_remains_unchanged(mobile_credential):
    CredentialMutationGuard, _CredentialMutationRejected = _guard_api()
    users, _user, _raw_token, _selector, context = mobile_credential
    guard = CredentialMutationGuard()
    lease = guard.admit(context)
    with users._lock:
        users._conn.execute("BEGIN IMMEDIATE")
        try:
            lease.validate_locked(users, now=1_001.0)
        finally:
            users._conn.rollback()
    lease.release()
