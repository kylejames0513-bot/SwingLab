"""FastAPI app: upload form, processing status, results page.

Everything analysis-related goes through swinglab.pipeline — this layer only
moves files, tracks job state, and renders pages. The JSON endpoints under
/api are the surface a future mobile app would call.
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
# later. Milestones 1-2 are single-machine with no auth, so everyone is
# allowed. When gating lands, raise HTTPException(402/403) here and nothing
# else in this module needs to change.
# ---------------------------------------------------------------------------
def ensure_user_can_analyze(request: Request) -> None:
    return None


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

    # -- pages ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def upload_page():
        return render("web_upload.html.j2")

    @app.post("/upload")
    async def upload(
        request: Request,
        video: UploadFile = File(...),
        hand: str = Form("right"),
        strikes: str = Form(""),
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

        job = manager.create_session()
        dest = job.session_dir / f"source{suffix}"
        with open(dest, "wb") as fh:
            while chunk := await video.read(UPLOAD_CHUNK):
                fh.write(chunk)
        manager.start(job, dest, hand, manual_strikes)
        return RedirectResponse(f"/session/{job.id}", status_code=303)

    @app.get("/session/{job_id}", response_class=HTMLResponse)
    def status_page(job_id: str):
        job = get_job_or_404(job_id)
        return render(
            "web_status.html.j2",
            job=job,
            done=job.status == DONE,
            failed=job.status == FAILED,
        )

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
        job = get_job_or_404(job_id)
        payload = job.as_dict()
        if job.status == DONE and job.report_rel:
            payload["report_url"] = f"/session/{job.id}/files/{job.report_rel}"
            metrics = job.session_dir / Path(job.report_rel).parent / "metrics.json"
            if metrics.is_file():
                payload["metrics_url"] = (
                    f"/session/{job.id}/files/"
                    + str(metrics.relative_to(job.session_dir))
                )
        return JSONResponse(payload)

    return app
