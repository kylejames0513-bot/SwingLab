"""Off-volume recovery-fence records and the offline cutover state machine.

This module is deliberately independent of web application composition.  Gate
3B defines and verifies the monotonic contract; a later composition gate decides
when an accepted baseline is available and when startup must invoke the store.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from swinglab.backups.core import BackupError, validate_backup_id
from swinglab.backups.store import (
    RecoveryFenceCASConflict,
    RecoveryFenceStoreError,
)
from swinglab.web.mobile_schema import (
    HMACDigest,
    MobileStateDomain,
    VersionedHMAC,
)


RECOVERY_FENCE_RECORD_FORMAT = "caddieinsight-recovery-fence-record/v1"
RECOVERY_FENCE_HEAD_FORMAT = "caddieinsight-recovery-fence-head/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_REMOTE_RECORD_KEY = re.compile(
    r"(?:^|/)records/([1-9][0-9]*)-([0-9a-f]{64})\.json"
)
_PHASES = (
    "lineage_prepared",
    "backup_verified",
    "record_published",
    "head_published",
    "scratch_verified",
    "accepted",
)
_PHASE_INDEX = {phase: index for index, phase in enumerate(_PHASES)}


class RecoveryFenceError(RuntimeError):
    """A safe fail-closed recovery-fence contract error."""


class RecoveryRecordKind(str, Enum):
    CUTOVER_BASELINE = "cutover_baseline"
    TOKEN_REVOKE = "token_revoke"
    PUSH_ENVIRONMENT_CUTOFF = "push_environment_cutoff"
    REVIEW_ACCESS_REVISION = "review_access_revision"


def _canonical_json(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RecoveryFenceError("Recovery-fence data is not canonical JSON.") from exc
    return (encoded + "\n").encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryFenceError("Recovery-fence JSON contains a duplicate key.")
        result[key] = value
    return result


def _strict_json(body: bytes, *, label: str) -> dict[str, Any]:
    if not isinstance(body, bytes) or not body:
        raise RecoveryFenceError(f"The recovery-fence {label} body is invalid.")
    try:
        text = body.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except RecoveryFenceError:
        raise
    except (UnicodeDecodeError, ValueError):
        raise RecoveryFenceError(
            f"The recovery-fence {label} is not valid canonical JSON."
        ) from None
    if not isinstance(value, dict) or _canonical_json(value) != body:
        raise RecoveryFenceError(
            f"The recovery-fence {label} encoding is not canonical."
        )
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise RecoveryFenceError(f"The recovery-fence {label} shape is invalid.")


def _valid_uuid(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise RecoveryFenceError(f"The recovery-fence {label} is invalid.")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise RecoveryFenceError(f"The recovery-fence {label} is invalid.") from None
    if str(parsed) != value:
        raise RecoveryFenceError(f"The recovery-fence {label} is not canonical.")
    return value


def _valid_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RecoveryFenceError(f"The recovery-fence {label} is invalid.")
    return value


def _valid_key_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _KEY_ID.fullmatch(value) is None:
        raise RecoveryFenceError(f"The recovery-fence {label} is invalid.")
    return value


def _valid_int(value: object, *, minimum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RecoveryFenceError(f"The recovery-fence {label} is invalid.")
    return value


def _valid_time(value: object, *, nullable: bool = False, label: str) -> float | None:
    if value is None and nullable:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise RecoveryFenceError(f"The recovery-fence {label} is invalid.")
    return float(value)


def _valid_hmac_pair(payload: dict[str, Any], prefix: str) -> None:
    _valid_key_id(payload.get(f"{prefix}_hmac_key_id"), label=f"{prefix} key ID")
    _valid_sha256(payload.get(f"{prefix}_hmac"), label=f"{prefix} HMAC")


@dataclass(frozen=True)
class CutoverBaselineEvent:
    event_id: str
    cutoff_at: float
    lineage_id: str
    minimum_backup_created_at: float
    baseline_backup_id: str
    manifest_sha256: str
    schema_generation: int
    baseline_db_checkpoint: str
    kind: RecoveryRecordKind = RecoveryRecordKind.CUTOVER_BASELINE

    def payload(self) -> dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "minimum_backup_created_at": self.minimum_backup_created_at,
            "baseline_backup_id": self.baseline_backup_id,
            "manifest_sha256": self.manifest_sha256,
            "schema_generation": self.schema_generation,
            "baseline_db_checkpoint": self.baseline_db_checkpoint,
        }


@dataclass(frozen=True)
class TokenRevokeEvent:
    event_id: str
    cutoff_at: float
    selector_hmac_key_id: str
    selector_hmac: str
    token_verifier_hmac_key_id: str
    token_verifier_hmac: str
    kind: RecoveryRecordKind = RecoveryRecordKind.TOKEN_REVOKE

    @classmethod
    def from_raw(
        cls,
        *,
        event_id: str,
        cutoff_at: float,
        selector: bytes | str,
        stored_token_verifier: bytes | str,
        keyring: VersionedHMAC,
    ) -> "TokenRevokeEvent":
        selector_key_id, selector_hmac = keyring.digest(
            MobileStateDomain.RECOVERY_SELECTOR, selector
        )
        verifier_key_id, verifier_hmac = keyring.digest(
            MobileStateDomain.RECOVERY_TOKEN_VERIFIER,
            stored_token_verifier,
        )
        return cls(
            event_id=event_id,
            cutoff_at=cutoff_at,
            selector_hmac_key_id=selector_key_id,
            selector_hmac=selector_hmac,
            token_verifier_hmac_key_id=verifier_key_id,
            token_verifier_hmac=verifier_hmac,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "selector_hmac_key_id": self.selector_hmac_key_id,
            "selector_hmac": self.selector_hmac,
            "token_verifier_hmac_key_id": self.token_verifier_hmac_key_id,
            "token_verifier_hmac": self.token_verifier_hmac,
        }


@dataclass(frozen=True)
class PushEnvironmentCutoffEvent:
    """Reserved Task-7 record shape; Gate 3B performs no provider call."""

    event_id: str
    cutoff_at: float
    deployment_environment: str
    expo_project_hmac_key_id: str
    expo_project_hmac: str
    activation_revision: int
    cutoff_revision: int
    last_provider_started_at: float | None
    last_provider_accepted_at: float | None
    provider_may_accept_until: float | None
    closed_at: float
    cutoff_skew_seconds: float
    provider_safe_after: float
    state: str = "closed"
    kind: RecoveryRecordKind = RecoveryRecordKind.PUSH_ENVIRONMENT_CUTOFF

    def payload(self) -> dict[str, Any]:
        return {
            "deployment_environment": self.deployment_environment,
            "expo_project_hmac_key_id": self.expo_project_hmac_key_id,
            "expo_project_hmac": self.expo_project_hmac,
            "activation_revision": self.activation_revision,
            "cutoff_revision": self.cutoff_revision,
            "last_provider_started_at": self.last_provider_started_at,
            "last_provider_accepted_at": self.last_provider_accepted_at,
            "provider_may_accept_until": self.provider_may_accept_until,
            "closed_at": self.closed_at,
            "cutoff_skew_seconds": self.cutoff_skew_seconds,
            "provider_safe_after": self.provider_safe_after,
            "state": self.state,
        }


@dataclass(frozen=True)
class ReviewAccessRevisionEvent:
    """Reserved Entitlements-Task-5 record shape; no account data is accepted."""

    event_id: str
    cutoff_at: float
    provider: str
    lane_revision: int
    supported_builds: tuple[dict[str, str], ...]
    window_state: str
    purchase_test_state: str
    credential_hmacs: tuple[HMACDigest, ...]
    kind: RecoveryRecordKind = RecoveryRecordKind.REVIEW_ACCESS_REVISION

    def payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "lane_revision": self.lane_revision,
            "supported_builds": [dict(row) for row in self.supported_builds],
            "window_state": self.window_state,
            "purchase_test_state": self.purchase_test_state,
            "credential_hmacs": [
                {"key_id": item.key_id, "digest": item.digest}
                for item in self.credential_hmacs
            ],
        }


RecoveryFenceEvent = (
    CutoverBaselineEvent
    | TokenRevokeEvent
    | PushEnvironmentCutoffEvent
    | ReviewAccessRevisionEvent
)


def _validated_payload(kind: str, payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RecoveryFenceError("The recovery-fence record payload is invalid.")
    if kind == RecoveryRecordKind.CUTOVER_BASELINE.value:
        expected = {
            "lineage_id",
            "minimum_backup_created_at",
            "baseline_backup_id",
            "manifest_sha256",
            "schema_generation",
            "baseline_db_checkpoint",
        }
        _exact_keys(payload, expected, label="cutover baseline payload")
        _valid_uuid(payload["lineage_id"], label="lineage ID")
        _valid_time(
            payload["minimum_backup_created_at"],
            label="minimum backup creation time",
        )
        try:
            validate_backup_id(payload["baseline_backup_id"])
        except (BackupError, TypeError):
            raise RecoveryFenceError(
                "The recovery-fence baseline backup ID is invalid."
            ) from None
        _valid_sha256(payload["manifest_sha256"], label="manifest SHA-256")
        _valid_int(payload["schema_generation"], minimum=1, label="schema generation")
        _valid_sha256(
            payload["baseline_db_checkpoint"],
            label="baseline database checkpoint",
        )
    elif kind == RecoveryRecordKind.TOKEN_REVOKE.value:
        expected = {
            "selector_hmac_key_id",
            "selector_hmac",
            "token_verifier_hmac_key_id",
            "token_verifier_hmac",
        }
        _exact_keys(payload, expected, label="token revoke payload")
        _valid_hmac_pair(payload, "selector")
        _valid_hmac_pair(payload, "token_verifier")
    elif kind == RecoveryRecordKind.PUSH_ENVIRONMENT_CUTOFF.value:
        expected = {
            "deployment_environment",
            "expo_project_hmac_key_id",
            "expo_project_hmac",
            "activation_revision",
            "cutoff_revision",
            "last_provider_started_at",
            "last_provider_accepted_at",
            "provider_may_accept_until",
            "closed_at",
            "cutoff_skew_seconds",
            "provider_safe_after",
            "state",
        }
        _exact_keys(payload, expected, label="push cutoff payload")
        if payload["deployment_environment"] not in {
            "development",
            "staging",
            "production",
        }:
            raise RecoveryFenceError("The recovery-fence push environment is invalid.")
        _valid_hmac_pair(payload, "expo_project")
        activation = _valid_int(
            payload["activation_revision"], minimum=1, label="activation revision"
        )
        cutoff = _valid_int(
            payload["cutoff_revision"], minimum=activation, label="cutoff revision"
        )
        if cutoff < activation:  # defensive for type checkers and future edits
            raise RecoveryFenceError("The recovery-fence cutoff revision regressed.")
        for field in (
            "last_provider_started_at",
            "last_provider_accepted_at",
            "provider_may_accept_until",
        ):
            _valid_time(payload[field], nullable=True, label=field)
        for field in ("closed_at", "cutoff_skew_seconds", "provider_safe_after"):
            _valid_time(payload[field], label=field)
        if payload["state"] != "closed":
            raise RecoveryFenceError("A recovery-fence push cutoff must be closed.")
    elif kind == RecoveryRecordKind.REVIEW_ACCESS_REVISION.value:
        expected = {
            "provider",
            "lane_revision",
            "supported_builds",
            "window_state",
            "purchase_test_state",
            "credential_hmacs",
        }
        _exact_keys(payload, expected, label="review access payload")
        if payload["provider"] not in {"apple", "google"}:
            raise RecoveryFenceError("The recovery-fence review provider is invalid.")
        _valid_int(payload["lane_revision"], minimum=1, label="lane revision")
        builds = payload["supported_builds"]
        if not isinstance(builds, list):
            raise RecoveryFenceError("The recovery-fence supported builds are invalid.")
        for row in builds:
            if not isinstance(row, dict):
                raise RecoveryFenceError("A recovery-fence supported build is invalid.")
            _exact_keys(
                row,
                {"application_id", "platform", "version", "build"},
                label="supported build",
            )
            if (
                row["platform"] not in {"ios", "android"}
                or any(
                    not isinstance(row[field], str)
                    or not row[field]
                    or len(row[field]) > 128
                    for field in ("application_id", "version", "build")
                )
            ):
                raise RecoveryFenceError("A recovery-fence supported build is invalid.")
        for field in ("window_state", "purchase_test_state"):
            if (
                not isinstance(payload[field], str)
                or not payload[field]
                or len(payload[field]) > 64
            ):
                raise RecoveryFenceError(f"The recovery-fence {field} is invalid.")
        credential_hmacs = payload["credential_hmacs"]
        if not isinstance(credential_hmacs, list):
            raise RecoveryFenceError("Recovery credential HMACs are invalid.")
        for item in credential_hmacs:
            if not isinstance(item, dict):
                raise RecoveryFenceError("A recovery credential HMAC is invalid.")
            _exact_keys(item, {"key_id", "digest"}, label="credential HMAC")
            _valid_key_id(item["key_id"], label="credential HMAC key ID")
            _valid_sha256(item["digest"], label="credential HMAC digest")
    else:
        raise RecoveryFenceError("The recovery-fence record kind is not supported.")
    return payload


def _require_payload_hmac_key_coverage(
    kind: str,
    payload: dict[str, Any],
    *,
    keyring: VersionedHMAC,
) -> None:
    if kind == RecoveryRecordKind.TOKEN_REVOKE.value:
        key_ids = (
            payload["selector_hmac_key_id"],
            payload["token_verifier_hmac_key_id"],
        )
    elif kind == RecoveryRecordKind.PUSH_ENVIRONMENT_CUTOFF.value:
        key_ids = (payload["expo_project_hmac_key_id"],)
    elif kind == RecoveryRecordKind.REVIEW_ACCESS_REVISION.value:
        key_ids = tuple(item["key_id"] for item in payload["credential_hmacs"])
    else:
        key_ids = ()
    if any(key_id not in keyring.key_ids for key_id in key_ids):
        raise RecoveryFenceError(
            "A recovery-fence payload HMAC key is unavailable."
        )


def _event_core(
    event: RecoveryFenceEvent,
    *,
    sequence: int,
    previous_record_key: str | None,
    previous_record_hash: str | None,
) -> dict[str, Any]:
    if not isinstance(event, (
        CutoverBaselineEvent,
        TokenRevokeEvent,
        PushEnvironmentCutoffEvent,
        ReviewAccessRevisionEvent,
    )):
        raise RecoveryFenceError("A closed recovery-fence event type is required.")
    if not isinstance(event.kind, RecoveryRecordKind):
        raise RecoveryFenceError("A closed recovery-fence record kind is required.")
    _valid_uuid(event.event_id, label="event ID")
    cutoff_at = _valid_time(event.cutoff_at, label="cutoff time")
    assert cutoff_at is not None
    payload = _validated_payload(event.kind.value, event.payload())
    if event.kind is RecoveryRecordKind.CUTOVER_BASELINE and (
        float(payload["minimum_backup_created_at"]) != float(event.cutoff_at)
    ):
        raise RecoveryFenceError(
            "The cutover cutoff must equal the verified backup creation time."
        )
    return {
        "format": RECOVERY_FENCE_RECORD_FORMAT,
        "sequence": sequence,
        "previous_record_key": previous_record_key,
        "previous_record_hash": previous_record_hash,
        "kind": event.kind.value,
        "event_id": event.event_id,
        "cutoff_at": cutoff_at,
        "payload": payload,
    }


@dataclass(frozen=True)
class PublishedRecoveryRecord:
    sequence: int
    previous_record_key: str | None
    previous_record_hash: str | None
    kind: str
    event_id: str
    cutoff_at: float
    payload: dict[str, Any]
    chain_hmac_key_id: str
    chain_hmac: str
    record_hash: str
    record_key: str
    body: bytes
    head_etag: str | None = None


@dataclass(frozen=True)
class ValidatedRecoveryChain:
    """One authenticated HEAD read bound to its fully validated ancestry."""

    head_etag: str
    records: tuple[PublishedRecoveryRecord, ...]


def _build_record(
    event: RecoveryFenceEvent,
    *,
    sequence: int,
    previous_record_key: str | None,
    previous_record_hash: str | None,
    keyring: VersionedHMAC,
    record_key: Callable[[int, str], str],
    requested_chain_hmac_key_id: str | None = None,
) -> PublishedRecoveryRecord:
    core = _event_core(
        event,
        sequence=sequence,
        previous_record_key=previous_record_key,
        previous_record_hash=previous_record_hash,
    )
    _require_payload_hmac_key_coverage(
        core["kind"],
        core["payload"],
        keyring=keyring,
    )
    if requested_chain_hmac_key_id is None:
        chain_key_id, chain_hmac = keyring.digest(
            MobileStateDomain.RECOVERY_CHAIN_LINK,
            _canonical_json(core),
        )
    else:
        chain_key_id = _valid_key_id(
            requested_chain_hmac_key_id,
            label="requested chain HMAC key ID",
        )
        try:
            chain_hmac = keyring.digest_with_key(
                chain_key_id,
                MobileStateDomain.RECOVERY_CHAIN_LINK,
                _canonical_json(core),
            )
        except KeyError:
            raise RecoveryFenceError(
                "The journaled recovery-fence chain HMAC key is unavailable."
            ) from None
    without_hash = {
        **core,
        "chain_hmac_key_id": chain_key_id,
        "chain_hmac": chain_hmac,
    }
    record_hash = hashlib.sha256(_canonical_json(without_hash)).hexdigest()
    data = {**without_hash, "record_hash": record_hash}
    body = _canonical_json(data)
    key = record_key(sequence, record_hash)
    return PublishedRecoveryRecord(
        sequence=sequence,
        previous_record_key=previous_record_key,
        previous_record_hash=previous_record_hash,
        kind=event.kind.value,
        event_id=event.event_id,
        cutoff_at=float(event.cutoff_at),
        payload=dict(core["payload"]),
        chain_hmac_key_id=chain_key_id,
        chain_hmac=chain_hmac,
        record_hash=record_hash,
        record_key=key,
        body=body,
    )


def _validate_record(
    body: bytes,
    *,
    keyring: VersionedHMAC,
    expected_key: str,
) -> PublishedRecoveryRecord:
    data = _strict_json(body, label="record")
    expected_fields = {
        "format",
        "sequence",
        "previous_record_key",
        "previous_record_hash",
        "kind",
        "event_id",
        "cutoff_at",
        "payload",
        "chain_hmac_key_id",
        "chain_hmac",
        "record_hash",
    }
    _exact_keys(data, expected_fields, label="record")
    if data["format"] != RECOVERY_FENCE_RECORD_FORMAT:
        raise RecoveryFenceError("The recovery-fence record format is unsupported.")
    sequence = _valid_int(data["sequence"], minimum=1, label="record sequence")
    _valid_uuid(data["event_id"], label="event ID")
    cutoff_at = _valid_time(data["cutoff_at"], label="cutoff time")
    assert cutoff_at is not None
    if data["previous_record_key"] is None:
        if data["previous_record_hash"] is not None:
            raise RecoveryFenceError("The recovery-fence previous link is incomplete.")
    else:
        if not isinstance(data["previous_record_key"], str):
            raise RecoveryFenceError("The recovery-fence previous record key is invalid.")
        _valid_sha256(
            data["previous_record_hash"], label="previous record hash"
        )
    kind = data["kind"]
    if not isinstance(kind, str):
        raise RecoveryFenceError("The recovery-fence record kind is invalid.")
    payload = _validated_payload(kind, data["payload"])
    chain_key_id = _valid_key_id(
        data["chain_hmac_key_id"], label="chain HMAC key ID"
    )
    chain_hmac = _valid_sha256(data["chain_hmac"], label="chain HMAC")
    record_hash = _valid_sha256(data["record_hash"], label="record hash")
    without_hash = dict(data)
    del without_hash["record_hash"]
    if hashlib.sha256(_canonical_json(without_hash)).hexdigest() != record_hash:
        raise RecoveryFenceError("The recovery-fence record hash did not validate.")
    core = dict(without_hash)
    del core["chain_hmac_key_id"]
    del core["chain_hmac"]
    try:
        expected_hmac = keyring.digest_with_key(
            chain_key_id,
            MobileStateDomain.RECOVERY_CHAIN_LINK,
            _canonical_json(core),
        )
    except KeyError:
        raise RecoveryFenceError(
            "The recovery-fence chain references a missing HMAC key."
        ) from None
    if not hmac.compare_digest(expected_hmac, chain_hmac):
        raise RecoveryFenceError("The recovery-fence chain HMAC did not validate.")
    _require_payload_hmac_key_coverage(kind, payload, keyring=keyring)
    key_match = _REMOTE_RECORD_KEY.search(expected_key)
    if (
        key_match is None
        or int(key_match.group(1)) != sequence
        or key_match.group(2) != record_hash
        or key_match.end() != len(expected_key)
    ):
        raise RecoveryFenceError("The recovery-fence record key did not validate.")
    return PublishedRecoveryRecord(
        sequence=sequence,
        previous_record_key=data["previous_record_key"],
        previous_record_hash=data["previous_record_hash"],
        kind=kind,
        event_id=data["event_id"],
        cutoff_at=cutoff_at,
        payload=dict(payload),
        chain_hmac_key_id=chain_key_id,
        chain_hmac=chain_hmac,
        record_hash=record_hash,
        record_key=expected_key,
        body=body,
    )


def _head_body(record: PublishedRecoveryRecord) -> bytes:
    return _canonical_json(
        {
            "format": RECOVERY_FENCE_HEAD_FORMAT,
            "sequence": record.sequence,
            "record_key": record.record_key,
            "record_hash": record.record_hash,
            "chain_hmac_key_id": record.chain_hmac_key_id,
            "chain_hmac": record.chain_hmac,
        }
    )


def _validate_head(body: bytes) -> dict[str, Any]:
    data = _strict_json(body, label="HEAD")
    _exact_keys(
        data,
        {
            "format",
            "sequence",
            "record_key",
            "record_hash",
            "chain_hmac_key_id",
            "chain_hmac",
        },
        label="HEAD",
    )
    if data["format"] != RECOVERY_FENCE_HEAD_FORMAT:
        raise RecoveryFenceError("The recovery-fence HEAD format is unsupported.")
    sequence = _valid_int(data["sequence"], minimum=1, label="HEAD sequence")
    record_hash = _valid_sha256(data["record_hash"], label="HEAD record hash")
    if not isinstance(data["record_key"], str):
        raise RecoveryFenceError("The recovery-fence HEAD record key is invalid.")
    match = _REMOTE_RECORD_KEY.search(data["record_key"])
    if (
        match is None
        or match.end() != len(data["record_key"])
        or int(match.group(1)) != sequence
        or match.group(2) != record_hash
    ):
        raise RecoveryFenceError("The recovery-fence HEAD record identity is invalid.")
    _valid_key_id(data["chain_hmac_key_id"], label="HEAD chain HMAC key ID")
    _valid_sha256(data["chain_hmac"], label="HEAD chain HMAC")
    return data


_WRITER_LOCKS_GUARD = threading.Lock()
_WRITER_LOCKS: dict[str, threading.RLock] = {}
_WRITER_DEPTH = threading.local()


def _resolved_lock_key(path: Path) -> str:
    try:
        value = str(path.resolve(strict=False))
    except OSError:
        value = str(path.absolute())
    return value.casefold() if os.name == "nt" else value


def _thread_lock(path: Path) -> threading.RLock:
    key = _resolved_lock_key(path)
    with _WRITER_LOCKS_GUARD:
        lock = _WRITER_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WRITER_LOCKS[key] = lock
        return lock


def _lock_file(file_descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                os.lseek(file_descriptor, 0, os.SEEK_SET)
                msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if (
                    exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                    and getattr(exc, "winerror", None) not in {33, 36}
                ):
                    raise
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_EX)


def _unlock_file(file_descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_UN)


def _unsupported_windows_directory_fsync(exc: OSError) -> bool:
    if os.name != "nt":
        return False
    unsupported_errnos = {
        errno.EACCES,
        errno.EPERM,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    return exc.errno in unsupported_errnos or getattr(exc, "winerror", None) in {
        1,  # ERROR_INVALID_FUNCTION
        5,  # ERROR_ACCESS_DENIED for opening a directory handle via os.open
        6,  # ERROR_INVALID_HANDLE from fsync on a directory descriptor
        50,  # ERROR_NOT_SUPPORTED
    }


def _fsync_directory(path: Path) -> None:
    """Durably publish a rename, failing closed on real storage I/O errors."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if _unsupported_windows_directory_fsync(exc):
            return
        raise RecoveryFenceError(
            "A recovery-fence parent directory could not be opened durably."
        ) from None
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if not _unsupported_windows_directory_fsync(exc):
            raise RecoveryFenceError(
                "A recovery-fence parent directory could not be synchronized durably."
            ) from None
    finally:
        os.close(descriptor)


def _ensure_private_directory(path: Path) -> None:
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    if not existed:
        _fsync_directory(path.parent)


def _durable_atomic_write(path: Path, body: bytes, *, immutable: bool = False) -> None:
    _ensure_private_directory(path.parent)
    if immutable and path.exists():
        try:
            existing = path.read_bytes()
        except OSError:
            raise RecoveryFenceError(
                "The local recovery-fence immutable record could not be read."
            ) from None
        if existing != body:
            raise RecoveryFenceError(
                "A different local recovery-fence immutable record already exists."
            )
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


@contextmanager
def _writer_lock(local_root: Path) -> Iterator[None]:
    _ensure_private_directory(local_root)
    lock_path = local_root / ".recovery-fence.lock"
    key = _resolved_lock_key(lock_path)
    depths = getattr(_WRITER_DEPTH, "depths", None)
    if depths is None:
        depths = {}
        _WRITER_DEPTH.depths = depths
    lock = _thread_lock(lock_path)
    with lock:
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        acquired = False
        try:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            if os.fstat(descriptor).st_size < 1:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
                _fsync_directory(local_root)
            _lock_file(descriptor)
            acquired = True
            depths[key] = 1
            yield
        finally:
            depths.pop(key, None)
            if acquired:
                _unlock_file(descriptor)
            os.close(descriptor)


class RecoveryFenceRemote(Protocol):
    def record_key(self, sequence: int, record_hash: str) -> str: ...

    def read_head(self): ...

    def read_record(self, key: str): ...

    def put_immutable_record(self, key: str, body: bytes): ...

    def compare_and_swap_head(self, body: bytes, *, expected_etag: str | None): ...


class RecoveryFenceLedger:
    """Serialize, publish, and validate one monotonic remote recovery chain."""

    def __init__(
        self,
        *,
        remote_store: RecoveryFenceRemote,
        keyring: VersionedHMAC,
        local_root: str | Path,
        db_path: str | Path | None = None,
        checkpoint_mode: str = "writable",
        max_cas_retries: int = 8,
    ):
        if not isinstance(keyring, VersionedHMAC):
            raise TypeError("A VersionedHMAC keyring is required.")
        if (
            isinstance(max_cas_retries, bool)
            or not isinstance(max_cas_retries, int)
            or max_cas_retries < 1
            or max_cas_retries > 64
        ):
            raise ValueError("max_cas_retries must be between 1 and 64.")
        if checkpoint_mode not in {"writable", "read_only"}:
            raise ValueError("checkpoint_mode must be writable or read_only.")
        if checkpoint_mode == "read_only" and db_path is None:
            raise ValueError("Read-only checkpoint mode requires a database path.")
        self._remote = remote_store
        self._keyring = keyring
        self._local_root = Path(local_root)
        self._state_root = self._local_root / ".recovery-fence"
        self._db_path = Path(db_path) if db_path is not None else None
        self._checkpoint_mode = checkpoint_mode
        self._max_cas_retries = max_cas_retries

    @property
    def local_root(self) -> Path:
        return self._local_root

    @property
    def checkpoint_mode(self) -> str:
        return self._checkpoint_mode

    def _open_checkpoint_connection(self) -> sqlite3.Connection:
        assert self._db_path is not None
        if self._checkpoint_mode == "writable":
            return sqlite3.connect(self._db_path)
        try:
            database_uri = self._db_path.expanduser().resolve(strict=True).as_uri()
            connection = sqlite3.connect(f"{database_uri}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA query_only").fetchone() != (1,):
                connection.close()
                raise RecoveryFenceError(
                    "The restore-only checkpoint connection is not query-only."
                )
            return connection
        except RecoveryFenceError:
            raise
        except (OSError, sqlite3.Error):
            raise RecoveryFenceError(
                "The local recovery-fence checkpoint could not be opened read-only."
            ) from None

    def _read_chain(self, *, allow_empty: bool) -> tuple[Any | None, tuple[PublishedRecoveryRecord, ...]]:
        try:
            head_object = self._remote.read_head()
        except RecoveryFenceStoreError as exc:
            raise RecoveryFenceError(
                "The recovery-fence HEAD could not be read."
            ) from exc
        if head_object is None:
            if allow_empty:
                self._assert_local_checkpoint_ancestry(())
                return None, ()
            raise RecoveryFenceError(
                "The mandatory recovery-fence cutover baseline HEAD is missing."
            )
        head = _validate_head(head_object.body)
        current_key = head["record_key"]
        expected_sequence = head["sequence"]
        expected_hash = head["record_hash"]
        expected_chain_key = head["chain_hmac_key_id"]
        expected_chain_hmac = head["chain_hmac"]
        seen_keys: set[str] = set()
        reverse_chain: list[PublishedRecoveryRecord] = []
        prefetched: tuple[str, PublishedRecoveryRecord] | None = None
        while True:
            if current_key in seen_keys:
                raise RecoveryFenceError("The recovery-fence chain contains a cycle.")
            seen_keys.add(current_key)
            if len(seen_keys) > 1_000_000:
                raise RecoveryFenceError("The recovery-fence chain exceeds its bound.")
            if prefetched is not None and prefetched[0] == current_key:
                record = prefetched[1]
                prefetched = None
            else:
                try:
                    remote_record = self._remote.read_record(current_key)
                except RecoveryFenceStoreError as exc:
                    raise RecoveryFenceError(
                        "A recovery-fence immutable record is missing."
                    ) from exc
                record = _validate_record(
                    remote_record.body,
                    keyring=self._keyring,
                    expected_key=current_key,
                )
            if (
                record.sequence != expected_sequence
                or record.record_hash != expected_hash
                or record.chain_hmac_key_id != expected_chain_key
                or record.chain_hmac != expected_chain_hmac
            ):
                raise RecoveryFenceError(
                    "The recovery-fence HEAD or previous link diverged."
                )
            reverse_chain.append(record)
            if expected_sequence == 1:
                if (
                    record.kind != RecoveryRecordKind.CUTOVER_BASELINE.value
                    or record.previous_record_key is not None
                    or record.previous_record_hash is not None
                ):
                    raise RecoveryFenceError(
                        "The recovery-fence genesis baseline is invalid."
                    )
                break
            if (
                record.kind == RecoveryRecordKind.CUTOVER_BASELINE.value
                or record.previous_record_key is None
                or record.previous_record_hash is None
            ):
                raise RecoveryFenceError(
                    "The recovery-fence chain has a sequence gap."
                )
            current_key = record.previous_record_key
            expected_sequence -= 1
            expected_hash = record.previous_record_hash
            # The predecessor's key ID/HMAC are learned only from its body.
            try:
                predecessor = self._remote.read_record(current_key)
            except RecoveryFenceStoreError as exc:
                raise RecoveryFenceError(
                    "A recovery-fence immutable record is missing."
                ) from exc
            parsed_predecessor = _validate_record(
                predecessor.body,
                keyring=self._keyring,
                expected_key=current_key,
            )
            expected_chain_key = parsed_predecessor.chain_hmac_key_id
            expected_chain_hmac = parsed_predecessor.chain_hmac
            prefetched = (current_key, parsed_predecessor)
        chain = tuple(reversed(reverse_chain))
        event_ids: dict[str, PublishedRecoveryRecord] = {}
        for record in chain:
            prior = event_ids.get(record.event_id)
            if prior is not None:
                raise RecoveryFenceError(
                    "The recovery-fence chain repeats a logical event ID."
                )
            event_ids[record.event_id] = record
        self._assert_local_checkpoint_ancestry(chain)
        return head_object, chain

    def load_chain(self) -> tuple[PublishedRecoveryRecord, ...]:
        """Refetch and validate HEAD plus every explicit predecessor to genesis."""

        return self.load_chain_snapshot().records

    def load_chain_snapshot(self) -> ValidatedRecoveryChain:
        """Return the validated chain and ETag from the same authenticated HEAD."""

        head_object, chain = self._read_chain(allow_empty=False)
        if head_object is None or not chain:  # pragma: no cover - allow_empty is false
            raise RecoveryFenceError(
                "The mandatory recovery-fence cutover baseline HEAD is missing."
            )
        records = (*chain[:-1], replace(chain[-1], head_etag=head_object.etag))
        return ValidatedRecoveryChain(
            head_etag=head_object.etag,
            records=records,
        )

    @staticmethod
    def _logical_record(record: PublishedRecoveryRecord) -> bytes:
        return _canonical_json(
            {
                "kind": record.kind,
                "event_id": record.event_id,
                "cutoff_at": record.cutoff_at,
                "payload": record.payload,
            }
        )

    @staticmethod
    def _logical_event(event: RecoveryFenceEvent) -> bytes:
        core = _event_core(
            event,
            sequence=1,
            previous_record_key=None,
            previous_record_hash=None,
        )
        return _canonical_json(
            {
                "kind": core["kind"],
                "event_id": core["event_id"],
                "cutoff_at": core["cutoff_at"],
                "payload": core["payload"],
            }
        )

    def _load_journaled_record(
        self,
        identity: tuple[str, str, str],
        *,
        logical_event: bytes,
    ) -> PublishedRecoveryRecord:
        try:
            remote_record = self._remote.read_record(identity[0])
        except RecoveryFenceStoreError as exc:
            raise RecoveryFenceError(
                "The journaled recovery-fence immutable record is unavailable."
            ) from exc
        record = _validate_record(
            remote_record.body,
            keyring=self._keyring,
            expected_key=identity[0],
        )
        if (
            record.record_hash != identity[1]
            or record.chain_hmac_key_id != identity[2]
            or self._logical_record(record) != logical_event
        ):
            raise RecoveryFenceError(
                "The journaled recovery-fence record identity diverged."
            )
        return record

    def _write_local_record(self, record: PublishedRecoveryRecord) -> None:
        name = Path(record.record_key).name
        _durable_atomic_write(
            self._state_root / "records" / name,
            record.body,
            immutable=True,
        )

    def _write_local_head(self, body: bytes) -> None:
        _durable_atomic_write(self._state_root / "HEAD", body)

    @staticmethod
    def _require_checkpoint_ancestry(
        existing: sqlite3.Row | tuple[Any, ...] | None,
        chain: tuple[PublishedRecoveryRecord, ...],
    ) -> None:
        if existing is None:
            return
        if not chain:
            raise RecoveryFenceError(
                "The local recovery-fence checkpoint has no remote HEAD ancestry."
            )
        prior_sequence = _valid_int(
            existing[0], minimum=1, label="checkpoint sequence"
        )
        if prior_sequence > len(chain):
            raise RecoveryFenceError(
                "The local recovery-fence checkpoint diverged from remote HEAD."
            )
        baseline = chain[0]
        baseline_identity = (
            baseline.payload["lineage_id"],
            baseline.payload["baseline_backup_id"],
            baseline.payload["schema_generation"],
        )
        if tuple(existing[1:4]) != baseline_identity:
            raise RecoveryFenceError(
                "The local recovery-fence checkpoint baseline diverged."
            )
        prior_record = chain[prior_sequence - 1]
        if (
            prior_record.sequence != prior_sequence
            or tuple(existing[4:7])
            != (
                prior_record.record_key,
                prior_record.record_hash,
                prior_record.chain_hmac_key_id,
            )
        ):
            raise RecoveryFenceError(
                "The local recovery-fence checkpoint is not in remote HEAD ancestry."
            )

    def _assert_local_checkpoint_ancestry(
        self,
        chain: tuple[PublishedRecoveryRecord, ...],
    ) -> None:
        if self._db_path is None:
            return
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_checkpoint_connection()
            existing = connection.execute(
                "SELECT head_sequence, lineage_id, baseline_backup_id, "
                "schema_generation, head_record_key, head_record_hash, "
                "chain_hmac_key_id FROM mobile_recovery_fence_checkpoints "
                "WHERE checkpoint_id = 1"
            ).fetchone()
        except RecoveryFenceError:
            raise
        except sqlite3.Error:
            raise RecoveryFenceError(
                "The local recovery-fence checkpoint could not be validated."
            ) from None
        finally:
            if connection is not None:
                connection.close()
        self._require_checkpoint_ancestry(existing, chain)

    def _save_checkpoint(
        self,
        *,
        validated_chain: tuple[PublishedRecoveryRecord, ...],
        head_etag: str,
    ) -> None:
        if self._db_path is None:
            return
        if self._checkpoint_mode == "read_only":
            raise RecoveryFenceError(
                "A read-only recovery-fence ledger cannot save a checkpoint."
            )
        if not validated_chain:
            raise RecoveryFenceError(
                "A validated recovery-fence chain is required for checkpointing."
            )
        head_record = validated_chain[-1]
        baseline = validated_chain[0]
        payload = baseline.payload
        now = time.time()
        try:
            with sqlite3.connect(self._db_path) as connection:
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT head_sequence, lineage_id, baseline_backup_id, "
                    "schema_generation, head_record_key, head_record_hash, "
                    "chain_hmac_key_id FROM mobile_recovery_fence_checkpoints "
                    "WHERE checkpoint_id = 1"
                ).fetchone()
                self._require_checkpoint_ancestry(existing, validated_chain)
                baseline_identity = (
                    payload["lineage_id"],
                    payload["baseline_backup_id"],
                    payload["schema_generation"],
                )
                connection.execute(
                    "INSERT INTO mobile_recovery_fence_checkpoints "
                    "(checkpoint_id, lineage_id, baseline_backup_id, "
                    "schema_generation, head_sequence, head_record_key, "
                    "head_record_hash, head_etag, chain_hmac_key_id, verified_at) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(checkpoint_id) DO UPDATE SET "
                    "lineage_id=excluded.lineage_id, "
                    "baseline_backup_id=excluded.baseline_backup_id, "
                    "schema_generation=excluded.schema_generation, "
                    "head_sequence=excluded.head_sequence, "
                    "head_record_key=excluded.head_record_key, "
                    "head_record_hash=excluded.head_record_hash, "
                    "head_etag=excluded.head_etag, "
                    "chain_hmac_key_id=excluded.chain_hmac_key_id, "
                    "verified_at=excluded.verified_at",
                    (
                        *baseline_identity,
                        head_record.sequence,
                        head_record.record_key,
                        head_record.record_hash,
                        head_etag,
                        head_record.chain_hmac_key_id,
                        now,
                    ),
                )
                connection.commit()
            _fsync_directory(self._db_path.parent)
        except RecoveryFenceError:
            raise
        except (OSError, sqlite3.Error):
            raise RecoveryFenceError(
                "The local recovery-fence checkpoint could not be persisted."
            ) from None

    def append_and_publish(
        self,
        event: RecoveryFenceEvent,
        *,
        on_phase: Callable[[str, PublishedRecoveryRecord], None] | None = None,
        resume_record_identity: tuple[str, str, str] | None = None,
    ) -> PublishedRecoveryRecord:
        """Append one event, rebasing only explicit provider CAS conflicts."""

        if self._checkpoint_mode == "read_only":
            raise RecoveryFenceError(
                "A read-only recovery-fence ledger cannot publish records."
            )

        if resume_record_identity is not None:
            if (
                not isinstance(resume_record_identity, tuple)
                or len(resume_record_identity) != 3
                or not isinstance(resume_record_identity[0], str)
            ):
                raise RecoveryFenceError(
                    "The journaled recovery-fence record identity is invalid."
                )
            _valid_sha256(
                resume_record_identity[1],
                label="journaled recovery-fence record hash",
            )
            _valid_key_id(
                resume_record_identity[2],
                label="journaled recovery-fence chain HMAC key ID",
            )
        logical_event = self._logical_event(event)
        with _writer_lock(self._local_root):
            journaled_record = (
                None
                if resume_record_identity is None
                else self._load_journaled_record(
                    resume_record_identity,
                    logical_event=logical_event,
                )
            )
            for _attempt in range(self._max_cas_retries):
                head_object, chain = self._read_chain(allow_empty=True)
                for existing in chain:
                    if existing.event_id != event.event_id:
                        continue
                    if self._logical_record(existing) != logical_event:
                        raise RecoveryFenceError(
                            "The recovery-fence event_id was reused for a "
                            "different logical event."
                        )
                    accepted_existing = replace(
                        existing,
                        head_etag=head_object.etag,
                    )
                    if on_phase is not None:
                        on_phase("record_published", accepted_existing)
                        on_phase("head_published", accepted_existing)
                    current = chain[-1]
                    current_head_body = _head_body(current)
                    self._write_local_head(current_head_body)
                    self._save_checkpoint(
                        validated_chain=chain,
                        head_etag=head_object.etag,
                    )
                    return accepted_existing

                if not chain:
                    if event.kind is not RecoveryRecordKind.CUTOVER_BASELINE:
                        raise RecoveryFenceError(
                            "A cutover baseline is required before later records."
                        )
                    sequence = 1
                    previous_key = None
                    previous_hash = None
                    expected_etag = None
                else:
                    if event.kind is RecoveryRecordKind.CUTOVER_BASELINE:
                        raise RecoveryFenceError(
                            "A different recovery-fence genesis already exists."
                        )
                    previous = chain[-1]
                    sequence = previous.sequence + 1
                    previous_key = previous.record_key
                    previous_hash = previous.record_hash
                    expected_etag = head_object.etag
                record = _build_record(
                    event,
                    sequence=sequence,
                    previous_record_key=previous_key,
                    previous_record_hash=previous_hash,
                    keyring=self._keyring,
                    record_key=self._remote.record_key,
                    requested_chain_hmac_key_id=(
                        None
                        if resume_record_identity is None
                        else resume_record_identity[2]
                    ),
                )
                if journaled_record is not None:
                    if record.sequence < journaled_record.sequence:
                        raise RecoveryFenceError(
                            "The journaled recovery-fence record sequence regressed."
                        )
                    if record.sequence == journaled_record.sequence:
                        if (
                            record.record_key,
                            record.record_hash,
                            record.chain_hmac_key_id,
                        ) != resume_record_identity:
                            raise RecoveryFenceError(
                                "The journaled recovery-fence orphan record diverged."
                            )
                    else:
                        predecessor_index = journaled_record.sequence - 2
                        if predecessor_index < 0 or predecessor_index >= len(chain):
                            raise RecoveryFenceError(
                                "The journaled recovery-fence orphan cannot be rebased."
                            )
                        original_predecessor = chain[predecessor_index]
                        if (
                            original_predecessor.record_key
                            != journaled_record.previous_record_key
                            or original_predecessor.record_hash
                            != journaled_record.previous_record_hash
                        ):
                            raise RecoveryFenceError(
                                "The journaled recovery-fence orphan ancestry diverged."
                            )
                self._write_local_record(record)
                try:
                    remote_record = self._remote.put_immutable_record(
                        record.record_key, record.body
                    )
                except RecoveryFenceStoreError as exc:
                    raise RecoveryFenceError(
                        "The recovery-fence immutable record was not published."
                    ) from exc
                if remote_record.body != record.body:
                    raise RecoveryFenceError(
                        "The recovery-fence immutable record readback diverged."
                    )
                if on_phase is not None:
                    on_phase("record_published", record)
                head_body = _head_body(record)
                try:
                    remote_head = self._remote.compare_and_swap_head(
                        head_body,
                        expected_etag=expected_etag,
                    )
                except RecoveryFenceCASConflict:
                    continue
                except RecoveryFenceStoreError as exc:
                    raise RecoveryFenceError(
                        "The recovery-fence HEAD was not accepted."
                    ) from exc
                parsed_head = _validate_head(remote_head.body)
                if (
                    remote_head.body != head_body
                    or parsed_head["record_key"] != record.record_key
                    or parsed_head["record_hash"] != record.record_hash
                ):
                    raise RecoveryFenceError(
                        "The recovery-fence HEAD readback diverged."
                    )
                accepted = replace(record, head_etag=remote_head.etag)
                if on_phase is not None:
                    on_phase("head_published", accepted)
                self._write_local_head(head_body)
                self._save_checkpoint(
                    validated_chain=(*chain, accepted),
                    head_etag=remote_head.etag,
                )
                return accepted
        raise RecoveryFenceError(
            "The recovery-fence HEAD remained contended; append failed closed."
        )


@dataclass(frozen=True)
class VerifiedBackupFacts:
    backup_id: str
    backup_created_at: float
    schema_generation: int
    manifest_sha256: str
    manifest_database_sha256: str
    baseline_db_checkpoint: str


@dataclass(frozen=True)
class ScratchVerificationProof:
    verified: bool
    lineage_id: str
    backup_id: str
    manifest_sha256: str
    baseline_db_checkpoint: str
    record_hash: str


@dataclass(frozen=True)
class BaselineApprovals:
    erasure_inventory_complete: bool
    dependent_routes_held: bool
    fresh_backup_authorized: bool
    scratch_restore_authorized: bool


class BaselineBackupVerifier(Protocol):
    def create_and_verify(self, *, lineage_id: str) -> VerifiedBackupFacts: ...


class BaselineScratchVerifier(Protocol):
    def verify_exact(
        self,
        *,
        lineage_id: str,
        facts: VerifiedBackupFacts,
        record: PublishedRecoveryRecord,
    ) -> ScratchVerificationProof: ...


@dataclass(frozen=True)
class BaselineJournal:
    operation_id: str
    phase: str
    request_hash: str
    lineage_id: str
    backup_id: str | None
    backup_created_at: float | None
    schema_generation: int | None
    manifest_sha256: str | None
    baseline_db_checkpoint: str | None
    record_key: str | None
    record_hash: str | None
    head_etag: str | None
    chain_hmac_key_id: str | None
    created_at: float
    updated_at: float


_JOURNAL_COLUMNS = (
    "operation_id, phase, request_hash, lineage_id, backup_id, "
    "backup_created_at, schema_generation, manifest_sha256, "
    "baseline_db_checkpoint, record_key, record_hash, head_etag, "
    "chain_hmac_key_id, created_at, updated_at"
)


def _journal_from_row(row: sqlite3.Row | tuple[Any, ...]) -> BaselineJournal:
    return BaselineJournal(*row)


def _validated_backup_facts(value: object) -> VerifiedBackupFacts:
    if not isinstance(value, VerifiedBackupFacts):
        raise RecoveryFenceError("The verified backup facts are unavailable.")
    try:
        validate_backup_id(value.backup_id)
    except (BackupError, TypeError):
        raise RecoveryFenceError("The verified backup ID is invalid.") from None
    _valid_time(value.backup_created_at, label="verified backup creation time")
    _valid_int(value.schema_generation, minimum=1, label="verified schema generation")
    _valid_sha256(value.manifest_sha256, label="verified manifest SHA-256")
    _valid_sha256(
        value.manifest_database_sha256,
        label="verified manifest database SHA-256",
    )
    _valid_sha256(
        value.baseline_db_checkpoint,
        label="verified baseline database checkpoint",
    )
    if value.baseline_db_checkpoint != value.manifest_database_sha256:
        raise RecoveryFenceError(
            "The baseline database checkpoint does not match the verified "
            "manifest database SHA-256."
        )
    return value


class CutoverBaselineInitializer:
    """Offline exact-retry orchestration with injected Gate-3C proof providers."""

    def __init__(
        self,
        *,
        ledger: RecoveryFenceLedger,
        db_path: str | Path,
        backup_verifier: BaselineBackupVerifier,
        scratch_verifier: BaselineScratchVerifier,
        lineage_factory: Callable[[], str] | None = None,
        phase_hook: Callable[[str], None] | None = None,
    ):
        self._ledger = ledger
        self._db_path = Path(db_path)
        self._backup_verifier = backup_verifier
        self._scratch_verifier = scratch_verifier
        self._lineage_factory = lineage_factory or (lambda: str(uuid.uuid4()))
        self._phase_hook = phase_hook

    def _snapshot(self, journal: BaselineJournal) -> None:
        body = _canonical_json(
            {
                field: getattr(journal, field)
                for field in BaselineJournal.__dataclass_fields__
            }
        )
        _durable_atomic_write(
            self._ledger.local_root
            / ".recovery-fence"
            / "baseline-journals"
            / f"{journal.operation_id}.json",
            body,
        )

    def _hook(self, phase: str) -> None:
        if self._phase_hook is not None:
            self._phase_hook(phase)

    def _load(self, operation_id: str) -> BaselineJournal | None:
        try:
            with sqlite3.connect(self._db_path) as connection:
                row = connection.execute(
                    f"SELECT {_JOURNAL_COLUMNS} FROM "
                    "mobile_recovery_baseline_journals WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
        except sqlite3.Error:
            raise RecoveryFenceError(
                "The local baseline journal could not be read."
            ) from None
        return None if row is None else _journal_from_row(row)

    def _prepare(
        self,
        *,
        operation_id: str,
        request_hash: str,
    ) -> BaselineJournal:
        now = time.time()
        try:
            with sqlite3.connect(self._db_path) as connection:
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    f"SELECT {_JOURNAL_COLUMNS} FROM "
                    "mobile_recovery_baseline_journals WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                created = row is None
                if row is None:
                    if connection.execute(
                        "SELECT 1 FROM mobile_recovery_accepted_baselines LIMIT 1"
                    ).fetchone() is not None:
                        raise RecoveryFenceError(
                            "A different cutover baseline is already accepted."
                        )
                    if connection.execute(
                        "SELECT 1 FROM mobile_recovery_baseline_journals "
                        "WHERE operation_id <> ? LIMIT 1",
                        (operation_id,),
                    ).fetchone() is not None:
                        raise RecoveryFenceError(
                            "A different cutover baseline operation already exists."
                        )
                    lineage_id = _valid_uuid(
                        self._lineage_factory(), label="lineage ID"
                    )
                    connection.execute(
                        "INSERT INTO mobile_recovery_baseline_journals "
                        "(operation_id, phase, request_hash, lineage_id, "
                        "created_at, updated_at) VALUES (?, 'lineage_prepared', ?, ?, ?, ?)",
                        (operation_id, request_hash, lineage_id, now, now),
                    )
                    row = connection.execute(
                        f"SELECT {_JOURNAL_COLUMNS} FROM "
                        "mobile_recovery_baseline_journals WHERE operation_id = ?",
                        (operation_id,),
                    ).fetchone()
                journal = _journal_from_row(row)
                if journal.request_hash != request_hash:
                    raise RecoveryFenceError(
                        "The baseline operation request conflicts with its journal."
                    )
                connection.commit()
            _fsync_directory(self._db_path.parent)
        except RecoveryFenceError:
            raise
        except sqlite3.Error:
            raise RecoveryFenceError(
                "The local baseline journal could not be prepared."
            ) from None
        self._snapshot(journal)
        if created:
            self._hook("lineage_prepared")
        return journal

    def _advance(
        self,
        journal: BaselineJournal,
        phase: str,
        **updates: Any,
    ) -> BaselineJournal:
        target = _PHASE_INDEX[phase]
        current = _PHASE_INDEX.get(journal.phase)
        if current is None:
            raise RecoveryFenceError("The baseline journal phase is invalid.")
        for name, value in updates.items():
            existing = getattr(journal, name)
            if existing is not None and existing != value:
                raise RecoveryFenceError(
                    "The baseline journal immutable facts conflict."
                )
        if current >= target:
            return journal
        if target != current + 1:
            raise RecoveryFenceError("The baseline journal phase transition is invalid.")
        now = time.time()
        assignments = ["phase = ?", "updated_at = ?"]
        values: list[Any] = [phase, now]
        for name, value in updates.items():
            assignments.append(f"{name} = ?")
            values.append(value)
        values.extend([journal.operation_id, journal.phase, journal.updated_at])
        try:
            with sqlite3.connect(self._db_path) as connection:
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "UPDATE mobile_recovery_baseline_journals SET "
                    + ", ".join(assignments)
                    + " WHERE operation_id = ? AND phase = ? AND updated_at = ?",
                    values,
                )
                if cursor.rowcount != 1:
                    raise RecoveryFenceError(
                        "The baseline journal changed during its transition."
                    )
                row = connection.execute(
                    f"SELECT {_JOURNAL_COLUMNS} FROM "
                    "mobile_recovery_baseline_journals WHERE operation_id = ?",
                    (journal.operation_id,),
                ).fetchone()
                connection.commit()
            _fsync_directory(self._db_path.parent)
        except RecoveryFenceError:
            raise
        except sqlite3.Error:
            raise RecoveryFenceError(
                "The baseline journal transition could not be persisted."
            ) from None
        advanced = _journal_from_row(row)
        self._snapshot(advanced)
        self._hook(phase)
        return advanced

    @staticmethod
    def _facts(journal: BaselineJournal) -> VerifiedBackupFacts:
        if any(
            value is None
            for value in (
                journal.backup_id,
                journal.backup_created_at,
                journal.schema_generation,
                journal.manifest_sha256,
                journal.baseline_db_checkpoint,
            )
        ):
            raise RecoveryFenceError("The baseline verified-backup facts are incomplete.")
        return _validated_backup_facts(
            VerifiedBackupFacts(
                backup_id=journal.backup_id,
                backup_created_at=journal.backup_created_at,
                schema_generation=journal.schema_generation,
                manifest_sha256=journal.manifest_sha256,
                manifest_database_sha256=journal.baseline_db_checkpoint,
                baseline_db_checkpoint=journal.baseline_db_checkpoint,
            )
        )

    def _accept(
        self,
        journal: BaselineJournal,
        facts: VerifiedBackupFacts,
        record: PublishedRecoveryRecord,
    ) -> BaselineJournal:
        now = time.time()
        try:
            with sqlite3.connect(self._db_path) as connection:
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE")
                checkpoint = connection.execute(
                    "SELECT lineage_id, baseline_backup_id, schema_generation, "
                    "head_record_key, head_record_hash, head_etag FROM "
                    "mobile_recovery_fence_checkpoints WHERE checkpoint_id = 1"
                ).fetchone()
                expected_checkpoint = (
                    journal.lineage_id,
                    facts.backup_id,
                    facts.schema_generation,
                    record.record_key,
                    record.record_hash,
                    record.head_etag,
                )
                if checkpoint is None or tuple(checkpoint) != expected_checkpoint:
                    raise RecoveryFenceError(
                        "The accepted baseline checkpoint does not match HEAD."
                    )
                existing = connection.execute(
                    "SELECT lineage_id, baseline_backup_id, minimum_backup_created_at, "
                    "manifest_sha256, schema_generation, baseline_db_checkpoint "
                    "FROM mobile_recovery_accepted_baselines"
                ).fetchall()
                accepted_facts = (
                    journal.lineage_id,
                    facts.backup_id,
                    facts.backup_created_at,
                    facts.manifest_sha256,
                    facts.schema_generation,
                    facts.baseline_db_checkpoint,
                )
                if existing and (
                    len(existing) != 1 or tuple(existing[0]) != accepted_facts
                ):
                    raise RecoveryFenceError(
                        "A conflicting cutover baseline is already accepted."
                    )
                if not existing:
                    connection.execute(
                        "INSERT INTO mobile_recovery_accepted_baselines "
                        "(lineage_id, baseline_backup_id, minimum_backup_created_at, "
                        "manifest_sha256, schema_generation, baseline_db_checkpoint, "
                        "accepted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (*accepted_facts, now),
                    )
                cursor = connection.execute(
                    "UPDATE mobile_recovery_baseline_journals SET "
                    "phase = 'accepted', updated_at = ? "
                    "WHERE operation_id = ? AND phase = 'scratch_verified' "
                    "AND updated_at = ?",
                    (now, journal.operation_id, journal.updated_at),
                )
                if cursor.rowcount != 1:
                    raise RecoveryFenceError(
                        "The baseline journal changed before acceptance."
                    )
                row = connection.execute(
                    f"SELECT {_JOURNAL_COLUMNS} FROM "
                    "mobile_recovery_baseline_journals WHERE operation_id = ?",
                    (journal.operation_id,),
                ).fetchone()
                connection.commit()
            _fsync_directory(self._db_path.parent)
        except RecoveryFenceError:
            raise
        except sqlite3.Error:
            raise RecoveryFenceError("The baseline acceptance failed closed.") from None
        accepted = _journal_from_row(row)
        self._snapshot(accepted)
        self._hook("accepted")
        return accepted

    def initialize(
        self,
        *,
        operation_id: str,
        request_hash: str,
        approvals: BaselineApprovals,
    ) -> BaselineJournal:
        _valid_uuid(operation_id, label="baseline operation ID")
        _valid_sha256(request_hash, label="baseline request hash")
        if not isinstance(approvals, BaselineApprovals) or any(
            value is not True
            for value in (
                approvals.erasure_inventory_complete,
                approvals.dependent_routes_held,
                approvals.fresh_backup_authorized,
                approvals.scratch_restore_authorized,
            )
        ):
            raise RecoveryFenceError(
                "Every offline cutover baseline approval is required."
            )
        with _writer_lock(self._ledger.local_root):
            journal = self._prepare(
                operation_id=operation_id,
                request_hash=request_hash,
            )
            if _PHASE_INDEX[journal.phase] < _PHASE_INDEX["backup_verified"]:
                facts = _validated_backup_facts(
                    self._backup_verifier.create_and_verify(
                        lineage_id=journal.lineage_id
                    )
                )
                journal = self._advance(
                    journal,
                    "backup_verified",
                    backup_id=facts.backup_id,
                    backup_created_at=facts.backup_created_at,
                    schema_generation=facts.schema_generation,
                    manifest_sha256=facts.manifest_sha256,
                    baseline_db_checkpoint=facts.baseline_db_checkpoint,
                )
            facts = self._facts(journal)
            event = CutoverBaselineEvent(
                event_id=operation_id,
                cutoff_at=facts.backup_created_at,
                lineage_id=journal.lineage_id,
                minimum_backup_created_at=facts.backup_created_at,
                baseline_backup_id=facts.backup_id,
                manifest_sha256=facts.manifest_sha256,
                schema_generation=facts.schema_generation,
                baseline_db_checkpoint=facts.baseline_db_checkpoint,
            )

            def record_phase(
                phase: str,
                record: PublishedRecoveryRecord,
            ) -> None:
                nonlocal journal
                if phase == "record_published":
                    journal = self._advance(
                        journal,
                        phase,
                        record_key=record.record_key,
                        record_hash=record.record_hash,
                        chain_hmac_key_id=record.chain_hmac_key_id,
                    )
                elif phase == "head_published":
                    journal = self._advance(
                        journal,
                        phase,
                        head_etag=record.head_etag,
                    )
                else:  # pragma: no cover - ledger owns the closed callback set
                    raise RecoveryFenceError("Unknown baseline ledger phase.")

            resume_values = (
                journal.record_key,
                journal.record_hash,
                journal.chain_hmac_key_id,
            )
            if any(value is not None for value in resume_values) and any(
                value is None for value in resume_values
            ):
                raise RecoveryFenceError(
                    "The baseline journal record identity is incomplete."
                )
            resume_identity = (
                None
                if resume_values[0] is None
                else (resume_values[0], resume_values[1], resume_values[2])
            )
            record = self._ledger.append_and_publish(
                event,
                on_phase=record_phase,
                resume_record_identity=resume_identity,
            )
            journal = self._load(operation_id) or journal
            if _PHASE_INDEX[journal.phase] < _PHASE_INDEX["scratch_verified"]:
                proof = self._scratch_verifier.verify_exact(
                    lineage_id=journal.lineage_id,
                    facts=facts,
                    record=record,
                )
                if (
                    not isinstance(proof, ScratchVerificationProof)
                    or proof.verified is not True
                    or proof.lineage_id != journal.lineage_id
                    or proof.backup_id != facts.backup_id
                    or proof.manifest_sha256 != facts.manifest_sha256
                    or proof.baseline_db_checkpoint != facts.baseline_db_checkpoint
                    or proof.record_hash != record.record_hash
                ):
                    raise RecoveryFenceError(
                        "The exact scratch restore proof did not validate."
                    )
                journal = self._advance(journal, "scratch_verified")
            if journal.phase == "scratch_verified":
                journal = self._accept(journal, facts, record)
            return journal


@dataclass(frozen=True)
class StartupRecoveryInputs:
    schema_generation: int
    recovery_fence_row_count: int = 0
    baseline_journal_row_count: int = 0
    nonterminal_revocation_count: int = 0
    mobile_native_auth_enabled: bool = False
    mobile_device_management_enabled: bool = False
    mobile_privacy_enabled: bool = False
    history_reset_enabled: bool = False
    shopify_privacy_webhooks_enabled: bool = False
    accepted_baseline: bool = False
    dedicated_credentials_available: bool = False
    immutable_record_round_trip_verified: bool = False
    head_cas_round_trip_verified: bool = False
    current_chain_validated: bool = False


@dataclass(frozen=True)
class StartupRecoveryDecision:
    remote_io_required: bool
    startup_allowed: bool
    reasons: tuple[str, ...]
    missing_requirements: tuple[str, ...]


def decide_startup_recovery(inputs: StartupRecoveryInputs) -> StartupRecoveryDecision:
    """Pure policy only; Gate 3B intentionally performs no startup I/O."""

    if not isinstance(inputs, StartupRecoveryInputs):
        raise TypeError("StartupRecoveryInputs is required.")
    _valid_int(inputs.schema_generation, minimum=0, label="startup schema generation")
    counts = {
        "recovery_fence_state": inputs.recovery_fence_row_count,
        "baseline_journal": inputs.baseline_journal_row_count,
        "nonterminal_revocation": inputs.nonterminal_revocation_count,
    }
    for name, value in counts.items():
        _valid_int(value, minimum=0, label=name)
    flags = {
        "mobile_native_auth": inputs.mobile_native_auth_enabled,
        "mobile_device_management": inputs.mobile_device_management_enabled,
        "mobile_privacy": inputs.mobile_privacy_enabled,
        "history_reset": inputs.history_reset_enabled,
        "shopify_privacy_webhooks": inputs.shopify_privacy_webhooks_enabled,
    }
    if any(not isinstance(value, bool) for value in flags.values()):
        raise RecoveryFenceError("A startup recovery feature predicate is invalid.")
    reasons = tuple(
        [name for name, value in counts.items() if value > 0]
        + [name for name, value in flags.items() if value]
    )
    if not reasons:
        return StartupRecoveryDecision(
            remote_io_required=False,
            startup_allowed=True,
            reasons=(),
            missing_requirements=(),
        )
    requirements = {
        "accepted_baseline": inputs.accepted_baseline,
        "dedicated_credentials": inputs.dedicated_credentials_available,
        "immutable_record_round_trip": inputs.immutable_record_round_trip_verified,
        "head_cas_round_trip": inputs.head_cas_round_trip_verified,
        "current_chain_validation": inputs.current_chain_validated,
    }
    if any(not isinstance(value, bool) for value in requirements.values()):
        raise RecoveryFenceError("A startup recovery proof predicate is invalid.")
    missing = tuple(name for name, value in requirements.items() if not value)
    return StartupRecoveryDecision(
        remote_io_required=True,
        startup_allowed=not missing,
        reasons=reasons,
        missing_requirements=missing,
    )
