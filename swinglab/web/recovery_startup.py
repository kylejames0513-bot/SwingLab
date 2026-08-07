"""Fail-closed production composition for the recovery-fence sidecar."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from swinglab.backups.core import BackupError
from swinglab.backups.restore_service import (
    _apply_recovery_chain,
    _preflight_chain_owners,
)
from swinglab.backups.store import (
    RecoveryFenceRemoteStore,
    RecoveryFenceStoreSettings,
)

from .mobile_schema import MOBILE_STATE_SCHEMA_GENERATION, VersionedHMAC
from .recovery_fence_ledger import (
    RecoveryFenceError,
    RecoveryFenceLedger,
    StartupRecoveryDecision,
    StartupRecoveryInputs,
    decide_startup_recovery,
)
from .users import UserStore


@dataclass(frozen=True)
class WebRecoveryComposition:
    ledger: object | None
    decision: StartupRecoveryDecision


def _count(connection: sqlite3.Connection, statement: str) -> int:
    try:
        return int(connection.execute(statement).fetchone()[0])
    except sqlite3.Error as exc:
        raise RecoveryFenceError(
            "The local recovery-fence startup state is unreadable."
        ) from exc


def _accepted_proof(connection: sqlite3.Connection) -> tuple[bool, bool]:
    accepted = connection.execute(
        "SELECT COUNT(*) FROM mobile_recovery_accepted_baselines"
    ).fetchone()[0]
    rows = connection.execute(
        "SELECT phase, record_key, record_hash, head_etag, chain_hmac_key_id"
        " FROM mobile_recovery_baseline_journals WHERE phase = 'accepted'"
    ).fetchall()
    if accepted not in (0, 1) or len(rows) not in (0, 1):
        raise RecoveryFenceError(
            "The accepted recovery baseline state is ambiguous."
        )
    durable_round_trip = bool(
        accepted == 1
        and len(rows) == 1
        and all(rows[0][index] for index in range(1, 5))
    )
    return accepted == 1, durable_round_trip


def _local_counts(connection: sqlite3.Connection) -> tuple[int, int, int]:
    recovery_rows = _count(
        connection,
        "SELECT (SELECT COUNT(*) FROM mobile_recovery_fence_checkpoints)"
        " + (SELECT COUNT(*) FROM mobile_recovery_accepted_baselines)",
    )
    baseline_journals = _count(
        connection, "SELECT COUNT(*) FROM mobile_recovery_baseline_journals"
    )
    nonterminal = _count(
        connection,
        "SELECT (SELECT COUNT(*) FROM mobile_auth_exchange_journals"
        " WHERE phase != 'complete')"
        " + (SELECT COUNT(*) FROM mobile_signout_journals"
        " WHERE phase != 'complete')"
        " + (SELECT COUNT(*) FROM mobile_device_revoke_journals"
        " WHERE phase != 'complete')",
    )
    return recovery_rows, baseline_journals, nonterminal


def compose_web_recovery_fence(
    *,
    users: UserStore,
    sessions_dir: str | Path,
    web_config: Mapping[str, object],
    deployment_environment: str,
    keyring: VersionedHMAC | None,
    injected_ledger: object | None,
    review_lane_active: bool,
    shopify_privacy_webhooks_enabled: bool,
) -> WebRecoveryComposition:
    """Validate/apply the remote chain before workers or routes are built.

    Development keeps an explicit injected ledger as a test seam.  It does not
    grant production authority: staging and production always require the
    locally accepted scratch-proven baseline and validate its remote ancestry.
    """

    if deployment_environment not in {"development", "staging", "production"}:
        raise RecoveryFenceError("A closed deployment environment is required.")
    if not isinstance(users, UserStore):
        raise TypeError("UserStore is required for recovery composition.")
    strict_environment = deployment_environment in {"staging", "production"}
    if strict_environment and injected_ledger is not None:
        raise RecoveryFenceError(
            "Injected recovery-fence ledgers are development-only."
        )
    with users._lock:
        recovery_rows, baseline_journals, nonterminal = _local_counts(users._conn)

    # Explicit fakes remain usable for crash/replay development tests. Real
    # staging and production can never take this shortcut.
    if not strict_environment and injected_ledger is not None:
        return WebRecoveryComposition(
            ledger=injected_ledger,
            decision=StartupRecoveryDecision(
                remote_io_required=False,
                startup_allowed=True,
                reasons=("development_injected_recovery",),
                missing_requirements=(),
            ),
        )

    require_account = web_config.get("require_account", False)
    native_enabled = web_config.get("mobile_native_auth_enabled", False)
    history_reset = web_config.get("history_reset_enabled", False)
    if type(require_account) is not bool or type(native_enabled) is not bool or type(
        history_reset
    ) is not bool:
        raise RecoveryFenceError("A recovery-dependent feature flag is invalid.")
    initial = StartupRecoveryInputs(
        schema_generation=MOBILE_STATE_SCHEMA_GENERATION,
        recovery_fence_row_count=recovery_rows,
        baseline_journal_row_count=baseline_journals,
        nonterminal_revocation_count=nonterminal,
        mobile_native_auth_enabled=bool(
            strict_environment and (native_enabled or review_lane_active)
        ),
        mobile_device_management_enabled=bool(strict_environment and require_account),
        mobile_privacy_enabled=False,
        history_reset_enabled=bool(strict_environment and history_reset),
        shopify_privacy_webhooks_enabled=bool(
            strict_environment and shopify_privacy_webhooks_enabled
        ),
    )
    preliminary = decide_startup_recovery(initial)
    if not preliminary.remote_io_required:
        return WebRecoveryComposition(ledger=injected_ledger, decision=preliminary)

    if keyring is None:
        raise RecoveryFenceError(
            "Recovery-fence startup requires MOBILE_STATE_HMAC_KEYRING."
        )
    with users._lock:
        accepted_baseline, durable_round_trip = _accepted_proof(users._conn)
    if not accepted_baseline or not durable_round_trip:
        raise RecoveryFenceError(
            "Recovery-fence startup requires an accepted scratch-proven baseline."
        )

    try:
        settings = RecoveryFenceStoreSettings.from_env()
        remote = RecoveryFenceRemoteStore(settings)
        ledger = RecoveryFenceLedger(
            remote_store=remote,
            keyring=keyring,
            local_root=Path(sessions_dir),
            db_path=users._db_path,
            checkpoint_mode="writable",
        )
    except (BackupError, RuntimeError, ValueError) as exc:
        raise RecoveryFenceError(
            "Dedicated recovery-fence credentials are unavailable."
        ) from exc

    try:
        snapshot = ledger.load_chain_snapshot()
        records = getattr(snapshot, "records", None)
        if not isinstance(records, tuple) or not records:
            raise RecoveryFenceError("The recovery-fence chain is empty.")
        _preflight_chain_owners(records, {})
        with users._lock:
            checkpoint = users._conn.execute(
                "SELECT head_sequence, lineage_id, baseline_backup_id,"
                " schema_generation, head_record_key, head_record_hash,"
                " chain_hmac_key_id FROM mobile_recovery_fence_checkpoints"
                " WHERE checkpoint_id = 1"
            ).fetchone()
            # Revalidate inside the application lock. The ledger already
            # validates its read, but a concurrent writer must not advance the
            # local checkpoint between remote readback and application.
            RecoveryFenceLedger._require_checkpoint_ancestry(
                checkpoint, records
            )
            checkpoint_sequence = int(checkpoint[0]) if checkpoint is not None else 0
            _apply_recovery_chain(
                users._conn,
                snapshot=snapshot,
                keyring=keyring,
                reconcilers={},
                restored_checkpoint_sequence=checkpoint_sequence,
                now=time.time(),
            )
    except RecoveryFenceError:
        raise
    except (BackupError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise RecoveryFenceError(
            "The current recovery-fence chain could not be applied."
        ) from exc

    final = decide_startup_recovery(
        StartupRecoveryInputs(
            **{
                **initial.__dict__,
                "accepted_baseline": True,
                "dedicated_credentials_available": True,
                "immutable_record_round_trip_verified": durable_round_trip,
                "head_cas_round_trip_verified": durable_round_trip,
                "current_chain_validated": True,
            }
        )
    )
    if not final.startup_allowed:
        raise RecoveryFenceError(
            "Recovery-fence startup requirements remain incomplete: "
            + ", ".join(final.missing_requirements)
        )
    return WebRecoveryComposition(ledger=ledger, decision=final)
