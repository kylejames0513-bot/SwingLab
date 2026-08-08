"""Background analysis jobs.

Robustness model: a SQLite database next to the session folders is the source
of truth for job state; the filesystem stores uploads and deliverables. A
bounded worker pool runs the analyses, so a burst of uploads queues up instead
of swamping the machine, and any job that was queued or running when the
process died is re-queued automatically on the next start — the uploaded
video is still in its session folder, so no work is lost.

Sessions written by pre-database versions (status.json files) are imported on
startup, so an upgrade in place keeps its history.

An owned upload can opt in (per clip) to one completion email: report link
when coaching is ready, an honest re-film note when the clip couldn't be
measured, or the humanized failure guidance (humanize.py) when the analysis
failed. The send is claimed by stamping ``notified_at`` BEFORE delivery, so
there is never more than one email per job.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

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
from ..proof_cycle_artifact import (
    build_proof_cycle_artifact,
    proof_cycle_enabled,
    proof_cycle_history_scan_limit,
    write_proof_cycle_artifact,
)
from ..report import (
    REPORT_OUTCOME_CAPTURE,
    REPORT_OUTCOME_COACHING,
    persisted_report_outcome,
)
from . import mailer
from .analysis_failures import classify_analysis_failure, effective_retryable
from .humanize import friendly_error

logger = logging.getLogger("swinglab.web.jobs")

PREPARING = "preparing"
QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
FAILED = "failed"
ACTIVE = (QUEUED, PROCESSING)
_FREE_REFILM_CREDITS_PER_MONTH = 1
_HISTORY_TRASH_NAME = ".history-trash"
_HISTORY_OPERATION_ID_RE = re.compile(r"[0-9a-f]{32}")
_SAFE_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

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
    log          TEXT NOT NULL DEFAULT '[]',
    notify_email INTEGER NOT NULL DEFAULT 0,
    notified_at  REAL,
    failure_code    TEXT,
    retryable       INTEGER NOT NULL DEFAULT 0,
    retry_expires_at REAL,
    retry_attempt   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS analysis_usage_monthly (
    user_hash          TEXT NOT NULL,
    month_start        INTEGER NOT NULL,
    coaching_eligible  INTEGER NOT NULL DEFAULT 0,
    refilm_rejections  INTEGER NOT NULL DEFAULT 0,
    expires_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    PRIMARY KEY (user_hash, month_start)
);
CREATE INDEX IF NOT EXISTS analysis_usage_monthly_expiry
    ON analysis_usage_monthly(expires_at);

CREATE TABLE IF NOT EXISTS history_reset_operations (
    operation_id  TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    subject_hash  TEXT,
    state         TEXT NOT NULL CHECK (state IN ('prepared', 'committed')),
    job_ids_json  TEXT NOT NULL,
    artifact_job_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS history_reset_operations_state
    ON history_reset_operations(state);
"""


class HistoryResetError(RuntimeError):
    """Base error for a user-history reset that did not commit."""


class HistoryResetConflict(HistoryResetError):
    """The account has active work, or its owned-job set changed mid-reset."""

    def __init__(self, message: str, active_job_ids: Iterable[str] = ()):
        self.active_job_ids = tuple(active_job_ids)
        super().__init__(message)


class HistoryResetSafetyError(HistoryResetError):
    """A persisted id or filesystem entry cannot be deleted safely."""


@dataclass(frozen=True)
class HistoryResetSummary:
    """Logical reset result; cleanup may finish on the next startup."""

    operation_id: str | None
    deleted_jobs: int
    cleanup_pending: bool

    @property
    def jobs_deleted(self) -> int:
        """Compatibility-friendly wording for callers rendering a count."""
        return self.deleted_jobs

    def as_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "deleted_jobs": self.deleted_jobs,
            "cleanup_pending": self.cleanup_pending,
        }


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
    notify_email: bool = False  # owner asked to be emailed at completion
    notified_at: float | None = None  # claim stamp — at most one email per job
    # Closed native analysis-failure classification (Task 5). None until a job
    # terminates in a classified failure; raw diagnostics stay in ``error``.
    failure_code: str | None = None
    retryable: bool = False
    retry_expires_at: float | None = None
    retry_attempt: int = 0

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
    def __init__(
        self,
        sessions_dir: Path,
        cfg: Config,
        user_store=None,
        *,
        recover_interrupted: bool = True,
    ):
        """``user_store`` (a swinglab.web.users.UserStore, duck-typed on
        ``.get(user_id)``) lets the runner check the owner's plan at
        analysis time for the coach-replay Pro gate. None — the default,
        and what any non-web caller gets — disables the gate entirely."""
        self.sessions_dir = sessions_dir
        self.cfg = cfg
        self._users = user_store
        self._closed = False
        sessions_dir.mkdir(parents=True, exist_ok=True)
        # One re-entrant lock serializes the single-replica job state machine,
        # including the short filesystem/SQLite two-phase history reset.  The
        # re-entrancy lets ordinary helpers such as ``_save`` keep their
        # lower-level locking contract when called by a larger operation.
        self._lock = threading.RLock()
        # Serialize any externally delivered view of swing history with a
        # customer reset. A weekly digest holds this only while composing,
        # claiming, and sending; ordinary uploads and reads remain concurrent.
        self._history_delivery_lock = threading.RLock()
        self._conn = sqlite3.connect(
            sessions_dir / "swinglab.db", check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout = 5000")
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
            if "notify_email" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN notify_email INTEGER NOT NULL"
                    " DEFAULT 0"
                )
            if "notified_at" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN notified_at REAL"
                )
            if "failure_code" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN failure_code TEXT")
            if "retryable" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0"
                )
            if "retry_expires_at" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN retry_expires_at REAL"
                )
            if "retry_attempt" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN retry_attempt INTEGER NOT NULL"
                    " DEFAULT 0"
                )
            history_columns = {
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(history_reset_operations)"
                )
            }
            if "artifact_job_ids_json" not in history_columns:
                self._conn.execute(
                    "ALTER TABLE history_reset_operations"
                    " ADD COLUMN artifact_job_ids_json TEXT NOT NULL DEFAULT '[]'"
                )
            self._conn.commit()
        workers = max(1, int(cfg.web.get("workers", 2)))
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="swinglab-worker"
        )
        self._recover_history_operations()
        self._import_legacy_sessions()
        self._cleanup_expired()
        if recover_interrupted:
            self.recover_interrupted()

    def close(self) -> None:
        """Release every thread-pool and SQLite resource exactly once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        # Analyses persist through this connection, so wait for the pool before
        # closing it rather than racing a background write.
        self._pool.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._conn.close()

    # -- lookup -----------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._from_row(row) if row else None

    def list_recent(self, limit: int = 50, user_id: str | None = None) -> list[Job]:
        """Most recent jobs; pass user_id to see only one account's sessions.

        Internal ``preparing`` rows (resumable-upload completion journals) are
        omitted so clients never observe a job before its source is published.
        """
        with self._lock:
            if user_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE status != ?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (PREPARING, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE user_id = ? AND status != ?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (user_id, PREPARING, limit),
                ).fetchall()
        return [self._from_row(r) for r in rows]

    def earliest_coaching_eligible_created_at(self, user_id: str) -> float | None:
        """Return the first current coaching-ready session time for one owner."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? AND status = ?"
                " ORDER BY created_at ASC",
                (user_id, DONE),
            ).fetchall()
        for row in rows:
            job = self._from_row(row)
            if self.coaching_eligible(job):
                return float(job.created_at)
        return None

    def list_comparable(
        self,
        *,
        user_id: str,
        club: str | None,
        through: float,
        limit: int = 50,
        hand: str | None = None,
        angle: str | None = None,
    ) -> list[Job]:
        """Finished same-user, same-context sessions up to one session.

        Club filtering is the established compatibility contract. Optional
        ``hand`` and ``angle`` filters let club-aware callers require the full
        capture context without changing any legacy caller. Filtering happens
        in SQLite before the limit, so a sparse context is not lost behind
        newer sessions made in another context. This bounded history never
        crosses accounts or lets a later session rewrite an older session's
        journal context. The eligibility scan is capped at five database rows
        per requested result (500 rows maximum) so a long account history
        cannot create unbounded report/JSON I/O on a results request.
        """
        wanted = min(max(int(limit), 1), 100)
        scan_limit = min(wanted * 5, 500)
        context_clauses = ["club IS NULL" if club is None else "club = ?"]
        params: list[object] = [user_id, DONE, through]
        if club is not None:
            params.append(club)
        if hand is not None:
            context_clauses.append("hand = ?")
            params.append(hand)
        if angle is not None:
            context_clauses.append("angle = ?")
            params.append(angle)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? AND status = ?"
                " AND created_at <= ? AND "
                + " AND ".join(context_clauses)
                + " ORDER BY created_at DESC LIMIT ?",
                (*params, scan_limit),
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
        occupied indefinitely with deliberately unusable footage. Durable,
        pseudonymous monthly receipts keep both facts after history deletion.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            self._purge_usage_receipts_locked()
            self._conn.commit()
            return self._usage_this_month_locked(user_id, now.timestamp())

    def usage_this_month_snapshot(self, user_id: str) -> int:
        """Read the current UTC-month usage without cleanup or other writes."""

        now = datetime.now(timezone.utc).timestamp()
        with self._lock:
            return self._usage_this_month_locked(user_id, now)

    def _usage_this_month_locked(self, user_id: str, now: float) -> int:
        month_start, month_end, _expires_at = self._month_window(now)
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? AND created_at >= ?"
            " AND created_at < ? AND status != ?",
            (user_id, month_start, month_end, FAILED),
        ).fetchall()
        receipt = self._conn.execute(
            "SELECT coaching_eligible, refilm_rejections"
            " FROM analysis_usage_monthly"
            " WHERE user_hash = ? AND month_start = ? AND expires_at > ?",
            (self._user_hash(user_id), month_start, now),
        ).fetchone()
        jobs = [self._from_row(row) for row in rows]
        active = sum(job.status in ACTIVE for job in jobs)
        finished = [job for job in jobs if job.status == DONE]
        eligible = sum(self.coaching_eligible(job) for job in finished)
        rejected = len(finished) - eligible
        if receipt is not None:
            eligible += int(receipt["coaching_eligible"])
            rejected += int(receipt["refilm_rejections"])
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
        month_start, month_end, _expires_at = self._month_window(now.timestamp())
        with self._lock:
            self._purge_usage_receipts_locked()
            self._conn.commit()
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? AND created_at >= ?"
                " AND created_at < ? AND status = ?",
                (user_id, month_start, month_end, DONE),
            ).fetchall()
            receipt = self._conn.execute(
                "SELECT refilm_rejections FROM analysis_usage_monthly"
                " WHERE user_hash = ? AND month_start = ? AND expires_at > ?",
                (self._user_hash(user_id), month_start, now.timestamp()),
            ).fetchone()
            live = sum(
                not self.coaching_eligible(self._from_row(row)) for row in rows
            )
        return live + (int(receipt["refilm_rejections"]) if receipt else 0)

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

    def history_cleanup_pending_count(self) -> int:
        """History operations still needing filesystem cleanup or recovery."""
        with self._lock:
            return self._history_operations_pending_locked()

    @contextmanager
    def history_delivery_guard(self) -> Iterator[None]:
        """Linearize an external history delivery with account reset."""

        with self._history_delivery_lock:
            yield

    def reset_user_history(
        self,
        user_id: str,
        *,
        delete_related: Callable[[sqlite3.Connection, str], None] | None = None,
    ) -> HistoryResetSummary:
        """Delete every terminal job owned by ``user_id`` without resetting quota.

        ``delete_related`` runs on this manager's SQLite connection inside the
        same ``BEGIN IMMEDIATE`` transaction, while the selected job rows still
        exist.  It must use the supplied connection and must not commit or roll
        back.  This lets the account store derive exact session ids, erase its
        own golf-history rows, and advance an account history epoch atomically.

        Active work makes the entire operation a conflict. Session directories
        are first atomically renamed into the same-volume ``.history-trash``.
        A prepared journal entry restores those names after any pre-commit
        failure; a committed entry retries physical cleanup after a crash.
        """
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")

        with self._history_delivery_lock, self._lock:
            self._recover_history_operations_locked()
            if self._history_operations_pending_locked():
                raise HistoryResetError(
                    "Earlier history cleanup must recover before another reset."
                )
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
            active_ids = [row["id"] for row in rows if row["status"] in ACTIVE]
            if active_ids:
                raise HistoryResetConflict(
                    "History cannot be reset while an analysis is queued or processing.",
                    active_ids,
                )

            # The callback still runs when there are no jobs: related rows can
            # outlive retention, and an auth/history epoch may still need to be
            # advanced. Existing pseudonymous quota receipts remain untouched.
            if not rows:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    current = self._conn.execute(
                        "SELECT id, status FROM jobs WHERE user_id = ?",
                        (user_id,),
                    ).fetchall()
                    if current:
                        raise HistoryResetConflict(
                            "Account history changed while the reset was starting.",
                            [
                                row["id"]
                                for row in current
                                if row["status"] in ACTIVE
                            ],
                        )
                    if delete_related is not None:
                        delete_related(self._conn, user_id)
                    self._conn.commit()
                except Exception as exc:
                    self._conn.rollback()
                    if isinstance(exc, HistoryResetError):
                        raise
                    raise HistoryResetError(
                        "Related history could not be deleted safely."
                    ) from exc
                return HistoryResetSummary(None, 0, False)

            try:
                usage = self._usage_contributions(rows)
                operation_id = self._prepare_history_operation_locked(
                    rows,
                    kind="user_reset",
                    subject_hash=self._user_hash(user_id),
                )
            except HistoryResetError:
                raise
            except Exception as exc:
                raise HistoryResetError(
                    "Session artifacts could not be prepared for deletion."
                ) from exc
            expected_ids = tuple(row["id"] for row in rows)
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                current = self._conn.execute(
                    "SELECT * FROM jobs WHERE user_id = ? ORDER BY id", (user_id,)
                ).fetchall()
                current_ids = tuple(row["id"] for row in current)
                active_ids = [
                    row["id"] for row in current if row["status"] in ACTIVE
                ]
                if active_ids or current_ids != expected_ids:
                    raise HistoryResetConflict(
                        "Account history changed while the reset was being prepared.",
                        active_ids,
                    )
                if delete_related is not None:
                    delete_related(self._conn, user_id)
                self._archive_usage_locked(usage)
                deleted = self._conn.execute(
                    "DELETE FROM jobs WHERE user_id = ?", (user_id,)
                ).rowcount
                if deleted != len(expected_ids):
                    raise HistoryResetConflict(
                        "Account history changed while the reset was committing."
                    )
                updated = self._conn.execute(
                    "UPDATE history_reset_operations"
                    " SET state = 'committed', updated_at = ?"
                    " WHERE operation_id = ? AND state = 'prepared'",
                    (time.time(), operation_id),
                ).rowcount
                if updated != 1:
                    raise HistoryResetError(
                        "The history reset journal could not be committed."
                    )
                self._conn.commit()
            except Exception as exc:
                self._conn.rollback()
                try:
                    self._abort_prepared_operation_locked(operation_id)
                except Exception:
                    logger.exception(
                        "Prepared history reset %s still needs recovery",
                        operation_id,
                    )
                if isinstance(exc, HistoryResetError):
                    raise
                raise HistoryResetError(
                    "History deletion could not be committed safely."
                ) from exc

            cleanup_pending = not self._finish_committed_operation_locked(
                operation_id
            )
            return HistoryResetSummary(
                operation_id=operation_id,
                deleted_jobs=len(expected_ids),
                cleanup_pending=cleanup_pending,
            )

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
        expected_history_epoch: int | None = None,
        notify_email: bool = False,
        *,
        job_id: str | None = None,
        status: str = QUEUED,
    ) -> Job:
        # Enter the manager lock before creating the directory so a concurrent
        # account reset cannot miss a half-created, not-yet-persisted session.
        with self._lock:
            if expected_history_epoch is not None:
                if not user_id:
                    raise ValueError(
                        "expected_history_epoch requires an owned session"
                    )
                owner = self._conn.execute(
                    "SELECT history_epoch FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                try:
                    current_epoch = (
                        int(owner["history_epoch"]) if owner is not None else None
                    )
                    expected_epoch = int(expected_history_epoch)
                except (TypeError, ValueError, OverflowError):
                    current_epoch = None
                    expected_epoch = -1
                if current_epoch is None or current_epoch != expected_epoch:
                    raise HistoryResetConflict(
                        "Swing history changed before the upload session was created."
                    )
            if status not in (PREPARING, QUEUED):
                raise ValueError("create_session status must be preparing or queued")
            if job_id is None:
                job_id = uuid.uuid4().hex[:12]
            elif not _SAFE_JOB_ID_RE.fullmatch(job_id):
                raise ValueError("job_id is not a safe session identifier")
            else:
                existing = self._conn.execute(
                    "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if existing is not None:
                    raise ValueError(f"job_id {job_id!r} already exists")
            job = Job(
                id=job_id,
                session_dir=self.sessions_dir / job_id,
                status=status,
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
                notify_email=bool(notify_email and user_id),
            )
            job.session_dir.mkdir(parents=True)
            self._save(job)
        return job

    def mark_queued(self, job: Job) -> Job:
        """Promote an internal preparing job to queued before ``submit``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job.id,)
            ).fetchone()
            if row is None:
                raise KeyError(job.id)
            current = self._from_row(row)
            if current.status == QUEUED:
                return current
            if current.status != PREPARING:
                raise ValueError(
                    f"job {job.id} cannot leave {current.status} for queued"
                )
            current.status = QUEUED
            self._save(current)
            return current

    def submit(self, job: Job, video_path: Path) -> None:
        self._pool.submit(self._run, job, video_path)

    def _classify_failure(self, job: Job, exc: BaseException) -> None:
        """Record the closed native failure code + retryability for a job.

        The raw ``error`` string stays a private diagnostic; only the classified
        code, retryable flag, and retry expiry reach the native API. Retryable
        collapses to non-retryable once the configured attempt cap is spent.
        """
        try:
            classified = classify_analysis_failure(exc)
        except BaseException:
            # Interrupted restart (KeyboardInterrupt/SystemExit) is re-raised by
            # the classifier: leave the job unclassified for recovery to requeue.
            raise
        max_attempts = int(self.cfg.web.get("mobile_analysis_retry_max_attempts", 2))
        window = int(self.cfg.web.get("mobile_analysis_retry_window_seconds", 86400))
        remaining = max(0, max_attempts - int(job.retry_attempt))
        retryable = effective_retryable(classified, remaining_attempts=remaining)
        job.failure_code = classified.code.value
        job.retryable = retryable
        job.retry_expires_at = (time.time() + window) if retryable else None

    def discard(self, job: Job) -> None:
        """Drop a session whose upload never completed."""
        with self._lock:
            shutil.rmtree(job.session_dir, ignore_errors=True)
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
            self._write_proof_cycle_artifact(job)
            self._delete_source_if_configured(job)
        except (ZeroStrikesError, VideoTooLongError, EventError, FFmpegError) as exc:
            job.status = FAILED
            job.error = str(exc)
            self._classify_failure(job, exc)
            self._delete_failed_source_if_configured(job)
        except Exception as exc:
            job.status = FAILED
            job.error = "Unexpected error during analysis:\n" + traceback.format_exc(
                limit=3
            )
            self._classify_failure(job, exc)
            # logger.exception carries the full traceback to the process log
            # (and to Sentry when the operator configured it — see app.py).
            logger.exception("Unexpected error during analysis of job %s", job.id)
            self._delete_failed_source_if_configured(job)
        self._save(job)
        self._notify_owner(job)
        self._cleanup_expired()

    # -- completion email (opt-in per upload) -----------------------------
    def _notify_owner(self, job: Job) -> None:
        """The "email me when my coaching is ready" send — at most one email
        per job, ever. The claim (stamping ``notified_at``) lands BEFORE the
        delivery attempt, the same rule as the weekly digest: a crash or a
        failed send loses one courtesy email instead of ever double-sending.
        Zero behavior without the upload-time opt-in, an owning account, a
        user store, or configured email delivery."""
        if not job.notify_email or job.user_id is None or self._users is None:
            return
        if job.status not in (DONE, FAILED):
            return
        if not mailer.enabled():
            return
        owner = self._users.get(job.user_id)
        if owner is None or not getattr(owner, "email", None):
            return
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET notified_at = ?"
                " WHERE id = ? AND notify_email = 1 AND notified_at IS NULL",
                (now, job.id),
            )
            self._conn.commit()
        if cursor.rowcount != 1:
            return  # already sent (or another worker owns the send)
        job.notified_at = now
        subject, body = self._completion_email(job, owner)
        try:
            mailer.send(owner.email, subject, body)
        except Exception:
            # The claim stands — a delivery retry could double-email, and the
            # result is still waiting on the (already working) session page.
            logger.error("Completion email delivery failed for job %s", job.id)

    def _completion_email(self, job: Job, owner) -> tuple[str, str]:
        """(subject, plain-text body) for a finished job — honest about the
        three real outcomes: coaching ready, re-film needed, or failed."""
        brand = str(self.cfg.brand["name"])
        base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
        session_url = f"{base}/session/{job.id}"
        checklist_url = f"{base}/#filming-checklist"
        metered = bool(
            self.cfg.web.get("require_account")
            and int(
                self.cfg.billing["pro_per_month"]
                if getattr(owner, "is_pro", False)
                else self.cfg.billing["free_per_month"]
            )
            > 0
        )
        if job.status == DONE and self.coaching_eligible(job):
            return (
                f"Your {brand} coaching is ready",
                "Your swing analysis has finished.\n\n"
                "One priority, one drill, and a re-film target are waiting"
                " on your report:\n"
                f"{session_url}\n",
            )
        if job.status == DONE:
            lines = [
                "Your clip was analyzed, but it couldn't be measured well"
                " enough for trustworthy coaching.",
                "The session page shows exactly what to change before you"
                " film again:",
                session_url,
                "",
                f"Filming checklist: {checklist_url}",
            ]
            if metered and job.user_id is not None and (
                self.refilm_rejections_this_month(job.user_id)
                <= _FREE_REFILM_CREDITS_PER_MONTH
            ):
                lines += [
                    "",
                    "Your first rejected clip each month doesn't use an"
                    " analysis — this was it, so the re-film costs you"
                    " nothing.",
                ]
            return (
                f"Your {brand} clip needs a re-film",
                "\n".join(lines) + "\n",
            )
        friendly = friendly_error(job.error)
        lines = [
            "Your swing analysis couldn't finish.",
            "",
            friendly.message,
        ]
        if friendly.tips:
            lines.append("")
            lines.extend(f"- {tip}" for tip in friendly.tips)
        lines += ["", f"Filming checklist: {checklist_url}"]
        if metered:
            lines += [
                "",
                "A failed upload never uses one of your monthly analyses"
                " — fix the clip and upload it again.",
            ]
        lines += ["", f"Upload again: {base}/"]
        return (
            f"Your {brand} analysis needs another clip",
            "\n".join(lines) + "\n",
        )

    def _write_proof_cycle_artifact(self, job: Job) -> None:
        """Persist an additive Proof Cycle sidecar without risking the report.

        This runs only after the pipeline has written its immutable report and
        metrics artifacts, while the current job is still absent from the
        manager's ``done`` query.  That means history contains only genuine
        previous sessions; a re-run can never count itself as evidence.
        """

        if not proof_cycle_enabled(self.cfg):
            return
        try:
            prior_jobs: list[Job] = []
            if job.user_id and job.club:
                prior_jobs = self.list_comparable(
                    user_id=job.user_id,
                    club=job.club,
                    through=job.created_at,
                    limit=proof_cycle_history_scan_limit(self.cfg),
                )
            artifact = build_proof_cycle_artifact(
                job,
                prior_jobs,
                self.cfg,
                baseline_job_for_id=self.get,
            )
            write_proof_cycle_artifact(job, artifact)
        except Exception:
            # A comparison is an enhancement.  Never turn a finished report
            # into a failed analysis because its optional sidecar did not write.
            logger.exception("Proof Cycle sidecar failed for job %s", job.id)
            job.log.append(
                "Proof Cycle check was unavailable; your report is still ready."
            )

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
            # notify_email is creation-time intent and notified_at is claimed
            # by _notify_owner's guarded UPDATE, so neither is in the conflict
            # update set — a stale in-memory job can never reopen a claim.
            self._conn.execute(
                "INSERT INTO jobs (id, status, created_at, updated_at, source_name,"
                " hand, angle, club, level, strikes, fast, client_ip, user_id, error,"
                " report_rel, swings_done, swings_total, log, notify_email,"
                " failure_code, retryable, retry_expires_at, retry_attempt)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET status = excluded.status,"
                " updated_at = excluded.updated_at, error = excluded.error,"
                " report_rel = excluded.report_rel, swings_done = excluded.swings_done,"
                " swings_total = excluded.swings_total, log = excluded.log,"
                " failure_code = excluded.failure_code,"
                " retryable = excluded.retryable,"
                " retry_expires_at = excluded.retry_expires_at,"
                " retry_attempt = excluded.retry_attempt",
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
                    int(job.notify_email),
                    job.failure_code,
                    int(job.retryable),
                    job.retry_expires_at,
                    int(job.retry_attempt),
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
            notify_email=bool(row["notify_email"]),
            notified_at=row["notified_at"],
            failure_code=self._row_value(row, "failure_code"),
            retryable=bool(self._row_value(row, "retryable") or 0),
            retry_expires_at=self._row_value(row, "retry_expires_at"),
            retry_attempt=int(self._row_value(row, "retry_attempt") or 0),
        )

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str):
        # Tolerate rows read before an in-place migration added the column.
        try:
            return row[key]
        except (IndexError, KeyError):
            return None

    # -- history deletion and quota receipts -----------------------------
    @staticmethod
    def _user_hash(user_id: str) -> str:
        """Stable pseudonymous key; never persist account ids in receipts."""
        return hashlib.sha256(
            b"caddieinsight-analysis-usage-v1\0" + user_id.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _month_window(timestamp: float) -> tuple[int, int, float]:
        current = datetime.fromtimestamp(timestamp, timezone.utc)
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            following = start.replace(year=start.year + 1, month=1)
        else:
            following = start.replace(month=start.month + 1)
        month_start = int(start.timestamp())
        month_end = int(following.timestamp())
        return month_start, month_end, float(month_end)

    def _usage_contributions(
        self, rows: Iterable[sqlite3.Row]
    ) -> dict[tuple[str, int, float], list[int]]:
        """Group terminal usage as [eligible, rejected] monthly receipts."""
        usage: dict[tuple[str, int, float], list[int]] = {}
        for row in rows:
            if row["status"] != DONE or not row["user_id"]:
                continue
            # Never read report/metrics files until their immediate path and
            # full tree have passed the reset's containment/link preflight.
            self._validate_session_dir_locked(row["id"])
            month_start, _month_end, expires_at = self._month_window(
                float(row["created_at"])
            )
            key = (
                self._user_hash(str(row["user_id"])),
                month_start,
                expires_at,
            )
            counts = usage.setdefault(key, [0, 0])
            if self.coaching_eligible(self._from_row(row)):
                counts[0] += 1
            else:
                counts[1] += 1
        return usage

    def _archive_usage_locked(
        self, usage: dict[tuple[str, int, float], list[int]]
    ) -> None:
        now = time.time()
        for (user_hash, month_start, expires_at), counts in usage.items():
            if expires_at <= now:
                continue
            self._conn.execute(
                "INSERT INTO analysis_usage_monthly"
                " (user_hash, month_start, coaching_eligible,"
                " refilm_rejections, expires_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_hash, month_start) DO UPDATE SET"
                " coaching_eligible = coaching_eligible"
                " + excluded.coaching_eligible,"
                " refilm_rejections = refilm_rejections"
                " + excluded.refilm_rejections,"
                " expires_at = MAX(expires_at, excluded.expires_at),"
                " updated_at = excluded.updated_at",
                (
                    user_hash,
                    month_start,
                    counts[0],
                    counts[1],
                    expires_at,
                    now,
                ),
            )

    def _purge_usage_receipts_locked(self) -> None:
        self._conn.execute(
            "DELETE FROM analysis_usage_monthly WHERE expires_at <= ?",
            (time.time(),),
        )

    @staticmethod
    def _is_link_or_junction(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        # Python 3.11 has no Path.is_junction(). On Windows, junctions and
        # other directory reparse points expose this file attribute through
        # lstat; reject all of them rather than trusting os.walk not to follow.
        try:
            attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise HistoryResetSafetyError(
                f"Filesystem entry could not be inspected safely: {path}"
            ) from exc
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(reparse and attributes & reparse)

    @staticmethod
    def _path_exists_safely(path: Path) -> bool:
        try:
            os.lstat(path)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise HistoryResetSafetyError(
                f"Filesystem entry could not be inspected safely: {path}"
            ) from exc
        return True

    def _validate_job_id(self, job_id: object) -> str:
        value = str(job_id)
        if (
            not _SAFE_JOB_ID_RE.fullmatch(value)
            or value.endswith(".")
            or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        ):
            raise HistoryResetSafetyError(
                f"Unsafe persisted session id cannot be deleted: {value!r}"
            )
        return value

    def _validate_tree_no_links(self, path: Path, root: Path) -> None:
        def walk_error(error: OSError) -> None:
            raise error

        for current, directories, files in os.walk(
            path, topdown=True, followlinks=False, onerror=walk_error
        ):
            current_path = Path(current)
            try:
                current_resolved = current_path.resolve(strict=True)
            except OSError as exc:
                raise HistoryResetSafetyError(
                    f"Session path could not be resolved safely: {current_path}"
                ) from exc
            if not current_resolved.is_relative_to(root):
                raise HistoryResetSafetyError(
                    f"Session path escapes the sessions directory: {current_path}"
                )
            for name in (*directories, *files):
                child = current_path / name
                if self._is_link_or_junction(child):
                    raise HistoryResetSafetyError(
                        f"Session contains a link that cannot be deleted safely: {child}"
                    )

    def _validate_session_dir_locked(self, job_id: object) -> tuple[str, Path, bool]:
        safe_id = self._validate_job_id(job_id)
        root = self.sessions_dir.resolve(strict=True)
        path = self.sessions_dir / safe_id
        present = self._path_exists_safely(path)
        if present and self._is_link_or_junction(path):
            raise HistoryResetSafetyError(
                f"Session directory is a link and cannot be deleted safely: {safe_id}"
            )
        if not present:
            return safe_id, path, False
        if not path.is_dir():
            raise HistoryResetSafetyError(
                f"Session path is not a directory: {safe_id}"
            )
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise HistoryResetSafetyError(
                f"Session directory could not be resolved safely: {safe_id}"
            ) from exc
        if resolved.parent != root:
            raise HistoryResetSafetyError(
                f"Session directory escapes the sessions root: {safe_id}"
            )
        self._validate_tree_no_links(path, resolved)
        return safe_id, path, True

    def _history_trash_root_locked(self, *, create: bool) -> Path:
        root = self.sessions_dir.resolve(strict=True)
        trash = self.sessions_dir / _HISTORY_TRASH_NAME
        present = self._path_exists_safely(trash)
        if present and self._is_link_or_junction(trash):
            raise HistoryResetSafetyError(
                "The history trash path is a link; cleanup was refused."
            )
        if create and not present:
            trash.mkdir(exist_ok=True)
            present = True
        if not present:
            return trash
        if not trash.is_dir() or trash.resolve(strict=True).parent != root:
            raise HistoryResetSafetyError(
                "The history trash path is not contained in the sessions root."
            )
        return trash

    def _history_operation_dir_locked(
        self, operation_id: object, *, create_trash: bool
    ) -> Path:
        value = str(operation_id)
        if not _HISTORY_OPERATION_ID_RE.fullmatch(value):
            raise HistoryResetSafetyError(
                f"Unsafe history operation id: {value!r}"
            )
        trash = self._history_trash_root_locked(create=create_trash)
        operation_dir = trash / value
        present = self._path_exists_safely(operation_dir)
        if present and self._is_link_or_junction(operation_dir):
            raise HistoryResetSafetyError(
                "A history operation path is a link; cleanup was refused."
            )
        if present:
            if not operation_dir.is_dir():
                raise HistoryResetSafetyError(
                    "A history operation path is not a directory."
                )
            resolved = operation_dir.resolve(strict=True)
            if not self._path_exists_safely(trash) or (
                resolved.parent != trash.resolve(strict=True)
            ):
                raise HistoryResetSafetyError(
                    "A history operation path escapes the history trash."
                )
        return operation_dir

    @staticmethod
    def _parse_history_ids(raw: object, *, label: str) -> tuple[str, ...]:
        try:
            values = json.loads(str(raw))
        except (TypeError, ValueError) as exc:
            raise HistoryResetSafetyError(
                f"Invalid {label} in the history operation journal."
            ) from exc
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise HistoryResetSafetyError(
                f"Invalid {label} in the history operation journal."
            )
        if len(values) != len(set(values)):
            raise HistoryResetSafetyError(
                f"Duplicate {label} in the history operation journal."
            )
        return tuple(values)

    def _prepare_history_operation_locked(
        self,
        rows: Iterable[sqlite3.Row],
        *,
        kind: str,
        subject_hash: str | None,
    ) -> str:
        if self._history_operations_pending_locked():
            raise HistoryResetError(
                "A history operation is already pending recovery."
            )
        selected_rows = tuple(rows)
        checked = [
            self._validate_session_dir_locked(row["id"]) for row in selected_rows
        ]
        job_ids = [item[0] for item in checked]
        selected_ids = set(job_ids)
        selected_folded = {job_id.casefold() for job_id in job_ids}
        aliases = [
            str(row["id"])
            for row in self._conn.execute("SELECT id FROM jobs").fetchall()
            if str(row["id"]) not in selected_ids
            and str(row["id"]).casefold() in selected_folded
        ]
        if aliases or len(selected_folded) != len(job_ids):
            raise HistoryResetSafetyError(
                "Case-insensitive session-id aliases make deletion unsafe."
            )
        artifact_ids = [item[0] for item in checked if item[2]]
        operation_id = uuid.uuid4().hex
        now = time.time()
        try:
            self._conn.execute(
                "INSERT INTO history_reset_operations"
                " (operation_id, kind, subject_hash, state, job_ids_json,"
                " artifact_job_ids_json, created_at, updated_at)"
                " VALUES (?, ?, ?, 'prepared', ?, ?, ?, ?)",
                (
                    operation_id,
                    kind,
                    subject_hash,
                    json.dumps(job_ids, separators=(",", ":")),
                    json.dumps(artifact_ids, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        try:
            operation_dir = self._history_operation_dir_locked(
                operation_id, create_trash=True
            )
            operation_dir.mkdir()
            for safe_id, source, had_artifacts in checked:
                if not had_artifacts:
                    continue
                # Revalidate immediately before each rename. The manager lock
                # closes application races; this second check also refuses a
                # filesystem swap between preflight and staging.
                _validated_id, source, exists = self._validate_session_dir_locked(
                    safe_id
                )
                if not exists:
                    raise HistoryResetSafetyError(
                        f"Session artifacts disappeared while preparing: {safe_id}"
                    )
                destination = operation_dir / safe_id
                if self._path_exists_safely(destination):
                    raise HistoryResetSafetyError(
                        f"History trash destination already exists: {safe_id}"
                    )
                source.replace(destination)
        except Exception:
            try:
                self._abort_prepared_operation_locked(operation_id)
            except Exception:
                logger.exception(
                    "Could not roll back prepared history operation %s",
                    operation_id,
                )
            raise
        return operation_id

    def _operation_row_locked(self, operation_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM history_reset_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()

    def _history_operations_pending_locked(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM history_reset_operations"
            ).fetchone()[0]
        )

    def _restore_prepared_artifacts_locked(self, row: sqlite3.Row) -> None:
        operation_id = str(row["operation_id"])
        job_ids = self._parse_history_ids(row["job_ids_json"], label="job ids")
        artifact_ids = self._parse_history_ids(
            row["artifact_job_ids_json"], label="artifact job ids"
        )
        if not set(artifact_ids).issubset(job_ids):
            raise HistoryResetSafetyError(
                "Artifact ids are not a subset of the journaled jobs."
            )
        persisted_ids = {
            str(persisted[0])
            for persisted in self._conn.execute("SELECT id FROM jobs").fetchall()
        }
        if not set(job_ids).issubset(persisted_ids):
            raise HistoryResetSafetyError(
                "A prepared history operation is missing its job rows."
            )
        operation_dir = self._history_operation_dir_locked(
            operation_id, create_trash=False
        )
        for job_id in reversed(artifact_ids):
            safe_id = self._validate_job_id(job_id)
            source = self.sessions_dir / safe_id
            staged = operation_dir / safe_id
            source_present = self._path_exists_safely(source)
            staged_present = self._path_exists_safely(staged)
            if source_present and self._is_link_or_junction(source):
                raise HistoryResetSafetyError(
                    f"Restored session path is a link: {safe_id}"
                )
            if source_present and staged_present:
                raise HistoryResetSafetyError(
                    f"Both live and staged session paths exist: {safe_id}"
                )
            if staged_present:
                if self._is_link_or_junction(staged):
                    raise HistoryResetSafetyError(
                        f"Staged session path is a link: {safe_id}"
                    )
                if not self._path_exists_safely(operation_dir) or (
                    staged.resolve(strict=True).parent
                    != operation_dir.resolve(strict=True)
                ):
                    raise HistoryResetSafetyError(
                        f"Staged session path escapes history trash: {safe_id}"
                    )
                staged.replace(source)
            elif not source_present:
                raise HistoryResetSafetyError(
                    f"Journaled session artifacts are missing: {safe_id}"
                )
        if self._path_exists_safely(operation_dir):
            operation_dir.rmdir()

    def _abort_prepared_operation_locked(self, operation_id: str) -> None:
        row = self._operation_row_locked(operation_id)
        if row is None:
            return
        if row["state"] != "prepared":
            raise HistoryResetSafetyError(
                "Only a prepared history operation can be rolled back."
            )
        self._restore_prepared_artifacts_locked(row)
        try:
            deleted = self._conn.execute(
                "DELETE FROM history_reset_operations"
                " WHERE operation_id = ? AND state = 'prepared'",
                (operation_id,),
            ).rowcount
            if deleted != 1:
                raise HistoryResetError(
                    "The prepared history journal changed during rollback."
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self._remove_empty_history_trash_locked()

    def _remove_empty_history_trash_locked(self) -> None:
        try:
            trash = self._history_trash_root_locked(create=False)
            if self._path_exists_safely(trash):
                trash.rmdir()
        except (OSError, HistoryResetSafetyError):
            pass

    def _finish_committed_operation_locked(self, operation_id: str) -> bool:
        row = self._operation_row_locked(operation_id)
        if row is None:
            return True
        if row["state"] != "committed":
            return False
        try:
            job_ids = self._parse_history_ids(
                row["job_ids_json"], label="job ids"
            )
            artifact_ids = self._parse_history_ids(
                row["artifact_job_ids_json"], label="artifact job ids"
            )
            if not set(artifact_ids).issubset(job_ids):
                raise HistoryResetSafetyError(
                    "Artifact ids are not a subset of the journaled jobs."
                )
            persisted_ids = {
                str(persisted[0])
                for persisted in self._conn.execute(
                    "SELECT id FROM jobs"
                ).fetchall()
            }
            if set(job_ids) & persisted_ids:
                raise HistoryResetSafetyError(
                    "A committed history operation still has job rows."
                )
            # SQLite can durably recover the commit even if a power loss made
            # one or more preceding directory renames disappear. Validate all
            # journaled live paths before deleting anything, then purge both
            # possible locations. The committed journal remains until neither
            # location contains owned artifacts.
            live_artifact_dirs: list[Path] = []
            for job_id in artifact_ids:
                _safe_id, live_path, live_present = (
                    self._validate_session_dir_locked(job_id)
                )
                if live_present:
                    live_artifact_dirs.append(live_path)
            operation_dir = self._history_operation_dir_locked(
                operation_id, create_trash=False
            )
            if self._path_exists_safely(operation_dir):
                self._validate_tree_no_links(
                    operation_dir, operation_dir.resolve(strict=True)
                )
            for live_path in live_artifact_dirs:
                shutil.rmtree(live_path)
            if self._path_exists_safely(operation_dir):
                shutil.rmtree(operation_dir)
        except (OSError, HistoryResetSafetyError):
            logger.exception(
                "History cleanup remains pending for operation %s", operation_id
            )
            return False
        try:
            deleted = self._conn.execute(
                "DELETE FROM history_reset_operations"
                " WHERE operation_id = ? AND state = 'committed'",
                (operation_id,),
            ).rowcount
            if deleted != 1:
                raise HistoryResetError(
                    "The committed history journal changed during cleanup."
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            logger.exception(
                "Cleaned history journal remains pending for operation %s",
                operation_id,
            )
            return False
        self._remove_empty_history_trash_locked()
        return True

    def _recover_history_operations(self) -> None:
        """Restore prepared operations and finish committed trash deletion."""
        with self._lock:
            self._recover_history_operations_locked()

    def _recover_history_operations_locked(self) -> None:
        rows = self._conn.execute(
            "SELECT * FROM history_reset_operations ORDER BY created_at"
        ).fetchall()
        for row in rows:
            operation_id = str(row["operation_id"])
            try:
                if row["state"] == "prepared":
                    self._abort_prepared_operation_locked(operation_id)
                elif row["state"] == "committed":
                    self._finish_committed_operation_locked(operation_id)
                else:
                    logger.error(
                        "Unknown history operation state %r for %s",
                        row["state"],
                        operation_id,
                    )
            except Exception:
                # Leave the durable row in place for health visibility and a
                # later retry; never guess at an unsafe filesystem path.
                logger.exception(
                    "History operation recovery remains pending for %s",
                    operation_id,
                )

    # -- startup passes ---------------------------------------------------
    def _source_path(self, job: Job) -> Path | None:
        """The uploaded video (saved as source.<ext> by the web layer)."""
        return next(job.session_dir.glob("source.*"), None)

    def recover_interrupted(
        self, *, blocked_user_ids: frozenset[str] = frozenset()
    ) -> None:
        """Resume interrupted work, excluding owners held by the caller."""
        if blocked_user_ids:
            self._requeue_interrupted(blocked_user_ids=blocked_user_ids)
        else:
            self._requeue_interrupted()

    def _requeue_interrupted(
        self, *, blocked_user_ids: frozenset[str] = frozenset()
    ) -> None:
        """Re-run jobs that were queued or running when the process died."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY created_at",
                ACTIVE,
            ).fetchall()
        for row in rows:
            job = self._from_row(row)
            if job.user_id in blocked_user_ids:
                continue
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
            else:
                # A restart-orphaned job is terminal too — the promised "one
                # email when it's done" still goes out (claim-guarded).
                self._notify_owner(job)

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
        """Delete expired terminal sessions after archiving monthly usage."""
        days = float(self.cfg.web.get("retention_days") or 0)
        with self._lock:
            self._recover_history_operations_locked()
            if self._history_operations_pending_locked():
                logger.error(
                    "Retention cleanup skipped while history recovery is pending."
                )
                return
            self._purge_usage_receipts_locked()
            self._conn.commit()
            if days <= 0:
                return
            cutoff = time.time() - days * 86400
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status IN (?, ?) AND updated_at < ?"
                " ORDER BY updated_at, id",
                (DONE, FAILED, cutoff),
            ).fetchall()
            for row in rows:
                operation_id: str | None = None
                try:
                    usage = self._usage_contributions((row,))
                    operation_id = self._prepare_history_operation_locked(
                        (row,), kind="retention", subject_hash=None
                    )
                    self._conn.execute("BEGIN IMMEDIATE")
                    current = self._conn.execute(
                        "SELECT * FROM jobs WHERE id = ?", (row["id"],)
                    ).fetchone()
                    if current is None or any(
                        current[column] != row[column]
                        for column in (
                            "status",
                            "created_at",
                            "updated_at",
                            "user_id",
                        )
                    ):
                        raise HistoryResetConflict(
                            "An expired session changed while cleanup was prepared."
                        )
                    self._archive_usage_locked(usage)
                    deleted = self._conn.execute(
                        "DELETE FROM jobs WHERE id = ? AND status = ?"
                        " AND updated_at = ? AND user_id IS ?",
                        (
                            row["id"],
                            row["status"],
                            row["updated_at"],
                            row["user_id"],
                        ),
                    ).rowcount
                    if deleted != 1:
                        raise HistoryResetConflict(
                            "An expired session changed while cleanup was committing."
                        )
                    updated = self._conn.execute(
                        "UPDATE history_reset_operations"
                        " SET state = 'committed', updated_at = ?"
                        " WHERE operation_id = ? AND state = 'prepared'",
                        (time.time(), operation_id),
                    ).rowcount
                    if updated != 1:
                        raise HistoryResetError(
                            "The retention cleanup journal could not be committed."
                        )
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    if operation_id is not None:
                        try:
                            self._abort_prepared_operation_locked(operation_id)
                        except Exception:
                            logger.exception(
                                "Expired session %s could not be restored",
                                row["id"],
                            )
                    logger.exception(
                        "Expired session %s cleanup was deferred", row["id"]
                    )
                    if self._history_operations_pending_locked():
                        return
                    continue
                if not self._finish_committed_operation_locked(operation_id):
                    return
