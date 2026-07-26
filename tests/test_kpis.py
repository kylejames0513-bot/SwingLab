"""The five business KPIs (swinglab.kpis) on seeded SQLite fixtures.

The math is pinned on exact numerators/denominators including the window
edges (account at the cutoff, first report at exactly 7 days, grant at
exactly 30 days), and the honesty rule is pinned everywhere: a metric the
data cannot support is None WITH a reason — never a fabricated number —
while a true zero (nobody filmed this week) is 0, not None.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from swinglab.config import Config
from swinglab.kpis import compute_kpis, format_value
from swinglab.web.users import UserStore

DAY = 86400
NOW = 1_700_000_000.0  # fixed "now" so window edges are exact


def accounts_cfg() -> Config:
    cfg = Config()
    cfg.web["require_account"] = True
    return cfg


def make_db(tmp_path):
    """A database shaped like the web app's: UserStore's tables (users,
    ledgers) plus a minimal jobs table (kpis is duck-typed on the columns
    it reads, like trends.py is on jobs)."""
    db = tmp_path / "swinglab.db"
    store = UserStore(db)
    with store._lock:
        store._conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,"
            " status TEXT NOT NULL, created_at REAL NOT NULL, user_id TEXT)"
        )
        store._conn.commit()
    return db, store


def add_user(store, email, created_at, plan="free", status="none"):
    user = store.create(email, "longenough")
    with store._lock:
        store._conn.execute(
            "UPDATE users SET created_at = ?, plan = ?, subscription_status = ?"
            " WHERE id = ?",
            (created_at, plan, status, user.id),
        )
        store._conn.commit()
    return user.id


def add_job(store, job_id, user_id, created_at, status="done"):
    with store._lock:
        store._conn.execute(
            "INSERT INTO jobs (id, status, created_at, user_id)"
            " VALUES (?, ?, ?, ?)",
            (job_id, status, created_at, user_id),
        )
        store._conn.commit()


def set_grant_time(store, order_id, applied_at):
    with store._lock:
        store._conn.execute(
            "UPDATE shopify_orders SET applied_at = ? WHERE order_id = ?",
            (applied_at, order_id),
        )
        store._conn.commit()


def by_key(results):
    return {k.key: k for k in results}


ALL_KEYS = (
    "activation_rate", "w1_refilm_rate", "free_to_pro_rate",
    "weekly_retained_filmers", "gear_attach_per_100_reports",
)


# -- honesty: None with a stated reason, never a fabricated number ------------

def test_accounts_disabled_gives_reasons_not_numbers(tmp_path):
    db, store = make_db(tmp_path)
    add_job(store, "j1", None, NOW - DAY)  # a report exists even in open mode
    cfg = Config()  # require_account False (bare-code default)
    kpis = by_key(compute_kpis(db, cfg, now=NOW))
    for key in ("activation_rate", "w1_refilm_rate", "free_to_pro_rate",
                "weekly_retained_filmers"):
        assert kpis[key].value is None
        assert "accounts are disabled" in kpis[key].reason
    # Gear attach needs no accounts: 0 gear orders over 1 report = 0.0.
    gear = kpis["gear_attach_per_100_reports"]
    assert (gear.value, gear.numerator, gear.denominator) == (0.0, 0, 1)


def test_missing_database_is_honest(tmp_path):
    kpis = by_key(compute_kpis(tmp_path / "nope.db", accounts_cfg(), now=NOW))
    assert set(kpis) == set(ALL_KEYS)
    for kpi in kpis.values():
        assert kpi.value is None and "no database" in kpi.reason


def test_empty_cohorts_are_reasons_not_zero_rates(tmp_path):
    db, store = make_db(tmp_path)
    kpis = by_key(compute_kpis(db, accounts_cfg(), now=NOW))
    assert "no accounts created" in kpis["activation_rate"].reason
    assert "finished analysis" in kpis["w1_refilm_rate"].reason
    assert "no activated accounts" in kpis["free_to_pro_rate"].reason
    assert "no finished reports" in kpis["gear_attach_per_100_reports"].reason
    # Weekly retained filmers is a count and zero is a real answer.
    weekly = kpis["weekly_retained_filmers"]
    assert (weekly.value, weekly.numerator, weekly.reason) == (0.0, 0, None)


# -- activation, with the window edges ---------------------------------------

def test_activation_math_and_window_edges(tmp_path):
    db, store = make_db(tmp_path)
    # Activated: first DONE at exactly +7 days (inclusive edge).
    u1 = add_user(store, "u1@x.co", NOW - 10 * DAY)
    add_job(store, "j1", u1, NOW - 10 * DAY + 7 * DAY)
    # Not activated: first DONE one second past the 7-day line.
    u2 = add_user(store, "u2@x.co", NOW - 10 * DAY)
    add_job(store, "j2", u2, NOW - 10 * DAY + 7 * DAY + 1)
    # In the cohort: created exactly at the 90-day cutoff; activated next day.
    u3 = add_user(store, "u3@x.co", NOW - 90 * DAY)
    add_job(store, "j3", u3, NOW - 89 * DAY)
    # Outside the window entirely: neither counted nor activated.
    u4 = add_user(store, "u4@x.co", NOW - 90 * DAY - 1)
    add_job(store, "j4", u4, NOW - 89 * DAY)
    # Failed jobs never activate anyone.
    u5 = add_user(store, "u5@x.co", NOW - 10 * DAY)
    add_job(store, "j5", u5, NOW - 9 * DAY, status="failed")

    kpi = by_key(compute_kpis(db, accounts_cfg(), now=NOW))["activation_rate"]
    assert (kpi.numerator, kpi.denominator) == (2, 4)
    assert kpi.value == pytest.approx(50.0)


def test_unclaimed_store_stubs_do_not_deflate_activation(tmp_path):
    db, store = make_db(tmp_path)
    u1 = add_user(store, "real@x.co", NOW - 10 * DAY)
    add_job(store, "j1", u1, NOW - 9 * DAY)
    stub = store.upsert_store_customer("stub@x.co", "cust-1")
    with store._lock:  # stub "created" inside the window
        store._conn.execute(
            "UPDATE users SET created_at = ? WHERE id = ?",
            (NOW - 5 * DAY, stub.id),
        )
        store._conn.commit()
    kpi = by_key(compute_kpis(db, accounts_cfg(), now=NOW))["activation_rate"]
    assert (kpi.numerator, kpi.denominator) == (1, 1)


# -- W1 re-film ---------------------------------------------------------------

def test_w1_refilm_math_and_edge(tmp_path):
    db, store = make_db(tmp_path)
    # Re-filmed: second DONE at exactly first + 7 days.
    a = add_user(store, "a@x.co", NOW - 30 * DAY)
    add_job(store, "a1", a, NOW - 29 * DAY)
    add_job(store, "a2", a, NOW - 29 * DAY + 7 * DAY)
    # Not re-filmed: second DONE a second too late.
    b = add_user(store, "b@x.co", NOW - 30 * DAY)
    add_job(store, "b1", b, NOW - 29 * DAY)
    add_job(store, "b2", b, NOW - 29 * DAY + 7 * DAY + 1)
    # One session only: in the denominator, not the numerator.
    c = add_user(store, "c@x.co", NOW - 30 * DAY)
    add_job(store, "c1", c, NOW - 29 * DAY)
    # Never filmed: not in this metric at all.
    add_user(store, "d@x.co", NOW - 30 * DAY)

    kpi = by_key(compute_kpis(db, accounts_cfg(), now=NOW))["w1_refilm_rate"]
    assert (kpi.numerator, kpi.denominator) == (1, 3)
    assert kpi.value == pytest.approx(100.0 / 3)


# -- free -> Pro --------------------------------------------------------------

def test_free_to_pro_over_activated_accounts(tmp_path):
    db, store = make_db(tmp_path)
    # Converted via Shopify: grant applied at exactly signup + 30 days.
    a = add_user(store, "a@x.co", NOW - 40 * DAY)
    add_job(store, "a1", a, NOW - 39 * DAY)
    store.record_order("o-a", "a@x.co", 31.0)
    set_grant_time(store, "o-a", NOW - 40 * DAY + 30 * DAY)
    # Not converted: the grant landed a second past 30 days.
    b = add_user(store, "b@x.co", NOW - 40 * DAY)
    add_job(store, "b1", b, NOW - 39 * DAY)
    store.record_order("o-b", "b@x.co", 31.0)
    set_grant_time(store, "o-b", NOW - 40 * DAY + 30 * DAY + 1)
    # Converted via Stripe: plan state, no ledger row.
    c = add_user(store, "c@x.co", NOW - 40 * DAY, plan="pro", status="active")
    add_job(store, "c1", c, NOW - 39 * DAY)
    # A Pro who never activated is not in this metric (denominator is
    # activated accounts).
    add_user(store, "d@x.co", NOW - 40 * DAY, plan="pro", status="active")

    kpi = by_key(compute_kpis(db, accounts_cfg(), now=NOW))["free_to_pro_rate"]
    assert (kpi.numerator, kpi.denominator) == (2, 3)
    assert kpi.value == pytest.approx(200.0 / 3)


# -- weekly retained filmers --------------------------------------------------

def test_weekly_retained_filmers_trailing_seven_days(tmp_path):
    db, store = make_db(tmp_path)
    a = add_user(store, "a@x.co", NOW - 60 * DAY)
    add_job(store, "a1", a, NOW - 7 * DAY)  # exactly on the edge: counted
    add_job(store, "a2", a, NOW - DAY)  # same account counted once
    b = add_user(store, "b@x.co", NOW - 60 * DAY)
    add_job(store, "b1", b, NOW - 7 * DAY - 1)  # just outside: not counted
    kpi = by_key(compute_kpis(db, accounts_cfg(), now=NOW))[
        "weekly_retained_filmers"
    ]
    assert (kpi.value, kpi.numerator, kpi.denominator) == (1.0, 1, None)


# -- gear attach --------------------------------------------------------------

def test_gear_attach_math_replay_cancel_and_window(tmp_path):
    db, store = make_db(tmp_path)
    for n in range(4):  # four DONE reports in the window (owner irrelevant)
        add_job(store, f"j{n}", None, NOW - (n + 1) * DAY)
    add_job(store, "old", None, NOW - 91 * DAY)  # outside the window

    assert store.record_gear_order(
        "g1", "kyle@x.co", [("SL-TEMPO-WAND", "Tempo Wand", 1)]
    )
    # Replayed webhook: refused, never double-counted.
    assert not store.record_gear_order(
        "g1", "kyle@x.co", [("SL-TEMPO-WAND", "Tempo Wand", 1)]
    )
    # A cancelled order drops out of the KPI.
    store.record_gear_order("g2", "kyle@x.co", [("SL-STICKS", "Sticks", 1)])
    store.cancel_gear_order("g2")
    # An order recorded before the window drops out too.
    store.record_gear_order("g3", "kyle@x.co", [("SL-MAT", "Mat", 1)])
    with store._lock:
        store._conn.execute(
            "UPDATE gear_orders SET created_at = ? WHERE order_id = 'g3'",
            (NOW - 91 * DAY,),
        )
        store._conn.commit()

    kpi = by_key(compute_kpis(db, accounts_cfg(), now=NOW))[
        "gear_attach_per_100_reports"
    ]
    assert (kpi.numerator, kpi.denominator) == (1, 4)
    assert kpi.value == pytest.approx(25.0)


def test_gear_attach_without_ledger_or_reports_is_honest(tmp_path):
    db, store = make_db(tmp_path)
    # No reports in the window: 0/0 stays None with the reason stated.
    kpis = by_key(compute_kpis(db, accounts_cfg(), now=NOW))
    assert kpis["gear_attach_per_100_reports"].value is None
    assert "no finished reports" in kpis["gear_attach_per_100_reports"].reason
    # A pre-ledger database (no gear_orders table): None, stated.
    with store._lock:
        store._conn.execute("DROP TABLE gear_orders")
        store._conn.commit()
    add_job(store, "j1", None, NOW - DAY)
    kpis = by_key(compute_kpis(db, accounts_cfg(), now=NOW))
    assert kpis["gear_attach_per_100_reports"].value is None
    assert "ledger" in kpis["gear_attach_per_100_reports"].reason


# -- read-only + custom window ------------------------------------------------

def test_since_window_narrows_the_cohort(tmp_path):
    db, store = make_db(tmp_path)
    u1 = add_user(store, "new@x.co", NOW - 5 * DAY)
    add_job(store, "j1", u1, NOW - 4 * DAY)
    u2 = add_user(store, "older@x.co", NOW - 20 * DAY)
    add_job(store, "j2", u2, NOW - 19 * DAY)
    wide = by_key(compute_kpis(db, accounts_cfg(), since_days=90, now=NOW))
    narrow = by_key(compute_kpis(db, accounts_cfg(), since_days=7, now=NOW))
    assert wide["activation_rate"].denominator == 2
    assert narrow["activation_rate"].denominator == 1


def test_compute_never_writes(tmp_path):
    db, store = make_db(tmp_path)
    before = db.read_bytes()
    compute_kpis(db, accounts_cfg(), now=NOW)
    assert db.read_bytes() == before


# -- the CLI surface ----------------------------------------------------------

def seed_for_cli(tmp_path):
    """A sessions dir seeded relative to REAL time (the CLI uses now())."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db, store = make_db(sessions)
    now = time.time()
    u1 = add_user(store, "u1@x.co", now - 10 * DAY)
    add_job(store, "j1", u1, now - 9 * DAY)
    add_user(store, "u2@x.co", now - 10 * DAY)
    store.record_gear_order("g1", "u1@x.co", [("SL-TEMPO-WAND", "Wand", 1)])
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("web:\n  require_account: true\n")
    return sessions, cfg_file


def test_cli_kpis_json(tmp_path, capsys):
    from swinglab.cli import main as cli_main

    sessions, cfg_file = seed_for_cli(tmp_path)
    code = cli_main([
        "kpis", "--sessions-dir", str(sessions), "--config", str(cfg_file),
        "--json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["window_days"] == 90
    assert set(payload["kpis"]) == set(ALL_KEYS)
    act = payload["kpis"]["activation_rate"]
    assert (act["numerator"], act["denominator"]) == (1, 2)
    assert act["value"] == pytest.approx(50.0)
    gear = payload["kpis"]["gear_attach_per_100_reports"]
    assert (gear["numerator"], gear["denominator"]) == (1, 1)


def test_cli_kpis_table_prints_values_and_honest_reasons(tmp_path, capsys):
    from swinglab.cli import main as cli_main

    sessions, cfg_file = seed_for_cli(tmp_path)
    code = cli_main([
        "kpis", "--sessions-dir", str(sessions), "--config", str(cfg_file),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "activation_rate" in out and "50.0%" in out
    assert "target > 50%" in out  # the analysis targets travel with the table
    # free_to_pro has an activated cohort but nobody converted: 0.0% is a
    # real number here, not a None.
    assert "free_to_pro_rate" in out and "0.0%" in out
    # weekly retained filmers: an honest count (0 — nobody filmed this week)
    assert "weekly_retained_filmers" in out
    # w1_refilm: u1 filmed once, never re-filmed — 0/1 is a number, not None
    assert "w1_refilm_rate" in out and "0/1" in out


def test_cli_kpis_since_validation(tmp_path, capsys):
    from swinglab.cli import main as cli_main

    sessions, cfg_file = seed_for_cli(tmp_path)
    code = cli_main([
        "kpis", "--sessions-dir", str(sessions), "--config", str(cfg_file),
        "--since", "0",
    ])
    assert code == 2
    assert "positive" in capsys.readouterr().err
    # nan slips past a plain <= 0 comparison — pinned rejected here.
    code = cli_main([
        "kpis", "--sessions-dir", str(sessions), "--config", str(cfg_file),
        "--since", "nan",
    ])
    assert code == 2
    assert "positive" in capsys.readouterr().err


def test_cli_kpis_reasons_on_open_instance(tmp_path, capsys):
    from swinglab.cli import main as cli_main

    sessions, _ = seed_for_cli(tmp_path)
    open_cfg = tmp_path / "open.yaml"
    open_cfg.write_text("web:\n  require_account: false\n")
    code = cli_main([
        "kpis", "--sessions-dir", str(sessions), "--config", str(open_cfg),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "accounts are disabled" in out
    assert "\N{EM DASH}" in out


def test_format_value_shapes():
    from swinglab.kpis import Kpi

    pct = Kpi("k", "K", value=33.333, unit="%", numerator=1, denominator=3)
    assert format_value(pct) == "33.3%"
    count = Kpi("k", "K", value=4.0, unit="accounts", numerator=4, denominator=None)
    assert format_value(count) == "4"
    none = Kpi("k", "K", value=None, unit="%", numerator=None, denominator=None,
               reason="why")
    assert format_value(none) == "\N{EM DASH}"
