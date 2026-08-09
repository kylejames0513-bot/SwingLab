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
passwordless "store accounts" that a verified signup with the same email
claims in place — purchases and the Shopify link carry over
(shopify_billing.py). Claims of pre-existing store identity or value always
require an emailed one-time code; they fail closed when delivery is
unavailable. Email delivery also supports password reset.

One account (web.passwordless_login, on by default but self-disabling
without email delivery): the login page asks for the email first and mails a
six-digit sign-in code — the same flow logs into an existing account,
claims an unclaimed store account with everything bought intact, or
creates a new account, so the store email IS the app identity and nobody
sets a password unless they want one ("Add a password" on /account, "use
your password instead" on the login page). Without email delivery, independent
local password flows remain available only where no pre-existing store
identity or value requires inbox proof.

The optional gear shop (a /shop page plus flag-matched training-aid
recommendations on finished analyses) is likewise inert until the SHOPIFY_*
environment variables are set — see shop.py.

Retention surfaces: /progress charts each account's metrics across finished
sessions (swinglab.trends + diagrams.trend_chart), and an opt-in weekly
practice-plan email (digest.py) runs on an hourly scheduler thread — only
when email delivery is configured and web.digest_enabled is on, and only to users who
asked for it.

Lifecycle email (all claim-before-send, at most once per subject): a paid
Shopify Pro order confirms itself or nudges account activation
(shopify_billing.py), an upload with the "email me when my coaching is
ready" checkbox gets one completion email — report link, or the humanized
failure guidance (jobs.py + humanize.py) — and a daily thread reminds
time-boxed Pro ~7 days before it lapses (digest.py, transactional, not
gated by digest consent).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import mimetypes
import os
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import ClientDisconnect

from .. import sample
from ..caddie_brief import (
    build_caddie_brief_from_payload,
    payload_has_coachable_data,
    payload_is_coaching_eligible,
    payload_has_unsupported_angle_data,
    payload_requires_refilm,
    payload_structure_is_valid,
)
from ..clubs import CLUB_LABELS
from ..coaching import flag_keys, priority_rule_version
from ..levels import LEVEL_LABELS
from ..config import Config
from . import api_models
from ..diagrams import drill_animation, drill_diagram, trend_chart
from ..drills import PLAN_TITLES, build_drills, gear_shop_url
from ..explainers import build_explainers
from ..metrics import ANGLES
from ..proof_cycle_artifact import (
    ARTIFACT_FILENAME,
    active_proof_cycle_target_for_context,
    proof_cycle_enabled,
    proof_cycle_history_scan_limit,
    proof_cycle_view,
    verified_proof_cycle_artifact,
)
from ..proof_cycle_practice import (
    practice_assignment_from_target,
    practice_transfer_view,
)
from ..report import (
    REPORT_OUTCOME_CAPTURE,
    REPORT_OUTCOME_COACHING,
    persisted_priority_rule_version,
    persisted_report_outcome,
)
from ..report_html import write_report_document_html
from ..trends import (
    FLAG_LABELS,
    build_trends,
    format_delta,
    format_value,
    session_sample,
    trend_sentence,
)
from ..integrations.shopify import admin as shopify_admin
from ..integrations.shopify import customer_accounts as shopify_customer_accounts
from ..integrations.shopify import customer_sync as shopify_customer_sync
from . import billing, digest, humanize, mailer, shop, shopify_billing
from .jobs import (
    DONE,
    FAILED,
    HistoryResetConflict,
    HistoryResetError,
    Job,
    JobManager,
)
from .throttle import Throttle
from .users import (
    MobileAPIToken,
    MobileAPITokenAuthEpochError,
    MobileAPITokenLimitError,
    PRODUCT_EVENT_NAMES,
    GolferProfile,
    HistoryAuthEpochError,
    HistoryEpochError,
    HistoryPrivacyExportConflict,
    PasswordAddConflict,
    User,
    UserStore,
    shopify_remote_privacy_lock,
)

logger = logging.getLogger("swinglab.web")

VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
UPLOAD_CHUNK = 1024 * 1024

LOGIN_WINDOW_S = 15 * 60  # window for web.login_attempts_per_15min
SIGNUP_WINDOW_S = 3600  # window for web.signups_per_hour_per_ip
# Pro time further out than this displays as "Lifetime" rather than a date
# (the SL-PRO-LIFE grant is 100 years of days; see billing.shopify_skus).
LIFETIME_DISPLAY_MIN_S = 50 * 365 * 86400
THROTTLED_MESSAGE = "Too many attempts — wait a few minutes and try again."
EMAIL_DELIVERY_MESSAGE = (
    "We couldn't send that email right now. Please try again in a moment."
)
EMAIL_DELIVERY_UNCERTAIN_MESSAGE = (
    "We couldn't confirm delivery. If a code arrives, it will still work; "
    "otherwise try again in a minute."
)
SHOPIFY_WEBHOOK_MAX_BODY_BYTES = 1024 * 1024
# The free matched re-film (allowances.free_matched_refilm) stays open this
# long after its coaching-ready baseline — long enough for a real practice
# week, short enough that the comparison still means something.
MATCHED_REFILM_WINDOW_DAYS = 14
LOGIN_FLOW_SESSION_KEY = "email_login_flow_nonce"
SIGNUP_FLOW_SESSION_KEY = "password_signup_flow_nonce"
PRODUCT_ANON_SESSION_KEY = "product_anon_id"
PRODUCT_EVENT_MAX_BODY_BYTES = 8 * 1024
SHOPIFY_ACCOUNT_BROWSER_SESSION_KEY = "shopify_customer_account_session"
HISTORY_RESET_SESSION_KEY = "history_reset_confirmation"
HISTORY_RESET_FLASH_KEY = "history_reset_flash"
HISTORY_SESSION_EPOCH_KEY = "history_epoch"
HISTORY_RESET_CONFIRMATION = "START OVER"
HISTORY_RESET_NONCE_TTL_S = 10 * 60
HISTORY_RESET_RECENT_AUTH_S = 15 * 60
PASSWORD_ADDED_REAUTH_SESSION_KEY = "password_added_requires_reauth"


def _shopify_sync_cohort_percent(raw: str | None) -> float:
    """Validate the explicit second gate for outbound customer creation."""

    try:
        value = float("0" if raw is None or not raw.strip() else raw)
    except (TypeError, ValueError, OverflowError):
        raise shopify_admin.ShopifyAdminConfigurationError(
            "Shopify customer synchronization cohort percentage is invalid."
        ) from None
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise shopify_admin.ShopifyAdminConfigurationError(
            "Shopify customer synchronization cohort percentage is invalid."
        )
    return value


def _cohort_includes_email(email: str, percent: float, secret: str) -> bool:
    """Select a stable cohort without logging or persisting customer PII."""

    if percent <= 0:
        return False
    if percent >= 100:
        return True
    digest = hmac.new(
        secret.encode("utf-8"),
        email.strip().lower().encode("utf-8"),
        hashlib.sha256,
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return bucket < percent / 100.0


async def _read_bounded_request_body(
    request: Request, max_bytes: int
) -> bytes:
    """Read an unauthenticated webhook body without unbounded buffering."""

    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length header.") from None
        if content_length < 0:
            raise HTTPException(400, "Invalid Content-Length header.")
        if content_length > max_bytes:
            raise HTTPException(413, "Webhook payload is too large.")

    payload = bytearray()
    try:
        async for chunk in request.stream():
            if len(payload) + len(chunk) > max_bytes:
                raise HTTPException(413, "Webhook payload is too large.")
            payload.extend(chunk)
    except ClientDisconnect:
        raise HTTPException(400, "Webhook upload was interrupted.") from None
    return bytes(payload)


def _normalized_origin(value: str) -> tuple[str, str, int] | None:
    """Reduce an HTTP(S) URL/origin to a comparable origin tuple."""

    try:
        parsed = urlsplit(str(value or "").strip())
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        if scheme not in ("http", "https") or not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


def _serialized_origin(origin: tuple[str, str, int]) -> str:
    scheme, hostname, port = origin
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port == default_port else f":{port}"
    return f"{scheme}://{rendered_host}{suffix}"


def _same_origin_form_post(request: Request) -> bool:
    """Reject browser-declared cross-origin form posts.

    SameSite=Lax already withholds the session cookie on ordinary cross-site
    POSTs. Origin/Referer validation is a second guard while retaining
    compatibility with non-browser clients that send neither header.
    """

    source = request.headers.get("origin")
    if source is None:
        source = request.headers.get("referer")
    if source is None:
        return True
    expected = os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)
    source_origin = _normalized_origin(source)
    expected_origin = _normalized_origin(expected)
    return (
        source_origin is not None
        and expected_origin is not None
        and hmac.compare_digest(
            repr(source_origin).encode("utf-8"),
            repr(expected_origin).encode("utf-8"),
        )
    )


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
    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
    )
    logger.info("Sentry error monitoring enabled.")
    return True


def client_ip(request: Request) -> str | None:
    """The client IP every limit/throttle keys on. With web.trusted_proxies
    configured, ProxyHeadersMiddleware has already rewritten request.client
    from X-Forwarded-For — so behind Railway (or any proxy) this is the real
    visitor, not the proxy. Every request-IP read goes through here."""
    return request.client.host if request.client else None


def exact_job_context(job: Job | None) -> tuple[str, str, str] | None:
    """Authoritative club, hand, and angle, or no comparable context."""

    if job is None:
        return None
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


def context_label(context: tuple[str, str, str] | None) -> str | None:
    if context is None:
        return None
    club, hand, angle = context
    return " · ".join(
        (
            CLUB_LABELS[club],
            "Right-handed" if hand == "right" else "Left-handed",
            "Face-on" if angle == "face-on" else "Down-the-line",
        )
    )


def matched_refilm_enabled(cfg: Config) -> bool:
    """Only the literal boolean true activates the free matched re-film."""

    return cfg.allowances.get("free_matched_refilm") is True


def matched_refilm_baseline(
    user: User | None,
    manager: JobManager,
    cfg: Config,
    *,
    now: float | None = None,
) -> Job | None:
    """This month's coaching-ready session that a free matched re-film may
    follow, or None when the credit cannot apply.

    The credit closes the free tier's proof loop: every surface teaches
    film -> practice -> re-film, so a free account that earned a
    coaching-ready baseline this calendar month (UTC, the same window as
    the allowance) keeps ONE more upload free while it stays comparable —
    same club, handedness, and camera angle, within 14 days of that
    baseline. Pro accounts never need it, and the flag fails closed like
    every other boolean gate. Whether the credit is still unspent is the
    caller's arithmetic (usage against the limit); this only finds the
    latest baseline that makes it possible.
    """
    if user is None or user.is_pro or not matched_refilm_enabled(cfg):
        return None
    timestamp = time.time() if now is None else now
    month_start = (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    window_s = MATCHED_REFILM_WINDOW_DAYS * 86400
    for job in manager.list_recent(user_id=user.id):
        created_at = float(job.created_at or 0.0)
        if job.status != DONE or not created_at:
            continue
        if created_at < month_start or timestamp - created_at > window_s:
            continue
        if exact_job_context(job) is None:
            continue
        if manager.coaching_eligible(job):
            return job
    return None


def ensure_user_can_analyze(
    user: User | None,
    manager: JobManager,
    cfg: Config,
    *,
    declared_context: tuple[str, str, str] | None = None,
) -> None:
    """The paywall. Free accounts get billing.free_per_month analyses per
    calendar month; Pro gets billing.pro_per_month (0 = unlimited). With
    allowances.free_matched_refilm on, a free account whose allowance is
    spent still gets ONE upload that matches this month's coaching-ready
    baseline (declared_context carries the upload's declared club, hand,
    and angle); a mismatched or second extra upload stays blocked."""
    if not cfg.web.get("require_account"):
        return
    if user is None:
        raise HTTPException(401, "Log in to analyze a swing.")
    limit = int(
        cfg.billing["pro_per_month"] if user.is_pro else cfg.billing["free_per_month"]
    )
    if limit <= 0:  # unlimited
        return
    used = manager.usage_this_month(user.id)
    if used >= limit:
        noun = "analysis" if limit == 1 else "analyses"
        if user.is_pro:
            raise HTTPException(
                402,
                f"You've reached this month's limit of {limit} {noun}. "
                "It resets on the 1st.",
            )
        baseline = matched_refilm_baseline(user, manager, cfg)
        if baseline is not None and used < limit + 1:
            required = exact_job_context(baseline)
            if declared_context == required:
                # The free matched re-film: the one extra upload that keeps
                # the film -> practice -> re-film loop closable on free.
                return
            raise HTTPException(
                402,
                f"You've used your {limit} free {noun} this month, but your "
                "matched re-film is still free: film "
                f"{context_label(required)} to match your coaching-ready "
                "baseline and this upload consumes nothing. A different "
                "club, handedness, or camera angle needs the normal "
                "allowance — upgrade to Pro for that, or come back on "
                "the 1st.",
            )
        pro_limit = int(cfg.billing["pro_per_month"])
        pro_allowance = (
            "unlimited swings"
            if pro_limit <= 0
            else f"up to {pro_limit} analyses each month"
        )
        raise HTTPException(
            402,
            f"You've used your {limit} free {noun} this month. "
            f"Upgrade to Pro for {pro_allowance} — or come back on the 1st.",
        )


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _base_url(request: Request) -> str:
    return os.environ.get("PUBLIC_BASE_URL") or str(request.base_url).rstrip("/")


def create_app(
    cfg: Config | None = None,
    sessions_dir: str | Path = "sessions",
    *,
    shopify_admin_client: shopify_admin.ShopifyAdminClient | None = None,
    start_shopify_sync_worker: bool = True,
) -> FastAPI:
    cfg = cfg or Config.load()
    shopify_sync_settings = shopify_customer_sync.validate_sync_settings(
        cfg.shopify_customer_sync
    )
    shopify_sync_enabled = shopify_sync_settings["enabled"]
    shopify_sync_cohort_percent = _shopify_sync_cohort_percent(
        os.environ.get("SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT")
    )
    configured_session_secret = os.environ.get("SWINGLAB_SECRET")
    if (
        shopify_sync_enabled
        and shopify_sync_cohort_percent > 0
        and not configured_session_secret
    ):
        raise shopify_admin.ShopifyAdminConfigurationError(
            "A stable SWINGLAB_SECRET is required for Shopify sync cohorts."
        )
    init_sentry()
    sessions_dir = Path(sessions_dir)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    # Users first: the job runner needs the store to decide the coach-replay
    # Pro gate (billing.replay_pro_only) at analysis time.
    users = UserStore(sessions_dir / "swinglab.db")
    manager = JobManager(
        sessions_dir,
        cfg,
        user_store=users,
        guided_html_writer=write_report_document_html,
    )
    throttle = Throttle(sessions_dir / "swinglab.db")
    code_send_locks: dict[
        tuple[str, str], tuple[threading.Lock, int]
    ] = {}
    code_send_locks_guard = threading.Lock()
    app = FastAPI(title=f"{cfg.brand['name']} — swing analysis")
    app.state.jobs = manager
    app.state.users = users
    app.state.cfg = cfg
    static_dir = Path(__file__).parent / "static"
    # Static assets contain only versioned public brand imagery and the
    # install shell (icon + service worker), never a report, user data, or
    # video.  The worker itself caches public help/offline pages only;
    # completed reports remain network-only.
    mimetypes.add_type("image/webp", ".webp", strict=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Customer Account sign-in is a separate, explicitly enabled migration
    # feature.  Invalid enabled configuration stops startup rather than
    # silently dropping visitors back into a weaker/ambiguous login path.
    customer_account_settings = (
        shopify_customer_accounts.CustomerAccountSettings.from_env()
    )
    shopify_customer_account_client = (
        shopify_customer_accounts.ShopifyCustomerAccountClient(
            customer_account_settings
        )
        if customer_account_settings is not None
        else None
    )
    app.state.shopify_customer_accounts = shopify_customer_account_client

    shopify_sync_coordinator = None
    if shopify_sync_enabled:
        binding_status = None
        binding_error = None
        if shopify_admin_client is None:
            try:
                shopify_admin_client = (
                    shopify_admin.ShopifyAdminClient.from_env(
                        timeout_seconds=float(
                            shopify_sync_settings.get(
                                "request_timeout_seconds"
                            )
                            or 10
                        ),
                    )
                )
            except shopify_admin.ShopifyAdminError:
                # Authentication/configuration drift blocks only outbound
                # sync. The rest of the web app and inbound signed webhooks
                # remain healthy and the PII-free state is visible below.
                binding_status = "unverifiable"
                binding_error = (
                    "Shopify Admin API authentication is unavailable."
                )
        shopify_sync_coordinator = (
            shopify_customer_sync.ShopifyCustomerSyncCoordinator(
                users,
                shopify_admin_client,
                shopify_sync_settings,
                binding_db_path=sessions_dir / "swinglab.db",
                initial_binding_status=binding_status,
                initial_binding_error=binding_error,
                start=False,
            )
        )
    app.state.shopify_admin_client = shopify_admin_client
    app.state.shopify_sync = shopify_sync_coordinator
    if shopify_sync_coordinator is not None:
        if start_shopify_sync_worker:
            app.router.add_event_handler(
                "startup", shopify_sync_coordinator.start
            )
        app.router.add_event_handler(
            "shutdown", shopify_sync_coordinator.shutdown
        )

    secret = configured_session_secret
    if not secret:
        secret = secrets.token_hex(32)
        if cfg.web.get("require_account"):
            logger.warning(
                "SWINGLAB_SECRET is not set — logins will not survive "
                "a restart. Set it to a long random string in the environment."
            )
    public_origin = _normalized_origin(os.environ.get("PUBLIC_BASE_URL"))
    storefront_origin = _normalized_origin(cfg.shop.get("store_url"))
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        same_site="lax",
        https_only=bool(
            public_origin is not None
            and public_origin[0] == "https"
        ),
    )

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
        # Templates use the compound ``.html.j2`` suffix, so ``html`` alone
        # does not match. Escape every Jinja HTML template by default; only
        # the audited SVG/animation fragments marked ``|safe`` opt out.
        autoescape=select_autoescape(["html", "j2"]),
    )

    # The public sample report: generated once at startup if absent (synthetic
    # session data through the real report machinery — see swinglab.sample),
    # then served with no auth at /sample-report/.
    sample_dir = sessions_dir / "sample-report"
    sample.ensure_sample_report(sample_dir, cfg)

    def current_user(request: Request) -> User | None:
        user_id = request.session.get("user_id")
        if not user_id:
            return None
        user = users.get(user_id)
        try:
            session_epoch = int(request.session.get("auth_epoch", 0))
        except (TypeError, ValueError):
            session_epoch = -1
        if user is None or session_epoch != user.auth_epoch:
            request.session.clear()
            return None
        return user

    def mobile_bearer_unauthorized() -> HTTPException:
        """Return one non-enumerating error for every bad device credential."""

        return HTTPException(
            401,
            "Invalid mobile access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def mobile_bearer_token(request: Request) -> str | None:
        """Read one strict Bearer token without accepting cookie fallback.

        The helper is called only from routes that intentionally support a
        native-client credential.  If any Authorization header is present,
        malformed values are failures rather than an excuse to silently use a
        browser session that happened to accompany the request.
        """

        authorization = request.headers.get("authorization")
        if authorization is None:
            return None
        scheme, separator, token = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not token
            or " " in token
        ):
            raise mobile_bearer_unauthorized()
        return token

    def api_v1_auth(request: Request) -> tuple[User, bool]:
        """Authenticate an owned mobile API call by bearer or cookie.

        ``True`` means a non-ambient Authorization header authenticated the
        request; those calls do not need browser Origin/Referer CSRF checks.
        Cookie-authenticated mutations keep their existing same-origin guard.
        """

        if not cfg.web.get("require_account"):
            raise HTTPException(404, "Account API is not enabled.")
        bearer = mobile_bearer_token(request)
        if bearer is not None:
            user = users.authenticate_mobile_api_token(bearer)
            if user is None:
                raise mobile_bearer_unauthorized()
            return user, True
        user = current_user(request)
        if user is None:
            raise HTTPException(401, "Log in first.")
        return user, False

    def session_access_user(request: Request) -> User | None:
        """Resolve a session/report owner, rejecting bad bearer credentials.

        Legacy ownerless sessions remain link-accessible in open mode.  A
        bearer header opts into the account-only mobile auth path, so it never
        broadens that legacy behavior.
        """

        if request.headers.get("authorization") is not None:
            user, _ = api_v1_auth(request)
            return user
        return current_user(request)

    def mobile_token_management_user(request: Request) -> User:
        """Require a same-origin browser session for device-token lifecycle.

        A device token cannot mint, enumerate, or revoke other device tokens.
        This is a deliberate cookie-only recovery/manage surface; an
        Authorization header is rejected even when the browser cookie is also
        present so invalid bearer input cannot fall back to that cookie.
        """

        if request.headers.get("authorization") is not None:
            raise mobile_bearer_unauthorized()
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        if not cfg.web.get("require_account"):
            raise HTTPException(404, "Account API is not enabled.")
        user = current_user(request)
        if user is None:
            raise HTTPException(401, "Log in first.")
        return user

    def establish_session(
        request: Request,
        user: User,
        *,
        fresh_auth: bool = True,
    ) -> None:
        """Bind a signed cookie and remember genuine authentication recency."""

        request.session["user_id"] = user.id
        request.session["auth_epoch"] = user.auth_epoch
        if fresh_auth:
            request.session[HISTORY_SESSION_EPOCH_KEY] = user.history_epoch
            request.session["authenticated_at"] = time.time()
            request.session.pop(PASSWORD_ADDED_REAUTH_SESSION_KEY, None)

    def flow_session_nonce(
        request: Request, key: str, *, create: bool = False
    ) -> str | None:
        """Read or mint an opaque nonce held only in the signed session."""

        value = request.session.get(key)
        if isinstance(value, str) and 20 <= len(value) <= 256:
            return value
        request.session.pop(key, None)
        if not create:
            return None
        value = secrets.token_urlsafe(32)
        request.session[key] = value
        return value

    def clear_flow_session_nonce(request: Request, key: str) -> None:
        request.session.pop(key, None)

    def product_anonymous_id(request: Request) -> str:
        """Return a signed-browser, non-PII identifier for funnel counts."""

        existing = request.session.get(PRODUCT_ANON_SESSION_KEY)
        if isinstance(existing, str) and 16 <= len(existing) <= 128:
            return existing
        anonymous_id = "a" + secrets.token_urlsafe(18)
        request.session[PRODUCT_ANON_SESSION_KEY] = anonymous_id
        return anonymous_id

    def record_product_event(
        request: Request,
        event_name: str,
        *,
        user: User | None = None,
        session_id: str | None = None,
        dedupe_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Best-effort first-party instrumentation that never blocks golf.

        Event rows are deliberately minimal and error handling stays silent to
        visitors: a measurement outage must not prevent a signup, upload, or
        results page.  The stored event schema forbids emails, request bodies,
        IP addresses, and free-form client properties.
        """

        try:
            resolved_user = user or current_user(request)
            users.record_product_event(
                event_name,
                user_id=resolved_user.id if resolved_user is not None else None,
                session_id=session_id,
                anonymous_id=(
                    None if resolved_user is not None else product_anonymous_id(request)
                ),
                metadata=metadata,
                dedupe_key=dedupe_key,
                expected_history_epoch=(
                    resolved_user.history_epoch
                    if resolved_user is not None
                    else None
                ),
            )
        except Exception:
            logger.warning("Product event write unavailable (event=%s).", event_name)

    async def bounded_json_object(request: Request) -> dict:
        raw = await _read_bounded_request_body(request, PRODUCT_EVENT_MAX_BODY_BYTES)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(400, "Invalid JSON payload.") from None
        if not isinstance(payload, dict):
            raise HTTPException(400, "Invalid JSON payload.")
        return payload

    async def product_event_json(request: Request) -> dict:
        payload = await bounded_json_object(request)
        # Keep this API narrowly versioned.  It has no generic metadata sink,
        # so a future client cannot accidentally send email, video labels, or
        # practice notes into analytics.
        if set(payload) - {"event", "session_id"}:
            raise HTTPException(400, "Invalid event payload.")
        return payload

    def profile_payload(profile: GolferProfile | None) -> dict | None:
        if profile is None:
            return None
        return {
            "display_name": profile.display_name,
            "experience_mode": profile.experience_mode,
            "handicap_range": profile.handicap_range,
            "primary_goal": profile.primary_goal,
            "practice_minutes": profile.practice_minutes,
            "sessions_per_week": profile.sessions_per_week,
            "handedness": profile.handedness,
            "camera_angle": profile.camera_angle,
            "preferred_club": profile.preferred_club,
            "reduced_motion": profile.reduced_motion,
            "marketing_email_opt_in": profile.marketing_email_opt_in,
            "is_complete": profile.is_complete,
            "updated_at": profile.updated_at,
        }

    def caddie_brief_payload(brief) -> dict | None:
        if brief is None:
            return None
        drill = None
        if brief.drill is not None:
            drill = {
                "id": brief.drill.id,
                "name": brief.drill.name,
                "aim": brief.drill.aim,
                "dosage": brief.drill.dosage,
                "pass_mark": brief.drill.success_metric,
            }
        return {
            "version": 1,
            "focus": {
                "key": brief.focus_flag,
                "name": brief.focus_name,
                "value": brief.focus_value,
                "benchmark": brief.benchmark_text,
                "why": brief.why,
                "cue": brief.fix,
            },
            "drill": drill,
            "trend": brief.trend,
            "warning": brief.warning,
            "refilm_required": brief.refilm_required,
            "recurring_sessions": brief.recurring_sessions,
            "remaining_issues": brief.remaining_issues,
        }

    def shopify_sync_eligible(email: str) -> bool:
        """Require both the feature flag and an explicit staged cohort."""

        return bool(
            shopify_sync_coordinator is not None
            and shopify_sync_coordinator.enrollment_allowed
            and shopify_sync_settings.get("auto_sync_new_users", True)
            and _cohort_includes_email(
                email, shopify_sync_cohort_percent, secret
            )
        )

    def queue_shopify_sync(
        user: User, *, identity_just_verified: bool = False
    ) -> None:
        """Persist and wake outbound sync without delaying registration."""

        if (
            shopify_sync_coordinator is None
            or not shopify_sync_settings.get("auto_sync_new_users", True)
            or not shopify_sync_eligible(user.email)
            or user.shopify_customer_id
        ):
            return
        # Only inbox-verified cohort members reach the automatic bridge.
        # Unverified or out-of-cohort users remain safe local accounts.
        if not user.email_verified:
            return
        if identity_just_verified or user.shopify_sync_status in (
            "not_started",
            "pending",
            "requires_review",
        ):
            shopify_sync_coordinator.enqueue(user.id)

    def require_admin(request: Request) -> None:
        """Apply the existing indistinguishable, constant-time admin guard."""

        token = os.environ.get("SWINGLAB_ADMIN_TOKEN") or ""
        if not token:
            raise HTTPException(404, "Not Found")
        auth = request.headers.get("authorization", "")
        supplied = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if not hmac.compare_digest(
            supplied.strip().encode("utf-8"), token.encode("utf-8")
        ):
            raise HTTPException(404, "Not Found")

    def shop_active() -> bool:
        return bool(cfg.shop.get("enabled")) and shop.enabled()

    def acquire_code_send_lock(key: tuple[str, str]) -> threading.Lock:
        """Reference-count a per-address lock without retaining attacker keys."""
        with code_send_locks_guard:
            lock, refs = code_send_locks.get(key, (threading.Lock(), 0))
            code_send_locks[key] = (lock, refs + 1)
        return lock

    def release_code_send_lock(key: tuple[str, str]) -> None:
        with code_send_locks_guard:
            lock, refs = code_send_locks[key]
            if refs == 1:
                del code_send_locks[key]
            else:
                code_send_locks[key] = (lock, refs - 1)

    def passwordless_active() -> bool:
        """Email-code sign-in is the primary flow only when the operator
        left web.passwordless_login on AND email delivery is configured —
        with either missing, the login/signup pages keep the classic password flows
        exactly (inert until configured, like every integration)."""
        return bool(cfg.web.get("passwordless_login")) and mailer.enabled()

    def claim_pending_pro(user: User) -> None:
        """Attach any Shopify Pro purchase made with this email before the
        account existed (or while logged out). Runs at signup and login."""
        users.claim_pending_grant(user.id, user.email)

    def club_aware_enabled() -> bool:
        """Only the literal boolean true activates context aggregation."""

        return cfg.coaching.get("club_aware_enabled") is True

    def latest_readable_job(jobs) -> Job | None:
        """Latest stored session that can honestly contribute one sample."""

        ordered = sorted(
            jobs,
            key=lambda job: (
                float(getattr(job, "created_at", 0.0) or 0.0),
                str(getattr(job, "id", "")),
            ),
            reverse=True,
        )
        for job in ordered:
            if session_sample(job, cfg) is not None:
                return job
        return None

    def matched_refilm_credit(user: User | None) -> dict | None:
        """The upload/today view of the free matched re-film: None when the
        credit cannot apply (flag off, open instance, Pro, unlimited free,
        or no coaching-ready baseline in this month's 14-day window), else
        the baseline's context and whether the credit is still unspent."""

        if not cfg.web.get("require_account") or user is None:
            return None
        limit = int(cfg.billing["free_per_month"])
        if limit <= 0:
            return None
        baseline = matched_refilm_baseline(user, manager, cfg)
        context = exact_job_context(baseline)
        if baseline is None or context is None:
            return None
        return {
            "available": manager.usage_this_month(user.id) < limit + 1,
            "label": context_label(context),
            "club": CLUB_LABELS[context[0]],
            "baseline_id": baseline.id,
        }

    def render(
        template: str,
        request: Request,
        *,
        public_shell: bool = False,
        **context,
    ) -> HTMLResponse:
        stripe_enabled = billing.enabled()
        # Service-worker cached pages must never carry a member's name,
        # entitlement, profile, or account navigation.  Render the explicitly
        # public shell anonymously even when the request includes a session.
        render_user = None if public_shell else current_user(request)
        header_profile = (
            users.get_golfer_profile(render_user.id)
            if render_user is not None and cfg.web.get("require_account")
            else None
        )
        pro_store_url = (
            shopify_billing.buy_url(cfg)
            if shopify_billing.commerce_enabled()
            else None
        )
        response = HTMLResponse(
            env.get_template(template).render(
                brand=cfg.brand,
                user=render_user,
                header_profile=header_profile,
                require_account=bool(cfg.web.get("require_account")),
                # Destructive controls fail closed: only the YAML boolean
                # true activates them. Strings such as "false" must not be
                # treated as truthy configuration.
                history_reset_enabled=(
                    cfg.web.get("history_reset_enabled") is True
                ),
                club_aware_enabled=club_aware_enabled(),
                billing_enabled=stripe_enabled,
                pro_store_url=pro_store_url,
                pro_available=stripe_enabled or bool(pro_store_url),
                coach_replay_enabled=bool(cfg.slowmo.get("annotated")),
                coach_replay_pro_only=bool(
                    cfg.slowmo.get("annotated")
                    and cfg.billing.get("replay_pro_only")
                    and cfg.web.get("require_account")
                ),
                free_per_month=int(cfg.billing["free_per_month"]),
                pro_per_month=int(cfg.billing["pro_per_month"]),
                # EFFECTIVE gates, not raw flags — copy that advertises a
                # Pro lock must track what is actually locked. The replay
                # gate only exists when the replay feature is on and
                # accounts are on (jobs.replay_locked applies the same
                # conditions); the progress gate only exists with accounts
                # on (/progress is 404 without them). A raw flag with the
                # rest missing must never put a false lock on a page.
                replay_pro_only=bool(
                    cfg.billing.get("replay_pro_only")
                    and cfg.slowmo.get("annotated")
                    and cfg.web.get("require_account")
                ),
                progress_pro_only=bool(
                    cfg.billing.get("progress_pro_only")
                    and cfg.web.get("require_account")
                ),
                # Effective, like the gates above: the free matched re-film
                # only exists where the paywall does (accounts on, a finite
                # free allowance) — pricing copy must never advertise a
                # credit no upload would ever need.
                free_matched_refilm=bool(
                    matched_refilm_enabled(cfg)
                    and cfg.web.get("require_account")
                    and int(cfg.billing["free_per_month"]) > 0
                ),
                shop_enabled=shop_active(),
                mail_enabled=mailer.enabled(),
                passwordless_login=passwordless_active(),
                shopify_customer_accounts_enabled=(
                    shopify_customer_account_client is not None
                ),
                storefront_url=(cfg.shop.get("store_url") or "").rstrip("/"),
                current_path=request.url.path,
                club_labels=CLUB_LABELS,
                **context,
            )
        )
        if public_shell:
            response.headers["Cache-Control"] = "public, max-age=300"
        elif render_user is not None:
            # Membership, profile, and owned-session pages are personalized.
            # Never let a shared/browser cache preserve an old entitlement or
            # a name/history state the member has since changed.
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
        elif (
            context.get("verify_email")
            or context.get("code_email")
            or context.get("reset_stage") == "confirm"
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    def render_no_store(
        template: str, request: Request, **context
    ) -> HTMLResponse:
        """Render verification/account-secret pages without cache retention."""

        response = render(template, request, **context)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    def get_job_or_404(
        job_id: str,
        request: Request,
        *,
        authenticated_user: User | None = None,
    ) -> Job:
        """404 for unknown ids AND other people's sessions (don't reveal
        which). Jobs with no owner (pre-accounts era, or open mode) stay
        reachable by link."""
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown session")
        if job.user_id is not None:
            user = authenticated_user or current_user(request)
            if user is None or user.id != job.user_id:
                raise HTTPException(404, "Unknown session")
        return job

    def resolved_report(job: Job) -> Path | None:
        if job.status != DONE or not job.report_rel:
            return None
        root = job.session_dir.resolve()
        target = (root / job.report_rel).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        return target

    def current_safe_report(job: Job) -> bool:
        """Whether an ineligible result uses the capture-only report format.

        Older reports were generated before the re-film trust boundary and can
        still contain coaching/metrics that the current eligibility decision
        rejects. Keep those gated, while allowing reports generated by this
        version because their template removes coaching and derived visuals.
        """
        target = resolved_report(job)
        if target is None:
            return False
        return persisted_report_outcome(target) == REPORT_OUTCOME_CAPTURE

    def valid_metrics_file(
        path: Path, *, angle: str | None = None
    ) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            payload_structure_is_valid(payload)
            and payload_is_coaching_eligible(payload, cfg, angle=angle)
            and not payload_has_unsupported_angle_data(
                payload, angle=angle
            )
        )

    def api_payload(job: Job) -> dict:
        payload = job.as_dict()
        payload["queue_position"] = manager.queue_position(job)
        if job.status == DONE:
            coaching_eligible = manager.coaching_eligible(job)
            payload["coaching_eligible"] = coaching_eligible
            payload["outcome"] = (
                "coaching_ready" if coaching_eligible else "refilm_required"
            )
            if job.report_rel and (
                coaching_eligible or current_safe_report(job)
            ):
                payload["report_url"] = (
                    f"/session/{job.id}/files/{job.report_rel}"
                )
            if job.report_rel and coaching_eligible:
                metrics = (
                    job.session_dir
                    / Path(job.report_rel).parent
                    / "metrics.json"
                )
                if metrics.is_file() and valid_metrics_file(
                    metrics, angle=job.angle
                ):
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
            jobs = manager.list_recent(user_id=user.id)
            if club_aware_enabled():
                latest = latest_readable_job(jobs)
                context = exact_job_context(latest)
                if latest is None or context is None:
                    return None
                club, hand, angle = context
                jobs = manager.list_comparable(
                    user_id=user.id,
                    club=club,
                    hand=hand,
                    angle=angle,
                    through=latest.created_at,
                )
            return trend_sentence(build_trends(jobs, cfg))
        except Exception:
            return None

    # -- pages ------------------------------------------------------------
    @app.get("/app.webmanifest")
    def web_manifest():
        brand_name = cfg.brand["name"]
        return JSONResponse(
            {
                # A stable id keeps an already-installed app pointed at this
                # entry if start_url ever changes; without it the browser
                # treats a new start_url as a different app.
                "id": "/",
                "name": f"{brand_name} — swing analysis",
                "short_name": brand_name,
                "description": (
                    "Film one swing, get one priority, one drill, and a "
                    "re-film target that tests the change."
                ),
                "start_url": "/today",
                "scope": "/",
                "display": "standalone",
                # Falls back left to right, so a browser that supports the
                # tab-strip-free window uses it and everything else lands on
                # plain standalone.
                "display_override": ["minimal-ui", "standalone"],
                "orientation": "portrait",
                "categories": ["sports", "health", "fitness"],
                "background_color": "#eef2ef",
                "theme_color": "#06110c",
                "lang": "en",
                "dir": "ltr",
                "icons": [
                    # Android reads the maskable entry for the launcher and
                    # the "any" entries everywhere else. Declaring one icon
                    # as both leaves the mark padded in one context or
                    # clipped in the other, so they are separate files.
                    {
                        "src": "/static/pwa-icon.svg",
                        "sizes": "any",
                        "type": "image/svg+xml",
                        "purpose": "any",
                    },
                    {
                        "src": "/static/pwa-icon-192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": "/static/pwa-icon-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": "/static/pwa-icon-maskable-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "maskable",
                    },
                ],
                "shortcuts": [
                    {
                        "name": "Analyze a swing",
                        "short_name": "Analyze",
                        "url": "/",
                        "icons": [
                            {
                                "src": "/static/pwa-icon-192.png",
                                "sizes": "192x192",
                                "type": "image/png",
                            }
                        ],
                    },
                    {
                        "name": "Today",
                        "short_name": "Today",
                        "url": "/today",
                    },
                    {
                        "name": "Swing history",
                        "short_name": "History",
                        "url": "/sessions",
                    },
                ],
            },
            media_type="application/manifest+json",
        )

    @app.get("/service-worker.js")
    def service_worker():
        response = FileResponse(
            static_dir / "service-worker.js",
            media_type="application/javascript",
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/offline", response_class=HTMLResponse)
    def offline_page(request: Request):
        return render("web_offline.html.j2", request, public_shell=True)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        user = current_user(request)
        golfer_profile = (
            users.get_golfer_profile(user.id) if user is not None else None
        )
        record_product_event(request, "landing_view", user=user)
        if cfg.web.get("require_account") and user is None:
            return render(
                "web_login.html.j2", request, error=None, landing=True,
                auth_view="landing",
            )
        left = quota_left(user)
        if user is not None:
            first_analysis = not any(
                manager.coaching_eligible(item)
                for item in manager.list_recent(user_id=user.id)
            )
        else:
            # Open-mode visitors have no durable account identity. Keep this
            # first-use hint in their signed browser session instead of
            # leaking whether anybody else has used this app instance.
            first_analysis = not bool(request.session.get("has_analysis"))
        refilm_rejections = (
            manager.refilm_rejections_this_month(user.id)
            if user is not None
            else 0
        )
        charged_refilm_attempts = max(0, refilm_rejections - 1)
        baseline_blocked = bool(
            user is not None
            and first_analysis
            and left == 0
            and charged_refilm_attempts
        )
        # The conversion moment: a free user out of analyses sees their own
        # trend next to the upgrade path — only when it truly exists.
        trend_line = (
            personal_trend(user)
            if left == 0 and user is not None and not user.is_pro
            else None
        )
        refilm_credit = matched_refilm_credit(user)
        # When the wall is real (allowance spent, no credit left), the
        # upgrade prompt names the golfer's own pending pass mark — the
        # re-film target their last coaching session asked them to prove.
        pending_pass_mark = None
        if (
            left == 0
            and user is not None
            and not user.is_pro
            and not (refilm_credit is not None and refilm_credit["available"])
        ):
            try:
                latest_eligible = next(
                    (
                        job
                        for job in manager.list_recent(user_id=user.id)
                        if job.status == DONE
                        and manager.coaching_eligible(job)
                    ),
                    None,
                )
                brief = (
                    caddie_brief_for(latest_eligible)
                    if latest_eligible is not None
                    else None
                )
                if (
                    brief is not None
                    and brief.drill is not None
                    and not brief.refilm_required
                ):
                    pending_pass_mark = brief.drill.success_metric
            except Exception:
                # A nice-to-have line must never take down the upload page.
                pending_pass_mark = None
        upload_transfer_assignment = active_proof_cycle_practice_assignment(
            user,
            club=(golfer_profile.preferred_club if golfer_profile else None),
            hand=(golfer_profile.handedness if golfer_profile else "right"),
            angle=(golfer_profile.camera_angle if golfer_profile else "face-on"),
            before=time.time(),
        )
        return render(
            "web_upload.html.j2",
            request,
            golfer_profile=golfer_profile,
            max_upload_mb=float(cfg.web.get("max_upload_mb") or 0),
            quota_left=left,
            trend_line=trend_line,
            # First-session guide: shown until the account has any session
            # at all — the panel IS the onboarding, so one upload retires it.
            first_run=(
                user is not None
                and not manager.list_recent(limit=1, user_id=user.id)
            ),
            first_analysis=first_analysis,
            baseline_blocked=baseline_blocked,
            refilm_rejections=refilm_rejections,
            charged_refilm_attempts=charged_refilm_attempts,
            refilm_credit=refilm_credit,
            pending_pass_mark=pending_pass_mark,
            upload_transfer_assignment=upload_transfer_assignment,
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

    # -- public drill library (no auth — same posture as the sample) -------
    @app.get("/drills", response_class=HTMLResponse)
    def drills_page(request: Request):
        """Every drill the practice plans prescribe, with setup diagrams,
        animations, dosages, and re-film pass marks — plus a curated
        four-week Beginner Path through them. Public on purpose: the
        library is the product's proof, not a secret."""
        library = build_drills(cfg.coaching)
        families = [
            {"key": key, "title": PLAN_TITLES[key], "drills": library[key]}
            for key in PLAN_TITLES
            if key in library
        ]
        drill_media = {
            d.id: {
                "diagram": drill_diagram(d.id, cfg.brand),
                "animation": drill_animation(d.id, cfg.brand),
            }
            for fam in families
            for d in fam["drills"]
        }
        return render(
            "web_drills.html.j2",
            request,
            families=families,
            drill_media=drill_media,
            gear_url=gear_shop_url(cfg),
        )

    # -- accounts ---------------------------------------------------------
    def customer_account_client_or_404():
        if shopify_customer_account_client is None:
            raise HTTPException(404, "Not Found")
        return shopify_customer_account_client

    def establish_customer_account_session(
        request: Request,
        user: User,
        identity: shopify_customer_accounts.CustomerAccountIdentity,
    ) -> None:
        # The provider id_token is kept server-side only so the Customer
        # Account logout endpoint can receive its required hint.  The signed
        # browser cookie holds only a random opaque lookup key.
        browser_session_id = users.issue_shopify_customer_account_browser_session(
            user.id,
            identity.id_token,
            expires_at=identity.expires_at,
        )
        request.session[SHOPIFY_ACCOUNT_BROWSER_SESSION_KEY] = browser_session_id
        establish_session(request, user)

    @app.get("/auth/shopify/start")
    def shopify_account_start(request: Request):
        client = customer_account_client_or_404()
        user = current_user(request)
        mode = "link" if user is not None else "login"
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = shopify_customer_accounts.new_pkce_verifier()
        try:
            users.issue_shopify_customer_account_oauth_state(
                state=state,
                verifier=verifier,
                nonce=nonce,
                user_id=user.id if user is not None else None,
                mode=mode,
            )
            target = client.authorization_url(
                state=state,
                nonce=nonce,
                verifier=verifier,
            )
        except shopify_customer_accounts.ShopifyCustomerAccountError as exc:
            raise HTTPException(503, str(exc)) from None
        return RedirectResponse(target, status_code=303)

    @app.get("/auth/shopify/callback", response_class=HTMLResponse)
    def shopify_account_callback(
        request: Request,
        state: str = "",
        code: str = "",
        error: str = "",
    ):
        client = customer_account_client_or_404()
        flow = users.consume_shopify_customer_account_oauth_state(state)
        if flow is None:
            raise HTTPException(400, "Shopify sign-in could not be verified. Start again.")
        # The authorization server may return error details intended for the
        # browser.  Do not reflect them: they can be unpredictable and are
        # not needed for a safe retry.
        if error or not code:
            return render_no_store(
                "web_login.html.j2",
                request,
                landing=False,
                auth_view="login",
                error="Shopify sign-in was not completed. Please try again.",
            )
        try:
            identity = client.authenticate_callback(
                code=code,
                verifier=flow.verifier,
                nonce=flow.nonce,
            )
        except shopify_customer_accounts.ShopifyCustomerAccountError as exc:
            return render_no_store(
                "web_login.html.j2",
                request,
                landing=False,
                auth_view="login",
                error=str(exc),
            )

        if flow.mode == "link":
            user = current_user(request)
            if (
                user is None
                or flow.user_id is None
                or not hmac.compare_digest(user.id, flow.user_id)
            ):
                raise HTTPException(400, "Shopify account linking expired. Start again.")
        else:
            # A Customer Account login may enter CaddieInsight only through a
            # durable, explicitly reconciled customer ID / prior subject.  No
            # email lookup, auto-create, or duplicate merge exists here.
            user = users.get_by_shopify_account_subject(identity.subject)
            if user is None:
                user = users.get_by_shopify(identity.customer_id)
            if user is None:
                return render_no_store(
                    "web_login.html.j2",
                    request,
                    landing=False,
                    auth_view="login",
                    error=(
                        "This Shopify account is not linked to a CaddieInsight "
                        "account yet. Sign in with your current app method, then "
                        "connect Shopify from your account page."
                    ),
                )
        try:
            profile_was_missing = (
                flow.mode == "login"
                and users.get_golfer_profile(user.id) is None
            )
            linked = users.link_shopify_customer_account(
                user.id,
                subject=identity.subject,
                customer_id=identity.customer_id,
                authenticated=True,
            )
            if flow.mode == "login":
                users.ensure_golfer_profile(linked.id)
            establish_customer_account_session(request, linked, identity)
        except ValueError as exc:
            return render_no_store(
                "web_login.html.j2",
                request,
                landing=False,
                auth_view="login",
                error=str(exc),
            )
        destination = (
            "/account?shopify_connected"
            if flow.mode == "link"
            else "/onboarding?welcome=1"
            if profile_was_missing
            else "/today"
        )
        return RedirectResponse(
            destination,
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/auth/shopify/logout")
    def shopify_account_logout(request: Request):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        client = customer_account_client_or_404()
        user = current_user(request)
        browser_session_id = request.session.get(SHOPIFY_ACCOUNT_BROWSER_SESSION_KEY)
        request.session.clear()
        browser_session = users.consume_shopify_customer_account_browser_session(
            browser_session_id
        )
        if (
            user is None
            or browser_session is None
            or not hmac.compare_digest(user.id, browser_session.user_id)
        ):
            return RedirectResponse("/", status_code=303)
        try:
            return RedirectResponse(
                client.logout_url(id_token=browser_session.id_token), status_code=303
            )
        except shopify_customer_accounts.ShopifyCustomerAccountError:
            # Local logout is already complete.  Do not trap someone in their
            # account because a provider discovery call is temporarily down.
            return RedirectResponse("/", status_code=303)

    def storefront_session_request_origin(request: Request) -> str:
        if storefront_origin is None or not cfg.web.get("require_account"):
            raise HTTPException(404, "Not Found")
        if request.headers.get("authorization") is not None:
            raise HTTPException(403, "Invalid storefront session request.")
        source_origin = _normalized_origin(request.headers.get("origin"))
        if source_origin is None or not hmac.compare_digest(
            repr(source_origin).encode("utf-8"),
            repr(storefront_origin).encode("utf-8"),
        ):
            raise HTTPException(403, "Invalid storefront session origin.")
        return _serialized_origin(storefront_origin)

    def storefront_session_response(
        payload: dict[str, object], origin: str
    ) -> JSONResponse:
        return JSONResponse(
            payload,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Cache-Control": "private, no-store",
                "Cross-Origin-Resource-Policy": "same-site",
                "Pragma": "no-cache",
                "Vary": "Origin",
            },
        )

    @app.get("/auth/storefront/session")
    def storefront_session_status(request: Request):
        origin = storefront_session_request_origin(request)
        user = current_user(request)
        if user is None:
            return storefront_session_response({"authenticated": False}, origin)
        profile = users.get_golfer_profile(user.id)
        return storefront_session_response(
            {
                "authenticated": True,
                "display_name": profile.display_name if profile else "",
                "is_pro": bool(user.is_pro),
            },
            origin,
        )

    @app.post("/auth/storefront/session")
    def storefront_session_logout(request: Request):
        origin = storefront_session_request_origin(request)
        user = current_user(request)
        browser_session_id = request.session.get(
            SHOPIFY_ACCOUNT_BROWSER_SESSION_KEY
        )
        request.session.clear()
        browser_session = (
            users.consume_shopify_customer_account_browser_session(
                browser_session_id
            )
            if browser_session_id is not None
            else None
        )
        if (
            shopify_customer_account_client is not None
            and user is not None
            and browser_session is not None
            and hmac.compare_digest(user.id, browser_session.user_id)
        ):
            try:
                return RedirectResponse(
                    shopify_customer_account_client.logout_url(
                        id_token=browser_session.id_token
                    ),
                    status_code=303,
                    headers={"Cache-Control": "no-store"},
                )
            except shopify_customer_accounts.ShopifyCustomerAccountError:
                pass
        return RedirectResponse(
            origin,
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    def send_code_email(
        email: str,
        purpose: str,
        *,
        session_nonce: str | None = None,
    ) -> None:
        """Issue + email a one-time code; a rate-limited (None) issue means
        a still-valid code is already in the inbox, so send nothing. The
        login message depends only on the purpose, never on whether the
        email has an account — all three account states read identically."""
        key = (email.strip().lower(), purpose)
        send_lock = acquire_code_send_lock(key)
        try:
            with send_lock:
                code = users.issue_email_code(
                    email, purpose, session_nonce=session_nonce
                )
                if code is None:
                    return
                action = {
                    "claim": "finish setting up your account",
                    "login": "sign in",
                }.get(purpose, "reset your password")
                try:
                    mailer.send(
                        email,
                        f"{cfg.brand['name']} verification code: {code}",
                        f"Your {cfg.brand['name']} verification code is {code}.\n\n"
                        f"Enter it to {action}. The code expires in 10 minutes.\n"
                        "If you didn't request this, you can ignore this email.",
                    )
                except mailer.EmailDeliveryRejected as exc:
                    # A definitive rejection cannot produce a usable email, so
                    # remove this exact code and let an immediate retry mint one.
                    users.discard_email_code(email, purpose, code)
                    logger.error(
                        "email-code delivery rejected (purpose=%s; detail=%s)",
                        purpose,
                        exc,
                    )
                    raise
                except mailer.EmailDeliveryUncertain as exc:
                    # A timeout/disconnect may occur after provider acceptance.
                    # Keep the code valid in case the email still arrives.
                    logger.error(
                        "email-code delivery uncertain (purpose=%s; detail=%s)",
                        purpose,
                        exc,
                    )
                    raise
                except Exception as exc:
                    # Unknown sender failures are ambiguous by default. Keeping
                    # the code avoids emailing a code that has been invalidated.
                    logger.error(
                        "email-code delivery outcome unknown (purpose=%s)",
                        purpose,
                    )
                    raise mailer.EmailDeliveryUncertain(
                        "Email delivery could not be confirmed."
                    ) from exc
        finally:
            release_code_send_lock(key)

    def delivery_error_message(exc: mailer.EmailDeliveryError) -> str:
        if isinstance(exc, mailer.EmailDeliveryUncertain):
            return EMAIL_DELIVERY_UNCERTAIN_MESSAGE
        return EMAIL_DELIVERY_MESSAGE

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current_user(request) is not None:
            return RedirectResponse("/", status_code=303)
        return render(
            "web_login.html.j2", request, error=None, landing=False,
            auth_view="login",
            prefill_email=request.query_params.get("email", ""),
            # ?password=1 is the "use your password instead" fallback: the
            # classic signup + login cards, even while code sign-in is on.
            show_password="password" in request.query_params,
        )

    @app.get("/signup", response_class=HTMLResponse)
    def signup_page(request: Request):
        if current_user(request) is not None:
            return RedirectResponse("/", status_code=303)
        return render(
            "web_login.html.j2", request, error=None, landing=False,
            auth_view="signup",
            prefill_email=request.query_params.get("email", ""),
            show_password="password" in request.query_params,
        )

    # -- email-code sign-in (primary once email delivery is configured) ----
    @app.post("/login/email")
    def login_email(
        request: Request,
        email: str = Form(""),
        auth_intent: str = Form("login"),
    ):
        """Step one of "Continue with email": send a sign-in code. The
        response — and the email itself — is identical whether the address
        has an account, an unclaimed store account, or nothing at all, so
        the form can't be used to test which emails exist."""
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        if not passwordless_active():
            raise HTTPException(503, "Email sign-in requires email to be set up.")
        auth_view = "signup" if auth_intent == "signup" else "login"
        try:
            normalized = users.validate_email(email)
        except ValueError as exc:
            return render(
                "web_login.html.j2", request, landing=False, error=str(exc),
                auth_view=auth_view,
            )
        # Same limits as password login (a code request costs an email and
        # a code row), keyed per client IP AND per target email — shared
        # with failed code entries below, so requests and guesses draw on
        # one budget.
        ip = client_ip(request)
        if not (
            throttle.allow("code-ip", ip, login_limit, LOGIN_WINDOW_S)
            and throttle.allow("code-email", normalized, login_limit, LOGIN_WINDOW_S)
        ):
            page = render(
                "web_login.html.j2", request, landing=False,
                error=THROTTLED_MESSAGE,
                auth_view=auth_view,
            )
            page.status_code = 429
            return page
        throttle.record("code-ip", ip)
        throttle.record("code-email", normalized)
        login_flow_nonce = flow_session_nonce(
            request, LOGIN_FLOW_SESSION_KEY, create=True
        )
        assert login_flow_nonce is not None
        try:
            send_code_email(
                normalized,
                "login",
                session_nonce=login_flow_nonce,
            )
        except mailer.EmailDeliveryError as exc:
            if isinstance(exc, mailer.EmailDeliveryRejected):
                clear_flow_session_nonce(request, LOGIN_FLOW_SESSION_KEY)
            delivery_context = (
                {"code_email": normalized, "auth_view": auth_view}
                if isinstance(exc, mailer.EmailDeliveryUncertain)
                else {"prefill_email": normalized, "auth_view": auth_view}
            )
            page = render(
                "web_login.html.j2", request, landing=False,
                error=delivery_error_message(exc), **delivery_context,
            )
            page.status_code = 503
            return page
        return render(
            "web_login.html.j2", request, landing=False, error=None,
            code_email=normalized, auth_view=auth_view,
        )

    @app.post("/login/code")
    def login_code(
        request: Request,
        email: str = Form(""),
        code: str = Form(""),
        auth_intent: str = Form("login"),
    ):
        """Step two: a correct code signs the user in — into an existing
        account, an unclaimed store account (claimed on the spot, Pro and
        purchases kept), or a brand-new one. The code machinery enforces
        the 10-minute expiry, single use, and the 5-wrong-guess burn; the
        wrong-code message never depends on the account's state."""
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        if not passwordless_active():
            raise HTTPException(503, "Email sign-in requires email to be set up.")
        auth_view = "signup" if auth_intent == "signup" else "login"
        ip = client_ip(request)
        normalized = email.strip().lower()
        if not (
            throttle.allow("code-ip", ip, login_limit, LOGIN_WINDOW_S)
            and throttle.allow("code-email", normalized, login_limit, LOGIN_WINDOW_S)
        ):
            page = render(
                "web_login.html.j2", request, landing=False,
                code_email=normalized, error=THROTTLED_MESSAGE,
                auth_view=auth_view,
            )
            page.status_code = 429
            return page
        login_flow_nonce = flow_session_nonce(
            request, LOGIN_FLOW_SESSION_KEY
        )
        if login_flow_nonce is None or not users.check_email_code(
            normalized,
            "login",
            code,
            session_nonce=login_flow_nonce,
        ):
            # Only failures are recorded — entering the right code first
            # try never eats into anyone's budget.
            throttle.record("code-ip", ip)
            throttle.record("code-email", normalized)
            return render(
                "web_login.html.j2", request, landing=False,
                code_email=normalized,
                auth_view=auth_view,
                error="That code didn't match (or expired) — check the "
                      "email, or send yourself a fresh code.",
            )
        prior_user = users.get_by_email(normalized)
        identity_just_verified = (
            prior_user is None or not prior_user.email_verified
        )
        try:
            user = users.verify_email_signin(
                normalized,
                shopify_sync_pending=bool(
                    shopify_sync_eligible(normalized)
                    and (
                        identity_just_verified
                    )
                ),
            )
        except ValueError as exc:  # can't happen for an email a code was
            return render(       # issued to, but never 500 on crafted input
                "web_login.html.j2", request, landing=False, error=str(exc),
                auth_view=auth_view,
            )
        claim_pending_pro(user)
        queue_shopify_sync(
            user,
            identity_just_verified=identity_just_verified,
        )
        if identity_just_verified:
            users.ensure_golfer_profile(user.id)
            record_product_event(
                request,
                "account_verified",
                user=user,
                dedupe_key=f"account_verified:{user.id}",
            )
        clear_flow_session_nonce(request, LOGIN_FLOW_SESSION_KEY)
        establish_session(request, user)
        destination = "/onboarding?welcome=1" if identity_just_verified else "/"
        return RedirectResponse(
            destination,
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form("")):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
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
                error=THROTTLED_MESSAGE, show_password=True,
                auth_view="login",
            )
            page.status_code = 429
            return page
        user = users.authenticate(email, password)
        if user is None:
            throttle.record("login-ip", ip)
            throttle.record("login-email", normalized_email)
            # An account with no password has none to be wrong about. With
            # code sign-in active, point at "Continue with email"; without
            # it, at signup (prefilled) to set a password — never a
            # misleading "wrong password".
            pending = users.get_by_email(email)
            if pending is not None and not pending.has_password:
                if passwordless_active():
                    return render(
                        "web_login.html.j2", request, landing=False,
                        error=None, code_notice=True,
                        prefill_email=pending.email,
                        auth_view="login",
                    )
                return render(
                    "web_login.html.j2", request, landing=False, error=None,
                    stub_notice=True, prefill_email=pending.email,
                    show_password=True, auth_view="signup",
                )
            return render(
                "web_login.html.j2", request, landing=False,
                error="Wrong email or password.", show_password=True,
                auth_view="login",
            )
        claim_pending_pro(user)
        establish_session(request, user)
        return RedirectResponse("/", status_code=303)

    @app.post("/signup")
    def signup(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        code: str = Form(""),
        digest_opt: str = Form("", alias="digest"),
        signup_intent: str = Form(""),
    ):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        ip = client_ip(request)
        if (
            not signup_intent.strip()
            and not throttle.allow(
                "signup-ip", ip, signup_limit, SIGNUP_WINDOW_S
            )
        ):
            page = render(
                "web_login.html.j2",
                request,
                landing=False,
                error=THROTTLED_MESSAGE,
                show_password=True,
                auth_view="signup",
            )
            page.status_code = 429
            return page

        # Verification continuations carry only an opaque, browser-bound
        # token. The associated password exists solely as a scrypt hash in
        # signup_intents and is consumed atomically after the email code.
        if signup_intent.strip():
            signup_flow_nonce = flow_session_nonce(
                request, SIGNUP_FLOW_SESSION_KEY
            )
            intent = (
                users.get_signup_intent(
                    signup_intent,
                    session_nonce=signup_flow_nonce,
                )
                if signup_flow_nonce is not None
                else None
            )
            if intent is None:
                page = render_no_store(
                    "web_login.html.j2",
                    request,
                    landing=False,
                    error="That signup verification expired — start again.",
                    show_password=True,
                    auth_view="signup",
                )
                page.status_code = 400
                return page
            if not code.strip():
                return render_no_store(
                    "web_login.html.j2",
                    request,
                    landing=False,
                    verify_email=intent.email,
                    verify_intent=signup_intent,
                    auth_view="signup",
                    error=(
                        "That code didn't match (or expired) — check the "
                        "email, or start again for a fresh code."
                    ),
                )
            try:
                user = users.complete_signup_intent_with_code(
                    signup_intent,
                    code,
                    shopify_sync_pending=shopify_sync_eligible(
                        intent.email
                    ),
                    session_nonce=signup_flow_nonce,
                )
            except ValueError as exc:
                page = render_no_store(
                    "web_login.html.j2",
                    request,
                    landing=False,
                    error=str(exc),
                    verify_email=intent.email,
                    verify_intent=signup_intent,
                    auth_view="signup",
                )
                page.status_code = 400
                return page
            profile_was_missing = users.get_golfer_profile(user.id) is None
            users.ensure_golfer_profile(user.id)
            claim_pending_pro(user)
            queue_shopify_sync(user, identity_just_verified=True)
            record_product_event(
                request,
                "account_verified",
                user=user,
                dedupe_key=f"account_verified:{user.id}",
            )
            clear_flow_session_nonce(request, SIGNUP_FLOW_SESSION_KEY)
            establish_session(request, user)
            return RedirectResponse(
                "/onboarding?welcome=1" if profile_was_missing else "/",
                status_code=303,
                headers={"Cache-Control": "no-store"},
            )

        wants_digest = digest_opt.lower() in ("on", "true", "1", "yes")
        # Signup throttle: per client IP, sliding hour window. Every initial
        # password signup costs a scrypt hash (and sometimes an email), so
        # throwaway addresses cannot consume that CPU for free.
        try:
            normalized = users.validate_signup(email, password)
        except ValueError as exc:
            return render(
                "web_login.html.j2", request, landing=False,
                error=str(exc), show_password=True,
                auth_view="signup",
            )
        # Record only attempts that clear validation — a typo'd password
        # doesn't cost the visitor one of their slots.
        throttle.record("signup-ip", ip)
        # Every password signup in a Shopify-connected deployment must prove
        # inbox ownership. Otherwise an attacker can pre-register a buyer's
        # email, keep a live session, and wait for a later customer webhook.
        # A deployment with no Shopify bridge keeps the historical local-only
        # no-mail signup path.
        existing_signup_user = users.get_by_email(normalized)
        identity_requires_verification = bool(
            shopify_billing.commerce_enabled()
            or users.has_unclaimed_value(normalized)
            or (
                existing_signup_user is None
                and shopify_sync_eligible(normalized)
            )
        )
        if identity_requires_verification:
            if not mailer.enabled():
                page = render_no_store(
                    "web_login.html.j2",
                    request,
                    landing=False,
                    error=(
                        "Secure account setup for this address requires email "
                        "verification, but email delivery is unavailable."
                    ),
                    show_password=True,
                    prefill_email=normalized,
                    auth_view="signup",
                )
                page.status_code = 503
                return page
            signup_flow_nonce = flow_session_nonce(
                request, SIGNUP_FLOW_SESSION_KEY, create=True
            )
            assert signup_flow_nonce is not None
            intent_token = users.issue_signup_intent(
                normalized,
                password,
                digest_opt_in=wants_digest,
                session_nonce=signup_flow_nonce,
            )
            try:
                send_code_email(
                    normalized,
                    "claim",
                    session_nonce=signup_flow_nonce,
                )
            except mailer.EmailDeliveryError as exc:
                if isinstance(exc, mailer.EmailDeliveryUncertain):
                    page = render_no_store(
                        "web_login.html.j2",
                        request,
                        landing=False,
                        error=delivery_error_message(exc),
                        verify_email=normalized,
                        verify_intent=intent_token,
                        auth_view="signup",
                    )
                else:
                    users.discard_signup_intent(intent_token)
                    clear_flow_session_nonce(
                        request, SIGNUP_FLOW_SESSION_KEY
                    )
                    page = render_no_store(
                        "web_login.html.j2",
                        request,
                        landing=False,
                        error=delivery_error_message(exc),
                        show_password=True,
                        prefill_email=normalized,
                        auth_view="signup",
                    )
                page.status_code = 503
                return page
            return render_no_store(
                "web_login.html.j2",
                request,
                landing=False,
                error=None,
                verify_email=normalized,
                verify_intent=intent_token,
                auth_view="signup",
            )

        try:
            user = users.create(
                normalized,
                password,
            )
        except ValueError as exc:
            return render(
                "web_login.html.j2", request, landing=False, error=str(exc),
                show_password=True, auth_view="signup",
            )
        if wants_digest:  # the signup checkbox is UNCHECKED by default
            users.set_digest_opt_in(user.id, True)
        profile_was_missing = users.get_golfer_profile(user.id) is None
        users.ensure_golfer_profile(user.id)
        claim_pending_pro(user)
        queue_shopify_sync(user)
        establish_session(request, user)
        return RedirectResponse(
            "/onboarding?welcome=1" if profile_was_missing else "/",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    # -- password reset (available once email delivery is configured) -----
    @app.get("/reset", response_class=HTMLResponse)
    def reset_page(request: Request):
        if not mailer.enabled():
            raise HTTPException(503, "Password reset requires email to be set up.")
        return render(
            "web_login.html.j2", request, error=None, landing=False,
            reset_stage="request", auth_view="login",
        )

    @app.post("/reset/request")
    def reset_request(request: Request, email: str = Form("")):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        if not mailer.enabled():
            raise HTTPException(503, "Password reset requires email to be set up.")
        normalized = email.strip().lower()
        user = users.get_by_email(normalized)
        if user is not None and user.has_password:
            try:
                send_code_email(normalized, "reset")
            except mailer.EmailDeliveryError:
                # Keep the same response as an unknown address. Returning a
                # provider-specific error only for real accounts would turn a
                # temporary outage into an account-enumeration tool.
                pass
        # Same response either way — don't reveal which emails have accounts.
        return render(
            "web_login.html.j2", request, error=None, landing=False,
            reset_stage="confirm", reset_email=normalized,
            auth_view="login",
        )

    @app.post("/reset/confirm")
    def reset_confirm(
        request: Request,
        email: str = Form(""),
        code: str = Form(""),
        password: str = Form(""),
    ):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        if not mailer.enabled():
            raise HTTPException(503, "Password reset requires email to be set up.")
        normalized = email.strip().lower()
        if len(password) < 8:
            # Reject before checking (and consuming) the single-use code.
            return render(
                "web_login.html.j2", request, landing=False,
                reset_stage="confirm", reset_email=normalized,
                auth_view="login",
                error="Password must be at least 8 characters.",
            )
        user = users.get_by_email(normalized)
        if user is None or not users.check_email_code(normalized, "reset", code):
            return render(
                "web_login.html.j2", request, landing=False,
                reset_stage="confirm", reset_email=normalized,
                auth_view="login",
                error="That code didn't match (or expired) — request a new one.",
            )
        users.set_password(user.id, password)
        claim_pending_pro(user)
        updated_user = users.get(user.id)
        if updated_user is None:
            raise HTTPException(400, "Account is unavailable.")
        establish_session(request, updated_user)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    def account_page(
        request: Request, user: User, password_error: str | None = None
    ) -> HTMLResponse:
        # A Shopify lifetime purchase lands as a very long day grant
        # (SL-PRO-LIFE = 100 years). Anything more than 50 years out is
        # displayed as "Lifetime" — a dated row for the year 2126 would be
        # technically true and practically silly.
        pro_lifetime = user.pro_until - time.time() > LIFETIME_DISPLAY_MIN_S
        raw_history_flash = request.session.pop(HISTORY_RESET_FLASH_KEY, None)
        history_reset_flash = None
        if isinstance(raw_history_flash, dict):
            try:
                deleted_jobs = max(0, int(raw_history_flash["deleted_jobs"]))
            except (KeyError, TypeError, ValueError):
                deleted_jobs = -1
            if deleted_jobs >= 0:
                history_reset_flash = {
                    "deleted_jobs": deleted_jobs,
                    "cleanup_pending": bool(
                        raw_history_flash.get("cleanup_pending")
                    ),
                }
        return render(
            "web_account.html.j2",
            request,
            usage=manager.usage_this_month(user.id),
            quota_left=quota_left(user),
            upgraded="upgraded" in request.query_params,
            shopify_connected="shopify_connected" in request.query_params,
            password_added="password_added" in request.query_params,
            password_error=password_error,
            history_reset_flash=history_reset_flash,
            golfer_profile=users.get_golfer_profile(user.id),
            pro_lifetime=pro_lifetime,
            pro_until_date=(
                time.strftime("%B %d, %Y", time.localtime(user.pro_until))
                if user.pro_until > time.time() and not pro_lifetime
                else None
            ),
        )

    def onboarding_response(
        request: Request,
        user: User,
        *,
        error: str | None = None,
        password_error: str | None = None,
        submitted: dict | None = None,
    ) -> HTMLResponse:
        context = {
            "profile": users.get_golfer_profile(user.id),
            "error": error,
            "password_error": password_error,
            "welcome": "welcome" in request.query_params,
            "password_added": "password_added" in request.query_params,
        }
        if submitted is not None:
            context["submitted"] = submitted
        return render("web_onboarding.html.j2", request, **context)

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return account_page(request, user)

    def history_reset_recent_auth(request: Request) -> bool:
        authenticated_at = request.session.get("authenticated_at")
        if isinstance(authenticated_at, bool) or not isinstance(
            authenticated_at, (int, float)
        ):
            return False
        age = time.time() - float(authenticated_at)
        return 0 <= age <= HISTORY_RESET_RECENT_AUTH_S

    def history_reset_session_is_current(
        request: Request, user: User
    ) -> bool:
        """Reject a pre-reset signed browser cookie at this destructive gate.

        Missing values map to generation zero so sessions issued before the
        compatibility floor continue to work until the account's first reset.
        """

        try:
            session_history_epoch = int(
                request.session.get(HISTORY_SESSION_EPOCH_KEY, 0)
            )
        except (TypeError, ValueError, OverflowError):
            return False
        return session_history_epoch == user.history_epoch

    def reset_requires_fresh_session(request: Request, user: User):
        if history_reset_session_is_current(request, user):
            return None
        request.session.clear()
        response = RedirectResponse("/login", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    def history_delete_page(
        request: Request,
        user: User,
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        nonce = secrets.token_urlsafe(32)
        request.session[HISTORY_RESET_SESSION_KEY] = {
            "nonce": nonce,
            "expires_at": time.time() + HISTORY_RESET_NONCE_TTL_S,
            # The signed cookie is replayable by design. Binding its nonce to
            # the current history generation makes an old cookie harmless
            # after the first committed reset or any intervening reset.
            "history_epoch": user.history_epoch,
        }
        response = render_no_store(
            "web_history_delete.html.j2",
            request,
            nonce=nonce,
            confirmation_phrase=HISTORY_RESET_CONFIRMATION,
            error=error,
            recent_auth_required=(
                bool(request.session.get(PASSWORD_ADDED_REAUTH_SESSION_KEY))
                or (
                    not user.has_password
                    and not history_reset_recent_auth(request)
                )
            ),
        )
        response.status_code = status_code
        return response

    @app.get("/account/history/delete", response_class=HTMLResponse)
    def account_history_delete(request: Request):
        if request.headers.get("authorization") is not None:
            raise mobile_bearer_unauthorized()
        if not cfg.web.get("require_account") or cfg.web.get(
            "history_reset_enabled"
        ) is not True:
            raise HTTPException(404, "Account history is unavailable.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        stale_session = reset_requires_fresh_session(request, user)
        if stale_session is not None:
            return stale_session
        return history_delete_page(request, user)

    @app.post("/account/history/delete", response_class=HTMLResponse)
    def account_history_delete_confirm(
        request: Request,
        nonce: str = Form(""),
        confirmation: str = Form(""),
        password: str = Form(""),
    ):
        if request.headers.get("authorization") is not None:
            raise mobile_bearer_unauthorized()
        if not cfg.web.get("require_account") or cfg.web.get(
            "history_reset_enabled"
        ) is not True:
            raise HTTPException(404, "Account history is unavailable.")
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        stale_session = reset_requires_fresh_session(request, user)
        if stale_session is not None:
            return stale_session

        confirmation_state = request.session.pop(
            HISTORY_RESET_SESSION_KEY, None
        )
        nonce_valid = False
        confirmation_history_epoch: int | None = None
        if isinstance(confirmation_state, dict):
            stored_nonce = confirmation_state.get("nonce")
            expires_at = confirmation_state.get("expires_at")
            stored_history_epoch = confirmation_state.get("history_epoch")
            if (
                isinstance(stored_nonce, str)
                and isinstance(expires_at, (int, float))
                and not isinstance(expires_at, bool)
                and isinstance(stored_history_epoch, int)
                and not isinstance(stored_history_epoch, bool)
                and stored_history_epoch >= 0
                and time.time() <= float(expires_at)
                and len(nonce) <= 256
            ):
                nonce_valid = hmac.compare_digest(stored_nonce, nonce)
                confirmation_history_epoch = stored_history_epoch
        if not nonce_valid:
            return history_delete_page(
                request,
                user,
                error="That confirmation expired. Review the details and try again.",
                status_code=400,
            )
        if confirmation != HISTORY_RESET_CONFIRMATION:
            return history_delete_page(
                request,
                user,
                error=f'Type "{HISTORY_RESET_CONFIRMATION}" exactly to continue.',
                status_code=400,
            )

        if request.session.get(PASSWORD_ADDED_REAUTH_SESSION_KEY):
            return history_delete_page(
                request,
                user,
                error=(
                    "For your security, sign out and sign back in before "
                    "starting over."
                ),
                status_code=403,
            )
        if user.has_password:
            ip = client_ip(request)
            if not (
                throttle.allow(
                    "history-reset-ip", ip, login_limit, LOGIN_WINDOW_S
                )
                and throttle.allow(
                    "history-reset-user", user.id, login_limit, LOGIN_WINDOW_S
                )
            ):
                return history_delete_page(
                    request,
                    user,
                    error=THROTTLED_MESSAGE,
                    status_code=429,
                )
            authenticated = users.authenticate(user.email, password)
            if authenticated is None or authenticated.id != user.id:
                throttle.record("history-reset-ip", ip)
                throttle.record("history-reset-user", user.id)
                return history_delete_page(
                    request,
                    user,
                    error="That password did not match. Your history was not changed.",
                    status_code=400,
                )
        elif not history_reset_recent_auth(request):
            return history_delete_page(
                request,
                user,
                error=(
                    "For your security, sign out and sign back in before "
                    "starting over."
                ),
                status_code=403,
            )

        try:
            with shopify_remote_privacy_lock(sessions_dir / "swinglab.db"):
                summary = manager.reset_user_history(
                    user.id,
                    delete_related=lambda connection, user_id: (
                        users.delete_swing_history_related(
                            connection,
                            user_id,
                            expected_auth_epoch=user.auth_epoch,
                            expected_history_epoch=(
                                confirmation_history_epoch
                            ),
                        )
                    ),
                )
        except HistoryResetConflict:
            return history_delete_page(
                request,
                user,
                error=(
                    "An analysis is still uploading or processing. Wait for it "
                    "to finish, then try again."
                ),
                status_code=409,
            )
        except HistoryResetError as exc:
            if isinstance(exc.__cause__, HistoryAuthEpochError):
                request.session.clear()
                response = RedirectResponse("/login", status_code=303)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Pragma"] = "no-cache"
                return response
            if isinstance(exc.__cause__, HistoryPrivacyExportConflict):
                return history_delete_page(
                    request,
                    user,
                    error=(
                        "A requested privacy export still contains this history. "
                        "Finish delivering it, then try again."
                    ),
                    status_code=409,
                )
            if isinstance(exc.__cause__, HistoryEpochError):
                return history_delete_page(
                    request,
                    user,
                    error=(
                        "Your swing history changed after this confirmation "
                        "was opened. Review the current history and try again."
                    ),
                    status_code=409,
                )
            logger.exception("Swing-history reset failed before a safe commit.")
            return history_delete_page(
                request,
                user,
                error=(
                    "We could not safely commit the reset. Your account and "
                    "allowance were not reset; please try again after recovery."
                ),
                status_code=503,
            )

        request.session[HISTORY_RESET_FLASH_KEY] = {
            "deleted_jobs": summary.deleted_jobs,
            "cleanup_pending": summary.cleanup_pending,
        }
        updated_user = users.get(user.id)
        if updated_user is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        request.session[HISTORY_SESSION_EPOCH_KEY] = (
            updated_user.history_epoch
        )
        response = RedirectResponse("/account", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Clear-Site-Data"] = '"cache"'
        return response

    @app.post("/account/password")
    def account_password(
        request: Request,
        password: str = Form(""),
        return_to: str = Form(""),
    ):
        """ "Add a password (optional)" for accounts that sign in by code.
        Being logged in (which took a code) is the proof of ownership.
        Accounts that already have a password change it through the
        code-verified reset flow, never here — so a walked-away session
        can't quietly swap a password it doesn't know."""
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        stale_session = reset_requires_fresh_session(request, user)
        if stale_session is not None:
            return stale_session
        if user.has_password:
            return RedirectResponse("/account", status_code=303)
        try:
            updated_user = users.add_password(
                user.id,
                password,
                expected_auth_epoch=user.auth_epoch,
                expected_history_epoch=user.history_epoch,
            )
        except PasswordAddConflict:
            request.session.clear()
            response = RedirectResponse("/login", status_code=303)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            return response
        except ValueError as exc:
            if return_to == "onboarding":
                return onboarding_response(
                    request, user, password_error=str(exc)
                )
            return account_page(request, user, password_error=str(exc))
        # Setting an optional password is not a new proof that the person at a
        # walked-away browser is still the account owner.  Preserve the prior
        # authentication timestamp instead of refreshing it here.
        establish_session(request, updated_user, fresh_auth=False)
        if not history_reset_recent_auth(request):
            request.session[PASSWORD_ADDED_REAUTH_SESSION_KEY] = True
        if return_to == "onboarding":
            return RedirectResponse(
                "/onboarding?password_added=1",
                status_code=303,
                headers={"Cache-Control": "no-store"},
            )
        return RedirectResponse("/account?password_added", status_code=303)

    @app.post("/account/digest")
    def account_digest(request: Request, enabled: str = Form("")):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        users.set_digest_opt_in(
            user.id, enabled.lower() in ("on", "true", "1", "yes")
        )
        return RedirectResponse("/account", status_code=303)

    @app.get("/onboarding", response_class=HTMLResponse)
    def onboarding_page(request: Request):
        if not cfg.web.get("require_account"):
            raise HTTPException(404, "Golfer setup needs accounts enabled.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return onboarding_response(request, user)

    @app.post("/onboarding")
    def onboarding_save(
        request: Request,
        display_name: str = Form(""),
        experience_mode: str = Form("improve"),
        handicap_range: str = Form(""),
        primary_goal: str = Form(""),
        practice_minutes: str = Form("20"),
        sessions_per_week: str = Form("2"),
        handedness: str = Form("right"),
        camera_angle: str = Form("face-on"),
        preferred_club: str = Form(""),
        reduced_motion: str = Form(""),
        marketing_email: str = Form(""),
        digest_opt: str = Form("", alias="digest"),
    ):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        if not cfg.web.get("require_account"):
            raise HTTPException(404, "Golfer setup needs accounts enabled.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            if not primary_goal.strip():
                raise ValueError("Choose a main goal for your golfer profile.")
            if not preferred_club.strip():
                raise ValueError("Choose a club for your golfer profile.")
            users.upsert_golfer_profile(
                user.id,
                display_name=display_name,
                experience_mode=experience_mode,
                handicap_range=handicap_range,
                primary_goal=primary_goal,
                practice_minutes=practice_minutes,
                sessions_per_week=sessions_per_week,
                handedness=handedness,
                camera_angle=camera_angle,
                preferred_club=preferred_club,
                reduced_motion=reduced_motion.lower() in ("on", "true", "1", "yes"),
                marketing_email_opt_in=(
                    marketing_email.lower() in ("on", "true", "1", "yes")
                ),
            )
        except ValueError as exc:
            return onboarding_response(
                request,
                user,
                error=str(exc),
                submitted={
                    "display_name": display_name,
                    "experience_mode": experience_mode,
                    "handicap_range": handicap_range,
                    "primary_goal": primary_goal,
                    "practice_minutes": practice_minutes,
                    "sessions_per_week": sessions_per_week,
                    "handedness": handedness,
                    "camera_angle": camera_angle,
                    "preferred_club": preferred_club,
                    "reduced_motion": reduced_motion,
                    "marketing_email": marketing_email,
                    "digest": digest_opt,
                },
            )
        # The form's checkbox reflects the account's current choice, so a
        # save is a genuine toggle through the same consent path as /account.
        users.set_digest_opt_in(
            user.id, digest_opt.lower() in ("on", "true", "1", "yes")
        )
        return RedirectResponse("/today?setup_saved", status_code=303)

    def practice_choices_for_drill(drill, profile: GolferProfile | None) -> list[dict]:
        """Turn one measured drill into deliberately bounded time choices."""

        if drill is None:
            return []
        preferred = profile.practice_minutes if profile else 20
        choices = (
            (10, "Quick reset", "Set up the drill, make a small set of slow reps, and stop while the cue is clear."),
            (20, "Standard session", "Run the listed drill dosage once, then make a few normal swings using the same cue."),
            (45, "Deep practice", "Use three short, focused sets with a reset between them; keep only the same cue and pass mark."),
        )
        return [
            {
                "minutes": minutes,
                "title": title,
                "detail": detail,
                "selected": minutes == preferred,
                "drill_name": drill.name,
                "aim": drill.aim,
                "dosage": drill.dosage,
                "pass_mark": drill.success_metric,
            }
            for minutes, title, detail in choices
        ]

    def current_practice_plan(brief, profile: GolferProfile | None) -> list[dict]:
        """Turn the current Caddie Brief into 10/20/45 minute choices."""

        if brief is None or brief.drill is None or brief.refilm_required:
            return []
        return practice_choices_for_drill(brief.drill, profile)

    def proof_cycle_practice_choices(assignment, profile: GolferProfile | None) -> list[dict]:
        """Reload only the exact drill ID the active target originally chose."""

        if assignment is None:
            return []
        for drills in build_drills(cfg.coaching).values():
            for drill in drills:
                if drill.id == assignment.drill_id:
                    return practice_choices_for_drill(drill, profile)
        # A future operator may remove a library drill. Keep the target and
        # its measurement valid, but do not silently substitute a new drill.
        return []

    @app.get("/today", response_class=HTMLResponse)
    def today_page(request: Request):
        if not cfg.web.get("require_account"):
            raise HTTPException(404, "Today needs accounts enabled.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        profile = users.get_golfer_profile(user.id)
        recent_jobs = manager.list_recent(limit=4, user_id=user.id)
        latest_job = recent_jobs[0] if recent_jobs else None
        brief = caddie_brief_for(latest_job) if latest_job is not None else None

        def today_report_available(job: Job | None) -> bool:
            """Match the established report route's customer-visible gate."""

            return bool(
                job is not None
                and resolved_report(job) is not None
                and (
                    manager.coaching_eligible(job)
                    or current_safe_report(job)
                )
            )

        latest_report_available = today_report_available(latest_job)
        latest_capture_report = bool(
            latest_job is not None and current_safe_report(latest_job)
        )
        if profile is None or not profile.is_complete:
            today_state = "setup"
        elif latest_job is None:
            today_state = "empty"
        elif latest_job.status == "queued":
            today_state = "queued"
        elif latest_job.status == "processing":
            today_state = "processing"
        elif latest_job.status == FAILED:
            today_state = "failed"
        elif (
            brief is not None and brief.refilm_required
        ) or latest_capture_report:
            today_state = "refilm"
        elif brief is not None and brief.drill is not None:
            today_state = "coaching_ready"
        else:
            # An accessible report can remain useful even when a structured
            # coaching card cannot be reconstructed from its sidecars.
            today_state = "legacy"

        preferred_club = (
            CLUB_LABELS.get(profile.preferred_club)
            if profile is not None and profile.is_complete
            else None
        )
        practice_minutes = (
            profile.practice_minutes
            if profile is not None
            and profile.is_complete
            and profile.practice_minutes in (10, 20, 45)
            else None
        )
        analyses_left = quota_left(user)
        refilm_credit = matched_refilm_credit(user)
        refilm_credit_open = bool(
            refilm_credit is not None and refilm_credit["available"]
        )
        allowance_text = (
            "Unlimited analyses"
            if analyses_left is None
            else (
                # The proof loop stays open even at zero: the tile must not
                # contradict the free matched re-film offered right below.
                "Allowance used — your matched re-film is still free"
                if analyses_left == 0 and refilm_credit_open
                else (
                    f"{analyses_left} analysis left this month"
                    if analyses_left == 1
                    else f"{analyses_left} analyses left this month"
                )
            )
        )
        today_tiles = (
            {
                "label": "Preferred club",
                "value": preferred_club or "Not set",
                "detail": (
                    "Your saved starting context"
                    if preferred_club
                    else "Choose one in golfer setup"
                ),
            },
            {
                "label": "Practice block",
                "value": (
                    f"{practice_minutes} minutes"
                    if practice_minutes is not None
                    else "Not set"
                ),
                "detail": (
                    "Your saved focused-session length"
                    if practice_minutes is not None
                    else "Choose a realistic block in setup"
                ),
            },
            {
                "label": "Plan & allowance",
                "value": "Pro member" if user.is_pro else "Free plan",
                "detail": allowance_text,
            },
        )

        recent_sessions = []
        for index, recent_job in enumerate(recent_jobs):
            recent_report_available = today_report_available(recent_job)
            recent_capture_report = current_safe_report(recent_job)
            recent_brief = (
                brief
                if recent_job.id == getattr(latest_job, "id", None)
                else (
                    caddie_brief_for(recent_job)
                    if recent_job.status == DONE
                    else None
                )
            )
            if recent_job.status == "queued":
                result_state = "queued"
                result_label = "Queued"
                result_detail = "Waiting for an analysis slot"
            elif recent_job.status == "processing":
                result_state = "processing"
                result_label = "Analyzing"
                result_detail = (
                    f"{recent_job.swings_done} of {recent_job.swings_total} swings analyzed"
                    if recent_job.swings_total
                    else "Reading the uploaded swing"
                )
            elif recent_job.status == FAILED:
                result_state = "failed"
                result_label = "Needs attention"
                result_detail = "Open for recovery guidance"
            elif (
                recent_brief is not None and recent_brief.refilm_required
            ) or recent_capture_report:
                result_state = "refilm"
                result_label = "Re-film needed"
                result_detail = "Capture needs another pass"
            elif recent_brief is not None:
                result_state = "coaching_ready"
                result_label = "Coaching ready"
                result_detail = recent_brief.focus_name
            elif recent_job.status == DONE and recent_report_available:
                result_state = "legacy"
                result_label = "Saved report"
                result_detail = "Open the preserved result"
            else:
                result_state = "complete"
                result_label = "Completed"
                result_detail = "No current coaching card is available"
            recent_sessions.append(
                {
                    "id": recent_job.id,
                    "position": "Latest" if index == 0 else "Earlier",
                    "when": (
                        time.strftime(
                            "%b %d", time.localtime(recent_job.created_at)
                        )
                        if recent_job.created_at
                        else None
                    ),
                    "club": CLUB_LABELS.get(recent_job.club)
                    or "Club not recorded",
                    "state": result_state,
                    "label": result_label,
                    "detail": result_detail,
                }
            )

        checkins = users.list_practice_checkins(user.id, limit=20)
        checked_session_ids = {checkin.session_id for checkin in checkins}
        latest_proof_artifact = (
            proof_cycle_artifact_for(latest_job)
            if latest_job is not None and latest_job.status == DONE
            else None
        )
        today_proof = proof_cycle_view(latest_proof_artifact)
        proof_cycle_practice_assignment = (
            proof_cycle_practice_assignment_for_job(
                latest_job, latest_proof_artifact
            )
            if latest_job is not None
            else None
        )
        structured_practice_choices = proof_cycle_practice_choices(
            proof_cycle_practice_assignment,
            profile,
        )
        if not structured_practice_choices:
            # Avoid a form that would log a target after its source drill was
            # intentionally removed or retuned out of the active library.
            proof_cycle_practice_assignment = None
        structured_practice_receipts = []
        current_practice_day = int(time.time() // 86400)
        if proof_cycle_practice_assignment is not None:
            try:
                structured_practice_receipts = users.list_proof_cycle_practice_evidence(
                    user.id,
                    baseline_session_id=(
                        proof_cycle_practice_assignment.baseline_session_id
                    ),
                    target_fingerprint=(
                        proof_cycle_practice_assignment.target_fingerprint
                    ),
                )
            except Exception:
                logger.exception("Proof Cycle practice receipt lookup failed")
        return render(
            "web_today.html.j2",
            request,
            profile=profile,
            latest_job=latest_job,
            latest_report_available=latest_report_available,
            today_state=today_state,
            today_tiles=today_tiles,
            analyses_left=analyses_left,
            refilm_credit=refilm_credit,
            recent_sessions=recent_sessions,
            today_proof=today_proof,
            caddie_brief=brief,
            practice_choices=(
                structured_practice_choices
                if proof_cycle_practice_assignment is not None
                else current_practice_plan(brief, profile)
            ),
            proof_cycle_practice_assignment=proof_cycle_practice_assignment,
            proof_cycle_practice_receipt=(
                next(
                    (
                        receipt
                        for receipt in structured_practice_receipts
                        if receipt.completed_day == current_practice_day
                    ),
                    None,
                )
            ),
            latest_practiced=(
                latest_job is not None and latest_job.id in checked_session_ids
            ),
            practice_done="practice_done" in request.query_params,
            setup_saved="setup_saved" in request.query_params,
        )

    @app.post("/practice/checkins")
    def practice_checkin(
        request: Request,
        session_id: str = Form(""),
        practice_minutes: str = Form(""),
        practice_outcome: str = Form(""),
    ):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        job = get_job_or_404(session_id, request)
        if (
            job.status != DONE
            or not manager.coaching_eligible(job)
            or caddie_brief_for(job) is None
        ):
            raise HTTPException(400, "This session is not ready for a practice check-in.")
        if practice_minutes or practice_outcome:
            assignment = proof_cycle_practice_assignment_for_job(job)
            if assignment is None:
                raise HTTPException(
                    409,
                    "This practice receipt no longer matches an active Proof Cycle target.",
                )
            try:
                users.record_proof_cycle_practice_evidence(
                    user.id,
                    baseline_session_id=assignment.baseline_session_id,
                    target_fingerprint=assignment.target_fingerprint,
                    drill_id=assignment.drill_id,
                    minutes=practice_minutes,
                    outcome=practice_outcome,
                    expected_history_epoch=user.history_epoch,
                )
            except HistoryEpochError:
                raise HTTPException(
                    409,
                    "Swing history changed while this request was in progress.",
                ) from None
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        try:
            users.record_practice_checkin(
                user.id,
                job.id,
                expected_history_epoch=user.history_epoch,
            )
        except HistoryEpochError:
            raise HTTPException(
                409,
                "Swing history changed while this request was in progress.",
            ) from None
        return RedirectResponse("/today?practice_done", status_code=303)

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
        # Progress Pro gate (billing.progress_pro_only), the same shape as
        # the coach-replay gate: free accounts see an honest locked teaser
        # instead of their charts, and no trend data is computed for it.
        # Open instances never reach here (the require_account check above).
        if cfg.billing.get("progress_pro_only") and not user.is_pro:
            return render("web_progress.html.j2", request, locked=True)
        listed = manager.list_recent(user_id=user.id)
        selected_context = None
        if club_aware_enabled():
            # Every chip resolves to the latest readable hand/angle context
            # for that club. The default is the latest readable session
            # overall. We never offer an "all clubs" aggregate because club,
            # hand, and camera angle are one comparison boundary.
            context_by_club: dict[
                str, tuple[Job, tuple[str, str, str]]
            ] = {}
            for club in sorted(CLUB_LABELS):
                latest_for_club = latest_readable_job(
                    job for job in listed if job.club == club
                )
                context = exact_job_context(latest_for_club)
                if latest_for_club is not None and context is not None:
                    context_by_club[club] = (latest_for_club, context)

            requested_club = request.query_params.get("club") or ""
            if requested_club in context_by_club:
                selected_job, selected_context = context_by_club[
                    requested_club
                ]
            else:
                selected_job = latest_readable_job(listed)
                selected_context = exact_job_context(selected_job)

            clubs_present = sorted(context_by_club)
            club_selected = (
                selected_context[0] if selected_context is not None else ""
            )
            if selected_job is None or selected_context is None:
                # A readable row without complete authoritative context must
                # not silently fall back to a mixed or inferred aggregate.
                listed = []
            else:
                club, hand, angle = selected_context
                listed = manager.list_comparable(
                    user_id=user.id,
                    club=club,
                    hand=hand,
                    angle=angle,
                    through=selected_job.created_at,
                )
        else:
            # Compatibility path: preserve the established optional club-only
            # filter exactly until the explicit activation flag is boolean true.
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
            context_label=context_label(selected_context),
        )

    @app.get("/pricing", response_class=HTMLResponse)
    def pricing(request: Request):
        # Per-card store deep links: each Pro card preselects its own
        # variant at checkout when the operator has mapped one
        # (billing.shopify_variant_ids). An unmapped card falls back to
        # the plain product page rather than guessing a variant.
        base_store_url = (
            shopify_billing.buy_url(cfg)
            if shopify_billing.commerce_enabled()
            else None
        )
        variant_ids = cfg.billing.get("shopify_variant_ids") or {}

        def plan_store_url(plan: str) -> str | None:
            if base_store_url is None:
                return None
            variant = str(variant_ids.get(plan) or "").strip()
            if not variant:
                return base_store_url
            return f"{base_store_url}?variant={variant}"

        # The quiet personal line: only for logged-in users with >= 2
        # sessions of real data (trend_sentence is None otherwise).
        return render(
            "web_pricing.html.j2", request,
            trend_line=personal_trend(current_user(request)),
            pro_store_url_monthly=plan_store_url("monthly"),
            pro_store_url_yearly=plan_store_url("yearly"),
            pro_store_url_lifetime=plan_store_url("lifetime"),
            # Display strings only — the store/Stripe stays the source of
            # truth for what is actually charged.
            pro_price_annual_text=cfg.billing.get("pro_price_annual_text"),
            pro_price_monthly_text=cfg.billing.get("pro_price_monthly_text"),
            pro_price_lifetime_text=cfg.billing.get("pro_price_lifetime_text"),
            pro_annual_badge_text=cfg.billing.get("pro_annual_badge_text"),
            # Renewal copy tracks what the store actually sells — false on
            # a passes-only store, where nothing auto-renews.
            store_subscriptions=bool(cfg.billing.get("store_subscriptions")),
        )

    # -- gear shop --------------------------------------------------------
    @app.get("/shop", response_class=HTMLResponse)
    def shop_page(request: Request):
        if not shop_active():
            raise HTTPException(404, "The gear shop isn't set up.")
        return render(
            "web_shop.html.j2",
            request,
            products=shop.fetch_products(cfg),
            first_sale_gate_active=bool(
                cfg.shop.get("first_sale_catalog_only")
            ),
        )

    # -- billing ----------------------------------------------------------
    @app.post("/billing/checkout")
    def checkout(request: Request):
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
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
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not billing.enabled() or not user.stripe_customer_id:
            raise HTTPException(503, "No subscription to manage yet.")
        return RedirectResponse(
            billing.create_portal_url(user, _base_url(request)), status_code=303
        )

    # Both the exact path and the trailing-slash variant are registered:
    # webhook senders (Shopify, Stripe) do NOT follow 3xx, so the default
    # trailing-slash redirect would turn a stray "/" in the configured URL
    # into a silently-failed delivery. Accepting both makes it robust.
    @app.post("/webhooks/stripe")
    @app.post("/webhooks/stripe/")
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
    @app.post("/webhooks/shopify/")
    async def shopify_webhook(request: Request):
        if not shopify_billing.webhook_endpoint_enabled():
            raise HTTPException(503, "Shopify webhooks aren't set up.")
        payload = await _read_bounded_request_body(
            request, SHOPIFY_WEBHOOK_MAX_BODY_BYTES
        )
        try:
            await run_in_threadpool(
                shopify_billing.handle_webhook,
                payload,
                request.headers.get("x-shopify-hmac-sha256", ""),
                request.headers.get("x-shopify-topic", ""),
                users,
                cfg,
                event_id=request.headers.get("x-shopify-webhook-id"),
                shop_domain=request.headers.get("x-shopify-shop-domain"),
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
        level: str = Form(""),
        strikes: str = Form(""),
        fast: str = Form(""),
        notify: str = Form(""),
        transfer_check: str = Form(""),
    ):
        if request.headers.get("authorization") is not None:
            # A device credential is explicit/non-ambient, while the browser
            # cookie path below retains its Origin/Referer CSRF boundary.
            user, _ = api_v1_auth(request)
        else:
            if not _same_origin_form_post(request):
                raise HTTPException(403, "Invalid request origin.")
            user = current_user(request)
        # The raw form values are fine here: the free matched re-film only
        # matches a validated baseline context, so junk simply never matches
        # — and quota decisions keep precedence over input validation.
        ensure_user_can_analyze(
            user, manager, cfg, declared_context=(club, hand, angle)
        )
        had_completed_analysis = bool(
            user is not None
            and any(
                item.status == DONE
                for item in manager.list_recent(user_id=user.id)
            )
        )
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
        if club not in CLUB_LABELS:
            raise HTTPException(
                400,
                "club must be one of: " + ", ".join(sorted(CLUB_LABELS)),
            )
        if level and level not in LEVEL_LABELS:
            raise HTTPException(
                400,
                "level must be one of: " + ", ".join(sorted(LEVEL_LABELS)),
            )
        normalized_transfer_check = transfer_check.strip().lower()
        if normalized_transfer_check not in {"", "on"}:
            raise HTTPException(400, "Invalid normal-swing transfer declaration.")
        upload_transfer_assignment = None
        if normalized_transfer_check == "on":
            upload_transfer_assignment = active_proof_cycle_practice_assignment(
                user,
                club=club,
                hand=hand,
                angle=angle,
                before=time.time(),
            )
            if upload_transfer_assignment is None:
                # The checkbox is intentionally advisory, never an authority:
                # changing club, hand, or angle after loading the form creates
                # an ordinary upload rather than a falsely linked transfer.
                raise HTTPException(
                    409,
                    "This upload no longer matches the active Proof Cycle target. "
                    "Use the same club, handedness, and camera angle or upload "
                    "it as a regular analysis.",
                )

        ip = client_ip(request)
        per_ip = int(cfg.web.get("max_active_jobs_per_ip") or 0)
        if ip and per_ip and manager.active_for_ip(ip) >= per_ip:
            raise HTTPException(
                429,
                f"You already have {per_ip} analyses queued or running — "
                "wait for one to finish before uploading another clip.",
            )

        try:
            job = manager.create_session(
                source_name=video.filename,
                hand=hand,
                angle=angle,
                club=club,
                level=level or None,
                strikes=manual_strikes,
                fast=fast.lower() in ("on", "true", "1", "yes"),
                client_ip=ip,
                user_id=user.id if user else None,
                # "Email me when my coaching is ready" — meaningful only for
                # an owned session (there is no address to notify otherwise).
                notify_email=(
                    user is not None
                    and notify.lower() in ("on", "true", "1", "yes")
                ),
                expected_history_epoch=(
                    user.history_epoch if user is not None else None
                ),
            )
        except HistoryResetConflict:
            raise HTTPException(
                409,
                "Swing history changed while this upload was starting. Try again.",
            ) from None
        if user is not None:
            record_product_event(
                request,
                "upload_started",
                user=user,
                session_id=job.id,
                dedupe_key=f"upload_started:{job.id}",
            )
            if had_completed_analysis:
                record_product_event(
                    request,
                    "repeat_analysis",
                    user=user,
                    session_id=job.id,
                    dedupe_key=f"repeat_analysis:{job.id}",
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
                        raise HTTPException(
                            413,
                            f"Video is larger than the {max_mb:g} MB upload limit.",
                        )
                    fh.write(chunk)
        except HTTPException:
            # Unwind the file context before deleting the session. Windows
            # cannot remove the open destination even though Linux can.
            manager.discard(job)
            raise
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
        if upload_transfer_assignment is not None:
            try:
                users.record_proof_cycle_transfer_check(
                    user.id,
                    session_id=job.id,
                    baseline_session_id=(
                        upload_transfer_assignment.baseline_session_id
                    ),
                    target_fingerprint=(
                        upload_transfer_assignment.target_fingerprint
                    ),
                    drill_id=upload_transfer_assignment.drill_id,
                    club=job.club,
                    hand=job.hand,
                    angle=job.angle,
                    normal_swings=True,
                    expected_history_epoch=user.history_epoch,
                )
            except HistoryEpochError:
                manager.discard(job)
                raise HTTPException(
                    409,
                    "Swing history changed while this upload was in progress.",
                ) from None
            except ValueError as exc:
                manager.discard(job)
                raise HTTPException(409, str(exc)) from None
        manager.submit(job, dest)
        if _wants_json(request):
            return JSONResponse({"id": job.id, "url": f"/session/{job.id}"})
        return RedirectResponse(f"/session/{job.id}", status_code=303)

    def caddie_brief_for(job: Job):
        """One concise coaching decision from this job and comparable history."""
        if job.status != DONE or not job.report_rel:
            return None
        report_path = resolved_report(job)
        if report_path is None:
            return None
        report_rule = persisted_priority_rule_version(report_path)
        if report_rule is None:
            return None
        exact_context_required = club_aware_enabled() or report_rule == 2
        context = exact_job_context(job) if exact_context_required else None
        if exact_context_required and context is None:
            # Never let mutable metrics metadata stand in for the authoritative
            # job row when rule 2 or the global exact-context policy applies.
            return None
        metrics = job.session_dir / Path(job.report_rel).parent / "metrics.json"
        try:
            payload = json.loads(metrics.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not payload_structure_is_valid(payload):
            if (
                isinstance(payload, dict)
                and payload_requires_refilm(payload, angle=job.angle)
            ):
                return build_caddie_brief_from_payload(
                    payload,
                    cfg,
                    angle=job.angle,
                    club=job.club,
                    rule_version=report_rule,
                )
            return None
        if (
            not payload_has_coachable_data(payload, angle=job.angle)
            and manager.coaching_eligible(job)
        ):
            # A current coaching report can remain usable after a partial
            # restore even when its structured payload is empty/corrupt.
            # Show the preserved-report recovery state, not a new diagnosis.
            return None

        previous_counts: dict[str, int] = {}
        personal_trend = None
        if job.user_id:
            comparable = manager.list_comparable(
                user_id=job.user_id,
                club=job.club,
                through=job.created_at,
                hand=(context[1] if context is not None else None),
                angle=(context[2] if context is not None else None),
            )
            all_trends = build_trends(comparable, cfg)
            personal_trend = trend_sentence(all_trends)
            for sample in all_trends.samples:
                if sample.job_id == job.id:
                    continue
                for flag in sample.flags:
                    previous_counts[flag] = previous_counts.get(flag, 0) + 1

        brief = build_caddie_brief_from_payload(
            payload,
            cfg,
            previous_flag_counts=previous_counts,
            trend=personal_trend,
            angle=job.angle,
            club=job.club,
            rule_version=report_rule,
        )
        if brief is None:
            return None
        if current_safe_report(job) and not brief.refilm_required:
            return None
        return brief

    def proof_cycle_practice_enabled() -> bool:
        """Require an explicit second gate before collecting practice context."""

        return (
            proof_cycle_enabled(cfg)
            and cfg.proof_cycle.get("practice_evidence_enabled") is True
        )

    def proof_cycle_artifact_for(job: Job):
        """Verify a completed sidecar without ever writing during a GET."""

        if not proof_cycle_enabled(cfg) or job.status != DONE:
            return None
        try:
            prior_jobs: list[Job] = []
            if job.user_id and job.club:
                prior_jobs = manager.list_comparable(
                    user_id=job.user_id,
                    club=job.club,
                    through=job.created_at,
                    limit=proof_cycle_history_scan_limit(cfg),
                )
            return verified_proof_cycle_artifact(
                job,
                prior_jobs,
                cfg,
                baseline_job_for_id=manager.get,
            )
        except Exception:
            # Sidecar validation is an optional result enhancement. A stale
            # disk artifact must never prevent a completed report from loading.
            logger.exception("Proof Cycle result validation failed for job %s", job.id)
            return None

    def active_proof_cycle_practice_assignment(
        user: User | None,
        *,
        club: object,
        hand: object,
        angle: object,
        before: float,
    ):
        """Find a target for a prospective upload, never trusting the form."""

        if not proof_cycle_practice_enabled() or user is None or not club:
            return None
        try:
            prior_jobs = manager.list_comparable(
                user_id=user.id,
                club=str(club),
                through=before,
                limit=proof_cycle_history_scan_limit(cfg),
            )
            target = active_proof_cycle_target_for_context(
                prior_jobs,
                cfg,
                user_id=user.id,
                club=club,
                hand=hand,
                angle=angle,
                before=before,
                baseline_job_for_id=manager.get,
            )
            return practice_assignment_from_target(target)
        except Exception:
            logger.exception("Proof Cycle practice target lookup failed")
            return None

    def proof_cycle_practice_assignment_for_job(job: Job, artifact=None):
        if not proof_cycle_practice_enabled() or not job.user_id:
            return None
        trusted = artifact if artifact is not None else proof_cycle_artifact_for(job)
        return practice_assignment_from_target(
            trusted.target if trusted is not None else None
        )

    def proof_cycle_practice_for(job: Job, artifact=None):
        """Render practice context only after the result sidecar re-verifies."""

        if not proof_cycle_practice_enabled() or not job.user_id:
            return None
        trusted = artifact if artifact is not None else proof_cycle_artifact_for(job)
        assignment = proof_cycle_practice_assignment_for_job(job, trusted)
        if trusted is None or assignment is None:
            return None
        baseline = manager.get(assignment.baseline_session_id)
        if baseline is None:
            return None
        try:
            evidence = users.list_proof_cycle_practice_evidence(
                job.user_id,
                baseline_session_id=assignment.baseline_session_id,
                target_fingerprint=assignment.target_fingerprint,
            )
            transfer_check = users.get_proof_cycle_transfer_check(job.user_id, job.id)
            return practice_transfer_view(
                trusted,
                evidence,
                transfer_check,
                user_id=job.user_id,
                refilm_session_id=job.id,
                club=job.club,
                hand=job.hand,
                angle=job.angle,
                baseline_created_at=baseline.created_at,
                refilm_created_at=job.created_at,
            )
        except Exception:
            # This card is additive context. Its data must never stop access
            # to the completed report or the already-verified Proof result.
            logger.exception("Proof Cycle practice result validation failed for job %s", job.id)
            return None

    def gear_for(job: Job, brief) -> list[dict]:
        """At most one optional aid tied to the brief's measured priority."""
        if (
            job.status != DONE
            or not job.report_rel
            or brief is None
            or not brief.focus_flag
            or brief.drill is None
            or not manager.coaching_eligible(job)
            or not shop_active()
        ):
            return []
        gear_flag = brief.drill.gear_tag.partition(":")[2]
        if not gear_flag:
            return []
        return shop.recommend(
            shop.fetch_products(cfg), [gear_flag], cfg, limit=1
        )

    @app.get("/session/{job_id}", response_class=HTMLResponse)
    def status_page(job_id: str, request: Request):
        access_user = session_access_user(request)
        job = get_job_or_404(
            job_id, request, authenticated_user=access_user
        )
        coaching_eligible = manager.coaching_eligible(job)
        safe_report_available = current_safe_report(job)
        if (
            not cfg.web.get("require_account")
            and coaching_eligible
        ):
            request.session["has_analysis"] = True
        failed = job.status == FAILED
        report_path = resolved_report(job)
        brief = caddie_brief_for(job) if report_path is not None else None
        proof_cycle_artifact = (
            proof_cycle_artifact_for(job) if report_path is not None else None
        )
        proof_cycle = proof_cycle_view(proof_cycle_artifact)
        proof_cycle_practice = (
            proof_cycle_practice_for(job, proof_cycle_artifact)
            if report_path is not None
            else None
        )
        if job.status == DONE:
            record_product_event(
                request,
                "upload_completed",
                user=access_user,
                session_id=job.id,
                dedupe_key=f"upload_completed:{job.id}",
            )
            if brief is not None and not brief.refilm_required:
                record_product_event(
                    request,
                    "brief_viewed",
                    user=access_user,
                    session_id=job.id,
                    dedupe_key=f"brief_viewed:{job.id}",
                )
        report_only = job.status == DONE and coaching_eligible and brief is None
        current_report_only = bool(
            report_only
            and report_path is not None
            and persisted_report_outcome(report_path)
            == REPORT_OUTCOME_COACHING
        )
        response = render(
            "web_status.html.j2",
            request,
            job=job,
            done=job.status == DONE,
            failed=failed,
            # Plain-English guidance instead of pipeline/CLI jargon; the raw
            # error stays available via the JSON API.
            error_help=humanize.friendly_error(job.error) if failed else None,
            queue_position=manager.queue_position(job),
            caddie_brief=brief,
            proof_cycle=proof_cycle,
            proof_cycle_practice=proof_cycle_practice,
            refilm_needed=job.status == DONE and not coaching_eligible,
            legacy_report=report_only,
            current_report_only=current_report_only,
            capture_report_available=(
                job.status == DONE
                and not coaching_eligible
                and safe_report_available
            ),
            gear=gear_for(job, brief),
        )
        if job.user_id is not None:
            # A mobile bearer may own this page without also having a browser
            # cookie, so render() cannot infer personalization on its own.
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get("/sessions", response_class=HTMLResponse)
    def sessions_page(request: Request):
        user = current_user(request)
        if cfg.web.get("require_account"):
            if user is None:
                return RedirectResponse("/login", status_code=303)
            listed = manager.list_recent(user_id=user.id)
        else:
            listed = manager.list_recent()
        refilm_ids = {
            job.id
            for job in listed
            if job.status == DONE and not manager.coaching_eligible(job)
        }
        return render(
            "web_sessions.html.j2",
            request,
            sessions=listed,
            refilm_ids=refilm_ids,
        )

    @app.get("/session/{job_id}/report")
    def report(job_id: str, request: Request):
        job = get_job_or_404(
            job_id,
            request,
            authenticated_user=session_access_user(request),
        )
        if job.status != DONE or not job.report_rel:
            return RedirectResponse(f"/session/{job_id}")
        if (
            not manager.coaching_eligible(job)
            and not current_safe_report(job)
        ):
            return RedirectResponse(f"/session/{job_id}", status_code=303)
        return RedirectResponse(f"/session/{job_id}/files/{job.report_rel}")

    @app.get("/session/{job_id}/files/{file_path:path}")
    def session_file(job_id: str, file_path: str, request: Request):
        job = get_job_or_404(
            job_id,
            request,
            authenticated_user=session_access_user(request),
        )
        root = job.session_dir.resolve()
        target = (root / file_path).resolve()
        if not target.is_relative_to(root):  # block path traversal
            raise HTTPException(404, "Not found")
        if not target.is_file():
            raise HTTPException(404, "Not found")
        if job.status != DONE:
            return RedirectResponse(
                f"/session/{job_id}", status_code=303
            )
        declared_report = resolved_report(job)

        def same_file(candidate: Path, expected: Path | None) -> bool:
            if expected is None or not expected.is_file():
                return False
            try:
                return candidate.samefile(expected)
            except OSError:
                return False

        target_name = target.name.rstrip(" .").casefold()
        declared_metrics = (
            declared_report.parent / "metrics.json"
            if declared_report is not None
            else None
        )
        declared_proof_cycle = (
            declared_report.parent / ARTIFACT_FILENAME
            if declared_report is not None
            else None
        )
        requested_proof_cycle = (
            target_name == ARTIFACT_FILENAME
            or same_file(target, declared_proof_cycle)
        )
        if requested_proof_cycle:
            # The sidecar is an internal, versioned enhancement rather than a
            # public artifact/API contract.  It contains no raw video or owner
            # identity, but it must stay behind the rendered customer surface.
            raise HTTPException(404, "Not found")
        requested_metrics = (
            target_name == "metrics.json"
            or same_file(target, declared_metrics)
        )
        if requested_metrics and not valid_metrics_file(
            target, angle=job.angle
        ):
            return RedirectResponse(
                f"/session/{job_id}", status_code=303
            )
        requested_report = same_file(target, declared_report)
        if (
            target_name.endswith(".html")
            and not requested_report
        ):
            return RedirectResponse(
                f"/session/{job_id}", status_code=303
            )
        if not manager.coaching_eligible(job):
            safe_capture = current_safe_report(job)
            slowmo_allowed = False
            if safe_capture and declared_report is not None:
                media_dir = declared_report.parent / "media"
                if media_dir.is_dir():
                    slowmo_allowed = any(
                        same_file(target, candidate)
                        for candidate in media_dir.glob("slowmo_*")
                        if candidate.is_file()
                    )
            allowed = (
                (requested_report and safe_capture)
                or slowmo_allowed
            )
            if not allowed:
                return RedirectResponse(
                    f"/session/{job_id}", status_code=303
                )
        response = FileResponse(target)
        if job.user_id is not None:
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    # -- JSON API (what a future mobile app talks to) ----------------------
    @app.get("/api/session/{job_id}")
    def api_status(job_id: str, request: Request):
        job = get_job_or_404(
            job_id,
            request,
            authenticated_user=session_access_user(request),
        )
        if (
            not cfg.web.get("require_account")
            and manager.coaching_eligible(job)
        ):
            request.session["has_analysis"] = True
        response = JSONResponse(api_payload(job))
        if job.user_id is not None:
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get("/api/sessions")
    def api_sessions(request: Request):
        user = session_access_user(request)
        if cfg.web.get("require_account"):
            if user is None:
                raise HTTPException(401, "Log in first.")
            listed = manager.list_recent(user_id=user.id)
        else:
            listed = manager.list_recent()

        def index_payload(job: Job) -> dict:
            payload = {
                "id": job.id,
                "status": job.status,
                "created_at": job.as_dict()["created_at"],
                "source_name": job.source_name,
                "swings_done": job.swings_done,
                "swings_total": job.swings_total,
            }
            if job.status == DONE:
                coaching_eligible = manager.coaching_eligible(job)
                payload["coaching_eligible"] = coaching_eligible
                payload["outcome"] = (
                    "coaching_ready"
                    if coaching_eligible
                    else "refilm_required"
                )
            return payload

        response = JSONResponse(
            {
                "sessions": [index_payload(job) for job in listed]
            }
        )
        if cfg.web.get("require_account"):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    # -- /api/v1: stable PWA/native resources ----------------------------
    def mobile_token_payload(token: MobileAPIToken) -> dict:
        """Serialize management/export-safe device lifecycle metadata only."""

        return {
            "selector": token.selector,
            "label": token.label,
            "created_at": token.created_at,
            "last_used_at": token.last_used_at,
            "expires_at": token.expires_at,
            "revoked_at": token.revoked_at,
            "active": token.active,
        }

    def no_store_json(payload: object, *, status_code: int = 200) -> JSONResponse:
        response = JSONResponse(payload, status_code=status_code)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    def api_v1_session_payload(job: Job) -> dict:
        payload = api_payload(job)
        payload["resource_version"] = 1
        return payload

    @app.get("/api/v1/me", response_model=api_models.MeResponse)
    def api_v1_me(request: Request):
        user, _ = api_v1_auth(request)
        return no_store_json(
            {
                "resource_version": 1,
                "identity": {
                    "id": user.id,
                    "email": user.email,
                    "email_verified": user.email_verified,
                    "history_epoch": user.history_epoch,
                    "shopify_customer_linked": bool(user.shopify_customer_id),
                    "shopify_account_state": user.shopify_account_migration_state,
                },
                "profile": profile_payload(users.get_golfer_profile(user.id)),
            }
        )

    @app.get("/api/v1/mobile-tokens", response_model=api_models.MobileTokenListResponse)
    def api_v1_mobile_tokens(request: Request):
        """List a browser owner's non-secret device token metadata."""

        user = mobile_token_management_user(request)
        return no_store_json(
            {
                "resource_version": 1,
                "tokens": [
                    mobile_token_payload(token)
                    for token in users.list_mobile_api_tokens(user.id)
                ],
            }
        )

    @app.post("/api/v1/mobile-tokens", response_model=api_models.MobileTokenIssueResponse)
    async def api_v1_issue_mobile_token(request: Request):
        """Issue a device token once to an authenticated same-origin browser."""

        user = mobile_token_management_user(request)
        payload = await bounded_json_object(request)
        if set(payload) != {"label"}:
            raise HTTPException(400, "A mobile device name is required.")
        try:
            raw_token, token = users.issue_mobile_api_token(
                user.id,
                payload["label"],
                expected_auth_epoch=user.auth_epoch,
            )
        except MobileAPITokenAuthEpochError:
            request.session.clear()
            raise HTTPException(401, "Log in again before adding a device.") from None
        except MobileAPITokenLimitError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        # The raw credential is deliberately absent from every later list or
        # export response.  This no-store response is the sole issue moment.
        return no_store_json(
            {
                "resource_version": 1,
                "token": raw_token,
                "device": mobile_token_payload(token),
            },
            status_code=201,
        )

    @app.delete("/api/v1/mobile-tokens/{selector}", response_model=api_models.MobileTokenRevokeResponse)
    def api_v1_revoke_mobile_token(selector: str, request: Request):
        user = mobile_token_management_user(request)
        if not users.revoke_mobile_api_token(user.id, selector):
            # Do not distinguish a malformed selector from a different
            # account's device record.
            raise HTTPException(404, "Mobile device not found.")
        return no_store_json({"resource_version": 1, "revoked": True})

    @app.get("/api/v1/profile", response_model=api_models.ProfileResponse)
    def api_v1_profile(request: Request):
        user, _ = api_v1_auth(request)
        return no_store_json(
            {
                "resource_version": 1,
                "profile": profile_payload(users.get_golfer_profile(user.id)),
            }
        )

    @app.put("/api/v1/profile", response_model=api_models.ProfileResponse)
    async def api_v1_update_profile(request: Request):
        user, via_bearer = api_v1_auth(request)
        if not via_bearer and not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        payload = await bounded_json_object(request)
        required = {
            "experience_mode",
            "handicap_range",
            "primary_goal",
            "practice_minutes",
            "sessions_per_week",
            "handedness",
            "camera_angle",
            "preferred_club",
            "reduced_motion",
            "marketing_email_opt_in",
        }
        allowed = required | {"display_name"}
        if not required.issubset(payload) or set(payload) - allowed:
            raise HTTPException(400, "A complete golfer profile is required.")
        if not isinstance(payload["reduced_motion"], bool) or not isinstance(
            payload["marketing_email_opt_in"], bool
        ):
            raise HTTPException(400, "Accessibility and marketing values must be boolean.")
        profile_update = {
            "experience_mode": payload["experience_mode"],
            "handicap_range": payload["handicap_range"],
            "primary_goal": payload["primary_goal"],
            "practice_minutes": payload["practice_minutes"],
            "sessions_per_week": payload["sessions_per_week"],
            "handedness": payload["handedness"],
            "camera_angle": payload["camera_angle"],
            "preferred_club": payload["preferred_club"],
            "reduced_motion": payload["reduced_motion"],
            "marketing_email_opt_in": payload["marketing_email_opt_in"],
        }
        if "display_name" in payload:
            profile_update["display_name"] = payload["display_name"]
        try:
            profile = users.upsert_golfer_profile(
                user.id,
                **profile_update,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return no_store_json(
            {"resource_version": 1, "profile": profile_payload(profile)}
        )

    @app.get("/api/v1/today", response_model=api_models.TodayResponse)
    def api_v1_today(request: Request):
        user, _ = api_v1_auth(request)
        profile = users.get_golfer_profile(user.id)
        recent = manager.list_recent(limit=1, user_id=user.id)
        latest = recent[0] if recent else None
        brief = caddie_brief_for(latest) if latest is not None else None
        checked = {
            checkin.session_id
            for checkin in users.list_practice_checkins(user.id, limit=20)
        }
        return no_store_json(
            {
                "resource_version": 1,
                "profile": profile_payload(profile),
                "latest_session": (
                    api_v1_session_payload(latest) if latest is not None else None
                ),
                "caddie_brief": caddie_brief_payload(brief),
                "practice_plan": current_practice_plan(brief, profile),
                "practice_checked_in": bool(latest and latest.id in checked),
            }
        )

    @app.get("/api/v1/sessions", response_model=api_models.SessionListResponse)
    def api_v1_sessions(request: Request):
        user, _ = api_v1_auth(request)
        return no_store_json(
            {
                "resource_version": 1,
                "sessions": [
                    api_v1_session_payload(job)
                    for job in manager.list_recent(user_id=user.id)
                ],
            }
        )

    @app.get("/api/v1/sessions/{job_id}", response_model=api_models.Session)
    def api_v1_session(job_id: str, request: Request):
        user, _ = api_v1_auth(request)
        return no_store_json(
            api_v1_session_payload(
                get_job_or_404(job_id, request, authenticated_user=user)
            )
        )

    @app.get("/api/v1/sessions/{job_id}/brief", response_model=api_models.SessionBriefResponse)
    def api_v1_session_brief(job_id: str, request: Request):
        user, _ = api_v1_auth(request)
        job = get_job_or_404(job_id, request, authenticated_user=user)
        if job.status != DONE:
            raise HTTPException(409, "This analysis is not complete.")
        brief = caddie_brief_for(job)
        if brief is None:
            raise HTTPException(404, "No Caddie Brief is available for this session.")
        return no_store_json(
            {"resource_version": 1, "caddie_brief": caddie_brief_payload(brief)}
        )

    @app.get("/api/v1/practice-checkins", response_model=api_models.PracticeCheckinListResponse)
    def api_v1_practice_checkins(request: Request):
        user, _ = api_v1_auth(request)
        return no_store_json(
            {
                "resource_version": 1,
                "checkins": [
                    {
                        "session_id": item.session_id,
                        "completed_at": item.completed_at,
                    }
                    for item in users.list_practice_checkins(user.id)
                ],
            }
        )

    @app.post("/api/v1/practice-checkins", response_model=api_models.PracticeCheckinResponse)
    async def api_v1_practice_checkin(request: Request):
        user, via_bearer = api_v1_auth(request)
        if not via_bearer and not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        payload = await bounded_json_object(request)
        if set(payload) != {"session_id"} or not isinstance(
            payload["session_id"], str
        ):
            raise HTTPException(400, "A session id is required.")
        job = get_job_or_404(
            payload["session_id"], request, authenticated_user=user
        )
        if (
            job.status != DONE
            or not manager.coaching_eligible(job)
            or caddie_brief_for(job) is None
        ):
            raise HTTPException(400, "This session is not ready for a practice check-in.")
        try:
            checkin = users.record_practice_checkin(
                user.id,
                job.id,
                expected_history_epoch=user.history_epoch,
            )
        except HistoryEpochError:
            raise HTTPException(
                409,
                "Swing history changed while this request was in progress.",
            ) from None
        return no_store_json(
            {
                "resource_version": 1,
                "checkin": {
                    "session_id": checkin.session_id,
                    "completed_at": checkin.completed_at,
                },
            }
        )

    @app.post("/api/v1/events", response_model=api_models.EventAccepted)
    async def api_v1_product_event(request: Request):
        # Event capture is intentionally not a bearer-token capability: it is
        # a browser telemetry surface with its existing same-origin guard.
        # Rejecting Authorization avoids cookie fallback if a client sends a
        # malformed or stale device token here.
        if request.headers.get("authorization") is not None:
            raise mobile_bearer_unauthorized()
        if not _same_origin_form_post(request):
            raise HTTPException(403, "Invalid request origin.")
        payload = await product_event_json(request)
        event_name = payload.get("event")
        if not isinstance(event_name, str) or event_name not in PRODUCT_EVENT_NAMES:
            raise HTTPException(400, "Unsupported product event.")
        session_id = payload.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise HTTPException(400, "Invalid event session id.")
        user = current_user(request)
        public_events = {
            "landing_view",
            "pro_clicked",
            "cart_started",
            "checkout_started",
        }
        if user is None and event_name not in public_events:
            raise HTTPException(401, "Log in first.")
        if session_id is not None and user is not None:
            get_job_or_404(session_id, request)
        record_product_event(
            request,
            event_name,
            user=user,
            session_id=session_id,
        )
        return JSONResponse({"accepted": True}, status_code=202)

    # -- operator KPIs (see swinglab.kpis) --------------------------------
    @app.get("/admin/product-events")
    def admin_product_events(request: Request):
        """PII-free conversion and migration counts for the operator only."""

        require_admin(request)
        raw_days = request.query_params.get("since_days", "30")
        try:
            since_days = float(raw_days)
        except ValueError:
            raise HTTPException(400, "since_days must be a positive number") from None
        if not math.isfinite(since_days) or not 0 < since_days <= 3650:
            raise HTTPException(400, "since_days must be a positive number")
        response = JSONResponse(
            {
                "since_days": since_days,
                "events": users.product_event_counts(
                    since=time.time() - since_days * 86400
                ),
                "shopify_customer_account_migration": (
                    users.shopify_account_migration_counts()
                ),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/admin/kpis")
    def admin_kpis(request: Request):
        """The five business KPIs as JSON, for the operator only. Gated by
        the SWINGLAB_ADMIN_TOKEN environment variable via a constant-time
        bearer comparison — and it answers 404 (never 401/403) when the
        variable is unset OR the token is wrong, so the endpoint's very
        existence is invisible to anyone without the credential."""
        require_admin(request)
        raw_since = request.query_params.get("since")
        if raw_since is None or raw_since == "":
            since_days = 90.0
        else:
            try:
                since_days = float(raw_since)
            except ValueError:
                raise HTTPException(400, "since must be a number of days")
            # An explicit 0 is rejected, not silently defaulted, and
            # nan/inf must not reach the window math (json can't carry
            # them back anyway).
            if not math.isfinite(since_days) or since_days <= 0:
                raise HTTPException(
                    400, "since must be a positive number of days"
                )
        from ..kpis import compute_kpis

        results = compute_kpis(
            sessions_dir / "swinglab.db", cfg, since_days=since_days
        )
        return JSONResponse(
            {
                "window_days": since_days,
                "kpis": {k.key: k.as_dict() for k in results},
            }
        )

    @app.get("/admin/shopify-sync")
    def admin_shopify_sync(request: Request):
        """PII-minimized customer-link health for the operator only."""

        require_admin(request)
        raw_limit = request.query_params.get("limit", "50")
        try:
            limit = int(raw_limit)
        except ValueError:
            raise HTTPException(400, "limit must be an integer")
        if not 1 <= limit <= 200:
            raise HTTPException(400, "limit must be between 1 and 200")
        after_ref = request.query_params.get("after") or None
        raw_after = None
        if after_ref:
            cursor_user = (
                shopify_customer_sync.find_user_by_operator_ref(
                    users,
                    after_ref,
                )
            )
            if cursor_user is None:
                raise HTTPException(400, "invalid continuation cursor")
            raw_after = cursor_user.id
        rows, raw_next_cursor = users.list_shopify_sync_health(
            limit=limit,
            after=raw_after,
        )
        sync_snapshot = (
            shopify_sync_coordinator.health_snapshot()
            if shopify_sync_coordinator is not None
            else {
                "binding_status": "disabled",
                "binding_blocked": False,
                "binding_safe_error": None,
                "binding_last_checked_at": None,
                "binding_last_verified_at": None,
                "binding_store_ref": None,
                "binding_shop_ref": None,
            }
        )
        return JSONResponse(
            {
                "enabled": shopify_sync_enabled,
                "binding": {
                    key: sync_snapshot.get(key)
                    for key in (
                        "binding_status",
                        "binding_blocked",
                        "binding_safe_error",
                        "binding_last_checked_at",
                        "binding_last_verified_at",
                        "binding_store_ref",
                        "binding_shop_ref",
                    )
                },
                "health": {
                    key: sync_snapshot.get(key)
                    for key in (
                        "worker_alive",
                        "last_loop_at",
                        "last_attempt_at",
                        "pending",
                        "failed",
                        "requires_review",
                        "due",
                        "oldest_due_at",
                        "total",
                    )
                },
                "users": [
                    {
                        "user_ref": (
                            shopify_customer_sync.operator_user_ref(user.id)
                        ),
                        "linked": bool(user.shopify_customer_id),
                        "status": user.shopify_sync_status,
                        "last_synced_at": user.shopify_last_synced_at,
                        "safe_error": user.shopify_sync_error,
                        "attempts": user.shopify_sync_attempts,
                        "manual_retry_available": user.shopify_sync_status
                        == "failed",
                        "manual_review_needed": user.shopify_sync_status
                        == "requires_review",
                        "auto_retry_at": user.shopify_sync_next_attempt_at,
                    }
                    for user in rows
                ],
                "next_cursor": (
                    shopify_customer_sync.operator_user_ref(rows[-1].id)
                    if raw_next_cursor and rows
                    else None
                ),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/admin/shopify-sync/ref/{user_ref}")
    def admin_shopify_sync_detail(user_ref: str, request: Request):
        """Reveal one exact Shopify identity only to the protected operator."""

        require_admin(request)
        user = shopify_customer_sync.find_user_by_operator_ref(
            users,
            user_ref,
        )
        if user is None:
            raise HTTPException(404, "User not found.")
        return JSONResponse(
            {
                "user_ref": shopify_customer_sync.operator_user_ref(user.id),
                "linked": bool(user.shopify_customer_id),
                "shopify_customer_id": user.shopify_customer_id,
                "status": user.shopify_sync_status,
                "last_synced_at": user.shopify_last_synced_at,
                "safe_error": user.shopify_sync_error,
                "attempts": user.shopify_sync_attempts,
                "manual_retry_available": user.shopify_sync_status
                == "failed",
                "manual_review_needed": user.shopify_sync_status
                == "requires_review",
                "auto_retry_at": user.shopify_sync_next_attempt_at,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/admin/shopify-sync/ref/{user_ref}/retry")
    def admin_retry_shopify_sync(user_ref: str, request: Request):
        require_admin(request)
        if shopify_sync_coordinator is None:
            raise HTTPException(
                503, "Shopify customer synchronization is disabled."
            )
        if (
            not shopify_sync_coordinator.binding_verified
            and not shopify_sync_coordinator.verify_store_binding()
        ):
            raise HTTPException(
                503,
                "Shopify customer synchronization is blocked by its "
                "store binding.",
            )
        if not shopify_sync_coordinator.worker_alive:
            shopify_sync_coordinator.start()
        if not shopify_sync_coordinator.worker_alive:
            raise HTTPException(
                503,
                "Shopify customer synchronization worker is unavailable.",
            )
        user = shopify_customer_sync.find_user_by_operator_ref(
            users,
            user_ref,
        )
        if user is None:
            raise HTTPException(404, "User not found.")
        if not shopify_sync_coordinator.enqueue(user.id):
            raise HTTPException(404, "User not found.")
        return JSONResponse(
            {
                "queued": True,
                "user_ref": shopify_customer_sync.operator_user_ref(user.id),
            },
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/healthz")
    def healthz():
        # disk_free_mb + sessions_count: disk-full is the most likely first
        # outage — this makes it visible to uptime monitors before it lands.
        if shopify_sync_coordinator is not None:
            sync_snapshot = shopify_sync_coordinator.health_snapshot()
        else:
            sync_snapshot = {
                "worker_alive": False,
                "last_loop_at": None,
                "last_attempt_at": None,
                "pending": 0,
                "failed": 0,
                "requires_review": 0,
                "due": 0,
                "oldest_due_at": None,
                "total": 0,
                "binding_status": "disabled",
                "binding_blocked": False,
                "binding_safe_error": None,
                "binding_last_checked_at": None,
                "binding_last_verified_at": None,
                "binding_store_ref": None,
                "binding_shop_ref": None,
            }
        now = time.time()
        worker_expected = bool(
            shopify_sync_enabled and start_shopify_sync_worker
        )
        worker_alive = bool(sync_snapshot.get("worker_alive"))
        last_loop_at = sync_snapshot.get("last_loop_at")
        pending = int(sync_snapshot.get("pending") or 0)
        failed = int(sync_snapshot.get("failed") or 0)
        binding_blocked = bool(sync_snapshot.get("binding_blocked"))
        sync_health = {
            "enabled": bool(shopify_sync_enabled),
            "worker_expected": worker_expected,
            "worker_alive": worker_alive,
            "worker_stale": bool(
                worker_expected
                and last_loop_at is not None
                and now - float(last_loop_at) > 300
            ),
            "backlog_present": bool(pending or failed),
            "review_present": bool(
                int(sync_snapshot.get("requires_review") or 0)
            ),
            "due_work_present": bool(
                int(sync_snapshot.get("due") or 0)
            ),
            "binding_status": sync_snapshot.get("binding_status"),
            "binding_blocked": binding_blocked,
        }
        return JSONResponse(
            {
                "status": (
                    "degraded"
                    if worker_expected
                    and (not worker_alive or binding_blocked)
                    else "ok"
                ),
                **manager.counts(),
                "disk_free_mb": shutil.disk_usage(sessions_dir).free // (1024 * 1024),
                "sessions_count": manager.sessions_count(),
                "history_cleanup_pending": (
                    manager.history_cleanup_pending_count()
                ),
                "shopify_customer_sync": sync_health,
                # Feature-state only: this supports a safe rollout check
                # without exposing a golfer, report, or comparison outcome.
                "proof_cycle": {
                    "enabled": proof_cycle_enabled(cfg),
                    "practice_evidence_enabled": (
                        cfg.proof_cycle.get("practice_evidence_enabled") is True
                    ),
                },
                # Non-sensitive feature state for an unambiguous activation
                # and rollback check. No golfer, report, or result data leaks.
                "club_aware_coaching": {
                    "enabled": club_aware_enabled(),
                    "priority_rule_version": priority_rule_version(cfg),
                },
            }
        )

    # FastAPI must keep ``club`` as ``Form("")`` above so authentication,
    # same-origin CSRF, and quota failures retain precedence over input
    # validation.  That runtime choice would otherwise advertise club as
    # optional in OpenAPI, so tighten the generated multipart contract after
    # FastAPI builds it.  This lets generated clients require a canonical club
    # without changing the handler's security ordering.
    default_openapi = app.openapi

    def openapi_with_required_upload_club():
        schema = default_openapi()
        multipart_schema = (
            schema["paths"]["/upload"]["post"]["requestBody"]["content"]
            ["multipart/form-data"]["schema"]
        )
        reference = multipart_schema.get("$ref")
        if reference:
            target = schema
            for segment in reference.removeprefix("#/").split("/"):
                target = target[segment]
        else:
            target = multipart_schema
        required = target.setdefault("required", [])
        if "club" not in required:
            required.append("club")
        club_schema = target["properties"]["club"]
        club_schema.pop("default", None)
        club_schema["enum"] = sorted(CLUB_LABELS)
        return schema

    app.openapi = openapi_with_required_upload_club

    # Weekly practice-plan digest: hourly daemon thread, started ONLY when
    # Email is configured AND web.digest_enabled is on — otherwise None and
    # zero behavior (see digest.py for the consent + claim-before-send rules).
    app.state.digest_thread = digest.start_scheduler(manager, users, cfg, secret)
    # Pro expiry reminder: daily daemon thread, transactional — gated only on
    # email delivery being configured (the digest kill-switch and consent
    # flags do not apply; see digest.py, bottom).
    app.state.pro_expiry_thread = digest.start_pro_expiry_scheduler(users, cfg)

    return app
