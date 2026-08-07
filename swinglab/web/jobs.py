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
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Iterator, assert_never

from ..caddie_brief import (
    payload_has_coachable_data,
    payload_is_coaching_eligible,
    payload_requires_refilm,
    payload_structure_is_valid,
)
from ..config import Config
from ..events import EventError
from ..ffmpeg import FFmpegError
from ..pipeline import SessionResult, VideoTooLongError, ZeroStrikesError, analyze_video
from ..proof_cycle_artifact import (
    build_proof_cycle_artifact,
    proof_cycle_enabled,
    proof_cycle_history_scan_limit,
    write_proof_cycle_artifact,
)
from ..report import (
    REPORT_OUTCOME_CAPTURE,
    REPORT_OUTCOME_COACHING,
    REPORT_PRESENTATION_VERSION,
    persisted_report_outcome,
)
from ..report_artifacts import (
    REPORT_CHECKSUMS_FILENAME,
    REPORT_MANIFEST_FILENAME,
    REPORT_VIEW_FILENAME,
    ReportEntitlementSnapshot,
    load_published_bundle,
    validate_persisted_report_policy,
)
from ..report_bundle import (
    REPORT_FILENAME,
    CoreReportBundleError,
    GuidedReportRendererUnavailable,
    ReportHtmlWriter,
    prepare_abandoned_report_bundle_cleanup,
)
from ..report_view import (
    GUIDED_REPORT_PRESENTATION_VERSION,
    ReportOutcome,
    ReportPresentationVersion,
    UnsupportedReportPresentationVersion,
    parse_report_presentation_version,
)
from . import mailer
from .humanize import friendly_error

logger = logging.getLogger("swinglab.web.jobs")

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
FAILED = "failed"
ACTIVE = (QUEUED, PROCESSING)
_FREE_REFILM_CREDITS_PER_MONTH = 1
_COMPLETION_COACHING = "coaching"
_COMPLETION_CAPTURE = "capture"
_COMPLETION_CORRUPT = "corrupt"
_MAX_RETRY_ANALYSIS_CHILDREN = 256
_REPORT_BUNDLE_FINAL_RE = re.compile(r"report-bundle-[0-9a-f]{32}\Z")
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


def configured_report_presentation(cfg: Config) -> str:
    """Resolve the presentation assigned to a future job from one strict gate."""

    if cfg.report.get("guided_presentation_enabled") is True:
        return GUIDED_REPORT_PRESENTATION_VERSION
    return REPORT_PRESENTATION_VERSION

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
    report_presentation_version TEXT NOT NULL DEFAULT 'premium-coach-v2',
    report_entitlements_json TEXT,
    report_view_rel TEXT,
    report_manifest_rel TEXT,
    report_checksums_rel TEXT,
    structured_report INTEGER NOT NULL DEFAULT 0,
    swings_done  INTEGER NOT NULL DEFAULT 0,
    swings_total INTEGER NOT NULL DEFAULT 0,
    log          TEXT NOT NULL DEFAULT '[]',
    notify_email INTEGER NOT NULL DEFAULT 0,
    notified_at  REAL
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
    report_presentation_version: str = REPORT_PRESENTATION_VERSION
    report_entitlements: ReportEntitlementSnapshot = field(
        default_factory=lambda: ReportEntitlementSnapshot("available")
    )
    report_view_rel: str | None = None
    report_manifest_rel: str | None = None
    report_checksums_rel: str | None = None
    structured_report: bool = False
    swings_done: int = 0
    swings_total: int = 0  # 0 until strike detection has counted the swings
    notify_email: bool = False  # owner asked to be emailed at completion
    notified_at: float | None = None  # claim stamp — at most one email per job

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
        guided_html_writer: ReportHtmlWriter | None = None,
    ):
        """``user_store`` (a swinglab.web.users.UserStore, duck-typed on
        ``.get(user_id)``) lets session creation capture the owner's plan for
        the coach-replay Pro gate. None — the default,
        and what any non-web caller gets — disables the gate entirely."""
        self.sessions_dir = sessions_dir
        self.cfg = cfg
        self._users = user_store
        self._guided_html_writer = guided_html_writer
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
            if "report_presentation_version" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN report_presentation_version"
                    " TEXT NOT NULL DEFAULT 'premium-coach-v2'"
                )
            if "report_entitlements_json" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN report_entitlements_json TEXT"
                )
            if "report_view_rel" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN report_view_rel TEXT"
                )
            if "report_manifest_rel" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN report_manifest_rel TEXT"
                )
            if "report_checksums_rel" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN report_checksums_rel TEXT"
                )
            if "structured_report" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN structured_report"
                    " INTEGER NOT NULL DEFAULT 0"
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
        eligible: list[Job] = []
        for row in rows:
            if self._completed_report_classification(row) != _COMPLETION_COACHING:
                continue
            try:
                eligible.append(self._from_row(row))
            except (TypeError, ValueError):
                # A malformed row cannot regain coaching/trend eligibility.
                continue
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
        month_start, month_end, _expires_at = self._month_window(now.timestamp())
        with self._lock:
            self._purge_usage_receipts_locked()
            self._conn.commit()
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? AND created_at >= ?"
                " AND created_at < ? AND status != ?",
                (user_id, month_start, month_end, FAILED),
            ).fetchall()
            receipt = self._conn.execute(
                "SELECT coaching_eligible, refilm_rejections"
                " FROM analysis_usage_monthly"
                " WHERE user_hash = ? AND month_start = ? AND expires_at > ?",
                (self._user_hash(user_id), month_start, now.timestamp()),
            ).fetchone()
            active = sum(row["status"] in ACTIVE for row in rows)
            completed = [
                self._completed_report_classification(row)
                for row in rows
                if row["status"] == DONE
            ]
            # Integrity failures consume an allowance: a completed analysis
            # cannot be converted into a courtesy re-film by mutating or
            # deleting its signed bundle after publication.
            eligible = sum(
                state in (_COMPLETION_COACHING, _COMPLETION_CORRUPT)
                for state in completed
            )
            rejected = sum(state == _COMPLETION_CAPTURE for state in completed)
        if receipt is not None:
            eligible += int(receipt["coaching_eligible"])
            rejected += int(receipt["refilm_rejections"])
        charged_rejected = max(
            0, rejected - _FREE_REFILM_CREDITS_PER_MONTH
        )
        return active + eligible + charged_rejected

    def coaching_eligible(self, job: Job) -> bool:
        """Whether a finished job can power coaching, trends, and quota."""
        if job.status != DONE:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job.id,)
            ).fetchone()
        if row is None or row["status"] != DONE:
            return False
        return self._completed_report_classification(row, legacy_job=job) == (
            _COMPLETION_COACHING
        )

    def _legacy_coaching_eligible(self, job: Job) -> bool:
        """Compatibility-only eligibility for pre-structured reports."""
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

    def _completed_report_classification(
        self, row: sqlite3.Row, *, legacy_job: Job | None = None
    ) -> str:
        """Classify DONE rows without trusting mutable structured artifacts.

        Structured reports are usable only when their complete persisted
        bundle and creation-time policy validate. Any integrity or policy
        failure is deliberately distinct from a genuine capture-only result:
        it cannot power coaching, but it still consumes quota.
        """

        if row["status"] != DONE:
            return _COMPLETION_CORRUPT
        try:
            presentation = parse_report_presentation_version(
                row["report_presentation_version"]
            )
        except UnsupportedReportPresentationVersion:
            return _COMPLETION_CORRUPT
        structured_sidecars = any(
            row[name] is not None
            for name in (
                "report_view_rel",
                "report_manifest_rel",
                "report_checksums_rel",
            )
        )
        genuine_legacy = (
            presentation is ReportPresentationVersion.LEGACY
            and not bool(row["structured_report"])
            and not structured_sidecars
        )
        if genuine_legacy:
            try:
                job = legacy_job if legacy_job is not None else self._from_row(row)
                return (
                    _COMPLETION_COACHING
                    if self._legacy_coaching_eligible(job)
                    else _COMPLETION_CAPTURE
                )
            except (OSError, TypeError, ValueError):
                return _COMPLETION_CORRUPT
        if not bool(row["structured_report"]):
            return _COMPLETION_CORRUPT

        try:
            values = tuple(
                row[name]
                for name in (
                    "report_rel",
                    "report_view_rel",
                    "report_manifest_rel",
                    "report_checksums_rel",
                )
            )
            child_name, direct_rels = self._parse_structured_report_rels(values)
            if child_name is None:
                raise CoreReportBundleError(
                    "completed structured report rels are missing"
                )
            safe_id = self._validate_job_id(row["id"])
            sessions_root = self.sessions_dir.resolve(strict=True)
            job_root = (self.sessions_dir / safe_id).resolve(strict=True)
            if job_root.parent != sessions_root:
                raise CoreReportBundleError("structured job root escapes sessions")
            analysis_session = (job_root / "out" / child_name).resolve(strict=True)
            analysis_session.relative_to(job_root)
            bundle = load_published_bundle(
                analysis_session,
                report_rel=direct_rels[0],
                report_view_rel=direct_rels[1],
                manifest_rel=direct_rels[2],
                checksums_rel=direct_rels[3],
            )
            validate_persisted_report_policy(
                bundle,
                report_presentation_version=row["report_presentation_version"],
                report_entitlements_json=row["report_entitlements_json"],
            )
        except Exception:
            # This is a read-side trust decision, not a worker failure. Keep it
            # fail-closed even for an unexpected malformed row or filesystem
            # condition; callers must never regain a courtesy retry this way.
            logger.warning(
                "Structured report integrity validation failed for job %s",
                row["id"],
                exc_info=True,
            )
            return _COMPLETION_CORRUPT
        if bundle.view.outcome == ReportOutcome.COACHING_READY:
            return _COMPLETION_COACHING
        if bundle.view.outcome == ReportOutcome.CAPTURE_ONLY:
            return _COMPLETION_CAPTURE
        return _COMPLETION_CORRUPT

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
                self._completed_report_classification(row) == _COMPLETION_CAPTURE
                for row in rows
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
        report_presentation_version: str | None = None,
    ) -> Job:
        presentation = (
            configured_report_presentation(self.cfg)
            if report_presentation_version is None
            else report_presentation_version
        )
        presentation = parse_report_presentation_version(presentation).value
        if (
            presentation == GUIDED_REPORT_PRESENTATION_VERSION
            and self._guided_html_writer is None
        ):
            raise GuidedReportRendererUnavailable(
                "guided report HTML writer is unavailable"
            )
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
            report_entitlements = self._capture_report_entitlements(user_id)
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
                notify_email=bool(notify_email and user_id),
                report_presentation_version=presentation,
                report_entitlements=report_entitlements,
            )
            job.session_dir.mkdir(parents=True)
            self._save(job)
        return job

    def submit(self, job: Job, video_path: Path) -> None:
        self._pool.submit(self._run, job, video_path)

    def discard(self, job: Job) -> None:
        """Drop a session whose upload never completed."""
        with self._lock:
            shutil.rmtree(job.session_dir, ignore_errors=True)
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
            self._conn.commit()

    # -- execution --------------------------------------------------------
    def _capture_report_entitlements(
        self, user_id: str | None
    ) -> ReportEntitlementSnapshot:
        if not self.cfg.slowmo.get("annotated"):
            return ReportEntitlementSnapshot("disabled")
        if (
            not self.cfg.billing.get("replay_pro_only")
            or not self.cfg.web.get("require_account")
            or self._users is None
            or user_id is None
        ):
            return ReportEntitlementSnapshot("available")
        owner = self._users.get(user_id)
        if owner is None or not getattr(owner, "is_pro", False):
            return ReportEntitlementSnapshot("locked")
        return ReportEntitlementSnapshot("available")

    def replay_locked(self, job: Job) -> bool:
        """Project the immutable creation-time entitlement for old callers."""

        return job.report_entitlements.coach_replay == "locked"

    def _mark_processing(self, job: Job) -> bool:
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error = NULL"
                " WHERE id = ? AND status = ?",
                (PROCESSING, now, job.id, QUEUED),
            )
            self._conn.commit()
            if cursor.rowcount != 1:
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job.id,)
                ).fetchone()
                if row is not None:
                    self._copy_persisted_state(job, self._from_row(row))
                return False
        job.status = PROCESSING
        job.error = None
        return True

    def _fail_processing_job(self, job: Job, error: str) -> bool:
        now = time.time()
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "UPDATE jobs SET status = ?, updated_at = ?, error = ?,"
                    " swings_done = ?, swings_total = ?, log = ?"
                    " WHERE id = ? AND status = ?",
                    (
                        FAILED,
                        now,
                        error,
                        job.swings_done,
                        job.swings_total,
                        json.dumps(job.log),
                        job.id,
                        PROCESSING,
                    ),
                )
                self._conn.commit()
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job.id,)
                ).fetchone()
                if row is not None:
                    persisted = self._from_row(row)
                    self._copy_persisted_state(job, persisted)
                    if persisted.status == FAILED and persisted.error == error:
                        return True
                raise
            if cursor.rowcount != 1:
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job.id,)
                ).fetchone()
                if row is not None:
                    self._copy_persisted_state(job, self._from_row(row))
                return False
        job.status = FAILED
        job.error = error
        return True

    @staticmethod
    def _copy_persisted_state(target: Job, persisted: Job) -> None:
        for name in (
            "status",
            "error",
            "report_rel",
            "report_view_rel",
            "report_manifest_rel",
            "report_checksums_rel",
            "structured_report",
            "swings_done",
            "swings_total",
            "log",
            "notified_at",
        ):
            setattr(target, name, getattr(persisted, name))

    def _retry_report_protection(
        self, job: Job
    ) -> tuple[str | None, tuple[str, ...]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT report_rel, report_view_rel, report_manifest_rel,"
                " report_checksums_rel FROM jobs WHERE id = ?",
                (job.id,),
            ).fetchone()
        if row is None:
            raise CoreReportBundleError("retry job row is missing")
        return self._parse_structured_report_rels(tuple(row))

    @staticmethod
    def _parse_structured_report_rels(
        values: tuple[object, ...],
    ) -> tuple[str | None, tuple[str, ...]]:
        if len(values) != 4:
            raise CoreReportBundleError(
                "persisted structured report rel count is invalid"
            )
        if all(value is None for value in values):
            return None, ()
        if any(value is None for value in values):
            raise CoreReportBundleError(
                "persisted structured report rels are incomplete"
            )
        if any(not isinstance(value, str) for value in values):
            raise CoreReportBundleError(
                "persisted structured report rel is not text"
            )
        if len(set(values)) != 4 or len({value.casefold() for value in values}) != 4:
            raise CoreReportBundleError(
                "persisted structured report rels are duplicated"
            )

        expected_names = (
            REPORT_FILENAME,
            REPORT_VIEW_FILENAME,
            REPORT_MANIFEST_FILENAME,
            REPORT_CHECKSUMS_FILENAME,
        )
        parsed: list[PurePosixPath] = []
        for value, expected_name in zip(values, expected_names):
            assert isinstance(value, str)
            path = PurePosixPath(value)
            if (
                path.is_absolute()
                or path.as_posix() != value
                or "\\" in value
                or ":" in value
                or len(path.parts) != 4
                or path.parts[0] != "out"
                or path.name != expected_name
                or any(
                    part in {"", ".", ".."} or part.endswith((".", " "))
                    for part in path.parts
                )
            ):
                raise CoreReportBundleError(
                    "persisted structured report rel is unsafe"
                )
            parsed.append(path)
        child_names = {path.parts[1] for path in parsed}
        bundle_names = {path.parts[2] for path in parsed}
        if len(child_names) != 1 or len(bundle_names) != 1:
            raise CoreReportBundleError(
                "persisted structured report rels cross analysis children"
            )
        bundle_name = next(iter(bundle_names))
        if _REPORT_BUNDLE_FINAL_RE.fullmatch(bundle_name) is None:
            raise CoreReportBundleError(
                "persisted structured report rels do not name a canonical bundle"
            )
        child_name = next(iter(child_names))
        return child_name, tuple(
            PurePosixPath(path.parts[2], path.parts[3]).as_posix()
            for path in parsed
        )

    @staticmethod
    def _retry_path_is_reparse(path: Path, info: os.stat_result) -> bool:
        if stat.S_ISLNK(info.st_mode):
            return True
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())

    def _cleanup_retry_report_bundles(self, job: Job) -> int:
        protected_child, protected_rels = self._retry_report_protection(job)
        out_dir = job.session_dir / "out"
        try:
            out_info = os.lstat(out_dir)
        except FileNotFoundError:
            return 0
        except OSError as exc:
            raise CoreReportBundleError(
                "retry output root cannot be inspected"
            ) from exc
        if self._retry_path_is_reparse(out_dir, out_info) or not stat.S_ISDIR(
            out_info.st_mode
        ):
            raise CoreReportBundleError("retry output root is not a plain directory")

        names: list[str] = []
        try:
            with os.scandir(out_dir) as scanned:
                for entry in scanned:
                    if len(names) >= _MAX_RETRY_ANALYSIS_CHILDREN:
                        raise CoreReportBundleError(
                            "retry analysis children exceed the direct-entry bound"
                        )
                    names.append(entry.name)
        except CoreReportBundleError:
            raise
        except OSError as exc:
            raise CoreReportBundleError(
                "retry output root cannot be enumerated"
            ) from exc

        plans: list[tuple[Path, tuple[str, ...]]] = []
        for name in sorted(names):
            child = out_dir / name
            try:
                info = os.lstat(child)
            except OSError as exc:
                raise CoreReportBundleError(
                    "retry analysis child cannot be inspected"
                ) from exc
            if self._retry_path_is_reparse(child, info) or not stat.S_ISDIR(
                info.st_mode
            ):
                continue
            plans.append(
                (
                    child,
                    protected_rels if name == protected_child else (),
                )
            )

        with ExitStack() as stack:
            prepared = [
                stack.enter_context(
                    prepare_abandoned_report_bundle_cleanup(
                        child,
                        protected_rels=child_rels,
                    )
                )
                for child, child_rels in plans
            ]
            return sum(cleanup.execute() for cleanup in prepared)

    def _record_active_recovery_error(
        self,
        job: Job,
        *,
        expected_status: str,
        error: str,
        log_message: str,
    ) -> bool:
        """Persist an actionable error while deliberately keeping a job active."""

        next_log = [*job.log]
        if not next_log or next_log[-1] != log_message:
            next_log.append(log_message)
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET updated_at = ?, error = ?, log = ?,"
                " swings_done = ?, swings_total = ?"
                " WHERE id = ? AND status = ?",
                (
                    time.time(),
                    error,
                    json.dumps(next_log),
                    job.swings_done,
                    job.swings_total,
                    job.id,
                    expected_status,
                ),
            )
            self._conn.commit()
            if cursor.rowcount != 1:
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job.id,)
                ).fetchone()
                if row is not None:
                    self._copy_persisted_state(job, self._from_row(row))
                return False
        job.status = expected_status
        job.error = error
        job.log = next_log
        return True

    def _cleanup_before_failure(
        self,
        job: Job,
        *,
        expected_status: str,
        error: str,
    ) -> bool:
        """Reclaim exact owned report graphs before exposing terminal failure.

        A refusal is not treated as successful cleanup. The active state and
        null publication fields remain recoverable on restart, with a durable
        operator/user-facing explanation instead of an unreachable FAILED row.
        """

        try:
            self._cleanup_retry_report_bundles(job)
        except Exception:
            logger.exception(
                "Report bundle cleanup remains pending for job %s", job.id
            )
            log_message = (
                "Report cleanup is pending; this analysis remains active for "
                "safe recovery and has not exposed a report."
            )
            pending_error = f"{error}\n\n{log_message}"
            self._record_active_recovery_error(
                job,
                expected_status=expected_status,
                error=pending_error,
                log_message=log_message,
            )
            return False

        if expected_status == PROCESSING:
            return self._fail_processing_job(job, error)
        return self._transition_interrupted_job(
            job,
            expected_status=expected_status,
            target_status=FAILED,
            error=error,
        )

    def _finish_processing_failure(self, job: Job, error: str) -> None:
        if not self._cleanup_before_failure(
            job, expected_status=PROCESSING, error=error
        ):
            return
        try:
            self._delete_failed_source_if_configured(job)
        except Exception:
            logger.exception("Failed source cleanup failed for job %s", job.id)
        self._notify_owner(job)

    def _run(self, job: Job, video_path: Path) -> None:
        def log(message: str) -> None:
            job.log.append(message)
            self._save(job)

        def progress(done: int, total: int) -> None:
            job.swings_done = done
            job.swings_total = total
            self._save(job)

        presentation = parse_report_presentation_version(
            job.report_presentation_version
        )
        if not self._mark_processing(job):
            return
        try:
            # Coach-replay Pro gate: projected only from the creation-time snapshot.
            # A later upgrade never rewrites this job (re-film to get the replay),
            # and the skip is stated honestly in the session log.
            locked = self.replay_locked(job)
            if locked:
                log(
                    "Coach replay is a Pro feature — skipped for this analysis. "
                    "Upgrade and re-film to get your swing annotated frame-by-frame."
                )
            self._cleanup_retry_report_bundles(job)
            if presentation is ReportPresentationVersion.GUIDED:
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
                    report_presentation_version=job.report_presentation_version,
                    report_entitlements=job.report_entitlements,
                    guided_html_writer=self._guided_html_writer,
                )
            elif presentation is ReportPresentationVersion.LEGACY:
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
            else:  # pragma: no cover - enum exhaustiveness guard
                assert_never(presentation)
        except (ZeroStrikesError, VideoTooLongError, EventError, FFmpegError) as exc:
            self._finish_processing_failure(job, str(exc))
            self._cleanup_expired()
            return
        except Exception:
            error = "Unexpected error during analysis:\n" + traceback.format_exc(
                limit=3
            )
            # logger.exception carries the full traceback to the process log
            # (and to Sentry when the operator configured it — see app.py).
            logger.exception("Unexpected error during analysis of job %s", job.id)
            self._finish_processing_failure(job, error)
            self._cleanup_expired()
            return

        try:
            self._complete_job(job, result)
        except Exception:
            logger.exception("Report publication validation failed for job %s", job.id)
            publication_error = (
                "Report publication could not be validated; this analysis remains "
                "active for safe recovery and no report was exposed."
            )
            self._record_active_recovery_error(
                job,
                expected_status=PROCESSING,
                error=publication_error,
                log_message=publication_error,
            )
            self._cleanup_expired()
            return

        # Keep the manager's externally visible terminal boundary closed until
        # the additive post-commit notes are durable under this re-entrant lock.
        with self._lock:
            self._write_proof_cycle_artifact(job)
            try:
                self._delete_source_if_configured(job)
            except Exception:
                logger.exception("Completed source cleanup failed for job %s", job.id)
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

        This runs only after the pipeline has written and the manager has
        committed its immutable core report. The bounded ``done`` query now
        contains that row, so it is explicitly removed before evidence builds;
        a re-run can never count itself as prior evidence.
        """

        if not proof_cycle_enabled(self.cfg):
            return
        try:
            prior_jobs: list[Job] = []
            if job.user_id and job.club:
                prior_jobs = [
                    candidate
                    for candidate in self.list_comparable(
                        user_id=job.user_id,
                        club=job.club,
                        through=job.created_at,
                        limit=proof_cycle_history_scan_limit(self.cfg),
                    )
                    if candidate.id != job.id
                ]
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
            try:
                self._append_terminal_log(
                    job,
                    "Proof Cycle check was unavailable; your report is still ready.",
                )
            except Exception:
                logger.exception(
                    "Proof Cycle failure note could not be persisted for job %s",
                    job.id,
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
            self._append_terminal_log(
                job,
                "Original upload deleted after analysis (configured data "
                "minimization) — re-analyzing this clip needs a fresh upload.",
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
            self._append_terminal_log(
                job,
                "Original upload deleted after the failed analysis (configured "
                "data minimization) — fix the clip and upload again to retry.",
            )

    def _result_rel(self, job: Job, path: Path | None, *, label: str) -> str:
        if path is None:
            raise ValueError(f"{label} is required")
        try:
            session_root = job.session_dir.resolve(strict=True)
            resolved = Path(path).resolve(strict=True)
            relative = resolved.relative_to(session_root)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{label} is not contained by the job session") from exc
        if not resolved.is_file():
            raise ValueError(f"{label} is not a file")
        return relative.as_posix()

    @staticmethod
    def _publication_row_matches(
        row: sqlite3.Row | None,
        *,
        report_rel: str,
        report_view_rel: str | None,
        report_manifest_rel: str | None,
        report_checksums_rel: str | None,
        structured_report: bool,
    ) -> bool:
        return bool(
            row is not None
            and row["status"] == DONE
            and row["report_rel"] == report_rel
            and row["report_view_rel"] == report_view_rel
            and row["report_manifest_rel"] == report_manifest_rel
            and row["report_checksums_rel"] == report_checksums_rel
            and bool(row["structured_report"]) is structured_report
        )

    def _complete_job(self, job: Job, result: SessionResult) -> None:
        """Validate core artifacts, then expose every terminal field atomically."""

        presentation = parse_report_presentation_version(
            job.report_presentation_version
        )
        if presentation is ReportPresentationVersion.GUIDED:
            guided = True
        elif presentation is ReportPresentationVersion.LEGACY:
            guided = False
        else:  # pragma: no cover - enum exhaustiveness guard
            assert_never(presentation)
        report_rel = self._result_rel(job, result.report_path, label="report path")
        report_view_rel: str | None = None
        report_manifest_rel: str | None = None
        report_checksums_rel: str | None = None
        if guided:
            if result.structured_report is not True:
                raise ValueError("guided completion requires a structured report")
            report_view_rel = self._result_rel(
                job, result.report_view_path, label="report view path"
            )
            report_manifest_rel = self._result_rel(
                job, result.manifest_path, label="report manifest path"
            )
            report_checksums_rel = self._result_rel(
                job, result.checksums_path, label="report checksums path"
            )
            try:
                job_root = job.session_dir.resolve(strict=True)
                analysis_session = Path(result.session_dir).resolve(strict=True)
                analysis_relative = analysis_session.relative_to(job_root)
                if (
                    len(analysis_relative.parts) != 2
                    or analysis_relative.parts[0] != "out"
                ):
                    raise ValueError("analysis session does not use canonical layout")
                direct_rels = tuple(
                    Path(path).resolve(strict=True)
                    .relative_to(analysis_session)
                    .as_posix()
                    for path in (
                        result.report_path,
                        result.report_view_path,
                        result.manifest_path,
                        result.checksums_path,
                    )
                )
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "structured report paths do not use their direct analysis session"
                ) from exc
            bundle = load_published_bundle(
                analysis_session,
                report_rel=direct_rels[0],
                report_view_rel=direct_rels[1],
                manifest_rel=direct_rels[2],
                checksums_rel=direct_rels[3],
            )
            if bundle.manifest.presentation_version != job.report_presentation_version:
                raise ValueError("published report presentation does not match the job")
        elif result.structured_report:
            raise ValueError("legacy completion cannot publish a structured report")

        entitlements_json = job.report_entitlements.to_json()
        legacy_available_json = ReportEntitlementSnapshot("available").to_json()
        now = time.time()
        committed = False
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT status, report_presentation_version,"
                    " report_entitlements_json FROM jobs WHERE id = ?",
                    (job.id,),
                ).fetchone()
                if row is None or row["status"] != PROCESSING:
                    raise RuntimeError("job is no longer processing")
                persisted_entitlements = (
                    validate_persisted_report_policy(
                        bundle,
                        report_presentation_version=row[
                            "report_presentation_version"
                        ],
                        report_entitlements_json=row["report_entitlements_json"],
                    )
                    if guided
                    else (
                        ReportEntitlementSnapshot("available")
                        if row["report_entitlements_json"] is None
                        else ReportEntitlementSnapshot.from_json(
                            row["report_entitlements_json"]
                        )
                    )
                )
                if (
                    row["report_presentation_version"]
                    != job.report_presentation_version
                    or persisted_entitlements != job.report_entitlements
                ):
                    raise RuntimeError("persisted report policy changed during analysis")
                cursor = self._conn.execute(
                    "UPDATE jobs SET status = ?, updated_at = ?, error = NULL,"
                    " report_rel = ?, report_view_rel = ?, report_manifest_rel = ?,"
                    " report_checksums_rel = ?, structured_report = ?,"
                    " swings_done = ?, swings_total = ?, log = ?"
                    " WHERE id = ? AND status = ?"
                    " AND report_presentation_version = ?"
                    " AND COALESCE(report_entitlements_json, ?) = ?",
                    (
                        DONE,
                        now,
                        report_rel,
                        report_view_rel,
                        report_manifest_rel,
                        report_checksums_rel,
                        int(guided),
                        job.swings_done,
                        job.swings_total,
                        json.dumps(job.log),
                        job.id,
                        PROCESSING,
                        job.report_presentation_version,
                        legacy_available_json,
                        entitlements_json,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("job publication guard did not match")
                try:
                    self._conn.commit()
                    committed = True
                except Exception:
                    if self._conn.in_transaction:
                        self._conn.rollback()
                    persisted = self._conn.execute(
                        "SELECT status, report_rel, report_view_rel,"
                        " report_manifest_rel, report_checksums_rel,"
                        " structured_report FROM jobs WHERE id = ?",
                        (job.id,),
                    ).fetchone()
                    if self._publication_row_matches(
                        persisted,
                        report_rel=report_rel,
                        report_view_rel=report_view_rel,
                        report_manifest_rel=report_manifest_rel,
                        report_checksums_rel=report_checksums_rel,
                        structured_report=guided,
                    ):
                        committed = True
                    else:
                        raise
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

        if not committed:
            raise RuntimeError("job publication did not commit")
        job.status = DONE
        job.error = None
        job.report_rel = report_rel
        job.report_view_rel = report_view_rel
        job.report_manifest_rel = report_manifest_rel
        job.report_checksums_rel = report_checksums_rel
        job.structured_report = guided

    def _append_terminal_log(self, job: Job, message: str) -> bool:
        """Append one post-completion note without reopening terminal state."""

        if job.status not in (DONE, FAILED):
            return False
        if not isinstance(message, str):
            raise TypeError("terminal log message must be text")

        expected_status = job.status
        expected_presentation = job.report_presentation_version
        entitlement_json = job.report_entitlements.to_json()
        legacy_available_json = ReportEntitlementSnapshot("available").to_json()
        next_log: list[str] | None = None
        committed = False
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT log FROM jobs WHERE id = ? AND status = ?"
                    " AND report_presentation_version = ?"
                    " AND COALESCE(report_entitlements_json, ?) = ?",
                    (
                        job.id,
                        expected_status,
                        expected_presentation,
                        legacy_available_json,
                        entitlement_json,
                    ),
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    return False
                persisted_log = json.loads(row["log"])
                if not isinstance(persisted_log, list) or any(
                    not isinstance(item, str) for item in persisted_log
                ):
                    raise ValueError("persisted job log is malformed")
                next_log = [*persisted_log, message]
                next_log_json = json.dumps(next_log)
                cursor = self._conn.execute(
                    "UPDATE jobs SET log = ? WHERE id = ? AND status = ?"
                    " AND report_presentation_version = ?"
                    " AND COALESCE(report_entitlements_json, ?) = ? AND log = ?",
                    (
                        next_log_json,
                        job.id,
                        expected_status,
                        expected_presentation,
                        legacy_available_json,
                        entitlement_json,
                        row["log"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("terminal log guard did not match")
                try:
                    self._conn.commit()
                    committed = True
                except Exception:
                    if self._conn.in_transaction:
                        self._conn.rollback()
                    persisted = self._conn.execute(
                        "SELECT log FROM jobs WHERE id = ? AND status = ?"
                        " AND report_presentation_version = ?"
                        " AND COALESCE(report_entitlements_json, ?) = ?",
                        (
                            job.id,
                            expected_status,
                            expected_presentation,
                            legacy_available_json,
                            entitlement_json,
                        ),
                    ).fetchone()
                    if persisted is not None and persisted["log"] == next_log_json:
                        committed = True
                    else:
                        raise
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

        if not committed or next_log is None:
            raise RuntimeError("terminal log append did not commit")
        job.log = next_log
        return True

    # -- persistence ------------------------------------------------------
    def _save(self, job: Job) -> None:
        with self._lock:
            # notify_email is creation-time intent and notified_at is claimed
            # by _notify_owner's guarded UPDATE, so neither is in the conflict
            # update set — a stale in-memory job can never reopen a claim.
            self._conn.execute(
                "INSERT INTO jobs (id, status, created_at, updated_at, source_name,"
                " hand, angle, club, level, strikes, fast, client_ip, user_id, error,"
                " report_rel, report_presentation_version, report_entitlements_json,"
                " report_view_rel, report_manifest_rel, report_checksums_rel,"
                " structured_report, swings_done, swings_total, log, notify_email)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET status = excluded.status,"
                " updated_at = excluded.updated_at, error = excluded.error,"
                " report_rel = excluded.report_rel, swings_done = excluded.swings_done,"
                " swings_total = excluded.swings_total, log = excluded.log"
                " WHERE jobs.status NOT IN ('done', 'failed')",
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
                    json.dumps(job.strikes) if job.strikes is not None else None,
                    int(job.fast),
                    job.client_ip,
                    job.user_id,
                    job.error,
                    job.report_rel,
                    job.report_presentation_version,
                    job.report_entitlements.to_json(),
                    job.report_view_rel,
                    job.report_manifest_rel,
                    job.report_checksums_rel,
                    int(job.structured_report),
                    job.swings_done,
                    job.swings_total,
                    json.dumps(job.log),
                    int(job.notify_email),
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
            report_presentation_version=row["report_presentation_version"],
            report_entitlements=(
                ReportEntitlementSnapshot("available")
                if row["report_entitlements_json"] is None
                else ReportEntitlementSnapshot.from_json(
                    row["report_entitlements_json"]
                )
            ),
            report_view_rel=row["report_view_rel"],
            report_manifest_rel=row["report_manifest_rel"],
            report_checksums_rel=row["report_checksums_rel"],
            structured_report=bool(row["structured_report"]),
            swings_done=row["swings_done"],
            swings_total=row["swings_total"],
            notify_email=bool(row["notify_email"]),
            notified_at=row["notified_at"],
        )

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
            classification = self._completed_report_classification(row)
            if classification in (_COMPLETION_COACHING, _COMPLETION_CORRUPT):
                counts[0] += 1
            elif classification == _COMPLETION_CAPTURE:
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

    def _transition_interrupted_job(
        self,
        job: Job,
        *,
        expected_status: str,
        target_status: str,
        error: str | None,
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error = ?, log = ?"
                " WHERE id = ? AND status = ?",
                (
                    target_status,
                    time.time(),
                    error,
                    json.dumps(job.log),
                    job.id,
                    expected_status,
                ),
            )
            self._conn.commit()
            if cursor.rowcount != 1:
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job.id,)
                ).fetchone()
                if row is not None:
                    self._copy_persisted_state(job, self._from_row(row))
                return False
        job.status = target_status
        job.error = error
        return True

    def _requeue_interrupted(self) -> None:
        """Re-run jobs that were queued or running when the process died."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY created_at",
                ACTIVE,
            ).fetchall()
        for row in rows:
            job = self._from_row(row)
            try:
                parse_report_presentation_version(job.report_presentation_version)
            except UnsupportedReportPresentationVersion as exc:
                error = str(exc)
                transitioned = self._cleanup_before_failure(
                    job,
                    expected_status=row["status"],
                    error=error,
                )
                if transitioned:
                    self._notify_owner(job)
                continue
            video = self._source_path(job)
            if video is None:
                error = (
                    "The server restarted while this analysis was waiting and "
                    "the uploaded video is gone. Please upload it again."
                )
                transitioned = self._cleanup_before_failure(
                    job,
                    expected_status=row["status"],
                    error=error,
                )
            else:
                job.log.append("Server restarted — analysis re-queued.")
                transitioned = self._transition_interrupted_job(
                    job,
                    expected_status=row["status"],
                    target_status=QUEUED,
                    error=None,
                )
            if not transitioned:
                continue
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
