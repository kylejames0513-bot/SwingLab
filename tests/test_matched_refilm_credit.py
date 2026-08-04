"""The free matched re-film credit (allowances.free_matched_refilm).

Every surface teaches Film -> Practice -> Re-film, so a free account that
earned a coaching-ready baseline this month must be able to close the loop:
ONE more upload within 14 days is free when the declared context matches
the baseline (same club, same handedness, same camera angle). One credit a
calendar month, the first-rejected-clip courtesy is unchanged, Pro accounts
are unaffected, and with the flag off the old wall is byte-for-byte back.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.coaching import FLAG_TEMPO
from swinglab.config import Config
from swinglab.drills import build_drills
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app, matched_refilm_baseline
from swinglab.web.users import PRO, UserStore

from tests.test_web import fake_analyze_ok, wait_for

REFILM_WARNING = (
    "Tracking was unstable for this swing — numbers may be off; "
    "film with a clear view."
)


def flagged_tempo_analyze(video_path, **kwargs):
    """A coaching-eligible session with a real flag, so a brief exists."""
    result = fake_analyze_ok(video_path, **kwargs)
    result.metrics_path.write_text(
        json.dumps(
            {"swings": [{"metrics": {"tempo_ratio": 2.0}}], "session_stats": {}}
        ),
        encoding="utf-8",
    )
    return result


def rejected_analyze(video_path, **kwargs):
    """A finished clip the trust boundary sends back for a re-film."""
    result = fake_analyze_ok(video_path, **kwargs)
    result.metrics_path.write_text(
        json.dumps(
            {
                "swings": [
                    {"metrics": {"tempo_ratio": 2.0}, "notes": [REFILM_WARNING]}
                ]
            }
        ),
        encoding="utf-8",
    )
    return result


def make_app(
    tmp_path,
    monkeypatch,
    *,
    credit=True,
    free_per_month=1,
    pro_per_month=0,
    analyze=fake_analyze_ok,
):
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
        "STRIPE_SECRET_KEY",
        "STRIPE_PRICE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(jobs_module, "analyze_video", analyze)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.billing["free_per_month"] = free_per_month
    cfg.billing["pro_per_month"] = pro_per_month
    cfg.allowances["free_matched_refilm"] = credit
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(client, email="kyle@example.com"):
    resp = client.post(
        "/signup",
        data={"email": email, "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def upload(client, *, club="iron", hand="right", angle="face-on"):
    return client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        data={"club": club, "hand": hand, "angle": angle},
        follow_redirects=False,
    )


def upload_done(client, **context):
    resp = upload(client, **context)
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    assert wait_for(client, job_id)["status"] == "done"
    return job_id


def usage(client, email="kyle@example.com"):
    users: UserStore = client.app.state.users
    return client.app.state.jobs.usage_this_month(users.get_by_email(email).id)


# -- the credit itself -------------------------------------------------------

def test_matched_refilm_is_free_after_a_coaching_ready_baseline(
    tmp_path, monkeypatch
):
    client = TestClient(make_app(tmp_path, monkeypatch))
    signup(client)
    upload_done(client)  # the baseline uses the single free analysis
    assert usage(client) == 1

    refilm = upload(client)  # same club, hand, and angle — the free re-film
    assert refilm.status_code == 303
    wait_for(client, refilm.headers["location"].rsplit("/", 1)[-1])
    assert usage(client) == 2

    third = upload(client)  # one credit per calendar month
    assert third.status_code == 402
    assert "Upgrade to Pro" in third.json()["detail"]


def test_mismatched_context_is_refused_and_does_not_burn_the_credit(
    tmp_path, monkeypatch
):
    client = TestClient(make_app(tmp_path, monkeypatch))
    signup(client)
    upload_done(client)  # baseline: iron, right-handed, face-on

    for mismatch in (
        {"club": "driver"},
        {"hand": "left"},
        {"angle": "dtl"},
    ):
        refused = upload(client, **mismatch)
        assert refused.status_code == 402
        detail = refused.json()["detail"]
        assert "matched re-film is still free" in detail
        assert "Iron · Right-handed · Face-on" in detail
        assert "consumes nothing" in detail

    # The refusals cost nothing: the matched upload still goes through.
    assert usage(client) == 1
    matched = upload(client)
    assert matched.status_code == 303


def test_credit_expires_14_days_after_the_baseline_and_never_crosses_months(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    manager = app.state.jobs
    cfg = app.state.cfg
    users: UserStore = app.state.users
    user = users.create("window@example.com", "longenough")

    def baseline_at(timestamp: float):
        job = manager.create_session(
            source_name="baseline.mov", club="iron", user_id=user.id
        )
        job.status = jobs_module.DONE
        result_dir = job.session_dir / "out"
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "report.html").write_text(
            "<html><body>report</body></html>", encoding="utf-8"
        )
        job.report_rel = "out/report.html"
        manager._save(job)
        # created_at is deliberately immutable through _save; backdate the
        # row directly, the same way the retention tests do.
        with manager._lock:
            manager._conn.execute(
                "UPDATE jobs SET created_at = ? WHERE id = ?",
                (timestamp, job.id),
            )
            manager._conn.commit()
        return job

    day = 86400.0
    mid_month = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc).timestamp()
    baseline_at(mid_month)
    assert (
        matched_refilm_baseline(user, manager, cfg, now=mid_month + 13 * day)
        is not None
    )
    assert (
        matched_refilm_baseline(user, manager, cfg, now=mid_month + 15 * day)
        is None
    )

    # A late-June baseline is not "this calendar month" seen from July,
    # even though July 2nd is inside the 14-day window.
    late_month = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc).timestamp()
    with manager._lock:
        manager._conn.execute(
            "UPDATE jobs SET created_at = ?", (late_month,)
        )
        manager._conn.commit()
    july_2nd = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc).timestamp()
    assert (
        matched_refilm_baseline(user, manager, cfg, now=late_month + 5 * day)
        is not None
    )
    assert matched_refilm_baseline(user, manager, cfg, now=july_2nd) is None


def test_pro_accounts_keep_their_own_limit_and_message(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, pro_per_month=1)
    app.state.users.create("pro@example.com", "longenough")
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": "pro@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303
    users: UserStore = app.state.users
    users.set_plan(users.get_by_email("pro@example.com").id, PRO, "active")

    upload_done(client)
    blocked = upload(client)  # a perfect context match changes nothing

    assert blocked.status_code == 402
    assert "reached this month's limit" in blocked.json()["detail"]
    assert "re-film" not in blocked.json()["detail"]


def test_flag_off_keeps_the_old_wall_exactly(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch, credit=False))
    signup(client)
    upload_done(client)

    blocked = upload(client)
    assert blocked.status_code == 402
    assert "Upgrade to Pro" in blocked.json()["detail"]
    assert "re-film" not in blocked.json()["detail"]

    html = client.get("/").text
    assert "Allowance used" in html
    assert "matched re-film is free" not in html
    assert 'id="submit" type="submit" disabled aria-disabled="true"' in html
    assert "var uploadAllowed = false;" in html


# -- the courtesy rule stays untouched ---------------------------------------

def test_first_rejected_clip_courtesy_is_unchanged_with_the_flag_on(
    tmp_path, monkeypatch
):
    client = TestClient(
        make_app(tmp_path, monkeypatch, analyze=rejected_analyze)
    )
    signup(client)

    first = upload(client)
    wait_for(client, first.headers["location"].rsplit("/", 1)[-1])
    assert usage(client) == 0  # the courtesy: a rejected first clip is free

    courtesy_retry = upload(client)
    assert courtesy_retry.status_code == 303
    wait_for(client, courtesy_retry.headers["location"].rsplit("/", 1)[-1])
    assert usage(client) == 1

    # No coaching-ready baseline exists, so no credit either: blocked with
    # the established message, exactly as before the flag.
    blocked = upload(client)
    assert blocked.status_code == 402
    assert "Upgrade to Pro" in blocked.json()["detail"]


def test_rejected_matched_refilm_falls_back_to_the_courtesy_shape(
    tmp_path, monkeypatch
):
    calls = 0

    def eligible_then_rejected(video_path, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return flagged_tempo_analyze(video_path, **kwargs)
        return rejected_analyze(video_path, **kwargs)

    client = TestClient(
        make_app(tmp_path, monkeypatch, analyze=eligible_then_rejected)
    )
    signup(client)
    upload_done(client)  # coaching-ready baseline
    assert usage(client) == 1

    rejected_refilm = upload(client)  # the free matched re-film — rejected
    assert rejected_refilm.status_code == 303
    wait_for(client, rejected_refilm.headers["location"].rsplit("/", 1)[-1])
    # The first rejected clip of the month never consumes anything, so the
    # credit is still open for a usable matched re-film.
    assert usage(client) == 1
    second_refilm = upload(client)
    assert second_refilm.status_code == 303
    wait_for(client, second_refilm.headers["location"].rsplit("/", 1)[-1])

    # A second rejection charges normally (the courtesy is once a month),
    # which also closes the credit: the loop is bounded.
    assert usage(client) == 2
    assert upload(client).status_code == 402


# -- the surfaces speak the credit state -------------------------------------

def test_upload_page_offers_the_credit_plainly_when_it_is_open(
    tmp_path, monkeypatch
):
    client = TestClient(
        make_app(tmp_path, monkeypatch, analyze=flagged_tempo_analyze)
    )
    signup(client)
    upload_done(client)

    html = client.get("/").text
    assert "Your matched re-film is free this month" in html
    assert "same club, same view" in html
    assert "Iron · Right-handed · Face-on" in html
    assert "Allowance used" not in html
    assert "Matched re-film free" in html
    assert "var uploadAllowed = true;" in html
    assert 'id="submit" type="submit">Upload &amp; analyze</button>' in html


def test_spent_credit_prompt_names_the_golfers_own_pass_mark(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch, analyze=flagged_tempo_analyze)
    # make_app clears the Stripe env, so set it after for a real Pro path.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_x")
    client = TestClient(app)
    signup(client)
    upload_done(client)  # baseline
    upload_done(client)  # the free matched re-film — credit spent

    html = client.get("/").text
    assert "Allowance used" in html
    assert "var uploadAllowed = false;" in html
    assert "Your pending pass mark is still waiting:" in html
    # The exact re-film target from the golfer's own coaching session, not
    # a generic upgrade line.
    tempo_drill = build_drills(Config().coaching)[FLAG_TEMPO][0]
    assert tempo_drill.success_metric in html


def test_today_refilm_cta_reflects_the_credit_state(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, analyze=flagged_tempo_analyze)
    users: UserStore = app.state.users
    user = users.create("today@example.com", "longenough")
    users.upsert_golfer_profile(
        user.id,
        display_name="Avery",
        experience_mode="improve",
        handicap_range="20_to_29",
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="iron",
    )
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": "today@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    upload_done(client)  # baseline: allowance used, credit open
    open_html = client.get("/today").text
    assert 'data-today-state="coaching_ready"' in open_html
    assert "Re-film free — same club, same view" in open_html
    assert "Allowance used — your matched re-film is still free" in open_html
    assert "Practice, then re-film" not in open_html

    upload_done(client)  # the free matched re-film — credit spent
    spent_html = client.get("/today").text
    assert "Re-film free" not in spent_html
    # No Pro sales channel is configured here, so the honest fallback shows.
    assert "Allowance used — your re-film unlocks on the 1st." in spent_html
    assert "0 analyses left this month" in spent_html


def test_today_spent_credit_points_at_pro_when_it_is_sellable(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch, analyze=flagged_tempo_analyze)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_x")
    users: UserStore = app.state.users
    user = users.create("pro-cta@example.com", "longenough")
    users.upsert_golfer_profile(
        user.id,
        display_name="Avery",
        experience_mode="improve",
        handicap_range="20_to_29",
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="iron",
    )
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": "pro-cta@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303
    upload_done(client)
    upload_done(client)

    html = client.get("/today").text
    assert "Allowance used — see Pro plans" in html
    assert 'href="/pricing"' in html


def test_pricing_free_tier_names_the_matched_refilm_only_when_real(
    tmp_path, monkeypatch
):
    with_credit = TestClient(make_app(tmp_path, monkeypatch))
    compact = " ".join(with_credit.get("/pricing").text.split())
    assert "1 full analysis + 1 matched re-film each month, forever" in compact
    assert "1 + 1 matched re-film" in compact

    without = TestClient(
        make_app(tmp_path / "off", monkeypatch, credit=False)
    )
    compact_off = " ".join(without.get("/pricing").text.split())
    assert "1 full swing analysis every month, forever" in compact_off
    assert "matched re-film each month" not in compact_off
