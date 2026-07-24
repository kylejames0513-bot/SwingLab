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

The optional gear shop (a /shop page plus flag-matched training-aid
recommendations on finished analyses) is likewise inert until the SHOPIFY_*
environment variables are set — see shop.py.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.sessions import SessionMiddleware

from ..coaching import flag_keys
from ..config import Config
from . import billing, shop, shopify_billing
from .jobs import DONE, FAILED, Job, JobManager
from .users import User, UserStore

VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
UPLOAD_CHUNK = 1024 * 1024


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
    sessions_dir = Path(sessions_dir)
    manager = JobManager(sessions_dir, cfg)
    users = UserStore(sessions_dir / "swinglab.db")
    app = FastAPI(title=f"{cfg.brand['name']} — swing analysis")
    app.state.jobs = manager
    app.state.users = users
    app.state.cfg = cfg

    secret = os.environ.get("SWINGLAB_SECRET")
    if not secret:
        secret = secrets.token_hex(32)
        if cfg.web.get("require_account"):
            print(
                "WARNING: SWINGLAB_SECRET is not set — logins will not survive "
                "a restart. Set it to a long random string in the environment."
            )
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax")

    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )

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

    # -- pages ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        if cfg.web.get("require_account") and current_user(request) is None:
            return render("web_login.html.j2", request, error=None, landing=True)
        return render(
            "web_upload.html.j2",
            request,
            max_upload_mb=float(cfg.web.get("max_upload_mb") or 0),
            quota_left=quota_left(current_user(request)),
        )

    # -- accounts ---------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current_user(request) is not None:
            return RedirectResponse("/", status_code=303)
        return render("web_login.html.j2", request, error=None, landing=False)

    @app.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form("")):
        user = users.authenticate(email, password)
        if user is None:
            return render(
                "web_login.html.j2", request, landing=False,
                error="Wrong email or password.",
            )
        claim_pending_pro(user)
        request.session["user_id"] = user.id
        return RedirectResponse("/", status_code=303)

    @app.post("/signup")
    def signup(request: Request, email: str = Form(""), password: str = Form("")):
        try:
            user = users.create(email, password)
        except ValueError as exc:
            return render(
                "web_login.html.j2", request, landing=False, error=str(exc)
            )
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

    @app.get("/pricing", response_class=HTMLResponse)
    def pricing(request: Request):
        return render("web_pricing.html.j2", request)

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

        client_ip = request.client.host if request.client else None
        per_ip = int(cfg.web.get("max_active_jobs_per_ip") or 0)
        if client_ip and per_ip and manager.active_for_ip(client_ip) >= per_ip:
            raise HTTPException(
                429,
                f"You already have {per_ip} analyses queued or running — "
                "wait for one to finish before uploading another clip.",
            )

        job = manager.create_session(
            source_name=video.filename,
            hand=hand,
            strikes=manual_strikes,
            fast=fast.lower() in ("on", "true", "1", "yes"),
            client_ip=client_ip,
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
        return render(
            "web_status.html.j2",
            request,
            job=job,
            done=job.status == DONE,
            failed=job.status == FAILED,
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
        return JSONResponse({"status": "ok", **manager.counts()})

    return app
