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
import logging
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

from ..caddie_brief import (
    payload_has_coachable_data,
    payload_is_coaching_eligible,
    payload_requires_refilm,
    payload_structure_is_valid,
)
from ..config import Config
from ..events import EventError
from ..ffmpeg import FFmpegError
from ..pipeline import VideoTooLongError, ZeroStrikesError, analyze_video
from ..report import (
    REPORT_OUTCOME_CAPTURE,
    REPORT_OUTCOME_COACHING,
    persisted_report_outcome,
)

logger = logging.getLogger("swinglab.web.jobs")

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
FAILED = "failed"
ACTIVE = (QUEUED, PROCESSING)
_FREE_REFILM_CREDITS_PER_MONTH = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    source_name  TEXT,
    hand         TEXT NOT NULL DEFAULT 'right',
    angle        TEXT NOT NULL DEFAULT 'face-on',
    club         TEXT,
    level        TEXT,
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
    angle: str = "face-on"  # camera angle: "face-on" | "dtl"
    club: str | None = None  # display context only — see swinglab.clubs
    level: str | None = None  # display framing only — see swinglab.levels
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
            "angle": self.angle,
            "club": self.club,
            "level": self.level,
            "fast": self.fast,
            "log": self.log,
            "error": self.error,
            "report": self.report_rel,
            "swings_done": self.swings_done,
            "swings_total": self.swings_total,
        }


class JobManager:
    def __init__(self, sessions_dir: Path, cfg: Config, user_store=None):
        """``user_store`` (a swinglab.web.users.UserStore, duck-typed on
        ``.get(user_id)``) lets the runner check the owner's plan at
        analysis time for the coach-replay Pro gate. None — the default,
        and what any non-web caller gets — disables the gate entirely."""
        self.sessions_dir = sessions_dir
        self.cfg = cfg
        self._users = user_store
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            sessions_dir / "swinglab.db", check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            # migrate older databases in place (pre-accounts, pre-angle/club)
            columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(jobs)")
            }
            if "user_id" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
            if "angle" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN angle TEXT NOT NULL"
                    " DEFAULT 'face-on'"
                )
            if "club" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN club TEXT")
            if "level" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN level TEXT")
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

    def list_comparable(
        self,
        *,
        user_id: str,
        club: str | None,
        through: float,
        limit: int = 50,
    ) -> list[Job]:
        """Finished same-user, same-club sessions up to one session.

        Filtering happens in SQLite before the limit, so a sparse club history
        is not lost behind newer sessions made with other clubs.  This is the
        bounded history used by the Caddie Brief; it never crosses accounts or
        lets a later session rewrite an older session's journal context. The
        eligibility scan is capped at five database rows per requested result
        (500 rows maximum) so a long account history cannot create unbounded
        report/JSON I/O on a results request.
        """
        wanted = min(max(int(limit), 1), 100)
        scan_limit = min(wanted * 5, 500)
        club_clause = "club IS NULL" if club is None else "club = ?"
        params: tuple = (
            (user_id, DONE, through)
            if club is None
            else (user_id, DONE, through, club)
        )
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? AND status = ?"
                " AND created_at <= ? AND "
                + club_clause
                + " ORDER BY created_at DESC LIMIT ?",
                params + (scan_limit,),
            ).fetchall()
        eligible = [
            job
            for job in (self._from_row(row) for row in rows)
            if self.coaching_eligible(job)
        ]
        return eligible[:wanted]

    def usage_this_month(self, user_id: str) -> int:
        """Coaching analyses used this month (UTC).

        Queued/processing jobs reserve an allowance. Failed uploads do not
        count. One finished clip that requires a re-film gets a courtesy retry
        each month; further rejected clips count so the AI workers cannot be
        occupied indefinitely with deliberately unusable footage.
        """
        now = datetime.now(timezone.utc)
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? AND created_at >= ?"
                " AND status != ?",
                (user_id, month_start, FAILED),
            ).fetchall()
        jobs = [self._from_row(row) for row in rows]
        active = sum(job.status in ACTIVE for job in jobs)
        finished = [job for job in jobs if job.status == DONE]
        eligible = sum(self.coaching_eligible(job) for job in finished)
        rejected = len(finished) - eligible
        charged_rejected = max(
            0, rejected - _FREE_REFILM_CREDITS_PER_MONTH
        )
        return active + eligible + charged_rejected

    def coaching_eligible(self, job: Job) -> bool:
        """Whether a finished job can power coaching, trends, and quota."""
        if job.status != DONE or not job.report_rel:
            return False
        root = job.session_dir.resolve()
        report = (root / job.report_rel).resolve()
        if not report.is_relative_to(root) or not report.is_file():
            return False
        persisted_outcome = persisted_report_outcome(report)
        if persisted_outcome == REPORT_OUTCOME_CAPTURE:
            return False
        path = job.session_dir / Path(job.report_rel).parent / "metrics.json"
        if not path.is_file():
            # Pre-metrics web sessions remain reachable and count as completed
            # for backward compatibility. A current capture-only report stays
            # rejected even if its metrics file is lost or partially restored.
            return True
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return persisted_outcome == REPORT_OUTCOME_COACHING
        if isinstance(payload, dict) and payload_requires_refilm(
            payload, angle=job.angle
        ):
            return False
        if not payload_structure_is_valid(payload):
            return persisted_outcome == REPORT_OUTCOME_COACHING
        eligible = payload_is_coaching_eligible(
            payload, self.cfg, angle=job.angle
        )
        if eligible:
            return True
        return (
            persisted_outcome == REPORT_OUTCOME_COACHING
            and not payload_has_coachable_data(payload, angle=job.angle)
        )

    def refilm_rejections_this_month(self, user_id: str) -> int:
        """Finished coaching-ineligible clips for one account this month."""
        now = datetime.now(timezone.utc)
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? AND created_at >= ?"
                " AND status = ?",
                (user_id, month_start, DONE),
            ).fetchall()
        return sum(
            not self.coaching_eligible(self._from_row(row)) for row in rows
        )

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

    def sessions_count(self) -> int:
        """Total sessions on disk-and-db (any status) — a /healthz gauge for
        watching growth against retention_days and the disk."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # -- submission -------------------------------------------------------
    def create_session(
        self,
        source_name: str | None = None,
        hand: str = "right",
        strikes: list[float] | None = None,
        fast: bool = False,
        client_ip: str | None = None,
        user_id: str | None = None,
        angle: str = "face-on",
        club: str | None = None,
        level: str | None = None,
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            session_dir=self.sessions_dir / job_id,
            created_at=time.time(),
            source_name=source_name,
            hand=hand,
            angle=angle,
            club=club,
            level=level,
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
    def replay_locked(self, job: Job) -> bool:
        """The coach-replay Pro gate (billing.replay_pro_only), decided at
        analysis time. True ONLY when the gate is on, accounts are on, and
        the job's owner is not Pro right now — so open instances
        (require_account off), ownerless pre-account jobs, and managers
        without a user store (CLI-adjacent embedders) are never gated. An
        owner whose account row has vanished has no Pro either — gated."""
        if not self.cfg.billing.get("replay_pro_only"):
            return False
        if not self.cfg.slowmo.get("annotated"):
            return False  # no replay feature at all — nothing to gate
        if not self.cfg.web.get("require_account"):
            return False
        if self._users is None or job.user_id is None:
            return False
        user = self._users.get(job.user_id)
        return user is None or not user.is_pro

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
        # Coach-replay Pro gate: decided HERE, at analysis time — a later
        # upgrade never rewrites an existing report (re-film to get the
        # replay), and the skip is stated honestly in the session log.
        locked = self.replay_locked(job)
        if locked:
            log(
                "Coach replay is a Pro feature — skipped for this analysis. "
                "Upgrade and re-film to get your swing annotated frame-by-frame."
            )
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
                angle=job.angle,
                club=job.club,
                level=job.level,
                replay_locked=locked,
            )
            job.report_rel = str(result.report_path.relative_to(job.session_dir))
            job.status = DONE
            self._delete_source_if_configured(job)
        except (ZeroStrikesError, VideoTooLongError, EventError, FFmpegError) as exc:
            job.status = FAILED
            job.error = str(exc)
            self._delete_failed_source_if_configured(job)
        except Exception:
            job.status = FAILED
            job.error = "Unexpected error during analysis:\n" + traceback.format_exc(
                limit=3
            )
            # logger.exception carries the full traceback to the process log
            # (and to Sentry when the operator configured it — see app.py).
            logger.exception("Unexpected error during analysis of job %s", job.id)
            self._delete_failed_source_if_configured(job)
        self._save(job)
        self._cleanup_expired()

    def _delete_source_if_configured(self, job: Job) -> None:
        """web.delete_source_after_done: drop the original upload once the
        job is DONE and its report exists — deliverables (report, media,
        metrics) stay. Never deletes when the report is missing, so a
        half-finished session keeps its source for the restart re-queue."""
        if not self.cfg.web.get("delete_source_after_done"):
            return
        if job.status != DONE or not job.report_rel:
            return
        if not (job.session_dir / job.report_rel).is_file():
            return
        source = self._source_path(job)
        if source is not None:
            source.unlink(missing_ok=True)
            job.log.append(
                "Original upload deleted after analysis (configured data "
                "minimization) — re-analyzing this clip needs a fresh upload."
            )

    def _delete_failed_source_if_configured(self, job: Job) -> None:
        """Same switch, failure path: a FAILED job's upload is never analyzed
        again (retries need a fresh upload anyway), and failed uploads do not
        count against quota — so keeping their sources would let one account
        fill the disk with refused clips (e.g. over-length videos) for free.
        Only fires for terminal FAILED state, never for restart-requeued work."""
        if not self.cfg.web.get("delete_source_after_done"):
            return
        if job.status != FAILED:
            return
        source = self._source_path(job)
        if source is not None:
            source.unlink(missing_ok=True)
            job.log.append(
                "Original upload deleted after the failed analysis (configured "
                "data minimization) — fix the clip and upload again to retry."
            )

    # -- persistence ------------------------------------------------------
    def _save(self, job: Job) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, status, created_at, updated_at, source_name,"
                " hand, angle, club, level, strikes, fast, client_ip, user_id, error,"
                " report_rel, swings_done, swings_total, log)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
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
                    job.angle,
                    job.club,
                    job.level,
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
            angle=row["angle"] or "face-on",
            club=row["club"],
            level=row["level"],
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
