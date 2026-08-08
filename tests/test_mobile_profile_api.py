"""Guarded native profile writes for Gate 4B."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from swinglab.api.contracts import ProfileUpdateRequest
from swinglab.config import Config
from swinglab.web.app import create_app
from tests.test_mobile_api_tokens import bearer, issue_token, profile_payload


def _app_client(tmp_path, *, profile_writes_enabled: bool = True):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_resources_enabled"] = True
    cfg.web["mobile_profile_writes_enabled"] = profile_writes_enabled
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
    )
    client = TestClient(app)
    user = app.state.users.create(
        "profile-writer@example.com",
        "longenough",
        email_verified=True,
    )
    login = client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    return app, client, user


def _close(app) -> None:
    for resource in (app.state.jobs, app.state.users, app.state.throttle):
        resource.close()


def _mobile_profile_body(user, **overrides) -> dict:
    body = {
        **profile_payload(),
        "expected_history_epoch": user.history_epoch,
    }
    body.update(overrides)
    return body


def _profile_count(app) -> int:
    return int(
        app.state.users._conn.execute(
            "SELECT COUNT(*) FROM golfer_profiles"
        ).fetchone()[0]
    )


def test_profile_update_request_contract_is_closed_and_normalized():
    """Catches open/optional native write fields drifting from the browser contract."""

    valid = ProfileUpdateRequest.model_validate(
        {
            "display_name": "  Kyle\u00a0Golfer  ",
            "experience_mode": "improve",
            "handicap_range": "20_to_29",
            "primary_goal": "consistency",
            "practice_minutes": 20,
            "sessions_per_week": 2,
            "handedness": "right",
            "camera_angle": "face-on",
            "preferred_club": "driver",
            "reduced_motion": False,
            "marketing_email_opt_in": False,
            "expected_history_epoch": 0,
        }
    )
    assert valid.display_name == "Kyle Golfer"
    assert valid.marketing_email_opt_in is False

    # Length is measured after NFKC/whitespace collapse, not on the raw wire value.
    padded = ProfileUpdateRequest.model_validate(
        {
            **valid.model_dump(),
            "display_name": "  " + ("x" * 50) + "  ",
        }
    )
    assert padded.display_name == "x" * 50
    assert len(padded.display_name) == 50

    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {
                **valid.model_dump(),
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {k: v for k, v in valid.model_dump().items() if k != "display_name"}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "display_name": "a\nb"}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "display_name": "x" * 51}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "display_name": "  " + ("y" * 51) + "  "}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "practice_minutes": 15}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "preferred_club": None}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "primary_goal": None}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "reduced_motion": 1}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "expected_history_epoch": -1}
        )
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(
            {**valid.model_dump(), "expected_history_epoch": True}
        )


def test_mobile_profile_flag_off_is_404_before_auth_body_or_writes(tmp_path):
    """Catches a disabled write route authenticating, validating, or mutating."""

    app, client, user = _app_client(tmp_path, profile_writes_enabled=False)
    try:
        before = _profile_count(app)
        token = issue_token(client, "Profile phone")["token"]

        cases = [
            client.put("/api/v1/mobile/profile"),
            client.put(
                "/api/v1/mobile/profile",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            ),
            client.put(
                "/api/v1/mobile/profile",
                json={"incomplete": True},
            ),
            client.put(
                "/api/v1/mobile/profile",
                json=_mobile_profile_body(user),
            ),
            client.put(
                "/api/v1/mobile/profile",
                headers=bearer("ciat_not-a-real-token"),
                json=_mobile_profile_body(user),
            ),
            client.put(
                "/api/v1/mobile/profile",
                headers=bearer(token),
                json=_mobile_profile_body(user),
            ),
        ]

        for response in cases:
            assert response.status_code == 404
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["pragma"] == "no-cache"
            assert response.json() == {
                "resource_version": 1,
                "code": "not_found",
                "message": "Mobile profile writes are not enabled.",
                "retryable": False,
                "reference_id": None,
            }
        assert _profile_count(app) == before
        assert app.state.users.get_golfer_profile(user.id) is None
    finally:
        _close(app)


def test_mobile_profile_write_is_strict_bearer_only(tmp_path):
    """Catches cookie auth or bad Authorization falling back to a session cookie."""

    app, client, user = _app_client(tmp_path)
    try:
        before = _profile_count(app)
        body = _mobile_profile_body(user)

        cookie_only = client.put("/api/v1/mobile/profile", json=body)
        assert cookie_only.status_code == 401
        assert cookie_only.json()["code"] == "bearer_required"
        assert cookie_only.headers["www-authenticate"] == "Bearer"
        assert cookie_only.headers["cache-control"] == "no-store"

        invalid = client.put(
            "/api/v1/mobile/profile",
            headers=bearer("ciat_this-is-not-a-valid-token"),
            json=body,
        )
        assert invalid.status_code == 401
        assert invalid.json()["code"] == "http_401"
        assert invalid.json()["message"] == "Invalid mobile access token."
        assert invalid.headers["cache-control"] == "no-store"
        assert _profile_count(app) == before
    finally:
        _close(app)


def test_mobile_profile_success_normalizes_and_preserves_completion_parity(tmp_path):
    """Catches incomplete native saves or marketing consent affecting is_complete."""

    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Profile phone")["token"]
        body = _mobile_profile_body(
            user,
            display_name="  Riley\u00a0Golfer  ",
            marketing_email_opt_in=False,
            preferred_club="iron",
            primary_goal="tempo",
        )

        first = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=body,
        )
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        assert first.headers["pragma"] == "no-cache"
        payload = first.json()
        assert payload["resource_version"] == 1
        profile = payload["profile"]
        assert profile["display_name"] == "Riley Golfer"
        assert profile["primary_goal"] == "tempo"
        assert profile["preferred_club"] == "iron"
        assert profile["marketing_email_opt_in"] is False
        assert profile["is_complete"] is True
        assert profile["reduced_motion"] is False

        stored = app.state.users.get_golfer_profile(user.id)
        assert stored is not None
        assert stored.display_name == "Riley Golfer"
        assert stored.is_complete is True
        assert stored.marketing_email_opt_in is False

        # Exact replay of a complete write remains successful and stable.
        second = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=body,
        )
        assert second.status_code == 200
        assert second.json()["profile"]["display_name"] == "Riley Golfer"
        assert second.json()["profile"]["is_complete"] is True

        # Independent marketing consent may be true without changing completion.
        opted_in = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(
                user,
                display_name="Riley Golfer",
                marketing_email_opt_in=True,
                preferred_club="iron",
                primary_goal="tempo",
            ),
        )
        assert opted_in.status_code == 200
        opted_profile = opted_in.json()["profile"]
        assert opted_profile["marketing_email_opt_in"] is True
        assert opted_profile["is_complete"] is True
    finally:
        _close(app)


def test_mobile_profile_rejects_schema_violations_with_typed_422(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Profile phone")["token"]
        before = _profile_count(app)
        response = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(user, practice_minutes=15, extra=True),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
        assert response.headers["cache-control"] == "no-store"
        assert _profile_count(app) == before
    finally:
        _close(app)


def test_mobile_profile_revoked_selector_cannot_write(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        issued = issue_token(client, "Profile phone")
        token = issued["token"]
        selector = issued["device"]["selector"]
        assert app.state.users.revoke_mobile_api_token(user.id, selector)

        response = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(user),
        )
        assert response.status_code == 401
        assert response.json()["message"] == "Invalid mobile access token."
        assert response.headers["cache-control"] == "no-store"
        assert app.state.users.get_golfer_profile(user.id) is None
    finally:
        _close(app)


def test_mobile_profile_final_write_revocation_never_upserts(tmp_path, monkeypatch):
    """Catches a lease writing after its selector is revoked at the final fence."""

    app, client, user = _app_client(tmp_path)
    try:
        issued = issue_token(client, "Profile phone")
        token = issued["token"]
        selector = issued["device"]["selector"]
        from swinglab.web.credential_mutations import CredentialMutationLease

        original = CredentialMutationLease.validate_locked

        def revoke_then_validate(self, user_store, *, now=None):
            observed = 1_700_000_000.0 if now is None else float(now)
            user_store._conn.execute(
                "UPDATE mobile_api_tokens SET revoked_at = ?"
                " WHERE selector = ? AND user_id = ?",
                (observed, selector, user.id),
            )
            return original(self, user_store, now=now)

        monkeypatch.setattr(
            CredentialMutationLease, "validate_locked", revoke_then_validate
        )

        response = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(user),
        )
        assert response.status_code == 401
        assert response.json()["message"] == "Invalid mobile access token."
        assert app.state.users.get_golfer_profile(user.id) is None
    finally:
        _close(app)


def test_mobile_profile_final_write_auth_epoch_bump_never_upserts(
    tmp_path, monkeypatch
):
    """Catches a lease writing after auth_epoch advances at the final fence."""

    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Profile phone")["token"]
        from swinglab.web.credential_mutations import CredentialMutationLease

        original = CredentialMutationLease.validate_locked

        def bump_auth_epoch_then_validate(self, user_store, *, now=None):
            user_store._conn.execute(
                "UPDATE users SET auth_epoch = auth_epoch + 1 WHERE id = ?",
                (user.id,),
            )
            return original(self, user_store, now=now)

        monkeypatch.setattr(
            CredentialMutationLease,
            "validate_locked",
            bump_auth_epoch_then_validate,
        )

        response = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(user),
        )
        assert response.status_code == 401
        assert response.json()["message"] == "Invalid mobile access token."
        assert response.headers["cache-control"] == "no-store"
        assert app.state.users.get_golfer_profile(user.id) is None
    finally:
        _close(app)


def test_mobile_profile_history_epoch_conflict_is_typed_409(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Profile phone")["token"]
        response = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(user, expected_history_epoch=user.history_epoch + 1),
        )
        assert response.status_code == 409
        assert response.json() == {
            "resource_version": 1,
            "code": "history_epoch_conflict",
            "message": "Swing history changed while this request was in progress.",
            "retryable": False,
            "reference_id": None,
        }
        assert response.headers["cache-control"] == "no-store"
        assert app.state.users.get_golfer_profile(user.id) is None
    finally:
        _close(app)


def test_mobile_profile_history_reset_race_never_recreates_profile(
    tmp_path, monkeypatch
):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Profile phone")["token"]
        users = app.state.users
        original = users._assert_history_epoch_locked

        def bump_epoch_then_assert(
            user_id, expected_history_epoch, *, session_ids=()
        ):
            users._conn.execute(
                "UPDATE users SET history_epoch = history_epoch + 1"
                " WHERE id = ?",
                (user_id,),
            )
            return original(
                user_id,
                expected_history_epoch,
                session_ids=session_ids,
            )

        monkeypatch.setattr(
            users, "_assert_history_epoch_locked", bump_epoch_then_assert
        )

        response = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(user),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "history_epoch_conflict"
        assert users.get_golfer_profile(user.id) is None
    finally:
        _close(app)


def test_mobile_profile_deletion_race_never_recreates_profile(tmp_path, monkeypatch):
    app, client, user = _app_client(tmp_path)
    try:
        token = issue_token(client, "Profile phone")["token"]
        users = app.state.users
        guard = app.state.credential_mutation_guard
        original_admit = guard.admit

        def admit_then_delete(context):
            lease = original_admit(context)
            users.delete_user(context.user.id)
            return lease

        monkeypatch.setattr(guard, "admit", admit_then_delete)

        response = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(user),
        )
        assert response.status_code == 401
        assert response.json()["message"] == "Invalid mobile access token."
        assert response.headers["cache-control"] == "no-store"
        assert users.get_golfer_profile(user.id) is None
        assert users.get(user.id) is None
    finally:
        _close(app)


def test_mobile_profile_write_never_mutates_another_account(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        other = app.state.users.create(
            "other-profile@example.com",
            "longenough",
            email_verified=True,
        )
        app.state.users.upsert_golfer_profile(
            other.id,
            display_name="Other Golfer",
            experience_mode="start",
            handicap_range="under_10",
            primary_goal="balance",
            practice_minutes=10,
            sessions_per_week=1,
            handedness="left",
            camera_angle="dtl",
            preferred_club="wedge",
            reduced_motion=True,
            marketing_email_opt_in=True,
        )
        token = issue_token(client, "Profile phone")["token"]

        response = client.put(
            "/api/v1/mobile/profile",
            headers=bearer(token),
            json=_mobile_profile_body(user, display_name="Owner Golfer"),
        )
        assert response.status_code == 200
        assert response.json()["profile"]["display_name"] == "Owner Golfer"

        other_profile = app.state.users.get_golfer_profile(other.id)
        assert other_profile is not None
        assert other_profile.display_name == "Other Golfer"
        assert other_profile.preferred_club == "wedge"
        assert other_profile.marketing_email_opt_in is True
    finally:
        _close(app)


def test_legacy_profile_put_remains_byte_compatible_beside_native_route(tmp_path):
    """Catches the additive native route changing legacy bodies or ordering."""

    app, client, _user = _app_client(tmp_path)
    try:
        legacy_v1_payload = {
            "experience_mode": "improve",
            "handicap_range": "20_to_29",
            "primary_goal": "",
            "practice_minutes": 20,
            "sessions_per_week": 2,
            "handedness": "right",
            "camera_angle": "face-on",
            "preferred_club": "iron",
            "reduced_motion": False,
            "marketing_email_opt_in": False,
        }
        response = client.put("/api/v1/profile", json=legacy_v1_payload)
        assert response.status_code == 200
        assert response.json()["profile"]["display_name"] is None
        assert response.json()["profile"]["primary_goal"] is None
        assert response.json()["profile"]["is_complete"] is False
        assert "expected_history_epoch" not in response.json()["profile"]
    finally:
        _close(app)


def test_openapi_exposes_mobile_profile_put_with_generated_request_model(tmp_path):
    app, _client, _user = _app_client(tmp_path)
    try:
        schema = app.openapi()
        path = schema["paths"]["/api/v1/mobile/profile"]["put"]
        assert path["security"] == [{"MobileBearer": []}]
        request_schema = path["requestBody"]["content"]["application/json"]["schema"]
        assert request_schema["title"] == "ProfileUpdateRequest"
        assert request_schema["additionalProperties"] is False
        assert "display_name" in request_schema["required"]
        assert "primary_goal" in request_schema["required"]
        assert "preferred_club" in request_schema["required"]
        assert "expected_history_epoch" in request_schema["required"]
        assert "ProfileResponse" in schema["components"]["schemas"]
        assert path["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ProfileResponse"
        }
    finally:
        _close(app)


def test_capabilities_expose_independent_profile_writes_flag(tmp_path):
    app, client, _user = _app_client(tmp_path, profile_writes_enabled=True)
    try:
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert response.json()["capabilities"]["features"]["profile_writes"] is True
    finally:
        _close(app)

    app_off, client_off, _user_off = _app_client(
        tmp_path / "flag-off", profile_writes_enabled=False
    )
    try:
        response = client_off.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert response.json()["capabilities"]["features"]["profile_writes"] is False
    finally:
        _close(app_off)
