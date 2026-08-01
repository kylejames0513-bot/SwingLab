"""The five business KPIs, computed from the app's own SQLite state.

These are the numbers the strategy review said must exist before any more
feature work: activation to first report, week-one re-film rate, free→Pro
conversion, weekly retained filmers, and gear attach per 100 reports. They
are read straight from the tables the app already writes — ``users``
(swinglab.web.users), ``jobs`` (swinglab.web.jobs), the Pro order ledger
``shopify_orders``, and the gear ledger ``gear_orders`` — over a read-only
connection. Like trends.py, this module is duck-typed on the schema and
imports none of the web stack, so it works from the CLI (``swinglab kpis``)
and from the admin endpoint alike.

The honesty rule is absolute: a metric whose data cannot support it returns
``value None`` with a stated ``reason`` (accounts disabled, no database yet,
empty cohort, missing ledger…) — a number is never fabricated to fill a
table cell. A real zero (e.g. nobody filmed this week) is reported as 0,
not None.

Definitions (window = the trailing ``--since`` days, default 90; cohorts
are claimed accounts — unclaimed store stubs cannot log in or film, so they
are excluded rather than silently deflating every rate):

- **activation_rate** — of accounts created in the window, the share whose
  first coaching-ready analysis landed within 7 days of signup.
- **w1_refilm_rate** — of those window accounts with at least one
  coaching-ready analysis, the share whose SECOND coaching-ready analysis
  landed within 7 days of their first (the re-film habit is the product's
  core loop).
- **free_to_pro_rate** — of the window's activated accounts, the share that
  gained Pro within 30 days of signup. Shopify grants are timed by the
  order ledger's ``applied_at``; a Stripe-subscribed account (``plan
  'pro'`` with a live status) counts as converted — Stripe state carries no
  grant timestamp, and a subscription necessarily starts after signup.
- **weekly_retained_filmers** — a count, not a rate: accounts with at least
  one coaching-ready analysis in the trailing 7 days (regardless of the
  window).
- **gear_attach_per_100_reports** — non-cancelled gear orders in the window
  per 100 coaching-ready reports in the window (both sides from
  ``created_at``).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .caddie_brief import (
    payload_has_coachable_data,
    payload_is_coaching_eligible,
    payload_requires_refilm,
    payload_structure_is_valid,
)
from .report import (
    REPORT_OUTCOME_CAPTURE,
    REPORT_OUTCOME_COACHING,
    persisted_report_outcome,
)

ACTIVATION_WINDOW_S = 7 * 86400
REFILM_WINDOW_S = 7 * 86400
CONVERSION_WINDOW_S = 30 * 86400
RETENTION_TRAILING_S = 7 * 86400
DEFAULT_SINCE_DAYS = 90.0

# Mirrors jobs.DONE / users._PRO_OK_STATUSES without importing the web
# layer (same pattern as trends.py's DONE).
_DONE = "done"
_PRO_OK_STATUSES = ("active", "trialing", "past_due")
_SHOPIFY_ACCOUNT_AUTHENTICATED = "shopify_authenticated"

# Analysis targets, quoted in the CLI/README so the numbers always travel
# with what "good" means: >50% activation, >25% W1 re-film, 2%+ conversion.
TARGETS = {
    "activation_rate": "> 50%",
    "w1_refilm_rate": "> 25%",
    "free_to_pro_rate": "2%+",
}


@dataclass(frozen=True)
class Kpi:
    key: str
    label: str
    value: float | None  # None ONLY with a reason — never a fabricated 0
    unit: str  # "%", "accounts", or "per 100 reports"
    numerator: int | None
    denominator: int | None  # None for plain counts
    reason: str | None = None  # stated exactly when value is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "reason": self.reason,
        }


def _none(key: str, label: str, unit: str, reason: str) -> Kpi:
    return Kpi(key, label, unit=unit, value=None,
               numerator=None, denominator=None, reason=reason)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _coaching_ready(row, sessions_dir: Path, cfg) -> bool:
    """Apply the product eligibility rule to a persisted KPI job row.

    A report without metrics predates re-film outcomes, so it remains a
    coaching-ready legacy result. New reports must have valid metrics that
    pass the same Caddie Brief decision used by the web app.
    """
    report_rel = row["report_rel"]
    if not isinstance(report_rel, str) or not report_rel:
        return False
    root = (sessions_dir / str(row["id"])).resolve()
    report = (root / report_rel).resolve()
    if not report.is_relative_to(root) or not report.is_file():
        return False
    persisted_outcome = persisted_report_outcome(report)
    if persisted_outcome == REPORT_OUTCOME_CAPTURE:
        return False
    metrics = report.parent / "metrics.json"
    if not metrics.is_file():
        return True
    try:
        payload = json.loads(metrics.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return persisted_outcome == REPORT_OUTCOME_COACHING
    job_angle = row["angle"] if "angle" in row.keys() else None
    if isinstance(payload, dict) and payload_requires_refilm(
        payload, angle=job_angle
    ):
        return False
    if not payload_structure_is_valid(payload):
        return persisted_outcome == REPORT_OUTCOME_COACHING
    eligible = payload_is_coaching_eligible(payload, cfg, angle=job_angle)
    if eligible:
        return True
    return (
        persisted_outcome == REPORT_OUTCOME_COACHING
        and not payload_has_coachable_data(payload, angle=job_angle)
    )


def compute_kpis(
    db_path: str | Path,
    cfg,
    since_days: float = DEFAULT_SINCE_DAYS,
    now: float | None = None,
) -> list[Kpi]:
    """All five KPIs, in a fixed order, from the SQLite file the web app
    writes (``<sessions_dir>/swinglab.db``). ``cfg`` is a swinglab Config —
    only ``web.require_account`` is consulted (per-account KPIs cannot
    exist on an open instance and say so instead of reading garbage).
    ``now`` is injectable for tests; the connection is read-only and the
    database is never modified."""
    import time

    now = time.time() if now is None else now
    cutoff = now - since_days * 86400
    window = f"the last {since_days:g} days"
    db_path = Path(db_path)

    labels = {
        "activation_rate": ("Activation to first report", "%"),
        "w1_refilm_rate": ("Week-one re-film rate", "%"),
        "free_to_pro_rate": ("Free \N{RIGHTWARDS ARROW} Pro conversion", "%"),
        "weekly_retained_filmers": ("Weekly retained filmers", "accounts"),
        "gear_attach_per_100_reports": ("Gear attach", "per 100 reports"),
    }

    def all_none(reason: str) -> list[Kpi]:
        return [
            _none(key, label, unit, reason)
            for key, (label, unit) in labels.items()
        ]

    if not db_path.is_file():
        return all_none(
            f"no database at {db_path} — nothing has been recorded yet"
        )

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = _tables(conn)
        accounts_on = bool(cfg.web.get("require_account"))
        account_gap = None  # reason the account cohort can't exist, if any
        if not accounts_on:
            account_gap = (
                "accounts are disabled (web.require_account is off) — "
                "there are no per-account signups or analyses to measure"
            )
        elif "users" not in tables:
            account_gap = "no users table in the database yet"

        # -- shared raw material ------------------------------------------
        ready_by_user: dict[str, list[float]] = {}
        ready_in_window = 0
        if "jobs" in tables:
            job_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(jobs)")
            }
            outcome_aware = "report_rel" in job_columns
            selected = "id, user_id, created_at"
            if outcome_aware:
                selected += ", report_rel"
                if "angle" in job_columns:
                    selected += ", angle"
            for row in conn.execute(
                f"SELECT {selected} FROM jobs WHERE status = ?", (_DONE,)
            ):
                # A jobs schema without report_rel predates rejected-clip
                # outcomes. Preserve that legacy KPI meaning explicitly.
                if outcome_aware and not _coaching_ready(
                    row, db_path.parent, cfg
                ):
                    continue
                if row["created_at"] >= cutoff:
                    ready_in_window += 1
                if row["user_id"]:
                    ready_by_user.setdefault(row["user_id"], []).append(
                        row["created_at"]
                    )
            for times in ready_by_user.values():
                times.sort()

        results: list[Kpi] = []

        # -- the four account KPIs ----------------------------------------
        if account_gap is not None:
            for key in (
                "activation_rate", "w1_refilm_rate",
                "free_to_pro_rate", "weekly_retained_filmers",
            ):
                label, unit = labels[key]
                results.append(_none(key, label, unit, account_gap))
        else:
            # Claimed accounts only: an unclaimed store stub (no password,
            # never signed in with a code) cannot act, so counting it would
            # deflate every rate with users who never had the chance to. A
            # password, a verified email (code sign-in), OR an authenticated
            # Shopify Customer Account counts as claimed. Databases predating
            # either additive column are read-only here, so the query uses
            # only the ownership proofs their schema can represent.
            user_columns = {
                r["name"] for r in conn.execute("PRAGMA table_info(users)")
            }
            claimed_parts = ["password_hash != ''"]
            claimed_parameters: list[object] = []
            if "email_verified_at" in user_columns:
                claimed_parts.append("email_verified_at IS NOT NULL")
            if "shopify_account_migration_state" in user_columns:
                claimed_parts.append("shopify_account_migration_state = ?")
                claimed_parameters.append(_SHOPIFY_ACCOUNT_AUTHENTICATED)
            claimed_sql = "(" + " OR ".join(claimed_parts) + ")"
            cohort = [
                row
                for row in conn.execute(
                    "SELECT id, email, created_at, plan, subscription_status"
                    f" FROM users WHERE {claimed_sql} AND created_at >= ?",
                    (*claimed_parameters, cutoff),
                )
            ]

            def first_done_delay(user_row) -> float | None:
                times = ready_by_user.get(user_row["id"])
                if not times:
                    return None
                return times[0] - user_row["created_at"]

            activated = [
                u for u in cohort
                if (d := first_done_delay(u)) is not None
                and d <= ACTIVATION_WINDOW_S
            ]

            # activation_rate
            label, unit = labels["activation_rate"]
            if not cohort:
                results.append(_none(
                    "activation_rate", label, unit,
                    f"no accounts created in {window}",
                ))
            else:
                results.append(Kpi(
                    "activation_rate", label,
                    value=100.0 * len(activated) / len(cohort), unit=unit,
                    numerator=len(activated), denominator=len(cohort),
                ))

            # w1_refilm_rate
            label, unit = labels["w1_refilm_rate"]
            filmers = [u for u in cohort if ready_by_user.get(u["id"])]
            refilmed = [
                u for u in filmers
                if len(ready_by_user[u["id"]]) >= 2
                and ready_by_user[u["id"]][1] - ready_by_user[u["id"]][0]
                <= REFILM_WINDOW_S
            ]
            if not filmers:
                results.append(_none(
                    "w1_refilm_rate", label, unit,
                    f"no account created in {window} has a coaching-ready "
                    "analysis yet",
                ))
            else:
                results.append(Kpi(
                    "w1_refilm_rate", label,
                    value=100.0 * len(refilmed) / len(filmers), unit=unit,
                    numerator=len(refilmed), denominator=len(filmers),
                ))

            # free_to_pro_rate
            label, unit = labels["free_to_pro_rate"]
            grant_at: dict[str, float] = {}
            if "shopify_orders" in tables:
                for row in conn.execute(
                    "SELECT email, MIN(applied_at) AS at FROM shopify_orders"
                    " WHERE days > 0 GROUP BY email"
                ):
                    grant_at[row["email"]] = row["at"]

            def converted(u) -> bool:
                at = grant_at.get(u["email"])
                if at is not None and at - u["created_at"] <= CONVERSION_WINDOW_S:
                    return True
                # Stripe: plan state without a grant timestamp — counted,
                # honestly documented (subscriptions start after signup).
                return (
                    u["plan"] == "pro"
                    and u["subscription_status"] in _PRO_OK_STATUSES
                )

            if not activated:
                results.append(_none(
                    "free_to_pro_rate", label, unit,
                    f"no activated accounts in {window} yet — activation "
                    "is the denominator here",
                ))
            else:
                pro = [u for u in activated if converted(u)]
                results.append(Kpi(
                    "free_to_pro_rate", label,
                    value=100.0 * len(pro) / len(activated), unit=unit,
                    numerator=len(pro), denominator=len(activated),
                ))

            # weekly_retained_filmers — a count; zero is a real answer.
            label, unit = labels["weekly_retained_filmers"]
            recent = {
                user_id
                for user_id, times in ready_by_user.items()
                if any(t >= now - RETENTION_TRAILING_S for t in times)
            }
            results.append(Kpi(
                "weekly_retained_filmers", label,
                value=float(len(recent)), unit=unit,
                numerator=len(recent), denominator=None,
            ))

        # -- gear_attach_per_100_reports (no accounts needed) --------------
        label, unit = labels["gear_attach_per_100_reports"]
        if "gear_orders" not in tables:
            results.append(_none(
                "gear_attach_per_100_reports", label, unit,
                "no gear order ledger in this database yet (it is created "
                "when the app runs with this version)",
            ))
        elif ready_in_window == 0:
            results.append(_none(
                "gear_attach_per_100_reports", label, unit,
                f"no coaching-ready reports in {window} — attach per 100 "
                "reports is 0/0",
            ))
        else:
            gear_orders = conn.execute(
                "SELECT COUNT(DISTINCT order_id) FROM gear_orders"
                " WHERE cancelled_at IS NULL AND created_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            results.append(Kpi(
                "gear_attach_per_100_reports", label,
                value=100.0 * gear_orders / ready_in_window, unit=unit,
                numerator=gear_orders, denominator=ready_in_window,
            ))
        return results
    finally:
        conn.close()


def format_value(kpi: Kpi) -> str:
    """One KPI's value the way the CLI table prints it ("—" for None)."""
    if kpi.value is None:
        return "\N{EM DASH}"
    if kpi.unit == "%":
        return f"{kpi.value:.1f}%"
    if kpi.unit == "accounts":
        return f"{int(kpi.value)}"
    return f"{kpi.value:.1f}"
