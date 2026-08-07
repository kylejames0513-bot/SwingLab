"""Crash-safe bearer-only sign-out and recovery-fence replay."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from swinglab.api.auth import MobileAuthContext
from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.credential_mutations import MobileSignOutService
from swinglab.web.mobile_schema import VersionedHMAC
from swinglab.web.recovery_fence_ledger import RecoveryFenceError
from swinglab.web.users import UserStore
from tests.test_recovery_fence_ledger import (
    _baseline_event as _real_baseline_event,
    _keyring as _real_keyring,
    _ledger as _real_ledger,
)


GOOD_KEY = "00112233445566778899aabbccddeeff"
OTHER_KEY = "ffeeddccbbaa99887766554433221100"


class FakeRecoveryFenceLedger:
    def __init__(self, *, outage: bool = False):
        self.outage = outage
        self.events = []
        self.before_publish = None
        self._lock = threading.Lock()
        self._accepted = {}

    def append_and_publish(self, event):
        if self.before_publish is not None:
            self.before_publish(event)
        if self.outage:
            raise RecoveryFenceError("synthetic recovery outage")
        with self._lock:
            accepted = self._accepted.get(event.event_id)
            if accepted is not None:
                return accepted
            self.events.append(event)
            accepted = SimpleNamespace(
                sequence=len(self.events),
                record_hash=f"{len(self.events):064x}",
            )
            self._accepted[event.event_id] = accepted
            return accepted


class NamedExtension:
    def __init__(self, extension_id: str, *, outcome=True):
        self.extension_id = extension_id
        self.outcome = outcome
        self.calls = []

    def close_for_sign_out(self, **kwargs):
        self.calls.append(kwargs["operation_id"])
        return self.outcome


def _keyring(key_id: str = "k1", fill: bytes = b"k") -> VersionedHMAC:
    return VersionedHMAC(key_id, {key_id: fill * 32})


def _app(
    sessions_dir,
    *,
    keyring: VersionedHMAC,
    ledger: FakeRecoveryFenceLedger,
    require_account: bool = True,
    drain_timeout_seconds: float = 0.0,
    extensions=(),
):
    cfg = Config()
    cfg.web["require_account"] = require_account
    return create_app(
        cfg,
        sessions_dir=sessions_dir,
        start_background_workers=False,
        start_shopify_sync_worker=False,
        mobile_state_hmac=keyring,
        recovery_fence_ledger=ledger,
        sign_out_drain_timeout_seconds=drain_timeout_seconds,
        sign_out_extensions=tuple(extensions),
    )


def _close_app(app) -> None:
    app.state.jobs.close()
    app.state.throttle.close()
    app.state.users.close()


def _issue(users, email: str, label: str):
    user = users.get_by_email(email)
    if user is None:
        user = users.create(email, "longenough")
    raw, token = users.issue_mobile_api_token(
        user.id,
        label,
        expected_auth_epoch=user.auth_epoch,
    )
    return user, raw, token


def _headers(raw_token: str, key: str = GOOD_KEY) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {raw_token}",
        "Idempotency-Key": key,
    }


def test_sign_out_fences_before_publish_and_revokes_only_current_selector(tmp_path):
    ledger = FakeRecoveryFenceLedger()
    app = _app(tmp_path / "sessions", keyring=_keyring(), ledger=ledger)
    users = app.state.users
    user, first_raw, first = _issue(users, "golfer@example.com", "First")
    _same_user, second_raw, second = _issue(users, user.email, "Second")

    def assert_prepared_before_remote_io(event):
        assert users._conn.in_transaction is False
        assert users._lock.acquire(blocking=False) is True
        users._lock.release()
        guard_lock = app.state.credential_mutation_guard._condition
        assert guard_lock.acquire(blocking=False) is True
        guard_lock.release()
        row = users._conn.execute(
            "SELECT phase FROM mobile_signout_journals"
        ).fetchone()
        token_row = users._conn.execute(
            "SELECT state, fenced_at, revoked_at FROM mobile_api_tokens"
            " WHERE selector = ?",
            (first.selector,),
        ).fetchone()
        assert row["phase"] == "prepared"
        assert token_row["state"] == "fenced"
        assert token_row["fenced_at"] is not None
        assert token_row["revoked_at"] is None

    ledger.before_publish = assert_prepared_before_remote_io
    response = TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(first_raw)
    )

    assert response.status_code == 204
    assert response.content == b""
    assert users.authenticate_mobile_api_principal(first_raw) is None
    assert users.authenticate_mobile_api_principal(second_raw) is not None
    assert len(ledger.events) == 1
    assert ledger.events[0].event_id
    assert ledger.events[0].payload()["selector_hmac"]
    row = users._conn.execute(
        "SELECT phase, recovery_sequence, recovery_record_hash"
        " FROM mobile_signout_journals"
    ).fetchone()
    assert tuple(row) == ("complete", 1, f"{1:064x}")
    assert first.selector != second.selector
    _close_app(app)


def test_sign_out_operation_id_passes_real_ledger_canonical_validation(tmp_path):
    keyring = _real_keyring()
    module, _remote, ledger = _real_ledger(
        tmp_path / "ledger",
        keyring=keyring,
    )
    ledger.append_and_publish(_real_baseline_event(module))
    app = _app(tmp_path / "sessions", keyring=keyring, ledger=ledger)
    _user, raw_token, _token = _issue(
        app.state.users, "golfer@example.com", "Phone"
    )

    response = TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    )

    assert response.status_code == 204
    chain = ledger.load_chain()
    operation_id = chain[-1].event_id
    assert str(uuid.UUID(operation_id)) == operation_id
    assert chain[-1].kind == "token_revoke"
    _close_app(app)


def test_publish_outage_returns_bounded_202_and_exact_retry_finishes(tmp_path):
    ledger = FakeRecoveryFenceLedger(outage=True)
    app = _app(tmp_path / "sessions", keyring=_keyring(), ledger=ledger)
    users = app.state.users
    _user, raw_token, token = _issue(users, "golfer@example.com", "Phone")

    response = TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    )

    assert response.status_code == 202
    assert response.json() == {
        "resource_version": 1,
        "status": "pending",
        "retry_after_seconds": 1,
    }
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"
    assert users.authenticate_mobile_api_principal(raw_token) is None
    row = users._conn.execute(
        "SELECT state, fenced_at, revoked_at FROM mobile_api_tokens"
        " WHERE selector = ?",
        (token.selector,),
    ).fetchone()
    assert row["state"] == "fenced"
    assert row["fenced_at"] is not None
    assert row["revoked_at"] is None
    assert users._conn.execute(
        "SELECT phase FROM mobile_signout_journals"
    ).fetchone()[0] == "prepared"

    ledger.outage = False
    replay = TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    )
    assert replay.status_code == 204
    _close_app(app)


def test_missing_recovery_publisher_fails_before_fencing_the_token(tmp_path):
    app = _app(
        tmp_path / "sessions",
        keyring=_keyring(),
        ledger=None,
    )
    users = app.state.users
    _user, raw_token, token = _issue(users, "golfer@example.com", "Phone")

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    )

    assert response.status_code == 503
    assert response.json()["code"] == "internal_error"
    assert users.authenticate_mobile_api_principal(raw_token) is not None
    row = users._conn.execute(
        "SELECT state, fenced_at, revoked_at FROM mobile_api_tokens"
        " WHERE selector = ?",
        (token.selector,),
    ).fetchone()
    assert tuple(row) == ("active", None, None)
    assert users._conn.execute(
        "SELECT COUNT(*) FROM mobile_signout_journals"
    ).fetchone()[0] == 0
    _close_app(app)


def test_sign_out_stays_pending_until_an_older_lease_drains(tmp_path):
    ledger = FakeRecoveryFenceLedger()
    app = _app(tmp_path / "sessions", keyring=_keyring(), ledger=ledger)
    users = app.state.users
    user, raw_token, token = _issue(users, "golfer@example.com", "Phone")
    context = MobileAuthContext(
        user=user,
        via_bearer=True,
        selector=token.selector,
        auth_epoch=user.auth_epoch,
    )
    earlier = app.state.credential_mutation_guard.admit(context)

    pending = TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    )

    assert pending.status_code == 202
    assert earlier.cancellation_requested is True
    assert users._conn.execute(
        "SELECT phase FROM mobile_signout_journals"
    ).fetchone()[0] == "recovery_fenced"
    earlier.release()
    assert TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    ).status_code == 204
    _close_app(app)


@pytest.mark.parametrize("first_outcome", [False, RuntimeError("synthetic")])
def test_sign_out_extension_pending_or_failure_is_replayed_without_leaking(
    tmp_path, first_outcome
):
    class Extension:
        extension_id = "test.retryable-cleanup.v1"

        def __init__(self):
            self.outcomes = [first_outcome, True]
            self.calls = []

        def close_for_sign_out(self, **kwargs):
            self.calls.append(
                (kwargs["operation_id"], kwargs["user_id"], kwargs["selector"])
            )
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    extension = Extension()
    app = _app(
        tmp_path / "sessions",
        keyring=_keyring(),
        ledger=FakeRecoveryFenceLedger(),
        extensions=(extension,),
    )
    users = app.state.users
    _user, raw_token, token = _issue(users, "golfer@example.com", "Phone")

    pending = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    )
    assert pending.status_code == 202
    assert "synthetic" not in pending.text
    assert users._conn.execute(
        "SELECT phase FROM mobile_signout_journals"
    ).fetchone()[0] == "recovery_fenced"

    complete = TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    )
    assert complete.status_code == 204
    assert len(extension.calls) == 2
    assert all(call[2] == token.selector for call in extension.calls)
    _close_app(app)


def test_sign_out_persists_the_exact_ordered_extension_contract_digest(tmp_path):
    extensions = (
        NamedExtension("push.current-selector.v1"),
        NamedExtension("export.selector-cleanup.v1"),
    )
    app = _app(
        tmp_path / "sessions",
        keyring=_keyring(),
        ledger=FakeRecoveryFenceLedger(outage=True),
        extensions=extensions,
    )
    _user, raw_token, _token = _issue(
        app.state.users, "golfer@example.com", "Phone"
    )
    assert TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    ).status_code == 202

    row = app.state.users._conn.execute(
        "SELECT extension_contract_version, extension_contract_sha256"
        " FROM mobile_signout_journals"
    ).fetchone()
    canonical = json.dumps(
        {
            "extensions": [extension.extension_id for extension in extensions],
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    assert row["extension_contract_version"] == 1
    assert row["extension_contract_sha256"] == hashlib.sha256(
        canonical
    ).hexdigest()
    _close_app(app)


@pytest.mark.parametrize(
    "extension_ids",
    [
        ("",),
        ("Uppercase.v1",),
        ("x" * 65,),
        ("push.v1", "push.v1"),
    ],
)
def test_sign_out_rejects_invalid_or_duplicate_extension_ids(
    tmp_path, extension_ids
):
    try:
        app = _app(
            tmp_path / "sessions",
            keyring=_keyring(),
            ledger=FakeRecoveryFenceLedger(),
            extensions=tuple(NamedExtension(value) for value in extension_ids),
        )
    except ValueError as exc:
        assert "extension" in str(exc).lower()
    else:
        _close_app(app)
        pytest.fail("Invalid sign-out extension IDs were accepted.")


@pytest.mark.parametrize(
    "resumed_ids",
    [
        (),
        ("push.changed.v1", "export.cleanup.v1"),
        ("export.cleanup.v1", "push.cleanup.v1"),
    ],
)
def test_startup_fails_closed_when_required_extension_membership_drifts(
    tmp_path, resumed_ids
):
    sessions_dir = tmp_path / "sessions"
    keyring = _keyring()
    original_ids = ("push.cleanup.v1", "export.cleanup.v1")
    first = _app(
        sessions_dir,
        keyring=keyring,
        ledger=FakeRecoveryFenceLedger(outage=True),
        extensions=tuple(NamedExtension(value) for value in original_ids),
    )
    _user, raw_token, _token = _issue(
        first.state.users, "golfer@example.com", "Phone"
    )
    assert TestClient(first).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    ).status_code == 202
    _close_app(first)

    try:
        resumed = _app(
            sessions_dir,
            keyring=keyring,
            ledger=FakeRecoveryFenceLedger(),
            require_account=False,
            extensions=tuple(NamedExtension(value) for value in resumed_ids),
        )
    except RuntimeError as exc:
        assert "extension contract" in str(exc).lower()
    else:
        _close_app(resumed)
        pytest.fail("Startup skipped a durable sign-out extension contract.")

    reopened = UserStore(
        sessions_dir / "swinglab.db",
        mobile_state_hmac=keyring,
    )
    try:
        assert reopened._conn.execute(
            "SELECT phase FROM mobile_signout_journals"
        ).fetchone()[0] == "prepared"
    finally:
        reopened.close()


def test_pending_replay_fails_closed_when_extension_contract_changes(tmp_path):
    keyring = _keyring()
    app = _app(
        tmp_path / "sessions",
        keyring=keyring,
        ledger=FakeRecoveryFenceLedger(outage=True),
        extensions=(NamedExtension("push.cleanup.v1"),),
    )
    users = app.state.users
    _user, raw_token, _token = _issue(users, "golfer@example.com", "Phone")
    assert TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    ).status_code == 202

    mismatched = MobileSignOutService(
        users,
        app.state.credential_mutation_guard,
        keyring=keyring,
        recovery_fence_ledger=FakeRecoveryFenceLedger(),
        drain_timeout_seconds=0.0,
        extensions=(NamedExtension("push.changed.v1"),),
    )
    with pytest.raises(RuntimeError, match="extension contract"):
        mismatched.sign_out(raw_token, GOOD_KEY)
    assert users._conn.execute(
        "SELECT phase FROM mobile_signout_journals"
    ).fetchone()[0] == "prepared"
    _close_app(app)


def test_concurrent_exact_replays_serialize_hooks_until_terminal_204(tmp_path):
    class BarrierExtension:
        extension_id = "test.concurrent-cleanup.v1"

        def __init__(self):
            self._barrier = threading.Barrier(2)
            self._lock = threading.Lock()
            self.calls = 0
            self.in_flight = 0
            self.max_in_flight = 0

        def close_for_sign_out(self, **_kwargs):
            with self._lock:
                self.calls += 1
                call_number = self.calls
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                try:
                    self._barrier.wait(timeout=0.4)
                except threading.BrokenBarrierError:
                    pass
                if call_number == 2:
                    time.sleep(0.2)
                return True
            finally:
                with self._lock:
                    self.in_flight -= 1

        def current_in_flight(self):
            with self._lock:
                return self.in_flight

    extension = BarrierExtension()
    ledger = FakeRecoveryFenceLedger(outage=True)
    app = _app(
        tmp_path / "sessions",
        keyring=_keyring(),
        ledger=ledger,
        extensions=(extension,),
    )
    _user, raw_token, _token = _issue(
        app.state.users, "golfer@example.com", "Phone"
    )
    assert TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    ).status_code == 202
    ledger.outage = False
    start = threading.Barrier(3)

    def replay():
        start.wait(timeout=2)
        response = TestClient(app).post(
            "/api/v1/auth/sign-out", headers=_headers(raw_token)
        )
        return response.status_code, extension.current_in_flight()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(replay) for _index in range(2)]
        start.wait(timeout=2)
        results = [future.result(timeout=5) for future in futures]

    assert results == [(204, 0), (204, 0)]
    assert extension.calls == 1
    assert extension.max_in_flight == 1
    assert extension.in_flight == 0
    assert app.state.sign_out_service.operation_lock_count == 0
    _close_app(app)


def test_lost_204_replays_before_normal_auth_but_conflicts_disclose_nothing(tmp_path):
    ledger = FakeRecoveryFenceLedger()
    app = _app(tmp_path / "sessions", keyring=_keyring(), ledger=ledger)
    users = app.state.users
    user, signed_out_raw, _signed_out = _issue(
        users, "golfer@example.com", "Signed out"
    )
    _same_user, active_raw, _active = _issue(users, user.email, "Still active")
    client = TestClient(app)

    assert client.post(
        "/api/v1/auth/sign-out", headers=_headers(signed_out_raw)
    ).status_code == 204
    assert client.post(
        "/api/v1/auth/sign-out", headers=_headers(signed_out_raw)
    ).status_code == 204

    wrong_key = client.post(
        "/api/v1/auth/sign-out",
        headers=_headers(signed_out_raw, OTHER_KEY),
    )
    reused_key = client.post(
        "/api/v1/auth/sign-out", headers=_headers(active_raw)
    )
    for response in (wrong_key, reused_key):
        assert response.status_code == 401
        assert response.json()["code"] == "http_401"
        assert response.json()["message"] == "Invalid mobile access token."
        assert "operation" not in response.text.lower()
    _close_app(app)


@pytest.mark.parametrize(
    "phase",
    ["prepared", "recovery_fenced", "extensions_closed", "token_revoked"],
)
def test_startup_resumes_every_nonterminal_phase_with_feature_flags_off(
    tmp_path, phase
):
    sessions_dir = tmp_path / phase
    keyring = _keyring()
    outage = FakeRecoveryFenceLedger(outage=True)
    first_app = _app(sessions_dir, keyring=keyring, ledger=outage)
    users = first_app.state.users
    _user, raw_token, token = _issue(users, "golfer@example.com", "Phone")
    assert TestClient(first_app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    ).status_code == 202

    if phase != "prepared":
        users._conn.execute(
            "UPDATE mobile_signout_journals SET phase = ?,"
            " recovery_sequence = 7, recovery_record_hash = ?",
            (phase, "7" * 64),
        )
    if phase == "token_revoked":
        users._conn.execute(
            "UPDATE mobile_api_tokens SET revoked_at = 1234"
            " WHERE selector = ?",
            (token.selector,),
        )
    users._conn.commit()
    _close_app(first_app)

    resumed_ledger = FakeRecoveryFenceLedger()
    resumed = _app(
        sessions_dir,
        keyring=keyring,
        ledger=resumed_ledger,
        require_account=False,
    )
    row = resumed.state.users._conn.execute(
        "SELECT phase FROM mobile_signout_journals"
    ).fetchone()
    assert row["phase"] == "complete"
    token_row = resumed.state.users._conn.execute(
        "SELECT revoked_at FROM mobile_api_tokens WHERE selector = ?",
        (token.selector,),
    ).fetchone()
    assert token_row["revoked_at"] is not None
    assert resumed.state.users.authenticate_mobile_api_principal(raw_token) is None
    assert len(resumed_ledger.events) == (1 if phase == "prepared" else 0)
    _close_app(resumed)


def test_startup_fails_closed_when_a_pending_journal_key_is_missing(tmp_path):
    sessions_dir = tmp_path / "sessions"
    first = _app(
        sessions_dir,
        keyring=_keyring("retired", b"r"),
        ledger=FakeRecoveryFenceLedger(outage=True),
    )
    _user, raw_token, _token = _issue(
        first.state.users, "golfer@example.com", "Phone"
    )
    assert TestClient(first).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    ).status_code == 202
    _close_app(first)

    with pytest.raises(RuntimeError, match="retired"):
        _app(
            sessions_dir,
            keyring=_keyring("current", b"c"),
            ledger=FakeRecoveryFenceLedger(),
            require_account=False,
        )


def test_sign_out_requires_one_strict_128_bit_hex_idempotency_key(tmp_path):
    app = _app(
        tmp_path / "sessions",
        keyring=_keyring(),
        ledger=FakeRecoveryFenceLedger(),
    )
    _user, raw_token, _token = _issue(
        app.state.users, "golfer@example.com", "Phone"
    )
    client = TestClient(app)
    invalid = (None, "", "0" * 31, "0" * 33, "g" * 32, GOOD_KEY + "," + OTHER_KEY)
    for key in invalid:
        headers = {"Authorization": f"Bearer {raw_token}"}
        if key is not None:
            headers["Idempotency-Key"] = key
        response = client.post("/api/v1/auth/sign-out", headers=headers)
        assert response.status_code == 400
        assert response.json()["code"] == "http_400"
        assert response.json()["message"] == "Invalid Idempotency-Key."
    assert app.state.users.authenticate_mobile_api_principal(raw_token) is not None
    _close_app(app)


def test_sign_out_is_bearer_only_and_never_falls_back_to_a_browser_cookie(
    tmp_path,
):
    app = _app(
        tmp_path / "sessions",
        keyring=_keyring(),
        ledger=FakeRecoveryFenceLedger(),
    )
    browser = TestClient(app)
    assert browser.post(
        "/signup",
        data={"email": "golfer@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    cookie_only = browser.post(
        "/api/v1/auth/sign-out",
        headers={"Idempotency-Key": GOOD_KEY},
    )
    assert cookie_only.status_code == 401
    assert cookie_only.json()["code"] == "bearer_required"

    invalid_bearer = browser.post(
        "/api/v1/auth/sign-out",
        headers={
            "Authorization": "Bearer ciat_not-a-valid-token",
            "Idempotency-Key": GOOD_KEY,
        },
    )
    assert invalid_bearer.status_code == 401
    assert invalid_bearer.json()["code"] == "http_401"
    assert invalid_bearer.json()["message"] == "Invalid mobile access token."
    assert app.state.users.get_by_email("golfer@example.com") is not None
    _close_app(app)


def test_sign_out_journal_persists_no_raw_selector_token_or_idempotency_secret(
    tmp_path,
):
    app = _app(
        tmp_path / "sessions",
        keyring=_keyring(),
        ledger=FakeRecoveryFenceLedger(outage=True),
    )
    users = app.state.users
    _user, raw_token, token = _issue(users, "golfer@example.com", "Phone")
    assert TestClient(app).post(
        "/api/v1/auth/sign-out", headers=_headers(raw_token)
    ).status_code == 202

    columns = {
        row["name"]
        for row in users._conn.execute(
            "PRAGMA table_info(mobile_signout_journals)"
        )
    }
    assert "selector" not in columns
    row = users._conn.execute(
        "SELECT * FROM mobile_signout_journals"
    ).fetchone()
    durable_values = {str(value) for value in row if value is not None}
    assert token.selector not in durable_values
    assert raw_token not in durable_values
    assert raw_token.split(".", 1)[1] not in durable_values
    assert GOOD_KEY not in durable_values
    assert row["selector_hmac_key_id"] == "k1"
    assert len(row["selector_hmac"]) == 64
    assert row["token_verifier_hmac_key_id"] == "k1"
    assert len(row["token_verifier_hmac"]) == 64
    assert row["idempotency_hmac_key_id"] == "k1"
    assert len(row["idempotency_hmac"]) == 64
    assert len(row["request_hash"]) == 64
    _close_app(app)
