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
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import threading
import time

from ..caddie_brief import (
    build_caddie_brief_from_payload,
)
from ..config import Config
from ..drills import CLEAN
from ..trends import (
    FLAG_LABELS,
    build_trends,
    metrics_json_path,
    trend_sentence,
)
from . import mailer

logger = logging.getLogger("swinglab.web.digest")

DIGEST_INTERVAL_S = 6.5 * 86400  # at most one email per user per ~week
TICK_S = 3600  # scheduler wakes hourly
_UNSUB_PURPOSE = "digest-unsubscribe"

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

def compose_digest(
    user, cfg: Config, jobs, base_url: str = "", secret: str = ""
) -> tuple[str, str] | None:
    """(subject, html) for this user's week, or None when no finished
    session has readable numbers yet (send nothing rather than guess).

    ``jobs`` is the user's job list (any statuses — only finished sessions
    count); ``base_url`` prefixes every link (PUBLIC_BASE_URL in
    production); ``secret`` signs the unsubscribe token.
    """
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
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    brief = build_caddie_brief_from_payload(
        payload,
        cfg,
        angle=getattr(latest_job, "angle", None),
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
    sentence = trend_sentence(trends)
    report_url = esc(f"{base_url}/session/{latest.job_id}/report")
    # The progress dashboard can be Pro-gated (billing.progress_pro_only,
    # effective only with accounts on). Don't send a free subscriber a
    # weekly link that dead-ends at a lock screen — point them at their
    # session history instead, which is theirs on every plan.
    progress_gated = bool(
        cfg.billing.get("progress_pro_only")
        and cfg.web.get("require_account")
        and not user.is_pro
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
                base_url=base_url, secret=secret,
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
