"""Background analysis jobs.

Single machine, no database: the filesystem is the store. Each upload gets a
session folder under the sessions directory; job state lives in memory for the
running process and is mirrored to status.json so a restart can still serve
finished results.
"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..events import EventError
from ..ffmpeg import FFmpegError
from ..pipeline import ZeroStrikesError, analyze_video

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
FAILED = "failed"


@dataclass
class Job:
    id: str
    session_dir: Path
    status: str = QUEUED
    log: list[str] = field(default_factory=list)
    error: str | None = None
    report_rel: str | None = None  # path of report.html relative to session_dir
    swings_done: int = 0
    swings_total: int = 0  # 0 until strike detection has counted the swings

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "log": self.log,
            "error": self.error,
            "report": self.report_rel,
            "swings_done": self.swings_done,
            "swings_total": self.swings_total,
        }


class JobManager:
    def __init__(self, sessions_dir: Path, cfg: Config):
        self.sessions_dir = sessions_dir
        self.cfg = cfg
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        sessions_dir.mkdir(parents=True, exist_ok=True)

    # -- lookup -----------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        return self._load_from_disk(job_id)

    def _load_from_disk(self, job_id: str) -> Job | None:
        """Recover a finished job after a process restart."""
        session_dir = self.sessions_dir / job_id
        status_file = session_dir / "status.json"
        if not status_file.is_file():
            return None
        data = json.loads(status_file.read_text())
        job = Job(
            id=job_id,
            session_dir=session_dir,
            status=data.get("status", FAILED),
            log=data.get("log", []),
            error=data.get("error"),
            report_rel=data.get("report"),
            swings_done=data.get("swings_done", 0),
            swings_total=data.get("swings_total", 0),
        )
        if job.status in (QUEUED, PROCESSING):
            # the process that owned this job is gone
            job.status = FAILED
            job.error = "The server restarted while this analysis was running."
        with self._lock:
            self._jobs.setdefault(job_id, job)
        return job

    def _persist(self, job: Job) -> None:
        (job.session_dir / "status.json").write_text(
            json.dumps(job.as_dict(), indent=2)
        )

    # -- submission -------------------------------------------------------
    def create_session(self) -> Job:
        job_id = uuid.uuid4().hex[:12]
        session_dir = self.sessions_dir / job_id
        session_dir.mkdir(parents=True)
        job = Job(id=job_id, session_dir=session_dir)
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job)
        return job

    def start(
        self,
        job: Job,
        video_path: Path,
        hand: str,
        manual_strikes: list[float] | None,
    ) -> None:
        thread = threading.Thread(
            target=self._run,
            args=(job, video_path, hand, manual_strikes),
            daemon=True,
        )
        thread.start()

    # -- execution --------------------------------------------------------
    def _run(
        self,
        job: Job,
        video_path: Path,
        hand: str,
        manual_strikes: list[float] | None,
    ) -> None:
        def log(message: str) -> None:
            job.log.append(message)
            self._persist(job)

        def progress(done: int, total: int) -> None:
            job.swings_done = done
            job.swings_total = total
            self._persist(job)

        job.status = PROCESSING
        self._persist(job)
        try:
            result = analyze_video(
                video_path,
                out_dir=job.session_dir / "out",
                hand=hand,
                manual_strikes=manual_strikes,
                cfg=self.cfg,
                log=log,
                progress=progress,
            )
            job.report_rel = str(result.report_path.relative_to(job.session_dir))
            job.status = DONE
        except (ZeroStrikesError, EventError, FFmpegError) as exc:
            job.status = FAILED
            job.error = str(exc)
        except Exception:
            job.status = FAILED
            job.error = "Unexpected error during analysis:\n" + traceback.format_exc(
                limit=3
            )
        self._persist(job)
