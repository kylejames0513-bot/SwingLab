"""Background analysis jobs.

Robustness model: a SQLite database next to the session folders is the source
of truth for job state; the filesystem stores uploads and deliverables. A
bounded worker pool runs the analyses, so a burst of uploads queues up instead
of swamping the machine, and any job that was queued or running when the
process died is re-queued automatically on the next start — the uploaded
video is still in its session folder, so no work is lost.

Sessions written by pre-database versions (status.json files) are imported on
startup, so an upgrade in place keeps its history.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..events import EventError
from ..ffmpeg import FFmpegError
from ..pipeline import ZeroStrikesError, analyze_video

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
FAILED = "failed"
ACTIVE = (QUEUED, PROCESSING)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    source_name  TEXT,
    hand         TEXT NOT NULL DEFAULT 'right',
    strikes      TEXT,
    fast         INTEGER NOT NULL DEFAULT 0,
    client_ip    TEXT,
    user_id      TEXT,
    error        TEXT,
    report_rel   TEXT,
    swings_done  INTEGER NOT NULL DEFAULT 0,
    swings_total INTEGER NOT NULL DEFAULT 0,
    log          TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
"""


@dataclass
class Job:
    id: str
    session_dir: Path
    status: str = QUEUED
    created_at: float = 0.0
    source_name: str | None = None
    hand: str = "right"
    strikes: list[float] | None = None
    fast: bool = False
    client_ip: str | None = None
    user_id: str | None = None
    log: list[str] = field(default_factory=list)
    error: str | None = None
    report_rel: str | None = None  # path of report.html relative to session_dir
    swings_done: int = 0
    swings_total: int = 0  # 0 until strike detection has counted the swings

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": datetime.fromtimestamp(
                self.created_at, timezone.utc
            ).isoformat(),
            "source_name": self.source_name,
            "hand": self.hand,
            "fast": self.fast,
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
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            sessions_dir / "swinglab.db", check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            # migrate pre-accounts databases in place
            columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(jobs)")
            }
            if "user_id" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
                self._conn.commit()
        workers = max(1, int(cfg.web.get("workers", 2)))
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="swinglab-worker"
        )
        self._import_legacy_sessions()
        self._cleanup_expired()
        self._requeue_interrupted()

    # -- lookup -----------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._from_row(row) if row else None

    def list_recent(self, limit: int = 50, user_id: str | None = None) -> list[Job]:
        """Most recent jobs; pass user_id to see only one account's sessions."""
        with self._lock:
            if user_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE user_id = ?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [self._from_row(r) for r in rows]

    def usage_this_month(self, user_id: str) -> int:
        """Analyses this calendar month (UTC). Failed runs don't count."""
        now = datetime.now(timezone.utc)
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE user_id = ? AND created_at >= ?"
                " AND status != ?",
                (user_id, month_start, FAILED),
            ).fetchone()[0]

    def queue_position(self, job: Job) -> int | None:
        """1-based place in line while queued, else None."""
        if job.status != QUEUED:
            return None
        with self._lock:
            ahead = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = ? AND created_at < ?",
                (QUEUED, job.created_at),
            ).fetchone()[0]
        return ahead + 1

    def active_for_ip(self, client_ip: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE client_ip = ? AND status IN (?, ?)",
                (client_ip, *ACTIVE),
            ).fetchone()[0]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM jobs WHERE status IN (?, ?) "
                "GROUP BY status",
                ACTIVE,
            ).fetchall()
        counts = {status: 0 for status in ACTIVE}
        counts.update({row[0]: row[1] for row in rows})
        return counts

    # -- submission -------------------------------------------------------
    def create_session(
        self,
        source_name: str | None = None,
        hand: str = "right",
        strikes: list[float] | None = None,
        fast: bool = False,
        client_ip: str | None = None,
        user_id: str | None = None,
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            session_dir=self.sessions_dir / job_id,
            created_at=time.time(),
            source_name=source_name,
            hand=hand,
            strikes=strikes,
            fast=fast,
            client_ip=client_ip,
            user_id=user_id,
        )
        job.session_dir.mkdir(parents=True)
        self._save(job)
        return job

    def submit(self, job: Job, video_path: Path) -> None:
        self._pool.submit(self._run, job, video_path)

    def discard(self, job: Job) -> None:
        """Drop a session whose upload never completed."""
        shutil.rmtree(job.session_dir, ignore_errors=True)
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
            self._conn.commit()

    # -- execution --------------------------------------------------------
    def _run(self, job: Job, video_path: Path) -> None:
        def log(message: str) -> None:
            job.log.append(message)
            self._save(job)

        def progress(done: int, total: int) -> None:
            job.swings_done = done
            job.swings_total = total
            self._save(job)

        job.status = PROCESSING
        self._save(job)
        try:
            result = analyze_video(
                video_path,
                out_dir=job.session_dir / "out",
                hand=job.hand,
                manual_strikes=job.strikes,
                cfg=self.cfg,
                fast=job.fast,
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
        self._save(job)
        self._cleanup_expired()

    # -- persistence ------------------------------------------------------
    def _save(self, job: Job) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, status, created_at, updated_at, source_name,"
                " hand, strikes, fast, client_ip, user_id, error, report_rel,"
                " swings_done, swings_total, log)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET status = excluded.status,"
                " updated_at = excluded.updated_at, error = excluded.error,"
                " report_rel = excluded.report_rel, swings_done = excluded.swings_done,"
                " swings_total = excluded.swings_total, log = excluded.log",
                (
                    job.id,
                    job.status,
                    job.created_at,
                    time.time(),
                    job.source_name,
                    job.hand,
                    json.dumps(job.strikes) if job.strikes else None,
                    int(job.fast),
                    job.client_ip,
                    job.user_id,
                    job.error,
                    job.report_rel,
                    job.swings_done,
                    job.swings_total,
                    json.dumps(job.log),
                ),
            )
            self._conn.commit()

    def _from_row(self, row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            session_dir=self.sessions_dir / row["id"],
            status=row["status"],
            created_at=row["created_at"],
            source_name=row["source_name"],
            hand=row["hand"],
            strikes=json.loads(row["strikes"]) if row["strikes"] else None,
            fast=bool(row["fast"]),
            client_ip=row["client_ip"],
            user_id=row["user_id"],
            log=json.loads(row["log"]),
            error=row["error"],
            report_rel=row["report_rel"],
            swings_done=row["swings_done"],
            swings_total=row["swings_total"],
        )

    # -- startup passes ---------------------------------------------------
    def _source_path(self, job: Job) -> Path | None:
        """The uploaded video (saved as source.<ext> by the web layer)."""
        return next(job.session_dir.glob("source.*"), None)

    def _requeue_interrupted(self) -> None:
        """Re-run jobs that were queued or running when the process died."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY created_at",
                ACTIVE,
            ).fetchall()
        for row in rows:
            job = self._from_row(row)
            video = self._source_path(job)
            if video is None:
                job.status = FAILED
                job.error = (
                    "The server restarted while this analysis was waiting and "
                    "the uploaded video is gone. Please upload it again."
                )
            else:
                job.status = QUEUED
                job.log.append("Server restarted — analysis re-queued.")
            self._save(job)
            if job.status == QUEUED:
                self.submit(job, video)

    def _import_legacy_sessions(self) -> None:
        """One-time import of sessions from the pre-database status.json era."""
        for status_file in sorted(self.sessions_dir.glob("*/status.json")):
            job_id = status_file.parent.name
            with self._lock:
                known = self._conn.execute(
                    "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
            if known:
                continue
            try:
                data = json.loads(status_file.read_text())
            except (OSError, ValueError):
                continue
            job = Job(
                id=job_id,
                session_dir=status_file.parent,
                status=data.get("status", FAILED),
                created_at=status_file.stat().st_mtime,
                log=data.get("log", []),
                error=data.get("error"),
                report_rel=data.get("report"),
                swings_done=data.get("swings_done", 0),
                swings_total=data.get("swings_total", 0),
            )
            self._save(job)

    def _cleanup_expired(self) -> None:
        """Delete finished sessions older than web.retention_days (0 = never)."""
        days = float(self.cfg.web.get("retention_days") or 0)
        if days <= 0:
            return
        cutoff = time.time() - days * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE status IN (?, ?) AND updated_at < ?",
                (DONE, FAILED, cutoff),
            ).fetchall()
        for row in rows:
            shutil.rmtree(self.sessions_dir / row["id"], ignore_errors=True)
            with self._lock:
                self._conn.execute("DELETE FROM jobs WHERE id = ?", (row["id"],))
                self._conn.commit()
