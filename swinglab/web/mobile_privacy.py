"""Purpose-bound native privacy step-up start/exchange (Task 6, first slice).

This module owns the email-code step-up challenge and its single deterministic
token exchange, modeled on :mod:`swinglab.web.mobile_auth`. Export, history
reset, and account deletion (which will consume the minted token) are added in
later slices; nothing here authenticates an ordinary API request.
"""

from __future__ import annotations

import html
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlencode

from . import mailer
from .throttle import KeyedThrottle
from .users import (
    PRIVACY_EXPORT_MAX_DOWNLOAD_BYTES,
    PrivacyExportConflict,
    PrivacyExportReceipt,
    PrivacyExportRejected,
    StepUpChallengeLimit,
    StepUpChallengeRejected,
    StepUpExchangeConflict,
    StepUpTokenGrant,
    UserStore,
)


logger = logging.getLogger("swinglab.web.mobile_privacy")


STEP_UP_WINDOW_SECONDS = 15 * 60
STEP_UP_STARTS_PER_SELECTOR = 5
STEP_UP_STARTS_PER_USER = 5
STEP_UP_STARTS_PER_IP = 20
STEP_UP_FAILED_EXCHANGES_PER_USER = 5
STEP_UP_FAILED_EXCHANGES_PER_IP = 20

_PURPOSE_LABELS = {
    "data_export": "export your data",
    "history_reset": "reset your swing history",
    "account_delete": "delete your account",
}


class MobileStepUpInvalidRequest(ValueError):
    """A step-up request failed public shape validation."""


class MobileStepUpRejected(RuntimeError):
    """A challenge/proof failure with one non-enumerating response."""


class MobileStepUpConflict(RuntimeError):
    """A consumed challenge or idempotency tuple conflicts."""


class MobileStepUpUnavailable(RuntimeError):
    """Step-up dependencies are unavailable."""


class MobileStepUpRateLimited(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many step-up attempts.")
        self.retry_after_seconds = max(1, min(900, int(retry_after_seconds)))


@dataclass(frozen=True)
class MobileStepUpStart:
    challenge_id: str
    expires_at: float


@dataclass(frozen=True)
class MobileStepUpExchange:
    step_up_token: str
    purpose: str
    expires_at: float


def _safe_client_ip(value: str | None) -> str:
    return value if isinstance(value, str) and value else "unavailable-client"


def _email_bodies(
    *, brand_name: str, public_base_url: str, challenge_id: str, purpose: str, code: str
) -> tuple[str, str, str]:
    grouped = code[:4] + "-" + code[4:]
    action = _PURPOSE_LABELS[purpose]
    query = urlencode({"challenge_id": challenge_id, "purpose": purpose})
    link = f"{public_base_url}/app/auth/callback?{query}" if public_base_url else ""
    subject = f"Confirm your request to {action} in {brand_name}"
    link_line = f"{link}\n\n" if link else ""
    text = (
        f"You asked to {action} in {brand_name}.\n\n"
        f"{link_line}"
        f"Enter this code on the device where you started, to confirm: {grouped}\n\n"
        "This code expires in 10 minutes. If you did not request this, ignore"
        " this email and no change is made."
    )
    safe_brand = html.escape(brand_name)
    safe_action = html.escape(action)
    safe_code = html.escape(grouped)
    link_html = (
        f'<p><a href="{html.escape(link, quote=True)}">Open {safe_brand}</a></p>'
        if link
        else ""
    )
    html_body = (
        "<!doctype html><html><body>"
        f"<p>You asked to {safe_action} in {safe_brand}.</p>"
        f"{link_html}"
        "<p>Enter this code on the device where you started, to confirm:"
        f" <strong>{safe_code}</strong></p>"
        "<p>This code expires in 10 minutes. If you did not request this,"
        " ignore this email and no change is made.</p>"
        "</body></html>"
    )
    return subject, text, html_body


class MobileStepUpService:
    """Own purpose-bound step-up challenge delivery and token exchange."""

    def __init__(
        self,
        users: UserStore,
        keyed_throttle: KeyedThrottle | None,
        *,
        enabled: bool,
        public_base_url: str | None,
        brand_name: str,
    ) -> None:
        self._users = users
        self._throttle = keyed_throttle
        self.enabled = bool(enabled)
        self._public_base_url = (public_base_url or "").rstrip("/")
        self._brand_name = brand_name
        self._now: Callable[[], float] = time.time

    def verify_enabled_readiness(self) -> None:
        if not self.enabled:
            return
        if self._throttle is None or self._users._mobile_state_hmac is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )

    def _purge_expired(self, now: float) -> None:
        if self._throttle is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )
        try:
            self._users.purge_expired_mobile_step_up_state(now=now)
            self._throttle.purge_expired(now=now)
        except (sqlite3.Error, RuntimeError) as exc:
            raise MobileStepUpUnavailable(
                "Native privacy step-up maintenance is unavailable."
            ) from exc

    def start(
        self,
        *,
        user_id: str,
        selector: str,
        auth_epoch: int,
        purpose: object,
        code_challenge: object,
        email: str | None,
        client_ip: str | None,
    ) -> MobileStepUpStart:
        if self._throttle is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )
        purpose_value = self._users._validate_step_up_purpose(purpose)
        ip = _safe_client_ip(client_ip)
        now = float(self._now())
        self._purge_expired(now)
        decision = self._throttle.consume_many(
            [
                (
                    "stepup-start-selector",
                    selector,
                    STEP_UP_STARTS_PER_SELECTOR,
                    STEP_UP_WINDOW_SECONDS,
                ),
                (
                    "stepup-start-user",
                    user_id,
                    STEP_UP_STARTS_PER_USER,
                    STEP_UP_WINDOW_SECONDS,
                ),
                (
                    "stepup-start-client-ip",
                    ip,
                    STEP_UP_STARTS_PER_IP,
                    STEP_UP_WINDOW_SECONDS,
                ),
            ],
            now=now,
        )
        if not decision.allowed:
            raise MobileStepUpRateLimited(decision.retry_after_seconds)
        try:
            challenge = self._users.begin_mobile_step_up(
                user_id=user_id,
                selector=selector,
                auth_epoch=auth_epoch,
                purpose=purpose_value,
                code_challenge=code_challenge,
                now=now,
            )
        except StepUpChallengeLimit as exc:
            raise MobileStepUpRateLimited(exc.retry_after_seconds) from exc
        except StepUpChallengeRejected as exc:
            raise MobileStepUpRejected("The step-up request is not eligible.") from exc
        except ValueError as exc:
            raise MobileStepUpInvalidRequest(str(exc)) from exc
        if challenge.send_required and email and mailer.enabled():
            subject, text, html_body = _email_bodies(
                brand_name=self._brand_name,
                public_base_url=self._public_base_url,
                challenge_id=challenge.challenge_id,
                purpose=challenge.purpose,
                code=challenge.email_code,
            )
            try:
                mailer.send(email, subject, text, html_body=html_body)
            except Exception:
                # Delivery state stays invisible to the API; an uncertain
                # provider outcome leaves the one-time challenge valid.
                pass
        return MobileStepUpStart(
            challenge_id=challenge.challenge_id,
            expires_at=challenge.expires_at,
        )

    def _debit_failed_exchange(
        self, *, user_id: str | None, client_ip: str, now: float
    ) -> None:
        if self._throttle is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )
        entries = [
            (
                "stepup-exchange-client-ip",
                client_ip,
                STEP_UP_FAILED_EXCHANGES_PER_IP,
                STEP_UP_WINDOW_SECONDS,
            )
        ]
        if user_id is not None:
            entries.append(
                (
                    "stepup-exchange-user",
                    user_id,
                    STEP_UP_FAILED_EXCHANGES_PER_USER,
                    STEP_UP_WINDOW_SECONDS,
                )
            )
        decision = self._throttle.consume_many(entries, now=now)
        if not decision.allowed:
            raise MobileStepUpRateLimited(decision.retry_after_seconds)

    def exchange(
        self,
        *,
        challenge_id: object,
        email_code: object,
        code_verifier: object,
        idempotency_key: object,
        client_ip: str | None,
    ) -> MobileStepUpExchange:
        if self._throttle is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )
        now = float(self._now())
        self._purge_expired(now)
        ip = _safe_client_ip(client_ip)
        user_id = (
            self._users.step_up_challenge_user_id(challenge_id)
            if isinstance(challenge_id, str)
            else None
        )
        try:
            grant: StepUpTokenGrant = self._users.prepare_mobile_step_up_exchange(
                challenge_id,
                email_code,
                code_verifier,
                idempotency_key,
                now=now,
            )
        except StepUpChallengeRejected as exc:
            self._debit_failed_exchange(user_id=user_id, client_ip=ip, now=now)
            raise MobileStepUpRejected("Invalid step-up challenge.") from exc
        except StepUpExchangeConflict as exc:
            self._debit_failed_exchange(user_id=user_id, client_ip=ip, now=now)
            raise MobileStepUpConflict("The step-up exchange conflicts.") from exc
        except ValueError as exc:
            raise MobileStepUpInvalidRequest(str(exc)) from exc
        return MobileStepUpExchange(
            step_up_token=grant.step_up_token,
            purpose=grant.purpose,
            expires_at=grant.expires_at,
        )


# --------------------------------------------------------------------------
# Native privacy export (Task 6, export slice)
# --------------------------------------------------------------------------

PRIVACY_EXPORT_DIRNAME = ".privacy_exports"
_EXPORT_CHUNK_BYTES = 512 * 1024
_EXPORT_SESSIONS_LIMIT = 500
# Minimal in-process download concurrency (a solid floor for this slice). The
# full durable per-receipt/owner start and byte budgets, crash-recovery
# convergence, and cooperative revocation drain remain deferred (see handoff).
_DOWNLOAD_SLOTS_PER_RECEIPT = 1
_DOWNLOAD_SLOTS_PER_OWNER = 2
_DOWNLOAD_SLOTS_GLOBAL = 4
_DOWNLOAD_RETRY_AFTER_S = 2


class PrivacyExportInvalidRequest(ValueError):
    """A privacy-export request failed public shape validation."""


class PrivacyExportRejectedError(RuntimeError):
    """A non-enumerating rejection of a data-export authorization."""


class PrivacyExportConflictError(RuntimeError):
    """A privacy-export idempotency key conflicts with a different request."""


class PrivacyExportNotFound(RuntimeError):
    """No export receipt is owned by the caller under this ID."""


class PrivacyExportNotReady(RuntimeError):
    """A requested export exists but is still pending or building."""


class PrivacyExportFailedState(RuntimeError):
    def __init__(self, failure_code: str) -> None:
        super().__init__("The export could not be produced.")
        self.failure_code = failure_code


class PrivacyExportExpired(RuntimeError):
    """A previously ready export has passed its download window."""


class PrivacyExportRangeUnsupported(RuntimeError):
    """A ranged download was requested; only whole-archive GET is served."""


class PrivacyExportBusy(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many concurrent exports for this owner.")
        self.retry_after_seconds = max(1, min(3600, int(retry_after_seconds)))


class PrivacyExportOverloaded(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Export downloads are temporarily saturated.")
        self.retry_after_seconds = max(1, min(3600, int(retry_after_seconds)))


class PrivacyExportUnavailable(RuntimeError):
    """Privacy-export dependencies are unavailable."""


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()


class PrivacyExportDownloadGuard:
    """Bounded in-process concurrency for ready-export downloads.

    This is the minimal admission promised for this slice: at most one active
    stream per receipt, two per owner, and four globally, plus ``Range``
    rejection performed by the route. Durable start/byte budgets and crash
    recovery are deferred; see the handoff note.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._per_receipt: dict[str, int] = {}
        self._per_owner: dict[str, int] = {}
        self._global = 0

    def admit(self, *, export_id: str, user_id: str) -> "_DownloadSlot":
        with self._lock:
            if self._per_receipt.get(export_id, 0) >= _DOWNLOAD_SLOTS_PER_RECEIPT:
                raise PrivacyExportBusy(_DOWNLOAD_RETRY_AFTER_S)
            if self._per_owner.get(user_id, 0) >= _DOWNLOAD_SLOTS_PER_OWNER:
                raise PrivacyExportBusy(_DOWNLOAD_RETRY_AFTER_S)
            if self._global >= _DOWNLOAD_SLOTS_GLOBAL:
                raise PrivacyExportOverloaded(_DOWNLOAD_RETRY_AFTER_S)
            self._per_receipt[export_id] = self._per_receipt.get(export_id, 0) + 1
            self._per_owner[user_id] = self._per_owner.get(user_id, 0) + 1
            self._global += 1
        return _DownloadSlot(self, export_id=export_id, user_id=user_id)

    def _release(self, export_id: str, user_id: str) -> None:
        with self._lock:
            receipt_count = self._per_receipt.get(export_id, 0) - 1
            if receipt_count <= 0:
                self._per_receipt.pop(export_id, None)
            else:
                self._per_receipt[export_id] = receipt_count
            owner_count = self._per_owner.get(user_id, 0) - 1
            if owner_count <= 0:
                self._per_owner.pop(user_id, None)
            else:
                self._per_owner[user_id] = owner_count
            self._global = max(0, self._global - 1)


class _DownloadSlot:
    def __init__(
        self, guard: PrivacyExportDownloadGuard, *, export_id: str, user_id: str
    ) -> None:
        self._guard = guard
        self._export_id = export_id
        self._user_id = user_id
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._guard._release(self._export_id, self._user_id)


@dataclass(frozen=True)
class PrivacyExportDownload:
    """One admitted, whole-archive download bound to a live concurrency slot."""

    export_id: str
    byte_size: int
    path: Path
    _slot: _DownloadSlot

    def stream(self) -> Iterator[bytes]:
        try:
            with self.path.open("rb") as handle:
                while True:
                    chunk = handle.read(_EXPORT_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk
        finally:
            self._slot.release()

    def release(self) -> None:
        self._slot.release()


class MobilePrivacyService:
    """Create, report, and stream owner-scoped native privacy exports."""

    def __init__(
        self,
        users: UserStore,
        job_manager,
        download_guard: PrivacyExportDownloadGuard,
        *,
        enabled: bool,
        sessions_dir: str | Path,
    ) -> None:
        self._users = users
        self._jobs = job_manager
        self._guard = download_guard
        self.enabled = bool(enabled)
        self._exports_dir = Path(sessions_dir) / PRIVACY_EXPORT_DIRNAME
        self._now: Callable[[], float] = time.time

    def verify_enabled_readiness(self) -> None:
        if not self.enabled:
            return
        if self._users._mobile_state_hmac is None:
            raise PrivacyExportUnavailable(
                "Native privacy export is not configured."
            )
        self._exports_dir.mkdir(parents=True, exist_ok=True)

    def _artifact_path(self, export_id: str) -> Path:
        return self._exports_dir / f"{export_id}.zip"

    def create_export(
        self,
        *,
        user_id: str,
        selector: str,
        auth_epoch: int,
        step_up_token: object,
        idempotency_key: object,
    ) -> PrivacyExportReceipt:
        try:
            return self._users.create_privacy_export(
                user_id=user_id,
                selector=selector,
                auth_epoch=auth_epoch,
                step_up_token=step_up_token,
                idempotency_key=idempotency_key,
                now=float(self._now()),
            )
        except PrivacyExportRejected as exc:
            raise PrivacyExportRejectedError(
                "Invalid data-export authorization."
            ) from exc
        except PrivacyExportConflict as exc:
            raise PrivacyExportConflictError(
                "The privacy export request conflicts."
            ) from exc
        except ValueError as exc:
            raise PrivacyExportInvalidRequest(str(exc)) from exc
        except (sqlite3.Error, RuntimeError) as exc:
            raise PrivacyExportUnavailable(
                "Native privacy export is temporarily unavailable."
            ) from exc

    def effective_status(
        self, receipt: PrivacyExportReceipt, *, now: float | None = None
    ) -> str:
        observed = float(self._now()) if now is None else float(now)
        if (
            receipt.status == "ready"
            and receipt.expires_at is not None
            and receipt.expires_at <= observed
        ):
            return "expired"
        return receipt.status

    def get_receipt(
        self, export_id: object, *, user_id: str
    ) -> PrivacyExportReceipt:
        receipt = self._users.get_privacy_export_receipt(
            export_id, user_id=user_id
        )
        if receipt is None:
            raise PrivacyExportNotFound("No export exists under this ID.")
        return receipt

    def open_download(
        self,
        export_id: object,
        *,
        user_id: str,
        auth_epoch: int,
    ) -> PrivacyExportDownload:
        receipt = self.get_receipt(export_id, user_id=user_id)
        status = self.effective_status(receipt)
        if status in {"pending", "building"}:
            raise PrivacyExportNotReady("The export is still building.")
        if status == "failed":
            raise PrivacyExportFailedState(receipt.failure_code or "build_failed")
        if status == "expired":
            raise PrivacyExportExpired("The export download window has closed.")
        # Reject closed/reset ownership before opening the descriptor.
        current = self._users.get(receipt.user_id)
        if (
            current is None
            or current.auth_epoch != auth_epoch
            or current.history_epoch != receipt.history_epoch
        ):
            raise PrivacyExportNotFound("No export exists under this ID.")
        path = self._artifact_path(receipt.export_id)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise PrivacyExportFailedState("artifact_missing") from exc
        if (
            receipt.byte_size is None
            or size != receipt.byte_size
            or not 1 <= size <= PRIVACY_EXPORT_MAX_DOWNLOAD_BYTES
        ):
            raise PrivacyExportFailedState("artifact_too_large")
        slot = self._guard.admit(export_id=receipt.export_id, user_id=user_id)
        return PrivacyExportDownload(
            export_id=receipt.export_id,
            byte_size=size,
            path=path,
            _slot=slot,
        )

    # -- builder ----------------------------------------------------------

    def _profile_document(self, user_id: str) -> dict:
        profile = self._users.get_golfer_profile(user_id)
        if profile is None:
            return {}
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
            "created_at": _iso(profile.created_at),
            "updated_at": _iso(profile.updated_at),
        }

    def _sessions_document(self, user_id: str) -> list[dict]:
        jobs = self._jobs.list_recent(_EXPORT_SESSIONS_LIMIT, user_id=user_id)
        summary: list[dict] = []
        for job in jobs:
            summary.append(
                {
                    "id": job.id,
                    "status": job.status,
                    "created_at": _iso(job.created_at),
                    "club": job.club,
                    "hand": job.hand,
                    "angle": job.angle,
                    "level": job.level,
                    "source_name": job.source_name,
                    "swings_done": int(job.swings_done),
                    "swings_total": int(job.swings_total),
                    "failure_code": job.failure_code,
                }
            )
        return summary

    def _write_archive(
        self, receipt: PrivacyExportReceipt, destination: Path
    ) -> None:
        profile = self._profile_document(receipt.user_id)
        sessions = self._sessions_document(receipt.user_id)
        manifest = {
            "schema": "caddieinsight-native-export-v1",
            "export_id": receipt.export_id,
            "generated_at": _iso(float(self._now())),
            "history_epoch": receipt.history_epoch,
            "contents": ["profile.json", "sessions.json"],
            "note": (
                "This archive contains the CaddieInsight data associated with"
                " your account. Additional owner-linked datasets are added as"
                " later features land."
            ),
        }
        # A fixed timestamp keeps archives deterministic and free of local
        # filesystem mtimes; ZIP entries carry only the curated JSON above.
        fixed = (1980, 1, 1, 0, 0, 0)

        def _entry(name: str, payload: object) -> zipfile.ZipInfo:
            info = zipfile.ZipInfo(name, date_time=fixed)
            info.compress_type = zipfile.ZIP_DEFLATED
            return info

        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr(
                _entry("manifest.json", manifest),
                json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            )
            archive.writestr(
                _entry("profile.json", profile),
                json.dumps(profile, sort_keys=True, indent=2) + "\n",
            )
            archive.writestr(
                _entry("sessions.json", sessions),
                json.dumps(sessions, sort_keys=True, indent=2) + "\n",
            )

    def build_leased(
        self, receipt: PrivacyExportReceipt, *, worker_id: str
    ) -> str:
        """Build one leased receipt's ZIP after rechecking owner and epoch.

        Returns the terminal status recorded (``ready`` or ``failed``).
        """

        current = self._users.get(receipt.user_id)
        if current is None:
            self._users.record_privacy_export_failed(
                receipt.export_id,
                worker_id=worker_id,
                failure_code="owner_changed",
                now=float(self._now()),
            )
            return "failed"
        if current.history_epoch != receipt.history_epoch:
            self._users.record_privacy_export_failed(
                receipt.export_id,
                worker_id=worker_id,
                failure_code="history_epoch_changed",
                now=float(self._now()),
            )
            return "failed"
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        final_path = self._artifact_path(receipt.export_id)
        staging = final_path.with_name(
            f".{receipt.export_id}.{secrets.token_hex(8)}.partial"
        )
        try:
            self._write_archive(receipt, staging)
            size = staging.stat().st_size
            if not 1 <= size <= PRIVACY_EXPORT_MAX_DOWNLOAD_BYTES:
                staging.unlink(missing_ok=True)
                self._users.record_privacy_export_failed(
                    receipt.export_id,
                    worker_id=worker_id,
                    failure_code="artifact_too_large",
                    now=float(self._now()),
                )
                return "failed"
            # Recheck epoch immediately before atomic publication.
            latest = self._users.get(receipt.user_id)
            if latest is None or latest.history_epoch != receipt.history_epoch:
                staging.unlink(missing_ok=True)
                self._users.record_privacy_export_failed(
                    receipt.export_id,
                    worker_id=worker_id,
                    failure_code="history_epoch_changed",
                    now=float(self._now()),
                )
                return "failed"
            os.replace(staging, final_path)
            published = self._users.record_privacy_export_ready(
                receipt.export_id,
                worker_id=worker_id,
                byte_size=size,
                now=float(self._now()),
            )
            if not published:
                # Lost the lease (crash reclaim/rebuild); drop the orphan file.
                final_path.unlink(missing_ok=True)
                return "failed"
            return "ready"
        except Exception:
            logger.exception(
                "Privacy export build failed export_id=%s", receipt.export_id
            )
            staging.unlink(missing_ok=True)
            self._users.record_privacy_export_failed(
                receipt.export_id,
                worker_id=worker_id,
                failure_code="build_failed",
                now=float(self._now()),
            )
            return "failed"


class PrivacyExportWorker:
    """Lease pending export receipts and build their archives in-process.

    ``start_background_workers=False`` simply never calls :meth:`start`, so
    tests drive :meth:`drain_once` directly. Startup reclaim of stale leases is
    handled by :meth:`lease_pending_privacy_export`, which also picks up
    ``building`` rows whose lease has expired.
    """

    def __init__(
        self,
        service: MobilePrivacyService,
        users: UserStore,
        *,
        enabled: bool,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._service = service
        self._users = users
        self.enabled = bool(enabled)
        self._poll_interval = max(0.05, float(poll_interval_seconds))
        self._worker_id = f"privacy-export-{secrets.token_hex(8)}"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def drain_once(self, *, now: float | None = None) -> bool:
        observed = time.time() if now is None else float(now)
        receipt = self._users.lease_pending_privacy_export(
            worker_id=self._worker_id, now=observed
        )
        if receipt is None:
            return False
        self._service.build_leased(receipt, worker_id=self._worker_id)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.drain_once()
            except Exception:
                logger.exception("Privacy export worker iteration failed.")
                processed = False
            if not processed:
                self._stop.wait(self._poll_interval)

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="privacy-export-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            self._thread = None
