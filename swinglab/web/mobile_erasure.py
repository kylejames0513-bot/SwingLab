"""Durable swing-history reset and account deletion (Task 6, erasure slice).

Both operations are journals first and endpoints second. A request opens (or
resumes) one durable journal, then this module drives that journal forward one
phase at a time. Every phase is idempotent, so a crash, a lost response, or a
concurrent retry converges on the same outcome instead of half-erasing an
account. The native routes and the browser confirmation page are two callers of
the same authority; neither reaches :class:`~swinglab.web.jobs.JobManager`
directly.

Phases, and what each transition has already finished:

``history_reset``
    ``prepared`` → old-epoch privacy exports cancelled/purged →
    ``exports_quiescing`` → recovery-fence ``history_reset`` record published →
    ``erasure_recorded`` → uploads discarded and local swing history deleted →
    ``local_erased`` → non-owner receipt written → ``complete``.

``account_delete``
    ``prepared`` → upload reservations discarded → ``analysis_quiescing`` →
    no owned analysis is queued or processing → ``jobs_closed`` → old-epoch
    privacy-export receipts cancelled/purged → ``files_quarantined`` →
    recovery-fence ``account_delete`` record published → ``erasure_recorded`` →
    local swing history erased and identity/private rows deleted →
    ``identity_deleted`` → non-PII receipt written → ``complete``.

The recovery fence is published *before* the local erase so a later restore of
an older backup can never resurrect erased history: the chain replay re-applies
the erasure. An unavailable fence therefore fails closed rather than erasing
locally, exactly like the other fenced credential operations.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .jobs import (
    HistoryResetConflict,
    HistoryResetError,
)
from .recovery_fence_ledger import (
    AccountDeleteEvent,
    HistoryResetEvent,
    RecoveryFenceError,
)
from .users import shopify_remote_privacy_lock
from .users import (
    HistoryAuthEpochError,
    HistoryEpochError,
    HistoryPrivacyExportConflict,
    PrivacyErasureBusy,
    PrivacyErasureConflict,
    PrivacyErasureEpochConflict,
    PrivacyErasureJournal,
    PrivacyErasureRejected,
    UserStore,
)


logger = logging.getLogger("swinglab.web.mobile_erasure")


ERASURE_RETRY_AFTER_S = 3
_RECORD_HASH = re.compile(r"[0-9a-f]{64}")


class PrivacyErasureInvalidRequest(ValueError):
    """An erasure request failed public shape validation."""


class PrivacyErasureRejectedError(RuntimeError):
    """One non-enumerating rejection of an erasure authorization."""


class PrivacyErasureConflictError(RuntimeError):
    """An idempotency key conflicts with a different erasure request."""


class PrivacyErasureEpochConflictError(RuntimeError):
    """Swing history changed before this reset was authorized."""


class PrivacyErasureBusyError(RuntimeError):
    """Owned work must finish before this erasure can be accepted."""


class PrivacyErasureUnavailable(RuntimeError):
    """Erasure dependencies (keyring, fence, journal) are unavailable."""


@dataclass(frozen=True)
class ErasureOutcome:
    """What the caller should report for one drive of an erasure journal."""

    kind: str
    operation_id: str
    complete: bool
    retry_after_seconds: int = 0
    deleted_jobs: int = 0
    cleanup_pending: bool = False
    history_epoch: int | None = None
    replayed: bool = False


class _OperationLocks:
    """Serialize concurrent drives of one operation without a global lock."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, int]] = {}

    def hold(self, operation_id: str):
        return _HeldOperationLock(self, operation_id)

    def _acquire(self, operation_id: str) -> threading.Lock:
        with self._guard:
            lock, holders = self._locks.get(
                operation_id, (threading.Lock(), 0)
            )
            self._locks[operation_id] = (lock, holders + 1)
        return lock

    def _release(self, operation_id: str) -> None:
        with self._guard:
            entry = self._locks.get(operation_id)
            if entry is None:
                return
            lock, holders = entry
            if holders <= 1:
                self._locks.pop(operation_id, None)
            else:
                self._locks[operation_id] = (lock, holders - 1)


class _HeldOperationLock:
    def __init__(self, locks: _OperationLocks, operation_id: str) -> None:
        self._locks = locks
        self._operation_id = operation_id
        self._lock: threading.Lock | None = None

    def __enter__(self) -> None:
        self._lock = self._locks._acquire(self._operation_id)
        self._lock.acquire()

    def __exit__(self, *_exc) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None
        self._locks._release(self._operation_id)


class PrivacyErasureService:
    """Drive the durable history-reset and account-deletion journals."""

    def __init__(
        self,
        users: UserStore,
        job_manager,
        privacy_export_service,
        *,
        enabled: bool,
        recovery_fence_ledger,
        upload_manager_provider: Callable[[], object | None],
    ) -> None:
        self._users = users
        self._jobs = job_manager
        self._exports = privacy_export_service
        self.enabled = bool(enabled)
        self._ledger = recovery_fence_ledger
        self._upload_manager_provider = upload_manager_provider
        self._locks = _OperationLocks()
        self._now: Callable[[], float] = time.time

    # -- readiness --------------------------------------------------------

    @property
    def journaled(self) -> bool:
        """Whether a durable, recovery-fenced journal can be opened.

        The browser reset predates the mobile-state keyring and the recovery
        fence. Where neither is configured (development and the legacy
        cookie-only tests) the browser keeps its original unjournaled reset;
        production composition already requires the fence whenever
        ``history_reset_enabled`` or ``mobile_privacy_enabled`` is live.
        """

        return (
            self._users._mobile_state_hmac is not None
            and self._ledger is not None
        )

    def verify_enabled_readiness(self) -> None:
        if not self.enabled:
            return
        if not self.journaled:
            raise PrivacyErasureUnavailable(
                "Native privacy erasure requires a recovery-fenced keyring."
            )

    def _require_journal(self) -> None:
        if not self.journaled:
            # Configuration drift, not a transient outage: never start an
            # erasure this process has no durable path to finish.
            raise PrivacyErasureUnavailable(
                "Recovery-fenced privacy erasure is not configured."
            )

    # -- pre-authentication replay ----------------------------------------

    def find_replay(
        self,
        kind: str,
        idempotency_key: object,
        *,
        expected_history_epoch: int | None = None,
        origin: str = "native",
    ) -> ErasureOutcome | None:
        """Resolve a durable replay before any credential is required.

        Account deletion revokes every credential while the journal is still
        running, so the only way a client can learn the fate of a lost 202/204
        is possession of the exact 128-bit idempotency key.
        """

        if not self.journaled:
            return None
        try:
            journal = self._users.find_privacy_erasure_operation(
                kind,
                idempotency_key,
                expected_history_epoch=expected_history_epoch,
                origin=origin,
            )
        except PrivacyErasureConflict as exc:
            raise PrivacyErasureConflictError(
                "The erasure request conflicts."
            ) from exc
        except ValueError as exc:
            raise PrivacyErasureInvalidRequest(str(exc)) from exc
        except (sqlite3.Error, RuntimeError) as exc:
            raise PrivacyErasureUnavailable(
                "Privacy erasure is temporarily unavailable."
            ) from exc
        if journal is None:
            return None
        if journal.complete:
            return ErasureOutcome(
                kind=kind,
                operation_id=journal.operation_id,
                complete=True,
                deleted_jobs=journal.deleted_jobs,
                history_epoch=journal.expected_history_epoch or None,
                replayed=True,
            )
        return self._drive(journal, replayed=True)

    # -- history reset ----------------------------------------------------

    def reset_history(
        self,
        *,
        user_id: str,
        selector: str | None,
        auth_epoch: int,
        expected_history_epoch: object,
        idempotency_key: object,
        step_up_token: object = None,
        origin: str = "native",
    ) -> ErasureOutcome:
        if origin == "browser" and not self.journaled:
            return self._unjournaled_browser_reset(
                user_id=user_id,
                auth_epoch=auth_epoch,
                expected_history_epoch=expected_history_epoch,
            )
        self._require_journal()
        try:
            journal = self._users.begin_history_reset(
                user_id=user_id,
                selector=selector,
                auth_epoch=auth_epoch,
                expected_history_epoch=expected_history_epoch,
                idempotency_key=idempotency_key,
                step_up_token=step_up_token,
                origin=origin,
                now=float(self._now()),
            )
        except PrivacyErasureRejected as exc:
            raise PrivacyErasureRejectedError(
                "Invalid history-reset authorization."
            ) from exc
        except PrivacyErasureEpochConflict as exc:
            raise PrivacyErasureEpochConflictError(str(exc)) from exc
        except PrivacyErasureBusy as exc:
            raise PrivacyErasureBusyError(str(exc)) from exc
        except PrivacyErasureConflict as exc:
            raise PrivacyErasureConflictError(
                "The erasure request conflicts."
            ) from exc
        except ValueError as exc:
            raise PrivacyErasureInvalidRequest(str(exc)) from exc
        except (sqlite3.Error, RuntimeError) as exc:
            raise PrivacyErasureUnavailable(
                "Privacy erasure is temporarily unavailable."
            ) from exc
        if journal.complete:
            return ErasureOutcome(
                kind="history_reset",
                operation_id=journal.operation_id,
                complete=True,
                deleted_jobs=journal.deleted_jobs,
                history_epoch=journal.expected_history_epoch or None,
                replayed=journal.replayed,
            )
        return self._drive(journal, replayed=journal.replayed)

    def _unjournaled_browser_reset(
        self,
        *,
        user_id: str,
        auth_epoch: int,
        expected_history_epoch: object,
    ) -> ErasureOutcome:
        """The pre-fence browser reset, kept for unfenced deployments.

        This is the exact algorithm the confirmation page shipped with. It runs
        only where no mobile-state keyring and no recovery fence exist, so no
        durable journal or fence record is possible in the first place.
        """

        summary = self._local_history_erase(
            user_id=user_id,
            expected_auth_epoch=int(auth_epoch),
            expected_history_epoch=(
                None
                if expected_history_epoch is None
                else int(expected_history_epoch)
            ),
        )
        current = self._users.get(user_id)
        return ErasureOutcome(
            kind="history_reset",
            operation_id="",
            complete=True,
            deleted_jobs=summary.deleted_jobs,
            cleanup_pending=bool(summary.cleanup_pending),
            history_epoch=current.history_epoch if current else None,
        )

    # -- account deletion -------------------------------------------------

    def delete_account(
        self,
        *,
        user_id: str,
        selector: str | None,
        auth_epoch: int,
        idempotency_key: object,
        step_up_token: object,
    ) -> ErasureOutcome:
        self._require_journal()
        try:
            journal = self._users.begin_account_delete(
                user_id=user_id,
                selector=selector,
                auth_epoch=auth_epoch,
                idempotency_key=idempotency_key,
                step_up_token=step_up_token,
                now=float(self._now()),
            )
        except PrivacyErasureRejected as exc:
            raise PrivacyErasureRejectedError(
                "Invalid account-deletion authorization."
            ) from exc
        except PrivacyErasureBusy as exc:
            raise PrivacyErasureBusyError(str(exc)) from exc
        except PrivacyErasureConflict as exc:
            raise PrivacyErasureConflictError(
                "The erasure request conflicts."
            ) from exc
        except ValueError as exc:
            raise PrivacyErasureInvalidRequest(str(exc)) from exc
        except (sqlite3.Error, RuntimeError) as exc:
            raise PrivacyErasureUnavailable(
                "Privacy erasure is temporarily unavailable."
            ) from exc
        if journal.complete:
            return ErasureOutcome(
                kind="account_delete",
                operation_id=journal.operation_id,
                complete=True,
                replayed=journal.replayed,
            )
        return self._drive(journal, replayed=journal.replayed)

    # -- crash recovery ---------------------------------------------------

    def resume_nonterminal(self) -> None:
        """Finish journals interrupted by a crash before serving requests."""

        if not self.journaled:
            return
        for kind in ("history_reset", "account_delete"):
            try:
                pending = self._users.nonterminal_privacy_erasure_operations(
                    kind
                )
            except sqlite3.Error:
                logger.exception("Erasure journals are unreadable at startup.")
                return
            for journal in pending:
                try:
                    self._drive(journal, replayed=True)
                except Exception:
                    # A journal that cannot finish yet stays durable and is
                    # retried by the owner's next request or the next boot.
                    logger.warning(
                        "Privacy erasure remains pending kind=%s phase=%s",
                        journal.kind,
                        journal.phase,
                    )

    # -- journal driver ---------------------------------------------------

    def _drive(
        self, journal: PrivacyErasureJournal, *, replayed: bool
    ) -> ErasureOutcome:
        with self._locks.hold(journal.operation_id):
            if journal.kind == "history_reset":
                return self._drive_history_reset(journal, replayed=replayed)
            return self._drive_account_delete(journal, replayed=replayed)

    def _pending(
        self, journal: PrivacyErasureJournal, *, replayed: bool
    ) -> ErasureOutcome:
        return ErasureOutcome(
            kind=journal.kind,
            operation_id=journal.operation_id,
            complete=False,
            retry_after_seconds=ERASURE_RETRY_AFTER_S,
            deleted_jobs=journal.deleted_jobs,
            cleanup_pending=journal.cleanup_pending,
            replayed=replayed,
        )

    def _reload(
        self, kind: str, operation_id: str
    ) -> PrivacyErasureJournal | None:
        return self._users.privacy_erasure_operation(kind, operation_id)

    def _publish(self, event) -> tuple[int, str]:
        published = self._ledger.append_and_publish(event)
        sequence = int(published.sequence)
        record_hash = str(published.record_hash)
        if sequence < 1 or _RECORD_HASH.fullmatch(record_hash) is None:
            raise RuntimeError(
                "Recovery-fence publication returned invalid readback."
            )
        return sequence, record_hash

    def _drive_history_reset(
        self, journal: PrivacyErasureJournal, *, replayed: bool
    ) -> ErasureOutcome:
        operation_id = journal.operation_id
        current: PrivacyErasureJournal | None = journal
        while current is not None:
            phase = current.phase
            if phase == "complete":
                owner = self._users.get(current.user_id)
                return ErasureOutcome(
                    kind="history_reset",
                    operation_id=operation_id,
                    complete=True,
                    deleted_jobs=current.deleted_jobs,
                    cleanup_pending=current.cleanup_pending,
                    history_epoch=owner.history_epoch if owner else None,
                    replayed=replayed,
                )
            if phase == "prepared":
                if not self._quiesce_owner_exports(current.user_id):
                    return self._pending(current, replayed=replayed)
                self._users.advance_privacy_erasure_phase(
                    "history_reset",
                    operation_id,
                    "prepared",
                    "exports_quiescing",
                )
            elif phase == "exports_quiescing":
                # The journal's own digest is republished verbatim so a crash
                # between phases cannot re-key the fenced owner identity.
                event = HistoryResetEvent(
                    event_id=operation_id,
                    cutoff_at=current.created_at,
                    stable_user_hmac_key_id=self._erasure_hmac(
                        current, "stable_user_hmac_key_id"
                    ),
                    stable_user_hmac=self._erasure_hmac(
                        current, "stable_user_hmac"
                    ),
                    erased_through_history_epoch=(
                        current.expected_history_epoch + 1
                    ),
                )
                try:
                    sequence, record_hash = self._publish(event)
                except RecoveryFenceError:
                    return self._pending(current, replayed=replayed)
                self._users.advance_privacy_erasure_phase(
                    "history_reset",
                    operation_id,
                    "exports_quiescing",
                    "erasure_recorded",
                    recovery_sequence=sequence,
                    recovery_record_hash=record_hash,
                )
            elif phase == "erasure_recorded":
                owner = self._users.get(current.user_id)
                if owner is None:
                    raise PrivacyErasureUnavailable(
                        "The account disappeared mid-reset."
                    )
                if owner.history_epoch > current.expected_history_epoch:
                    # A concurrent reset already advanced the epoch this
                    # journal fenced; the erasure it records is satisfied.
                    self._users.advance_privacy_erasure_phase(
                        "history_reset",
                        operation_id,
                        "erasure_recorded",
                        "local_erased",
                        deleted_jobs=0,
                        cleanup_pending=False,
                    )
                    current = self._reload("history_reset", operation_id)
                    continue
                try:
                    summary = self._local_history_erase(
                        user_id=current.user_id,
                        expected_auth_epoch=None,
                        expected_history_epoch=current.expected_history_epoch,
                    )
                except HistoryResetConflict as exc:
                    raise PrivacyErasureBusyError(
                        "An analysis is still uploading or processing."
                    ) from exc
                except HistoryPrivacyExportConflict as exc:
                    raise PrivacyErasureBusyError(
                        "A requested privacy export still contains this history."
                    ) from exc
                except HistoryEpochError as exc:
                    raise PrivacyErasureEpochConflictError(
                        "Swing history changed before this reset was authorized."
                    ) from exc
                except HistoryResetError as exc:
                    cause = exc.__cause__
                    if isinstance(cause, HistoryPrivacyExportConflict):
                        raise PrivacyErasureBusyError(
                            "A requested privacy export still contains this"
                            " history."
                        ) from exc
                    if isinstance(cause, HistoryAuthEpochError):
                        raise PrivacyErasureRejectedError(
                            "Invalid history-reset authorization."
                        ) from exc
                    if isinstance(cause, HistoryEpochError):
                        raise PrivacyErasureEpochConflictError(
                            "Swing history changed before this reset was"
                            " authorized."
                        ) from exc
                    logger.warning(
                        "History reset stays pending operation_id=%s",
                        operation_id,
                    )
                    return self._pending(current, replayed=replayed)
                self._users.advance_privacy_erasure_phase(
                    "history_reset",
                    operation_id,
                    "erasure_recorded",
                    "local_erased",
                    deleted_jobs=int(summary.deleted_jobs),
                    cleanup_pending=bool(summary.cleanup_pending),
                )
            elif phase == "local_erased":
                # The archive of an erased epoch must not stay downloadable
                # even if a lagging worker published it mid-reset.
                self._quiesce_owner_exports(current.user_id)
                self._users.complete_history_reset(operation_id)
            else:
                raise PrivacyErasureUnavailable(
                    "A history-reset journal phase is invalid."
                )
            current = self._reload("history_reset", operation_id)
        raise PrivacyErasureUnavailable("A history-reset journal disappeared.")

    def _drive_account_delete(
        self, journal: PrivacyErasureJournal, *, replayed: bool
    ) -> ErasureOutcome:
        operation_id = journal.operation_id
        current: PrivacyErasureJournal | None = journal
        while True:
            if current is None:
                # ``complete_account_delete`` removes the journal and leaves
                # only the non-PII receipt behind.
                return ErasureOutcome(
                    kind="account_delete",
                    operation_id=operation_id,
                    complete=True,
                    replayed=replayed,
                )
            phase = current.phase
            if phase == "complete":
                return ErasureOutcome(
                    kind="account_delete",
                    operation_id=operation_id,
                    complete=True,
                    replayed=replayed,
                )
            if phase == "prepared":
                self._discard_uploads(current.user_id)
                self._users.advance_privacy_erasure_phase(
                    "account_delete",
                    operation_id,
                    "prepared",
                    "analysis_quiescing",
                )
            elif phase == "analysis_quiescing":
                if self._users.active_owned_job_ids(current.user_id):
                    # An in-flight analysis is never destroyed underneath its
                    # own worker; deletion waits and the client retries.
                    return self._pending(current, replayed=replayed)
                self._users.advance_privacy_erasure_phase(
                    "account_delete",
                    operation_id,
                    "analysis_quiescing",
                    "jobs_closed",
                )
            elif phase == "jobs_closed":
                # Export receipts are cancelled before the fence so a lagging
                # worker cannot publish a ready ZIP for an account that is about
                # to be erased. Session/history destruction waits until after
                # ``erasure_recorded`` so a fence outage never half-erases.
                if self._users.get(current.user_id) is not None:
                    if not self._quiesce_owner_exports(current.user_id):
                        return self._pending(current, replayed=replayed)
                self._users.advance_privacy_erasure_phase(
                    "account_delete",
                    operation_id,
                    "jobs_closed",
                    "files_quarantined",
                )
            elif phase == "files_quarantined":
                owner = self._users.get(current.user_id)
                event = AccountDeleteEvent(
                    event_id=operation_id,
                    cutoff_at=current.created_at,
                    stable_user_hmac_key_id=(
                        self._erasure_hmac(current, "stable_user_hmac_key_id")
                    ),
                    stable_user_hmac=(
                        self._erasure_hmac(current, "stable_user_hmac")
                    ),
                    normalized_email_hmac_key_id=(
                        self._erasure_hmac(
                            current, "normalized_email_hmac_key_id"
                        )
                    ),
                    normalized_email_hmac=(
                        self._erasure_hmac(current, "normalized_email_hmac")
                    ),
                    erased_through_history_epoch=(
                        int(owner.history_epoch)
                        if owner is not None
                        else int(current.expected_history_epoch or 0)
                    ),
                )
                try:
                    sequence, record_hash = self._publish(event)
                except RecoveryFenceError:
                    return self._pending(current, replayed=replayed)
                self._users.advance_privacy_erasure_phase(
                    "account_delete",
                    operation_id,
                    "files_quarantined",
                    "erasure_recorded",
                    recovery_sequence=sequence,
                    recovery_record_hash=record_hash,
                )
            elif phase == "erasure_recorded":
                owner = self._users.get(current.user_id)
                if owner is not None:
                    try:
                        self._local_history_erase(
                            user_id=current.user_id,
                            expected_auth_epoch=None,
                            expected_history_epoch=None,
                        )
                    except (
                        HistoryResetConflict,
                        HistoryResetError,
                        HistoryEpochError,
                        HistoryPrivacyExportConflict,
                    ):
                        logger.warning(
                            "Account deletion stays pending operation_id=%s",
                            operation_id,
                        )
                        return self._pending(current, replayed=replayed)
                    # Exports may have been republished by a lagging worker
                    # between the pre-fence quiesce and this post-fence erase.
                    self._quiesce_owner_exports(current.user_id)
                    self._users.delete_account_identity(current.user_id)
                self._users.advance_privacy_erasure_phase(
                    "account_delete",
                    operation_id,
                    "erasure_recorded",
                    "identity_deleted",
                )
            elif phase == "identity_deleted":
                self._users.complete_account_delete(operation_id)
            else:
                raise PrivacyErasureUnavailable(
                    "An account-deletion journal phase is invalid."
                )
            current = self._reload("account_delete", operation_id)

    def _erasure_hmac(
        self, journal: PrivacyErasureJournal, column: str
    ) -> str:
        value = self._users.privacy_erasure_hmac(
            journal.kind, journal.operation_id, column
        )
        if not value:
            raise PrivacyErasureUnavailable(
                "An erasure journal is missing its owner digest."
            )
        return value

    # -- local effects ----------------------------------------------------

    def _discard_uploads(self, user_id: str) -> None:
        manager = self._upload_manager_provider()
        if manager is None:
            return
        try:
            manager.discard_for_user(user_id)
        except Exception:
            # Reservations are ephemeral; a discard failure must not block a
            # durable erasure. The next phase deletes their bound sessions.
            logger.warning("Upload reservations remain for one erasure owner.")

    def _local_history_erase(
        self,
        *,
        user_id: str,
        expected_auth_epoch: int | None,
        expected_history_epoch: int | None,
    ):
        """Discard reservations, then erase jobs and related rows atomically."""

        with shopify_remote_privacy_lock(self._users._db_path):
            self._discard_uploads(user_id)
            return self._jobs.reset_user_history(
                user_id,
                delete_related=lambda connection, owner: (
                    self._users.delete_swing_history_related(
                        connection,
                        owner,
                        expected_auth_epoch=expected_auth_epoch,
                        expected_history_epoch=expected_history_epoch,
                    )
                ),
            )

    def _quiesce_owner_exports(self, user_id: str) -> bool:
        """Purge an owner's export receipts and unlink unreachable archives.

        Receipt rows are deleted first so a leased worker can neither publish a
        ready archive nor keep a downloadable receipt for an epoch that is about
        to be erased; its ``os.replace`` then leaves an orphan that the archive
        sweep removes.
        """

        try:
            export_ids = self._users.purge_privacy_exports_for_user(user_id)
        except sqlite3.Error:
            logger.exception("Privacy export receipts could not be purged.")
            return False
        if self._exports is not None:
            self._exports.unlink_export_artifacts(export_ids)
            self._exports.sweep_orphan_export_artifacts()
        return True
