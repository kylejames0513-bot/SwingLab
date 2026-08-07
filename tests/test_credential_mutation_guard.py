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
