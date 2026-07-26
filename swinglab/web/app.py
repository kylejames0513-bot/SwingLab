"""FastAPI app: accounts, upload, live status, results, history, billing,
and the JSON API.

Everything analysis-related goes through swinglab.pipeline — this layer only
moves files, tracks job state, and renders pages. The JSON endpoints under
/api are the surface a future mobile app talks to.

Product model (config.yaml): with ``web.require_account`` on, visitors sign
up (email + password, hashed locally), get ``billing.free_per_month``
analyses a month, and upgrade to Pro for ``billing.pro_per_month``
(0 = unlimited). Pro is sold two ways, both inert until configured and both
webhook-driven: through the Shopify store (shopify_billing.py — preferred
when configured, one checkout for gear and memberships) or through a Stripe
subscription (billing.py). Set SWINGLAB_SECRET so logins survive restarts.

Accounts can also start on the store: Shopify customer webhooks provision
passwordless "store accounts" that signing up with the same email claims in
place — purchases and the Shopify link carry over (shopify_billing.py).
With SMTP configured (mailer.py, inert otherwise), claims of pre-existing
value are verified with an emailed one-time code, and password reset works
the same way.

The optional gear shop (a /shop page plus flag-matched training-aid
recommendations on finished analyses) is likewise inert until the SHOPIFY_*
environment variables are set — see shop.py.

Retention surfaces: /progress charts each account's metrics across finished
sessions (swinglab.trends + diagrams.trend_chart), and an opt-in weekly
practice-plan email (digest.py) runs on an hourly scheduler thread — only
when SMTP is configured and web.digest_enabled is on, and only to users who
asked for it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import ClientDisconnect

from .. import sample
from ..clubs import CLUB_LABELS
from ..coaching import flag_keys
from ..config import Config
from ..diagrams import trend_chart
from ..explainers import build_explainers
from ..metrics import ANGLES
from ..trends import FLAG_LABELS, build_trends, format_delta, format_value, trend_sentence
from . import billing, digest, humanize, mailer, shop, shopify_billing
from .jobs import DONE, FAILED, Job, JobManager
from .throttle import Throttle
from .users import User, UserStore

logger = logging.getLogger("swinglab.web")

VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
UPLOAD_CHUNK = 1024 * 1024

LOGIN_WINDOW_S = 15 * 60  # window for web.login_attempts_per_15min
SIGNUP_WINDOW_S = 3600  # window for web.signups_per_hour_per_ip
THROTTLED_MESSAGE = "Too many attempts — wait a few minutes and try again."


def init_sentry() -> bool:
    """Optional error monitoring, inert-until-configured like every other
    integration: initializes Sentry only when SENTRY_DSN is set AND
    sentry-sdk is importable (pip install "swinglab[ops]"). Every exception
    path works identically without it."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed — error "
            "monitoring stays off. Install it with: pip install \"swinglab[ops]\""
        )
        return False
    sentry_sdk.init(dsn=dsn)
    logger.info("Sentry error monitoring enabled.")
    return True


def client_ip(request: Request) -> str | None:
    """The client IP every limit/throttle keys on. With web.trusted_proxies
    configured, ProxyHeadersMiddleware has already rewritten request.client
    from X-Forwarded-For — so behind Railway (or any proxy) this is the real
    visitor, not the proxy. Every request-IP read goes through here."""
    return request.client.host if request.client else None


def ensure_user_can_analyze(
    user: User | None, manager: JobManager, cfg: Config
) -> None:
    """The paywall. Free accounts get billing.free_per_month analyses per
    calendar month; Pro gets billing.pro_per_month (0 = unlimited)."""
    if not cfg.web.get("require_account"):
        return
    if user is None:
        raise HTTPException(401, "Log in to analyze a swing.")
    limit = int(
        cfg.billing["pro_per_month"] if user.is_pro else cfg.billing["free_per_month"]
    )
    if limit <= 0:  # unlimited
        return
    if manager.usage_this_month(user.id) >= limit:
        if user.is_pro:
            raise HTTPException(
                402,
                f"You've reached this month's limit of {limit} analyses. "
                "It resets on the 1st.",
            )
        raise HTTPException(
            402,
            f"You've used your {limit} free analyses this month. "
            "Upgrade to Pro for unlimited swings — or come back on the 1st.",
        )


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _base_url(request: Request) -> str:
    return os.environ.get("PUBLIC_BASE_URL") or str(request.base_url).rstrip("/")


def create_app(
    cfg: Config | None = None, sessions_dir: str | Path = "sessions"
) -> FastAPI:
    cfg = cfg or Config.load()
    init_sentry()
    sessions_dir = Path(sessions_dir)
    manager = JobManager(sessions_dir, cfg)
    users = UserStore(sessions_dir / "swinglab.db")
    throttle = Throttle(sessions_dir / "swinglab.db")
    app = FastAPI(title=f"{cfg.brand['name']} — swing analysis")
    app.state.jobs = manager
    app.state.users = users
    app.state.cfg = cfg

    secret = os.environ.get("SWINGLAB_SECRET")
    if not secret:
        secret = secrets.token_hex(32)
        if cfg.web.get("require_account"):
            logger.warning(
                "SWINGLAB_SECRET is not set — logins will not survive "
                "a restart. Set it to a long random string in the environment."
            )
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax")

    # Real client IPs behind a proxy: trust X-Forwarded-For from
    # web.trusted_proxies ("*" = any hop, right for a PaaS whose proxy is
    # the only thing that can reach the app; a list of IPs for a bare VM
    # with its own nginx/Caddy; ""/null = off). Without this, everyone
    # behind the proxy shares its IP and max_active_jobs_per_ip caps the
    # whole site. See config.yaml for the honest spoofing caveats.
    trusted_proxies = cfg.web.get("trusted_proxies")
    if trusted_proxies:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted_proxies)

    login_limit = int(cfg.web.get("login_attempts_per_15min") or 0)
    signup_limit = int(cfg.web.get("signups_per_hour_per_ip") or 0)

    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )

    # The public sample report: generated once at startup if absent (synthetic
    # session data through the real report machinery — see swinglab.sample),
    # then served with no auth at /sample-report/.
    sample_dir = sessions_dir / "sample-report"
    sample.ensure_sample_report(sample_dir, cfg)

    def current_user(request: Request) -> User | None:
        user_id = request.session.get("user_id")
        return users.get(user_id) if user_id else None

    def shop_active() -> bool:
        return bool(cfg.shop.get("enabled")) and shop.enabled()

    def claim_pending_pro(user: User) -> None:
        """Attach any Shopify Pro purchase made with this email before the
        account existed (or while logged out). Runs at signup and login."""
        days = users.pop_pending_grant(user.email)
        if days:
            users.grant_pro_days(user.id, days)

    def render(template: str, request: Request, **context) -> HTMLResponse:
        return HTMLResponse(
            env.get_template(template).render(
                brand=cfg.brand,
                user=current_user(request),
                require_account=bool(cfg.web.get("require_account")),
                billing_enabled=billing.enabled(),
                pro_store_url=(
                    shopify_billing.buy_url(cfg)
                    if shopify_billing.enabled()
                    else None
                ),
                free_per_month=int(cfg.billing["free_per_month"]),
                shop_enabled=shop_active(),
                mail_enabled=mailer.enabled(),
                club_labels=CLUB_LABELS,
                **context,
            )
        )

    def get_job_or_404(job_id: str, request: Request) -> Job:
        """404 for unknown ids AND other people's sessions (don't reveal
        which). Jobs with no owner (pre-accounts era, or open mode) stay
        reachable by link."""
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown session")
        if job.user_id is not None:
            user = current_user(request)
            if user is None or user.id != job.user_id:
                raise HTTPException(404, "Unknown session")
        return job

    def api_payload(job: Job) -> dict:
        payload = job.as_dict()
        payload["queue_position"] = manager.queue_position(job)
        if job.status == DONE and job.report_rel:
            payload["report_url"] = f"/session/{job.id}/files/{job.report_rel}"
            metrics = job.session_dir / Path(job.report_rel).parent / "metrics.json"
            if metrics.is_file():
                payload["metrics_url"] = (
                    f"/session/{job.id}/files/"
                    + str(metrics.relative_to(job.session_dir))
                )
        return payload

    def quota_left(user: User | None) -> int | None:
        """Analyses left this month, or None when unlimited/not applicable."""
        if not cfg.web.get("require_account") or user is None:
            return None
        limit = int(
            cfg.billing["pro_per_month"] if user.is_pro
            else cfg.billing["free_per_month"]
        )
        if limit <= 0:
            return None
        return max(0, limit - manager.usage_this_month(user.id))

    def personal_trend(user: User | None) -> str | None:
        """The user's own trend sentence, or None — never a made-up number
        (trend_sentence needs two sessions of real data), and never an
        error on a page that only wanted a nice-to-have line."""
        if user is None:
            return None
        try:
            return trend_sentence(
                build_trends(manager.list_recent(user_id=user.id), cfg)
            )
        except Exception:
            return None

    # -- pages ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        user = current_user(request)
        if cfg.web.get("require_account") and user is None:
            return render("web_login.html.j2", request, error=None, landing=True)
        left = quota_left(user)
        # The conversion moment: a free user out of analyses sees their own
        # trend next to the upgrade path — only when it truly exists.
        trend_line = (
            personal_trend(user)
            if left == 0 and user is not None and not user.is_pro
            else None
        )
        return render(
            "web_upload.html.j2",
            request,
            max_upload_mb=float(cfg.web.get("max_upload_mb") or 0),
            quota_left=left,
            trend_line=trend_line,
        )

    # -- public sample report (no auth — the wow-moment, un-walled) --------
    @app.get("/sample-report")
    def sample_report_redirect():
        # Trailing slash matters: the report's media paths are relative.
        return RedirectResponse("/sample-report/", status_code=307)

    @app.get("/sample-report/{file_path:path}")
    def sample_report_file(file_path: str = ""):
        root = sample_dir.resolve()
        target = (root / (file_path or "report.html")).resolve()
        if not target.is_relative_to(root):  # block path traversal
            raise HTTPException(404, "Not found")
        if not target.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(target)

    # -- accounts ---------------------------------------------------------
    def send_code_email(email: str, purpose: str) -> None:
        """Issue + email a one-time code; a rate-limited (None) issue means
        a still-valid code is already in the inbox, so send nothing."""
        code = users.issue_email_code(email, purpose)
        if code is None:
            return
        action = (
            "finish setting up your account"
            if purpose == "claim"
            else "reset your password"
        )
        mailer.send(
            email,
            f"{cfg.brand['name']} verification code: {code}",
            f"Your {cfg.brand['name']} verification code is {code}.\n\n"
            f"Enter it to {action}. The code expires in 10 minutes.\n"
            "If you didn't request this, you can ignore this email.",
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current_user(request) is not None:
            return RedirectResponse("/", status_code=303)
        return render(
            "web_login.html.j2", request, error=None, landing=False,
            prefill_email=request.query_params.get("email", ""),
        )

    @app.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form("")):
        # Throttle check FIRST — before the (deliberately expensive) scrypt
        # verification, so a brute-forcer can't burn CPU either. Keyed per
        # client IP AND per target email: a distributed guess at one account
        # and a spray from one address both hit a wall. Only FAILED attempts
        # are recorded, and the sliding window expires by itself — the
        # legitimate owner is never locked out beyond it.
        ip = client_ip(request)
        normalized_email = email.strip().lower()
        if not (
            throttle.allow("login-ip", ip, login_limit, LOGIN_WINDOW_S)
            and throttle.allow(
                "login-email", normalized_email, login_limit, LOGIN_WINDOW_S
            )
        ):
            page = render(
                "web_login.html.j2", request, landing=False,
                error=THROTTLED_MESSAGE,
            )
            page.status_code = 429
            return page
        user = users.authenticate(email, password)
        if user is None:
            throttle.record("login-ip", ip)
            throttle.record("login-email", normalized_email)
            # An unclaimed store account has no password to be wrong about —
            # point the customer at signup (prefilled) instead.
            pending = users.get_by_email(email)
            if pending is not None and not pending.has_password:
                return render(
                    "web_login.html.j2", request, landing=False, error=None,
                    stub_notice=True, prefill_email=pending.email,
                )
            return render(
                "web_login.html.j2", request, landing=False,
                error="Wrong email or password.",
            )
        claim_pending_pro(user)
        request.session["user_id"] = user.id
        return RedirectResponse("/", status_code=303)

    @app.post("/signup")
    def signup(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        code: str = Form(""),
        digest_opt: str = Form("", alias="digest"),
    ):
        wants_digest = digest_opt.lower() in ("on", "true", "1", "yes")
        # Signup throttle: per client IP, sliding hour window. Every signup
        # costs a scrypt hash (and, with SMTP on, an email) — this stops
        # throwaway-email loops from getting that CPU for free.
        ip = client_ip(request)
        if not throttle.allow("signup-ip", ip, signup_limit, SIGNUP_WINDOW_S):
            page = render(
                "web_login.html.j2", request, landing=False,
                error=THROTTLED_MESSAGE,
            )
            page.status_code = 429
            return page
        try:
            normalized = users.validate_signup(email, password)
        except ValueError as exc:
            return render(
                "web_login.html.j2", request, landing=False, error=str(exc)
            )
        # Record only attempts that clear validation — a typo'd password
        # doesn't cost the visitor one of their slots.
        throttle.record("signup-ip", ip)
        # When email is configured, claiming an address that already has
        # value attached (an unclaimed store account, or a Pro purchase
        # made before signup) must prove control of the inbox first. When
        # it isn't, signup proceeds exactly as before — see the README's
        # security note.
        if mailer.enabled() and users.has_unclaimed_value(normalized):
            if not code.strip():
                send_code_email(normalized, "claim")
                return render(
                    "web_login.html.j2", request, landing=False, error=None,
                    verify_email=normalized, verify_password=password,
                    verify_digest="on" if wants_digest else "",
                )
            if not users.check_email_code(normalized, "claim", code):
                return render(
                    "web_login.html.j2", request, landing=False,
                    verify_email=normalized, verify_password=password,
                    verify_digest="on" if wants_digest else "",
                    error="That code didn't match (or expired) — check the "
                          "email, or resubmit without a code for a new one.",
                )
        try:
            user = users.create(normalized, password)
        except ValueError as exc:
            return render(
                "web_login.html.j2", request, landing=False, error=str(exc)
            )
        if wants_digest:  # the signup checkbox is UNCHECKED by default
            users.set_digest_opt_in(user.id, True)
        claim_pending_pro(user)
        request.session["user_id"] = user.id
        return RedirectResponse("/", status_code=303)

    # -- password reset (available once SWINGLAB_SMTP_* is configured) ----
    @app.get("/reset", response_class=HTMLResponse)
    def reset_page(request: Request):
        if not mailer.enabled():
            raise HTTPException(503, "Password reset requires email to be set up.")
        return render(
            "web_login.html.j2", request, error=None, landing=False,
            reset_stage="request",
        )

    @app.post("/reset/request")
    def reset_request(request: Request, email: str = Form("")):
        if not mailer.enabled():
            raise HTTPException(503, "Password reset requires email to be set up.")
        normalized = email.strip().lower()
        user = users.get_by_email(normalized)
        if user is not None and user.has_password:
            send_code_email(normalized, "reset")
        # Same response either way — don't reveal which emails have accounts.
        return render(
            "web_login.html.j2", request, error=None, landing=False,
            reset_stage="confirm", reset_email=normalized,
        )

    @app.post("/reset/confirm")
    def reset_confirm(
        request: Request,
        email: str = Form(""),
        code: str = Form(""),
        password: str = Form(""),
    ):
        if not mailer.enabled():
            raise HTTPException(503, "Password reset requires email to be set up.")
        normalized = email.strip().lower()
        if len(password) < 8:
            # Reject before checking (and consuming) the single-use code.
            return render(
                "web_login.html.j2", request, landing=False,
                reset_stage="confirm", reset_email=normalized,
                error="Password must be at least 8 characters.",
            )
        user = users.get_by_email(normalized)
        if user is None or not users.check_email_code(normalized, "reset", code):
            return render(
                "web_login.html.j2", request, landing=False,
                reset_stage="confirm", reset_email=normalized,
                error="That code didn't match (or expired) — request a new one.",
            )
        users.set_password(user.id, password)
        claim_pending_pro(user)
        request.session["user_id"] = user.id
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return render(
            "web_account.html.j2",
            request,
            usage=manager.usage_this_month(user.id),
            quota_left=quota_left(user),
            upgraded="upgraded" in request.query_params,
            pro_until_date=(
                time.strftime("%B %d, %Y", time.localtime(user.pro_until))
                if user.pro_until > time.time()
                else None
            ),
        )

    @app.post("/account/digest")
    def account_digest(request: Request, enabled: str = Form("")):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        users.set_digest_opt_in(
            user.id, enabled.lower() in ("on", "true", "1", "yes")
        )
        return RedirectResponse("/account", status_code=303)

    @app.get("/email/unsubscribe", response_class=HTMLResponse)
    def email_unsubscribe(request: Request, token: str = ""):
        """One-click opt-out from the weekly digest. Works logged out: the
        HMAC token (signed with SWINGLAB_SECRET) proves the link came from
        an email we sent. Idempotent — a second click is still a 200."""
        user_id = digest.verify_unsubscribe_token(token, secret)
        if user_id is None or users.get(user_id) is None:
            raise HTTPException(404, "That unsubscribe link isn't valid.")
        users.set_digest_opt_in(user_id, False)
        return render("web_unsubscribed.html.j2", request)

    # -- progress dashboard ------------------------------------------------
    @app.get("/progress", response_class=HTMLResponse)
    def progress_page(request: Request):
        if not cfg.web.get("require_account"):
            # No accounts, no per-user history to chart — same rule as the
            # other account-shaped surfaces.
            raise HTTPException(404, "Progress tracking needs accounts enabled.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        listed = manager.list_recent(user_id=user.id)
        # Club filter — display context only, shown once >1 club is present.
        clubs_present = sorted({j.club for j in listed if j.club})
        club_selected = request.query_params.get("club") or ""
        if club_selected not in clubs_present:
            club_selected = ""
        if club_selected:
            listed = [j for j in listed if j.club == club_selected]
        trends = build_trends(listed, cfg)
        explainers = build_explainers(cfg.coaching)
        cards = []
        if trends.session_count >= 2:
            for name, mt in trends.metrics.items():
                values = [v for _, v in mt.points]
                improved = None
                if mt.delta is not None and mt.worse is not None and mt.delta != 0:
                    moved_worse = (
                        mt.delta > 0 if mt.worse == "higher" else mt.delta < 0
                    )
                    improved = not moved_worse
                cards.append({
                    "label": mt.label,
                    "sessions": len(values),
                    "chart": trend_chart(
                        values, mt.benchmark, cfg.brand,
                        worse=mt.worse or "higher",
                    ),
                    "latest": format_value(name, mt.latest),
                    "best": (
                        format_value(name, mt.best)
                        if mt.best is not None else "\N{EM DASH}"
                    ),
                    "delta": (
                        format_delta(name, mt.delta)
                        if mt.delta is not None else "\N{EM DASH}"
                    ),
                    "delta_class": (
                        "" if improved is None else "good" if improved else "bad"
                    ),
                    "benchmark_text": mt.benchmark_text,
                    # Same plain-English strings the report's expanders use.
                    "explainer": explainers.get(name),
                })
        span = None
        if trends.session_count >= 2:
            span = " \N{EN DASH} ".join(
                time.strftime("%b %d", time.localtime(ts))
                for ts in (
                    trends.samples[0].finished_at, trends.samples[-1].finished_at
                )
            )
        return render(
            "web_progress.html.j2",
            request,
            cards=cards,
            flags_strip=[
                {"label": FLAG_LABELS.get(flag, flag), "count": count}
                for flag, count in trends.flag_counts.items()
            ],
            session_count=trends.session_count,
            span=span,
            sentence=trend_sentence(trends),
            latest_job_id=(
                trends.samples[-1].job_id if trends.samples else None
            ),
            clubs_present=clubs_present,
            club_selected=club_selected,
        )

    @app.get("/pricing", response_class=HTMLResponse)
    def pricing(request: Request):
        # The quiet personal line: only for logged-in users with >= 2
        # sessions of real data (trend_sentence is None otherwise).
        return render(
            "web_pricing.html.j2", request,
            trend_line=personal_trend(current_user(request)),
            # Display strings only — the store/Stripe stays the source of
            # truth for what is actually charged.
            pro_price_annual_text=cfg.billing.get("pro_price_annual_text"),
            pro_price_monthly_text=cfg.billing.get("pro_price_monthly_text"),
        )

    # -- gear shop --------------------------------------------------------
    @app.get("/shop", response_class=HTMLResponse)
    def shop_page(request: Request):
        if not shop_active():
            raise HTTPException(404, "The gear shop isn't set up.")
        return render(
            "web_shop.html.j2", request, products=shop.fetch_products(cfg)
        )

    # -- billing ----------------------------------------------------------
    @app.post("/billing/checkout")
    def checkout(request: Request):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not billing.enabled():
            raise HTTPException(503, "Payments aren't set up yet.")
        return RedirectResponse(
            billing.create_checkout_url(user, _base_url(request)), status_code=303
        )

    @app.post("/billing/portal")
    def portal(request: Request):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not billing.enabled() or not user.stripe_customer_id:
            raise HTTPException(503, "No subscription to manage yet.")
        return RedirectResponse(
            billing.create_portal_url(user, _base_url(request)), status_code=303
        )

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request):
        if not billing.enabled():
            raise HTTPException(503, "Payments aren't set up.")
        payload = await request.body()
        try:
            billing.handle_webhook(
                payload, request.headers.get("stripe-signature", ""), users
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return JSONResponse({"received": True})

    @app.post("/webhooks/shopify")
    async def shopify_webhook(request: Request):
        if not shopify_billing.enabled():
            raise HTTPException(503, "Shopify billing isn't set up.")
        payload = await request.body()
        try:
            shopify_billing.handle_webhook(
                payload,
                request.headers.get("x-shopify-hmac-sha256", ""),
                request.headers.get("x-shopify-topic", ""),
                users,
                cfg,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return JSONResponse({"received": True})

    # -- analysis ---------------------------------------------------------
    @app.post("/upload")
    async def upload(
        request: Request,
        video: UploadFile = File(...),
        hand: str = Form("right"),
        angle: str = Form("face-on"),
        club: str = Form(""),
        strikes: str = Form(""),
        fast: str = Form(""),
    ):
        user = current_user(request)
        ensure_user_can_analyze(user, manager, cfg)
        suffix = Path(video.filename or "clip.mov").suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            raise HTTPException(
                400,
                f"Unsupported file type {suffix or '(none)'}; expected one of "
                + ", ".join(sorted(VIDEO_SUFFIXES)),
            )
        manual_strikes = None
        if strikes.strip():
            try:
                manual_strikes = [
                    float(part)
                    for part in strikes.replace(";", ",").split(",")
                    if part.strip()
                ]
            except ValueError:
                raise HTTPException(400, f'Bad strike times: "{strikes}"')
        if hand not in ("right", "left"):
            raise HTTPException(400, 'hand must be "right" or "left"')
        if angle not in ANGLES:
            raise HTTPException(
                400, 'angle must be "face-on" or "dtl" (down the line)'
            )
        if club and club not in CLUB_LABELS:
            raise HTTPException(
                400,
                "club must be one of: " + ", ".join(sorted(CLUB_LABELS)),
            )

        ip = client_ip(request)
        per_ip = int(cfg.web.get("max_active_jobs_per_ip") or 0)
        if ip and per_ip and manager.active_for_ip(ip) >= per_ip:
            raise HTTPException(
                429,
                f"You already have {per_ip} analyses queued or running — "
                "wait for one to finish before uploading another clip.",
            )

        job = manager.create_session(
            source_name=video.filename,
            hand=hand,
            angle=angle,
            club=club or None,
            strikes=manual_strikes,
            fast=fast.lower() in ("on", "true", "1", "yes"),
            client_ip=ip,
            user_id=user.id if user else None,
        )
        max_mb = float(cfg.web.get("max_upload_mb") or 0)
        max_bytes = int(max_mb * 1024 * 1024)
        dest = job.session_dir / f"source{suffix}"
        received = 0
        try:
            with open(dest, "wb") as fh:
                while chunk := await video.read(UPLOAD_CHUNK):
                    received += len(chunk)
                    if max_bytes and received > max_bytes:
                        manager.discard(job)
                        raise HTTPException(
                            413,
                            f"Video is larger than the {max_mb:g} MB upload limit.",
                        )
                    fh.write(chunk)
        except OSError:
            manager.discard(job)
            raise HTTPException(
                500, "Could not store the upload — the server may be out of disk."
            )
        except ClientDisconnect:
            # The uploader hung up mid-transfer. Without this cleanup the
            # half-written job would sit QUEUED forever — eating one of the
            # visitor's max_active_jobs_per_ip slots AND a monthly-quota
            # analysis for a video that never arrived. discard() removes the
            # partial file and the job row, so neither is counted.
            manager.discard(job)
            # Nobody is listening, but the middleware stack still wants a
            # response object to unwind cleanly.
            return JSONResponse(
                {"detail": "Upload interrupted — the connection closed before "
                           "the video finished uploading."},
                status_code=400,
            )
        except asyncio.CancelledError:
            # Server-side cancellation (shutdown, or an ASGI server that maps
            # disconnects to task cancellation): same cleanup, but the
            # cancellation itself must keep propagating.
            manager.discard(job)
            raise
        manager.submit(job, dest)
        if _wants_json(request):
            return JSONResponse({"id": job.id, "url": f"/session/{job.id}"})
        return RedirectResponse(f"/session/{job.id}", status_code=303)

    def gear_for(job: Job) -> list[dict]:
        """Training gear matched to a finished analysis's flags. Empty when
        the shop is off, the metrics are unreadable, or nothing matches —
        the results page simply omits the section."""
        if job.status != DONE or not job.report_rel or not shop_active():
            return []
        metrics = job.session_dir / Path(job.report_rel).parent / "metrics.json"
        try:
            payload = json.loads(metrics.read_text(encoding="utf-8"))
            flags = flag_keys(payload, cfg) if isinstance(payload, dict) else []
        except (OSError, ValueError):
            flags = []
        return shop.recommend(shop.fetch_products(cfg), flags, cfg)

    @app.get("/session/{job_id}", response_class=HTMLResponse)
    def status_page(job_id: str, request: Request):
        job = get_job_or_404(job_id, request)
        failed = job.status == FAILED
        return render(
            "web_status.html.j2",
            request,
            job=job,
            done=job.status == DONE,
            failed=failed,
            # Plain-English guidance instead of pipeline/CLI jargon; the raw
            # error stays available via the JSON API.
            error_help=humanize.friendly_error(job.error) if failed else None,
            queue_position=manager.queue_position(job),
            gear=gear_for(job),
        )

    @app.get("/sessions", response_class=HTMLResponse)
    def sessions_page(request: Request):
        user = current_user(request)
        if cfg.web.get("require_account"):
            if user is None:
                return RedirectResponse("/login", status_code=303)
            listed = manager.list_recent(user_id=user.id)
        else:
            listed = manager.list_recent()
        return render("web_sessions.html.j2", request, sessions=listed)

    @app.get("/session/{job_id}/report")
    def report(job_id: str, request: Request):
        job = get_job_or_404(job_id, request)
        if job.status != DONE or not job.report_rel:
            return RedirectResponse(f"/session/{job_id}")
        return RedirectResponse(f"/session/{job_id}/files/{job.report_rel}")

    @app.get("/session/{job_id}/files/{file_path:path}")
    def session_file(job_id: str, file_path: str, request: Request):
        job = get_job_or_404(job_id, request)
        root = job.session_dir.resolve()
        target = (root / file_path).resolve()
        if not target.is_relative_to(root):  # block path traversal
            raise HTTPException(404, "Not found")
        if not target.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(target)

    # -- JSON API (what a future mobile app talks to) ----------------------
    @app.get("/api/session/{job_id}")
    def api_status(job_id: str, request: Request):
        return JSONResponse(api_payload(get_job_or_404(job_id, request)))

    @app.get("/api/sessions")
    def api_sessions(request: Request):
        user = current_user(request)
        if cfg.web.get("require_account"):
            if user is None:
                raise HTTPException(401, "Log in first.")
            listed = manager.list_recent(user_id=user.id)
        else:
            listed = manager.list_recent()
        return JSONResponse(
            {
                "sessions": [
                    {
                        "id": job.id,
                        "status": job.status,
                        "created_at": job.as_dict()["created_at"],
                        "source_name": job.source_name,
                        "swings_done": job.swings_done,
                        "swings_total": job.swings_total,
                    }
                    for job in listed
                ]
            }
        )

    @app.get("/healthz")
    def healthz():
        # disk_free_mb + sessions_count: disk-full is the most likely first
        # outage — this makes it visible to uptime monitors before it lands.
        return JSONResponse(
            {
                "status": "ok",
                **manager.counts(),
                "disk_free_mb": shutil.disk_usage(sessions_dir).free // (1024 * 1024),
                "sessions_count": manager.sessions_count(),
            }
        )

    # Weekly practice-plan digest: hourly daemon thread, started ONLY when
    # SMTP is configured AND web.digest_enabled is on — otherwise None and
    # zero behavior (see digest.py for the consent + claim-before-send rules).
    app.state.digest_thread = digest.start_scheduler(manager, users, cfg, secret)

    return app
