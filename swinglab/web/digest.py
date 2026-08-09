"""The weekly practice-plan email — "one drill a week", made real.

Consent-first and inert by default, the same rule as every integration:
nothing ever sends unless email delivery is configured (mailer.py), config
``web.digest_enabled`` is on, AND the user ticked "Email me one drill a
week" (unchecked at signup; toggled on the account page; one-click
unsubscribe link in every email). With any of the three missing, this
module does exactly nothing.

What a digest contains, all pulled from real data and never invented:
the first Caddie Brief drill for the user's LATEST finished session (name,
dosage, and pass mark — the same action the results page leads with),
one progress line from swinglab.trends when two sessions exist, and links
to the latest report and /progress. Self-contained HTML: inline styles,
brand colors from config, no images, no external assets at all.

Delivery model: an hourly daemon thread (started by app.py only when the
preconditions above hold). Each tick sends to opted-in users whose last
send is at least DIGEST_INTERVAL_S (6.5 days) old and who have at least
one finished session. The send is CLAIMED first — digest_last_sent_at is
stamped in SQLite before the delivery attempt — so a crash mid-send skips a
week instead of ever double-emailing within one. The thread never raises.

Unsubscribe links carry an HMAC-SHA256 token over the user id + purpose,
signed with SWINGLAB_SECRET (constant-time compare), so they work logged
out and cannot be forged for another account.

This module also hosts the Pro expiry reminder (bottom of the file): a
separate daily daemon thread sending one transactional email ~7 days before
a time-boxed Pro grant lapses. It shares the claim-before-send discipline
but none of the digest's consent gates — transactional account mail is
governed only by whether email delivery is configured at all.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..caddie_brief import (
    build_caddie_brief_from_payload,
)
from ..clubs import CLUB_LABELS
from ..config import Config
from ..drills import CLEAN
from ..metrics import ANGLES
from ..report import persisted_priority_rule_version
from ..trends import (
    FLAG_LABELS,
    build_trends,
    metrics_json_path,
    trend_sentence,
)
from . import mailer, shopify_billing
from .users import entitled_to_coach_features

logger = logging.getLogger("swinglab.web.digest")

DIGEST_INTERVAL_S = 6.5 * 86400  # at most one email per user per ~week
TICK_S = 3600  # scheduler wakes hourly
_UNSUB_PURPOSE = "digest-unsubscribe"

# Pro expiry reminder (transactional — no digest consent involved): one
# email per expiry period, sent when pro_until is within this lead window.
PRO_EXPIRY_LEAD_S = 7 * 86400
PRO_EXPIRY_TICK_S = 86400  # a daily check is plenty for a 7-day window
_PRO_EXPIRY_KIND = "pro_expiry_reminder"

# Subject-line focus per drill family (the practice_plan block keys):
# plain and honest, no hype.
_FOCUS = {
    "tempo": "tame the tempo",
    "sway": "quiet the head",
    "hip-slide": "turn, don't slide",
    "head-dip": "hold your height",
    "arm-extension": "keep the lead arm long",
    "balance": "hold the finish",
    "consistency": "one tempo, every swing",
    CLEAN: "keep it clean",
}


# -- unsubscribe tokens ------------------------------------------------------

def unsubscribe_token(user_id: str, secret: str) -> str:
    """`<user_id>.<hmac>` — verifiable logged-out, forgeable by no one
    without SWINGLAB_SECRET."""
    mac = hmac.new(
        secret.encode(), f"{_UNSUB_PURPOSE}:{user_id}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{user_id}.{mac}"


def verify_unsubscribe_token(token: str, secret: str) -> str | None:
    """The user id the token was minted for, or None for anything invalid.
    Constant-time comparison — a forged token learns nothing."""
    user_id, _, mac = (token or "").partition(".")
    if not user_id or not mac:
        return None
    expected = hmac.new(
        secret.encode(), f"{_UNSUB_PURPOSE}:{user_id}".encode(), hashlib.sha256
    ).hexdigest()
    # Compare as bytes: compare_digest on str raises TypeError for non-ASCII
    # input, and a crafted token must fail verification, not crash the route.
    return user_id if hmac.compare_digest(mac.encode(), expected.encode()) else None


# -- eligibility (pure — the scheduler and its tests share this rule) --------

def eligible(user, now: float) -> bool:
    """Consent + timing: opted in, and never sent or sent over
    DIGEST_INTERVAL_S ago. (The has-a-finished-session requirement is
    enforced separately by compose_digest returning None.)"""
    if not getattr(user, "digest_opt_in", False):
        return False
    last = getattr(user, "digest_last_sent_at", None)
    return last is None or now - last >= DIGEST_INTERVAL_S


# -- composing ---------------------------------------------------------------

def _club_aware_enabled(cfg: Config) -> bool:
    return cfg.coaching.get("club_aware_enabled") is True


def _exact_job_context(job) -> tuple[str, str, str] | None:
    club = getattr(job, "club", None)
    hand = getattr(job, "hand", None)
    angle = getattr(job, "angle", None)
    if (
        club not in CLUB_LABELS
        or hand not in ("right", "left")
        or angle not in ANGLES
    ):
        return None
    return club, hand, angle


def _context_label(context: tuple[str, str, str]) -> str:
    club, hand, angle = context
    return " · ".join(
        (
            CLUB_LABELS[club],
            "right-handed" if hand == "right" else "left-handed",
            "face-on" if angle == "face-on" else "down-the-line",
        )
    )

def _first_digest_of_month(user, now: float) -> bool:
    """Whether this send is the user's first digest of the UTC calendar
    month (digest_last_sent_at is read BEFORE the claim stamps it)."""
    last = getattr(user, "digest_last_sent_at", None)
    if last is None:
        return True
    current = datetime.fromtimestamp(now, timezone.utc)
    previous = datetime.fromtimestamp(float(last), timezone.utc)
    return (current.year, current.month) != (previous.year, previous.month)


def compose_digest(
    user, cfg: Config, jobs, base_url: str = "", secret: str = "",
    now: float | None = None,
) -> tuple[str, str] | None:
    """(subject, html) for this user's week, or None when no finished
    session has readable numbers yet (send nothing rather than guess).

    ``jobs`` is the user's job list (any statuses — only finished sessions
    count); ``base_url`` prefixes every link (PUBLIC_BASE_URL in
    production); ``secret`` signs the unsubscribe token; ``now`` anchors the
    first-digest-of-the-month allowance note (current time by default).
    """
    now = time.time() if now is None else now
    jobs = list(jobs)
    trends = build_trends(jobs, cfg)
    if not trends.samples:
        return None
    latest = trends.samples[-1]
    latest_job = next(
        (job for job in jobs if job.id == latest.job_id), None
    )
    if latest_job is None:
        return None
    metrics_path = metrics_json_path(latest_job)
    if metrics_path is None:
        return None
    report_path = metrics_path.parent / Path(str(latest_job.report_rel)).name
    report_rule = persisted_priority_rule_version(report_path)
    if report_rule is None:
        return None

    exact_context = None
    if _club_aware_enabled(cfg) or report_rule == 2:
        # The active gate applies exact context globally. A persisted rule-2
        # report keeps that same promise during rollback, so its digest also
        # replays the report's exact club + hand + angle boundary.
        exact_context = _exact_job_context(latest_job)
        if exact_context is None:
            return None
        club, hand, angle = exact_context
        exact_jobs = [
            job
            for job in jobs
            if (
                getattr(job, "club", None) == club
                and getattr(job, "hand", None) == hand
                and getattr(job, "angle", None) == angle
                and float(getattr(job, "created_at", 0.0) or 0.0)
                <= float(latest_job.created_at)
            )
        ]
        trends = build_trends(exact_jobs, cfg)
        latest = next(
            (
                sample
                for sample in reversed(trends.samples)
                if sample.job_id == latest_job.id
            ),
            None,
        )
        if latest is None:
            return None
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sentence = trend_sentence(trends)
    if exact_context is not None:
        previous_counts: dict[str, int] = {}
        for sample in trends.samples:
            if sample.job_id == latest_job.id:
                continue
            for flag in sample.flags:
                previous_counts[flag] = previous_counts.get(flag, 0) + 1
        brief = build_caddie_brief_from_payload(
            payload,
            cfg,
            previous_flag_counts=previous_counts,
            trend=sentence,
            angle=getattr(latest_job, "angle", None),
            club=getattr(latest_job, "club", None),
            rule_version=report_rule,
        )
    else:
        brief = build_caddie_brief_from_payload(
            payload,
            cfg,
            angle=getattr(latest_job, "angle", None),
            club=getattr(latest_job, "club", None),
            rule_version=report_rule,
        )
    if brief is None or brief.refilm_required or brief.drill is None:
        return None
    full_baseline = all(
        field in latest.means
        for field in (
            "tempo_ratio",
            "head_sway_backswing_sw",
            "hip_slide_backswing_sw",
        )
    )
    limited_baseline = not latest.flags and (
        latest.angle == "dtl" or not full_baseline
    )
    # The weekly promise is deliberately singular. This is the exact drill
    # selected by the same Caddie Brief priority used on status/report pages.
    drills = [brief.drill]
    focus = (
        (
            "protect the readable rhythm"
            if "tempo_ratio" in latest.means
            else "complete the baseline"
        )
        if limited_baseline
        else _FOCUS.get(
            brief.focus_flag or CLEAN, brief.focus_name.lower()
        )
    )
    subject = f"This week: {focus} (1 drill)"

    esc = html.escape
    primary = esc(str(cfg.brand["primary_color"]))
    accent = esc(str(cfg.brand["accent_color"]))
    name = esc(str(cfg.brand["name"]))
    report_url = esc(f"{base_url}/session/{latest.job_id}/report")
    # The progress dashboard can be Pro-gated (billing.progress_pro_only,
    # effective only with accounts on). Don't send a free subscriber a
    # weekly link that dead-ends at a lock screen — point them at their
    # session history instead, which is theirs on every plan.
    progress_gated = bool(
        cfg.billing.get("progress_pro_only")
        and cfg.web.get("require_account")
        and not entitled_to_coach_features(
            user, bool(cfg.billing.get("coach_tier_enabled"))
        )
    )
    if progress_gated:
        progress_url = esc(f"{base_url}/sessions")
        progress_label = "Your sessions"
    else:
        progress_url = esc(f"{base_url}/progress")
        progress_label = "Your progress"
    unsub_url = esc(
        f"{base_url}/email/unsubscribe?token={unsubscribe_token(user.id, secret)}"
    )

    if latest.flags:
        flagged = ", ".join(
            FLAG_LABELS.get(flag, flag) for flag in latest.flags
        )
        context_line = f"Your last session flagged: <strong>{esc(flagged)}</strong>."
    else:
        context_line = (
            (
                "Your last down-the-line session came back clean on tempo — "
                "this week stays with rhythm, the measurement this angle "
                "supports."
            )
            if latest.angle == "dtl"
            else (
                "Your last session stayed inside the lines it could read — "
                "this week protects that rhythm and rebuilds a fuller baseline."
            )
            if limited_baseline and "tempo_ratio" in latest.means
            else (
                "Your last session stayed inside the lines it could read — "
                "this week is about capturing a fuller baseline."
            )
            if limited_baseline
            else (
                "Your last session came back clean — this week is about keeping "
                "the baseline current."
            )
        )

    exact_context_line = ""
    if exact_context is not None:
        exact_context_line = (
            '<p style="margin:0 0 12px;font-size:13px;color:#555;">'
            "<strong>Comparison context:</strong> "
            f"{esc(_context_label(exact_context))}. "
            "This plan and trend use only matching swings.</p>"
        )

    mono = "font-family:ui-monospace,Menlo,Consolas,monospace;"
    drill_cards = "".join(
        '<div style="border:1px solid #e0e0e0;border-radius:8px;'
        'padding:14px 16px;margin:0 0 12px;">'
        f'<p style="margin:0;font-weight:700;color:{primary};">{esc(d.name)}</p>'
        f'<p style="margin:6px 0 8px;color:#444;">{esc(d.aim)}</p>'
        f'<p style="margin:0;font-size:13px;color:#555;"><span style="{mono}'
        f'color:#777;">dosage</span> {esc(d.dosage)}</p>'
        f'<p style="margin:4px 0 0;font-size:13px;color:#555;"><span style="'
        f'{mono}color:#777;">pass mark</span> {esc(d.success_metric)}</p>'
        "</div>"
        for d in drills
    )

    also = ""
    other_flags = [
        flag for flag in latest.flags if flag != brief.focus_flag
    ]
    if other_flags:
        others = ", ".join(
            esc(FLAG_LABELS.get(flag, flag)) for flag in other_flags
        )
        also = (
            f'<p style="margin:0 0 16px;font-size:13px;color:#666;">Also '
            f"flagged: {others} — the full plans are on your report.</p>"
        )

    progress_line = ""
    if sentence:
        progress_line = (
            f'<p style="margin:0 0 16px;padding:10px 14px;background:#f4f7f5;'
            f"border-left:4px solid {accent};border-radius:4px;color:#333;"
            f'font-size:14px;">{esc(sentence)}.</p>'
        )

    # Free plans reset on the 1st. The first digest of each calendar month
    # carries that (and only that) as a short note — consented digest mail
    # only, never a separate unsolicited email.
    reset_note = ""
    free_per_month = int(cfg.billing.get("free_per_month") or 0)
    if (
        cfg.web.get("require_account")
        and free_per_month > 0
        and not getattr(user, "is_pro", False)
        and _first_digest_of_month(user, now)
    ):
        allowance = (
            "free analysis is"
            if free_per_month == 1
            else f"{free_per_month} free analyses are"
        )
        reset_note = (
            f'<p style="margin:0 0 16px;padding:10px 14px;background:#fff4e7;'
            f"border-left:4px solid {accent};border-radius:4px;color:#333;"
            f'font-size:14px;">New month: your {allowance} ready — film '
            "this month's check-in.</p>"
        )

    body = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,'
        "Arial,sans-serif;max-width:560px;margin:0 auto;color:#1c1c1c;"
        'font-size:15px;line-height:1.5;">'
        f'<div style="background:{primary};color:#ffffff;border-radius:8px 8px 0 0;'
        'padding:14px 20px;font-weight:700;">'
        f"{name} — this week's practice plan</div>"
        '<div style="border:1px solid #e0e0e0;border-top:none;'
        'border-radius:0 0 8px 8px;padding:20px;">'
        f'<p style="margin:0 0 12px;">{context_line}</p>'
        f"{exact_context_line}"
        f"{reset_note}"
        f"{progress_line}"
        f"{drill_cards}"
        f"{also}"
        f'<p style="margin:0 0 4px;"><a href="{report_url}" '
        f'style="color:{primary};">Your latest report</a> · '
        f'<a href="{progress_url}" style="color:{primary};">{progress_label}</a></p>'
        f'<p style="margin:16px 0 0;font-size:12px;color:#888;">'
        f"You asked {name} for one drill a week — this is it, once a week, "
        "nothing else. "
        f'<a href="{unsub_url}" style="color:#888;">Unsubscribe</a></p>'
        f'<p style="margin:8px 0 0;font-size:11px;color:#999;font-style:italic;">'
        f"{esc(str(cfg.brand['disclaimer']))}</p>"
        "</div></div>"
    )
    return subject, body


# -- sending -----------------------------------------------------------------

def run_once(users, manager, cfg: Config, secret: str, now: float | None = None) -> int:
    """One scheduler tick: send to every currently-eligible user. Returns
    how many digests went out. Zero behavior when email is unconfigured or
    the digest is disabled in config."""
    if not mailer.enabled() or not cfg.web.get("digest_enabled", True):
        return 0
    now = time.time() if now is None else now
    base_url = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    sent = 0
    for user in users.digest_optins():
        if not eligible(user, now):
            continue
        # Compose, claim, and deliver as one history-visibility interval. A
        # reset either commits after this send or commits before composition;
        # it can never delete the source history between compose and SMTP.
        with manager.history_delivery_guard():
            composed = compose_digest(
                user, cfg, manager.list_recent(user_id=user.id),
                base_url=base_url, secret=secret, now=now,
            )
            if composed is None:
                continue  # no finished history — keep eligibility
            if not users.claim_digest_send(user.id, now, DIGEST_INTERVAL_S):
                continue  # another worker claimed this week's send
            subject, body = composed
            try:
                mailer.send(user.email, subject, body, html=True)
                sent += 1
                logger.info("digest: sent weekly practice plan")
            except Exception:
                # The claim above stands: a failed send waits for next week
                # rather than risking a double-send on retry.
                logger.error("digest: weekly practice-plan delivery failed")
    return sent


def _loop(manager, users, cfg: Config, secret: str) -> None:
    while True:
        try:
            run_once(users, manager, cfg, secret)
        except Exception:  # never let the thread die
            logger.exception("digest: tick failed")
        time.sleep(TICK_S)


def start_scheduler(manager, users, cfg: Config, secret: str) -> threading.Thread | None:
    """Start the hourly digest thread (daemon — dies with the process).
    Returns None — and nothing at all runs — unless email is configured AND
    ``web.digest_enabled`` is on. The first tick runs immediately so a
    restart never silently skips a due week."""
    if not mailer.enabled() or not cfg.web.get("digest_enabled", True):
        return None
    thread = threading.Thread(
        target=_loop,
        args=(manager, users, cfg, secret),
        daemon=True,
        name="swinglab-digest",
    )
    thread.start()
    return thread


# -- Pro expiry reminder (transactional, not the digest) ---------------------

def run_pro_expiry_reminders_once(users, cfg: Config, now: float | None = None) -> int:
    """One reminder ~7 days before a time-boxed Pro grant lapses. Returns
    how many reminders went out.

    Transactional account mail: no digest consent or kill-switch applies —
    only configured email delivery gates it. The send is CLAIMED first in
    the lifecycle ledger, keyed on (user, exact expiry timestamp), so each
    expiry period reminds exactly once; extending Pro moves pro_until and
    naturally arms the next period. Lifetime grants sit ~100 years out and
    never enter the window; Stripe-subscription Pro is excluded by the
    store query (it renews on its own)."""
    if not mailer.enabled():
        return 0
    now = time.time() if now is None else now
    base_url = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    extend_url = (
        shopify_billing.buy_url(cfg)
        if shopify_billing.commerce_enabled()
        else f"{base_url}/pricing"
    )
    brand = str(cfg.brand["name"])
    pro_per_month = int(cfg.billing.get("pro_per_month") or 0)
    allowance = (
        "unlimited analyses"
        if pro_per_month <= 0
        else f"up to {pro_per_month} analyses a month"
    )
    sent = 0
    for user in users.pro_expiring_between(now, now + PRO_EXPIRY_LEAD_S):
        if not user.email:
            continue
        if not users.claim_lifecycle_email(
            _PRO_EXPIRY_KIND,
            f"{user.id}:{int(user.pro_until)}",
            user_id=user.id,
        ):
            continue  # this expiry period was already reminded
        days_left = max(1, math.ceil((user.pro_until - now) / 86400))
        end_day = datetime.fromtimestamp(
            user.pro_until, timezone.utc
        ).strftime("%B %d, %Y")
        noun = "day" if days_left == 1 else "days"
        try:
            mailer.send(
                user.email,
                f"{brand} Pro ends in {days_left} {noun}",
                f"Your {brand} Pro access ends on {end_day} —"
                f" {days_left} {noun} from now.\n\n"
                f"Extend it on the store to keep {allowance}:\n"
                f"{extend_url}\n\n"
                "If it lapses, your account, swing history, and the free"
                " monthly analysis all stay — only the Pro allowance"
                " stops.",
            )
            sent += 1
            logger.info("pro-expiry: reminder sent")
        except Exception:
            # The claim stands: losing one reminder beats ever nagging twice
            # for the same period.
            logger.error("pro-expiry: reminder delivery failed")
    return sent


def _pro_expiry_loop(users, cfg: Config) -> None:
    while True:
        try:
            run_pro_expiry_reminders_once(users, cfg)
        except Exception:  # never let the thread die
            logger.exception("pro-expiry: tick failed")
        time.sleep(PRO_EXPIRY_TICK_S)


def start_pro_expiry_scheduler(users, cfg: Config) -> threading.Thread | None:
    """Start the daily Pro expiry reminder thread (daemon — dies with the
    process). Returns None — and nothing runs — unless email delivery is
    configured; the digest kill-switch does not apply to transactional
    mail. The first tick runs immediately so a restart never skips a due
    reminder."""
    if not mailer.enabled():
        return None
    thread = threading.Thread(
        target=_pro_expiry_loop,
        args=(users, cfg),
        daemon=True,
        name="swinglab-pro-expiry",
    )
    thread.start()
    return thread
