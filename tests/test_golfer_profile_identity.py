"""Focused coverage for CaddieInsight-owned golfer profile identity."""

from __future__ import annotations

import sqlite3

import pytest

from swinglab.web.users import UserStore


@pytest.fixture
def store(tmp_path):
    user_store = UserStore(tmp_path / "users.db")
    try:
        yield user_store
    finally:
        user_store._conn.close()


def _profile_values(**overrides):
    values = {
        "experience_mode": "improve",
        "handicap_range": "",
        "primary_goal": "consistency",
        "practice_minutes": 20,
        "sessions_per_week": 2,
        "handedness": "right",
        "camera_angle": "face-on",
        "preferred_club": "",
    }
    values.update(overrides)
    return values


def test_fresh_schema_has_nullable_display_name(tmp_path):
    user_store = UserStore(tmp_path / "fresh.db")
    try:
        columns = {
            row["name"]: row
            for row in user_store._conn.execute(
                "PRAGMA table_info(golfer_profiles)"
            )
        }
        assert columns["display_name"]["type"] == "TEXT"
        assert columns["display_name"]["notnull"] == 0
    finally:
        user_store._conn.close()


def test_legacy_profile_schema_migrates_once_and_preserves_rows(tmp_path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE golfer_profiles (
            user_id TEXT PRIMARY KEY,
            experience_mode TEXT NOT NULL DEFAULT 'improve',
            handicap_range TEXT,
            primary_goal TEXT,
            practice_minutes INTEGER NOT NULL DEFAULT 20,
            sessions_per_week INTEGER NOT NULL DEFAULT 2,
            handedness TEXT NOT NULL DEFAULT 'right',
            camera_angle TEXT NOT NULL DEFAULT 'face-on',
            preferred_club TEXT,
            reduced_motion INTEGER NOT NULL DEFAULT 0,
            marketing_email_opt_in INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        INSERT INTO golfer_profiles (
            user_id, handicap_range, primary_goal, created_at, updated_at
        ) VALUES ('legacy-user', '10_to_14', 'tempo', 10, 20);
        """
    )
    connection.close()

    first = UserStore(database)
    try:
        profile = first.get_golfer_profile("legacy-user")
        assert profile is not None
        assert profile.display_name is None
        assert profile.primary_goal == "tempo"
        assert not profile.is_complete
    finally:
        first._conn.close()

    second = UserStore(database)
    try:
        display_columns = [
            row
            for row in second._conn.execute(
                "PRAGMA table_info(golfer_profiles)"
            )
            if row["name"] == "display_name"
        ]
        assert len(display_columns) == 1
        assert second.get_golfer_profile("legacy-user") is not None
    finally:
        second._conn.close()


def test_ensure_profile_is_claimed_only_and_idempotent(store):
    password_user = store.create("password@example.com", "longenough")
    first = store.ensure_golfer_profile(password_user.id, now=100)
    second = store.ensure_golfer_profile(password_user.id, now=200)

    assert first == second
    assert first.created_at == 100
    assert first.updated_at == 100
    assert first.display_name is None
    assert first.primary_goal is None
    assert not first.is_complete

    verified_user = store.verify_email_signin("verified@example.com")
    assert not verified_user.has_password
    assert verified_user.email_verified
    assert store.ensure_golfer_profile(verified_user.id).user_id == verified_user.id


def test_ensure_profile_refuses_unclaimed_shopify_stub(store):
    stub = store.upsert_store_customer(
        "stub@example.com", "7001", updated_at=100
    )
    assert stub is not None
    assert not stub.claimed

    with pytest.raises(ValueError, match="Verify your account"):
        store.ensure_golfer_profile(stub.id)
    with pytest.raises(ValueError, match="Verify your account"):
        store.upsert_golfer_profile(
            stub.id,
            display_name="Not the owner",
            **_profile_values(),
        )

    assert store.get_golfer_profile(stub.id) is None

    authenticated = store.link_shopify_customer_account(
        stub.id,
        subject="gid://shopify/Customer/7001",
        customer_id="7001",
        authenticated=True,
    )
    assert authenticated.claimed
    profile = store.ensure_golfer_profile(stub.id)
    assert profile.user_id == stub.id


def test_display_name_normalizes_and_omission_preserves_it(store):
    user = store.create("golfer@example.com", "longenough")
    profile = store.upsert_golfer_profile(
        user.id,
        display_name="  Ｋyle   O'Neil  ",
        **_profile_values(),
    )

    assert profile.display_name == "Kyle O'Neil"
    assert profile.handicap_range is None
    assert profile.preferred_club is None
    assert profile.is_complete

    preserved = store.upsert_golfer_profile(
        user.id,
        **_profile_values(primary_goal="tempo"),
    )
    assert preserved.display_name == "Kyle O'Neil"
    assert preserved.primary_goal == "tempo"
    assert preserved.is_complete


@pytest.mark.parametrize(
    "display_name",
    (
        None,
        "",
        "   ",
        "Kyle\nMahon",
        "Kyle\tMahon",
        "Kyle\x00Mahon",
        "Kyle\u2028Mahon",
        "x" * 51,
    ),
)
def test_display_name_rejects_blank_control_line_and_oversize_values(
    store, display_name
):
    user = store.create(f"golfer-{abs(hash(str(display_name)))}@example.com", "longenough")

    with pytest.raises(ValueError):
        store.upsert_golfer_profile(
            user.id,
            display_name=display_name,
            **_profile_values(),
        )


def test_legacy_profile_save_allows_missing_goal_but_stays_incomplete(store):
    user = store.create("goal@example.com", "longenough")

    profile = store.upsert_golfer_profile(
        user.id,
        display_name="Kyle",
        **_profile_values(primary_goal=""),
    )

    assert profile.primary_goal is None
    assert not profile.is_complete
