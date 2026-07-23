"""FastAPI app: upload, live status, results, session history, JSON API.

Everything analysis-related goes through swinglab.pipeline — this layer only
moves files, tracks job state, and renders pages. The JSON endpoints under
/api are the surface a future mobile app talks to.

Guardrails live in config.yaml under ``web``: worker count, upload size cap,
per-visitor active-job limit, and session retention.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config
from .jobs import DONE, FAILED, Job, JobManager

VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
UPLOAD_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# PAYMENT / ACCOUNT GATING STUB
#
# This is where account checks, quotas, or payment verification will plug in
# later. Per-IP limits and upload caps below handle abuse; when real accounts
# land, raise HTTPException(402/403) here and nothing else in this module
# needs to change.
# ---------------------------------------------------------------------------
def ensure_user_can_analyze(request: Request) -> None:
    return None


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def create_app(
    cfg: Config | None = None, sessions_dir: str | Path = "sessions"
) -> FastAPI:
    cfg = cfg or Config.load()
    manager = JobManager(Path(sessions_dir), cfg)
    app = FastAPI(title=f"{cfg.brand['name']} — swing analysis")
    app.state.jobs = manager
    app.state.cfg = cfg

    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )

    def render(template: str, **context) -> HTMLResponse:
        return HTMLResponse(
            env.get_template(template).render(brand=cfg.brand, **context)
        )

    def get_job_or_404(job_id: str) -> Job:
        job = manager.get(job_id)
        if job is None:
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

    # -- pages ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def upload_page():
        return render(
            "web_upload.html.j2",
            max_upload_mb=float(cfg.web.get("max_upload_mb") or 0),
        )

    @app.post("/upload")
    async def upload(
        request: Request,
        video: UploadFile = File(...),
        hand: str = Form("right"),
        strikes: str = Form(""),
        fast: str = Form(""),
    ):
        ensure_user_can_analyze(request)
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

    @app.get("/session/{job_id}", response_class=HTMLResponse)
    def status_page(job_id: str):
        job = get_job_or_404(job_id)
        return render(
            "web_status.html.j2",
            job=job,
            done=job.status == DONE,
            failed=job.status == FAILED,
            queue_position=manager.queue_position(job),
        )

    @app.get("/sessions", response_class=HTMLResponse)
    def sessions_page():
        return render("web_sessions.html.j2", sessions=manager.list_recent())

    @app.get("/session/{job_id}/report")
    def report(job_id: str):
        job = get_job_or_404(job_id)
        if job.status != DONE or not job.report_rel:
            return RedirectResponse(f"/session/{job_id}")
        return RedirectResponse(f"/session/{job_id}/files/{job.report_rel}")

    @app.get("/session/{job_id}/files/{file_path:path}")
    def session_file(job_id: str, file_path: str):
        job = get_job_or_404(job_id)
        root = job.session_dir.resolve()
        target = (root / file_path).resolve()
        if not target.is_relative_to(root):  # block path traversal
            raise HTTPException(404, "Not found")
        if not target.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(target)

    # -- JSON API (what a future mobile app talks to) ----------------------
    @app.get("/api/session/{job_id}")
    def api_status(job_id: str):
        return JSONResponse(api_payload(get_job_or_404(job_id)))

    @app.get("/api/sessions")
    def api_sessions():
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
                    for job in manager.list_recent()
                ]
            }
        )

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok", **manager.counts()})

    return app
