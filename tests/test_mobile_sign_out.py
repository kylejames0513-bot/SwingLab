"""Crash-safe bearer-only sign-out and recovery-fence replay."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from swinglab.api.auth import MobileAuthContext
from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.mobile_schema import VersionedHMAC
from swinglab.web.recovery_fence_ledger import RecoveryFenceError


GOOD_KEY = "00112233445566778899aabbccddeeff"
OTHER_KEY = "ffeeddccbbaa99887766554433221100"


class FakeRecoveryFenceLedger:
    def __init__(self, *, outage: bool = False):
        self.outage = outage
        self.events = []
        self.before_publish = None

    def append_and_publish(self, event):
        if self.before_publish is not None:
            self.before_publish(event)
        if self.outage:
            raise RecoveryFenceError("synthetic recovery outage")
        self.events.append(event)
        return SimpleNamespace(
            sequence=len(self.events),
            record_hash=f"{len(self.events):064x}",
        )


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
