"""Offline preparation of verified backup copies for a later service cutover.

This module never promotes a candidate over ``/data/sessions`` and never starts
the web application or a worker.  It retains one verified extraction as
read-only evidence and mutates only a second uniquely named working tree.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping

from swinglab.web.mobile_schema import (
    MOBILE_STATE_GENERATIONS,
    MobileStateDomain,
    VersionedHMAC,
    detect_mobile_state_generation,
    ensure_mobile_state_schema,
    require_mobile_state_key_coverage,
    validate_mobile_state_schema,
)
from swinglab.web.recovery_fence_ledger import (
    CutoverBaselineInitializer,
    PublishedRecoveryRecord,
    RecoveryFenceLedger,
    RecoveryRecordKind,
    ScratchVerificationProof,
    ValidatedRecoveryChain,
    VerifiedBackupFacts,
    _durable_atomic_write,
    _fsync_directory,
)

from .core import (
    COMPLETE_FILE,
    DATABASE_BUNDLE_PATH,
    MANIFEST_FILE,
    BackupError,
    _canonical_json,
    _assert_safe_scratch_root,
    _copy_and_hash,
    _is_link_or_reparse,
    _join_under,
    _load_and_verify_manifest_snapshot,
    _load_json_bytes,
    _read_regular_file_snapshot,
    _safe_relative_path,
    _sha256_file,
    create_backup,
    load_and_verify_manifest,
    restore_backup,
    validate_backup_id,
    verify_bundle_files,
)
from .store import (
    RecoveryFenceRemoteStore,
    RecoveryFenceStoreSettings,
    S3Settings,
    download_bundle,
    upload_bundle,
)


@dataclass(frozen=True)
class RetainedRestoreEvidence:
    bundle_dir: Path
    restore_dir: Path
    manifest: dict[str, object]
    manifest_sha256: str
    complete_sha256: str
    file_sha256: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RestoredCredentialTableRegistry:
    generation: int
    table_names: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        generation = self.generation
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation not in MOBILE_STATE_GENERATIONS
        ):
            raise ValueError(
                "Restore credential tables must come from a generation-owned registry."
            )
        object.__setattr__(
            self,
            "table_names",
            MOBILE_STATE_GENERATIONS[generation].restored_credential_tables,
        )


DEFAULT_RESTORED_CREDENTIAL_TABLES = RestoredCredentialTableRegistry(1)


@dataclass(frozen=True)
class RestoreCredentialReset:
    marker_id: str
    users_reset: int
    rows_deleted: tuple[tuple[str, int], ...]
    already_prepared: bool


RecoveryRecordReconciler = Callable[
    [sqlite3.Connection, PublishedRecoveryRecord, VersionedHMAC], None
]


@dataclass(frozen=True)
class ServiceRestoreResult:
    ready: bool
    backup_id: str
    lineage_id: str
    manifest_sha256: str
    baseline_db_checkpoint: str
    head_record_hash: str
    retained_restore_dir: Path
    working_dir: Path
    credential_reset_marker_id: str
    readiness_receipt: str = field(repr=False)


@dataclass(frozen=True)
class RetainedBackupKeyUsageAudit:
    by_backup_id: tuple[tuple[str, tuple[str, ...]], ...]
    by_key_id: tuple[tuple[str, tuple[str, ...]], ...]


_READINESS_COMMIT_FORMAT = "caddieinsight-service-restore-readiness-commit/v1"
_READINESS_RECEIPT = re.compile(r"[0-9a-f]{64}")


def _readiness_commit_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.commit{path.suffix}")


def _readiness_path_sha256(path: Path) -> str:
    canonical_path = os.path.normcase(str(path.resolve(strict=False)))
    return hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()


def _accept_service_restore_readiness(
    path: Path,
    receipt: str | None,
) -> dict[str, object]:
    """Validate the only authoritative readiness representation."""

    if not isinstance(receipt, str) or _READINESS_RECEIPT.fullmatch(receipt) is None:
        raise BackupError("A service-restore readiness receipt is required.")
    commit_path = _readiness_commit_path(path)
    if any(
        _is_link_or_reparse(candidate) or not candidate.is_file()
        for candidate in (path, commit_path)
    ):
        raise BackupError("Service-restore readiness evidence is missing or unsafe.")
    try:
        marker_bytes = _read_regular_file_snapshot(path)
        commit_bytes = _read_regular_file_snapshot(commit_path)
        marker = _load_json_bytes(marker_bytes)
    except BackupError:
        raise BackupError(
            "Service-restore readiness evidence is missing or unsafe."
        ) from None
    marker_sha256 = hashlib.sha256(marker_bytes).hexdigest()
    receipt_sha256 = hashlib.sha256(receipt.encode("ascii")).hexdigest()
    expected_commit = _canonical_json(
        {
            "format": _READINESS_COMMIT_FORMAT,
            "marker_sha256": marker_sha256,
            "marker_path_sha256": _readiness_path_sha256(path),
            "receipt_sha256": receipt_sha256,
        }
    )
    if (
        _canonical_json(marker) != marker_bytes
        or not hmac.compare_digest(commit_bytes, expected_commit)
    ):
        raise BackupError("Service-restore readiness evidence did not authenticate.")
    return marker


def _discard_failed_readiness_artifacts(*paths: Path) -> None:
    """Best-effort diagnostics cleanup; receipt withholding is authoritative."""

    changed = False
    for path in paths:
        try:
            present = path.exists() or _is_link_or_reparse(path)
        except Exception:
            continue
        if not present:
            continue
        try:
            path.unlink()
            changed = True
            continue
        except OSError:
            quarantine = path.with_name(
                f".{path.name}.failed-{uuid.uuid4().hex}"
            )
        try:
            os.replace(path, quarantine)
            changed = True
        except OSError:
            pass
    if changed and paths:
        try:
            _fsync_directory(paths[0].parent)
        except Exception:
            pass


def _durably_publish_readiness(path: Path, body: bytes) -> str:
    commit_path = _readiness_commit_path(path)
    if (
        _is_link_or_reparse(path)
        or _is_link_or_reparse(commit_path)
        or path.exists()
        or commit_path.exists()
        or _is_link_or_reparse(path.parent)
        or not path.parent.is_dir()
    ):
        raise BackupError("The service-restore readiness path is unsafe.")
    receipt = secrets.token_hex(32)
    commit_body = _canonical_json(
        {
            "format": _READINESS_COMMIT_FORMAT,
            "marker_sha256": hashlib.sha256(body).hexdigest(),
            "marker_path_sha256": _readiness_path_sha256(path),
            "receipt_sha256": hashlib.sha256(receipt.encode("ascii")).hexdigest(),
        }
    )
    try:
        _durable_atomic_write(path, body, immutable=True)
        _durable_atomic_write(commit_path, commit_body, immutable=True)
        _accept_service_restore_readiness(path, receipt)
    except Exception:
        _discard_failed_readiness_artifacts(path, commit_path)
        raise BackupError("The service-restore readiness marker was not published.") from None
    return receipt


def audit_retained_backup_key_usage(
    bundle_dirs: Iterable[Path],
) -> RetainedBackupKeyUsageAudit:
    """Return verified deterministic key usage without claiming offline re-keying."""

    by_backup: dict[str, tuple[str, ...]] = {}
    for bundle_dir in bundle_dirs:
        manifest = load_and_verify_manifest(Path(bundle_dir))
        verify_bundle_files(Path(bundle_dir).expanduser().resolve(), manifest)
        backup_id = str(manifest["backup_id"])
        if backup_id in by_backup:
            raise BackupError("A retained backup ID was supplied more than once.")
        mobile_state = manifest.get("mobile_state")
        key_ids: tuple[str, ...] = ()
        if isinstance(mobile_state, dict):
            declared = mobile_state.get("referenced_hmac_key_ids")
            if not isinstance(declared, list):
                raise BackupError("A retained backup key attestation is invalid.")
            key_ids = tuple(str(key_id) for key_id in declared)
        by_backup[backup_id] = key_ids
    key_backups: dict[str, list[str]] = {}
    for backup_id, key_ids in by_backup.items():
        for key_id in key_ids:
            key_backups.setdefault(key_id, []).append(backup_id)
    return RetainedBackupKeyUsageAudit(
        by_backup_id=tuple(sorted(by_backup.items())),
        by_key_id=tuple(
            (key_id, tuple(sorted(backup_ids)))
            for key_id, backup_ids in sorted(key_backups.items())
        ),
    )


def _manifest_files(
    root: Path, manifest: dict[str, object], *, sessions_layout: bool
) -> tuple[tuple[str, Path, str, int], ...]:
    database = manifest["database"]
    artifacts = manifest["artifacts"]
    assert isinstance(database, dict) and isinstance(artifacts, dict)
    rows: list[tuple[str, Path, str, int]] = []
    database_relative = (
        PurePosixPath("swinglab.db")
        if sessions_layout
        else _safe_relative_path(str(database["path"]))
    )
    rows.append(
        (
            DATABASE_BUNDLE_PATH,
            _join_under(root, database_relative),
            str(database["sha256"]),
            int(database["size"]),
        )
    )
    files = artifacts["files"]
    assert isinstance(files, list)
    for item in files:
        assert isinstance(item, dict)
        relative = _safe_relative_path(str(item["path"]))
        destination_relative = (
            relative if sessions_layout else PurePosixPath("artifacts") / relative
        )
        rows.append(
            (
                f"artifacts/{relative.as_posix()}",
                _join_under(root, destination_relative),
                str(item["sha256"]),
                int(item["size"]),
            )
        )
    return tuple(rows)


def _verify_manifest_files(
    root: Path, manifest: dict[str, object], *, sessions_layout: bool
) -> tuple[tuple[str, str], ...]:
    verified = []
    for label, path, expected_sha256, expected_size in _manifest_files(
        root, manifest, sessions_layout=sessions_layout
    ):
        if _is_link_or_reparse(path) or not path.is_file():
            raise BackupError("A retained restore file is missing or unsafe.")
        sha256, size = _sha256_file(path)
        if sha256 != expected_sha256 or size != expected_size:
            raise BackupError("A retained restore file checksum did not match.")
        verified.append((label, sha256))
    return tuple(sorted(verified))


def _verify_evidence_metadata(
    evidence: RetainedRestoreEvidence,
    root: Path,
) -> None:
    for name, expected_sha256 in (
        (MANIFEST_FILE, evidence.manifest_sha256),
        (COMPLETE_FILE, evidence.complete_sha256),
    ):
        path = _join_under(root, _safe_relative_path(name))
        try:
            actual_sha256 = hashlib.sha256(
                _read_regular_file_snapshot(path)
            ).hexdigest()
        except BackupError:
            raise BackupError(
                "Retained restore metadata is missing or unsafe."
            ) from None
        if actual_sha256 != expected_sha256:
            raise BackupError("Retained restore metadata changed after validation.")


def retain_verified_restore_evidence(
    bundle_dir: Path, scratch_root: Path
) -> RetainedRestoreEvidence:
    """Extract one verified bundle and freeze every evidence file read-only."""

    expanded_bundle = bundle_dir.expanduser()
    metadata = _load_and_verify_manifest_snapshot(expanded_bundle)
    manifest = metadata.manifest
    bundle_dir = expanded_bundle.resolve()
    verify_bundle_files(bundle_dir, manifest)
    manifest_sha256 = metadata.manifest_sha256
    complete_sha256 = metadata.complete_sha256
    bundle_hashes = _verify_manifest_files(
        bundle_dir, manifest, sessions_layout=False
    )
    restored = restore_backup(bundle_dir, scratch_root)
    restore_dir = Path(restored["restore_dir"])
    try:
        for metadata_name, expected_sha256 in (
            (MANIFEST_FILE, manifest_sha256),
            (COMPLETE_FILE, complete_sha256),
        ):
            sha256, _ = _copy_and_hash(
                bundle_dir / metadata_name, restore_dir / metadata_name
            )
            if sha256 != expected_sha256:
                raise BackupError("Retained restore metadata did not match the bundle.")
        retained_hashes = _verify_manifest_files(
            restore_dir, manifest, sessions_layout=False
        )
        if retained_hashes != bundle_hashes:
            raise BackupError("Retained restore evidence diverged from the bundle.")
        for _label, path, _sha256, _size in _manifest_files(
            restore_dir, manifest, sessions_layout=False
        ):
            path.chmod(0o400)
        (restore_dir / MANIFEST_FILE).chmod(0o400)
        (restore_dir / COMPLETE_FILE).chmod(0o400)
        final_metadata = _load_and_verify_manifest_snapshot(bundle_dir)
        verify_bundle_files(bundle_dir, final_metadata.manifest)
        if (
            final_metadata.manifest != manifest
            or final_metadata.manifest_sha256 != manifest_sha256
            or final_metadata.complete_sha256 != complete_sha256
            or _verify_manifest_files(
                bundle_dir, manifest, sessions_layout=False
            )
            != bundle_hashes
        ):
            raise BackupError("The immutable backup changed during extraction.")
        return RetainedRestoreEvidence(
            bundle_dir=bundle_dir,
            restore_dir=restore_dir,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            complete_sha256=complete_sha256,
            file_sha256=retained_hashes,
        )
    except Exception:
        # The unique retained directory remains available for diagnosis. It is
        # never selected as a service candidate after an exception.
        raise


def create_service_working_copy(
    evidence: RetainedRestoreEvidence, scratch_root: Path
) -> Path:
    """Make and additively migrate a unique sessions-layout working copy."""

    if not isinstance(evidence, RetainedRestoreEvidence):
        raise TypeError("RetainedRestoreEvidence is required.")
    root = _assert_safe_scratch_root(scratch_root, evidence.bundle_dir)
    _verify_evidence_metadata(evidence, evidence.bundle_dir)
    _verify_evidence_metadata(evidence, evidence.restore_dir)
    before = _verify_manifest_files(
        evidence.restore_dir, evidence.manifest, sessions_layout=False
    )
    if before != evidence.file_sha256:
        raise BackupError("Retained restore evidence changed before working copy.")
    working_dir = root / (
        f"service-working-{evidence.manifest['backup_id']}-{uuid.uuid4().hex[:8]}"
    )
    working_dir.mkdir(mode=0o700, exist_ok=False)
    try:
        source_rows = _manifest_files(
            evidence.restore_dir, evidence.manifest, sessions_layout=False
        )
        destination_rows = _manifest_files(
            working_dir, evidence.manifest, sessions_layout=True
        )
        for source, destination in zip(source_rows, destination_rows, strict=True):
            label, source_path, expected_sha256, expected_size = source
            destination_label, destination_path, _, _ = destination
            if label != destination_label:
                raise BackupError("Working restore file mapping is invalid.")
            sha256, size = _copy_and_hash(source_path, destination_path)
            if sha256 != expected_sha256 or size != expected_size:
                raise BackupError("Working restore file checksum did not match.")
        if _verify_manifest_files(
            working_dir, evidence.manifest, sessions_layout=True
        ) != evidence.file_sha256:
            raise BackupError("Working restore copy did not match retained evidence.")
        if _verify_manifest_files(
            evidence.restore_dir, evidence.manifest, sessions_layout=False
        ) != evidence.file_sha256:
            raise BackupError("Retained restore evidence changed during working copy.")
        _verify_evidence_metadata(evidence, evidence.bundle_dir)
        _verify_evidence_metadata(evidence, evidence.restore_dir)

        db_path = working_dir / "swinglab.db"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            ensure_mobile_state_schema(connection)
            connection.commit()
            validate_mobile_state_schema(connection)
            if detect_mobile_state_generation(connection) != 1:
                raise BackupError("The service working copy did not reach generation 1.")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("The service working copy failed SQLite integrity.")
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return working_dir
    except sqlite3.Error as exc:
        raise BackupError("The service working copy migration failed closed.") from exc


def _marker_id(source_backup_id: str, source_lineage_id: str | None) -> str:
    material = (
        "caddieinsight-mobile-restore-credential-reset/v1\0"
        + source_backup_id
        + "\0"
        + (source_lineage_id or "")
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _restored_credential_table_inventory(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str], ...]:
    try:
        generation = detect_mobile_state_generation(connection)
    except (RuntimeError, sqlite3.Error) as exc:
        raise BackupError(
            "The restored credential schema generation is invalid."
        ) from exc
    registry = RestoredCredentialTableRegistry(generation)
    existing: dict[str, str] = {}
    for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ):
        actual = str(row[0])
        key = actual.casefold()
        prior = existing.setdefault(key, actual)
        if prior != actual:
            raise BackupError("SQLite table names are not canonical.")
    return tuple(
        (expected, existing[expected.casefold()])
        for expected in registry.table_names
        if expected.casefold() in existing
    )


def _quoted_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def prepare_restored_auth_state(
    connection: sqlite3.Connection,
    *,
    source_backup_id: str,
    source_lineage_id: str | None,
    now: float,
) -> RestoreCredentialReset:
    """Invalidate all restored credentials in one idempotent IMMEDIATE txn."""

    validate_backup_id(source_backup_id)
    if source_lineage_id is not None:
        try:
            lineage = uuid.UUID(source_lineage_id)
        except (ValueError, AttributeError):
            raise BackupError("The restore source lineage ID is invalid.") from None
        if str(lineage) != source_lineage_id:
            raise BackupError("The restore source lineage ID is not canonical.")
    if (
        isinstance(now, bool)
        or not isinstance(now, (int, float))
        or not math.isfinite(now)
        or now < 0
    ):
        raise BackupError("The restore preparation time is invalid.")
    if connection.in_transaction:
        raise BackupError("Restore credential preparation requires no outer transaction.")

    marker_id = _marker_id(source_backup_id, source_lineage_id)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT source_backup_id, source_lineage_id, prepared_at FROM "
            "mobile_restore_credential_reset_markers WHERE marker_id = ?",
            (marker_id,),
        ).fetchone()
        expected = (source_backup_id, source_lineage_id, float(now))
        if existing is not None:
            if tuple(existing[:2]) != expected[:2]:
                raise BackupError("The restore credential reset marker conflicts.")
            connection.commit()
            return RestoreCredentialReset(marker_id, 0, (), True)

        users_reset = connection.execute(
            "UPDATE users SET auth_epoch = auth_epoch + 1, password_hash = ''"
        ).rowcount
        deleted: list[tuple[str, int]] = []
        for expected, actual in _restored_credential_table_inventory(connection):
            count = connection.execute(
                f"DELETE FROM {_quoted_sqlite_identifier(actual)}"
            ).rowcount
            deleted.append((expected, count))
        connection.execute(
            "INSERT INTO mobile_restore_credential_reset_markers "
            "(marker_id, source_backup_id, source_lineage_id, prepared_at) "
            "VALUES (?, ?, ?, ?)",
            (marker_id, source_backup_id, source_lineage_id, float(now)),
        )
        connection.commit()
        return RestoreCredentialReset(
            marker_id=marker_id,
            users_reset=users_reset,
            rows_deleted=tuple(deleted),
            already_prepared=False,
        )
    except BackupError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise BackupError("The restored credential reset failed closed.") from exc


def _restored_user_epoch_snapshot(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, int], ...]:
    rows = connection.execute(
        "SELECT id, auth_epoch FROM users ORDER BY id"
    ).fetchall()
    if any(
        not isinstance(row[0], str)
        or not row[0]
        or isinstance(row[1], bool)
        or not isinstance(row[1], int)
        or row[1] < 0
        for row in rows
    ):
        raise BackupError("The restored user credential epochs are invalid.")
    return tuple((str(row[0]), int(row[1])) for row in rows)


def _validate_restored_auth_reset_postconditions(
    connection: sqlite3.Connection,
    *,
    expected_user_epochs: tuple[tuple[str, int], ...],
) -> None:
    for _expected, actual in _restored_credential_table_inventory(connection):
        if connection.execute(
            f"SELECT COUNT(*) FROM {_quoted_sqlite_identifier(actual)}"
        ).fetchone()[0] != 0:
            raise BackupError("The restored credential reset was not preserved.")
    current = connection.execute(
        "SELECT id, password_hash, auth_epoch FROM users ORDER BY id"
    ).fetchall()
    current_epochs = tuple((str(row[0]), row[2]) for row in current)
    if (
        current_epochs != expected_user_epochs
        or any(row[1] != "" for row in current)
    ):
        raise BackupError("The restored credential reset was not preserved.")


def _require_key_coverage(
    connection: sqlite3.Connection,
    *,
    keyring: VersionedHMAC,
    manifest_key_ids: object,
    chain: tuple[PublishedRecoveryRecord, ...] = (),
) -> None:
    if not isinstance(keyring, VersionedHMAC):
        raise TypeError("A VersionedHMAC keyring is required.")
    if not isinstance(manifest_key_ids, list):
        raise BackupError("The backup HMAC key attestation is unavailable.")
    key_ids = set(manifest_key_ids)
    for record in chain:
        key_ids.add(record.chain_hmac_key_id)
        if record.kind == RecoveryRecordKind.TOKEN_REVOKE.value:
            key_ids.update(
                {
                    str(record.payload["selector_hmac_key_id"]),
                    str(record.payload["token_verifier_hmac_key_id"]),
                }
            )
        elif record.kind == RecoveryRecordKind.PUSH_ENVIRONMENT_CUTOFF.value:
            key_id = record.payload.get("expo_project_hmac_key_id")
            if key_id is not None:
                key_ids.add(str(key_id))
        elif record.kind == RecoveryRecordKind.REVIEW_ACCESS_REVISION.value:
            credentials = record.payload.get("credential_hmacs", [])
            if isinstance(credentials, list):
                key_ids.update(
                    str(item.get("key_id"))
                    for item in credentials
                    if isinstance(item, dict) and item.get("key_id") is not None
                )
    try:
        require_mobile_state_key_coverage(connection, keyring)
        keyring.require_key_ids(key_ids)
    except RuntimeError as exc:
        raise BackupError(
            "Required mobile-state HMAC key ID coverage is unavailable."
        ) from exc


def _validated_service_chain(ledger: object) -> ValidatedRecoveryChain:
    loader = getattr(ledger, "load_chain_snapshot", None)
    if not callable(loader):
        raise BackupError("A validated recovery-chain snapshot provider is required.")
    try:
        snapshot = loader()
    except Exception as exc:
        # RecoveryFenceError is already safe, but normalizing here also keeps an
        # injected provider from leaking transport or credential details.
        raise BackupError("The current recovery-fence chain failed validation.") from exc
    if (
        not isinstance(snapshot, ValidatedRecoveryChain)
        or not isinstance(snapshot.head_etag, str)
        or not snapshot.head_etag
        or not snapshot.records
        or snapshot.records[-1].head_etag != snapshot.head_etag
    ):
        raise BackupError("The validated recovery-fence chain snapshot is invalid.")
    return snapshot


def _validate_candidate_lineage(
    evidence: RetainedRestoreEvidence,
    snapshot: ValidatedRecoveryChain,
) -> tuple[str, PublishedRecoveryRecord]:
    manifest = evidence.manifest
    extension = manifest.get("recovery_fence")
    if not isinstance(extension, dict):
        raise BackupError(
            "A service restore requires a cutover-baseline recovery manifest."
        )
    baseline = snapshot.records[0]
    if baseline.kind != RecoveryRecordKind.CUTOVER_BASELINE.value:
        raise BackupError("The recovery-fence genesis is not a cutover baseline.")
    payload = baseline.payload
    required_payload = {
        "lineage_id",
        "minimum_backup_created_at",
        "baseline_backup_id",
        "manifest_sha256",
        "schema_generation",
        "baseline_db_checkpoint",
    }
    if set(payload) != required_payload:
        raise BackupError("The recovery-fence baseline payload is invalid.")
    expected_extension = (
        payload["lineage_id"],
        payload["baseline_backup_id"],
        payload["schema_generation"],
        payload["baseline_db_checkpoint"],
    )
    actual_extension = (
        extension.get("lineage_id"),
        extension.get("baseline_backup_id"),
        extension.get("baseline_schema_generation"),
        extension.get("baseline_db_checkpoint"),
    )
    if actual_extension != expected_extension:
        raise BackupError("The backup recovery lineage does not match genesis.")
    created_at_value = manifest.get("created_at_epoch")
    minimum_value = payload["minimum_backup_created_at"]
    if (
        isinstance(created_at_value, bool)
        or not isinstance(created_at_value, (int, float))
        or not math.isfinite(created_at_value)
        or created_at_value < 0
        or isinstance(minimum_value, bool)
        or not isinstance(minimum_value, (int, float))
        or not math.isfinite(minimum_value)
        or minimum_value < 0
    ):
        raise BackupError("The service candidate creation time is invalid.")
    created_at = float(created_at_value)
    minimum_created_at = float(minimum_value)
    if created_at < minimum_created_at:
        raise BackupError("The backup predates the service-restore cutover baseline.")
    is_baseline = manifest["backup_id"] == payload["baseline_backup_id"]
    if is_baseline:
        if (
            extension.get("baseline_manifest_sha256") is not None
            or evidence.manifest_sha256 != payload["manifest_sha256"]
            or manifest["database"]["sha256"]
            != payload["baseline_db_checkpoint"]
            or created_at != minimum_created_at
        ):
            raise BackupError(
                "The exact baseline backup identity does not match remote genesis."
            )
    elif extension.get("baseline_manifest_sha256") != payload["manifest_sha256"]:
        raise BackupError(
            "The later backup does not bind the accepted baseline manifest."
        )
    return str(payload["lineage_id"]), baseline


def _validate_candidate_checkpoint_ancestry(
    connection: sqlite3.Connection,
    *,
    evidence: RetainedRestoreEvidence,
    snapshot: ValidatedRecoveryChain,
) -> None:
    """Prove a restored checkpoint is an exact ancestor of the loaded chain."""

    baseline = snapshot.records[0]
    is_baseline = (
        evidence.manifest["backup_id"] == baseline.payload["baseline_backup_id"]
    )
    rows = connection.execute(
        "SELECT lineage_id, baseline_backup_id, schema_generation, "
        "head_sequence, head_record_key, head_record_hash, head_etag, "
        "chain_hmac_key_id FROM mobile_recovery_fence_checkpoints "
        "WHERE checkpoint_id=1"
    ).fetchall()
    if not rows:
        if is_baseline:
            return
        raise BackupError("A later service candidate is missing its chain checkpoint.")
    if len(rows) != 1:
        raise BackupError("The restored recovery checkpoint is ambiguous.")
    row = rows[0]
    sequence = row[3]
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 1
        or sequence > len(snapshot.records)
    ):
        raise BackupError("The restored recovery checkpoint ancestry is invalid.")
    ancestor = snapshot.records[sequence - 1]
    expected = (
        baseline.payload["lineage_id"],
        baseline.payload["baseline_backup_id"],
        baseline.payload["schema_generation"],
        sequence,
        ancestor.record_key,
        ancestor.record_hash,
        ancestor.chain_hmac_key_id,
    )
    actual = (*row[:6], row[7])
    if actual != expected or not isinstance(row[6], str) or not row[6]:
        raise BackupError("The restored recovery checkpoint is not in chain ancestry.")
    if sequence == len(snapshot.records) and row[6] != snapshot.head_etag:
        raise BackupError("The restored recovery checkpoint HEAD is not current.")


def _validate_candidate_baseline_journal(
    connection: sqlite3.Connection,
    *,
    evidence: RetainedRestoreEvidence,
    snapshot: ValidatedRecoveryChain,
) -> None:
    baseline = snapshot.records[0]
    payload = baseline.payload
    rows = connection.execute(
        "SELECT operation_id, phase, request_hash, lineage_id, backup_id, "
        "backup_created_at, schema_generation, manifest_sha256, "
        "baseline_db_checkpoint, record_key, record_hash, head_etag, "
        "chain_hmac_key_id FROM mobile_recovery_baseline_journals "
        "WHERE lineage_id=?",
        (payload["lineage_id"],),
    ).fetchall()
    if len(rows) != 1 or str(rows[0][0]) != baseline.event_id:
        raise BackupError("The restored baseline journal does not match remote genesis.")
    row = rows[0]
    if re.fullmatch(r"[0-9a-f]{64}", str(row[2])) is None:
        raise BackupError("The restored baseline journal request identity is invalid.")
    is_baseline = (
        evidence.manifest["backup_id"] == payload["baseline_backup_id"]
    )
    if is_baseline:
        if row[1] != "lineage_prepared" or any(value is not None for value in row[4:]):
            raise BackupError("The exact baseline journal is not lineage-prepared.")
        return
    expected = (
        payload["baseline_backup_id"],
        float(payload["minimum_backup_created_at"]),
        payload["schema_generation"],
        payload["manifest_sha256"],
        payload["baseline_db_checkpoint"],
        baseline.record_key,
        baseline.record_hash,
    )
    if (
        row[1] != "accepted"
        or tuple(row[4:11]) != expected
        or not isinstance(row[11], str)
        or not row[11]
        or row[12] != baseline.chain_hmac_key_id
    ):
        raise BackupError("The accepted baseline journal conflicts with remote genesis.")


def _preflight_chain_owners(
    records: tuple[PublishedRecoveryRecord, ...],
    reconcilers: Mapping[str, RecoveryRecordReconciler],
) -> None:
    built_in = {
        RecoveryRecordKind.CUTOVER_BASELINE.value,
        RecoveryRecordKind.TOKEN_REVOKE.value,
    }
    reserved = {
        RecoveryRecordKind.PUSH_ENVIRONMENT_CUTOFF.value,
        RecoveryRecordKind.REVIEW_ACCESS_REVISION.value,
    }
    for record in records:
        if record.kind in built_in:
            continue
        if record.kind not in reserved or not callable(reconcilers.get(record.kind)):
            raise BackupError(
                "Every reserved recovery-fence record kind requires an owned reconciler."
            )


def _apply_token_revoke(
    connection: sqlite3.Connection,
    record: PublishedRecoveryRecord,
    keyring: VersionedHMAC,
) -> None:
    selector_key_id = str(record.payload["selector_hmac_key_id"])
    verifier_key_id = str(record.payload["token_verifier_hmac_key_id"])
    expected_selector = str(record.payload["selector_hmac"])
    expected_verifier = str(record.payload["token_verifier_hmac"])
    for selector, token_hash in connection.execute(
        "SELECT selector, token_hash FROM mobile_api_tokens"
    ).fetchall():
        selector_match = (
            keyring.digest_with_key(
                selector_key_id, MobileStateDomain.RECOVERY_SELECTOR, str(selector)
            )
            == expected_selector
        )
        verifier_match = (
            keyring.digest_with_key(
                verifier_key_id,
                MobileStateDomain.RECOVERY_TOKEN_VERIFIER,
                str(token_hash),
            )
            == expected_verifier
        )
        if selector_match != verifier_match:
            raise BackupError("A token-revoke recovery record matched only one identity.")
        if selector_match:
            connection.execute(
                "DELETE FROM mobile_api_tokens WHERE selector = ?", (selector,)
            )


def _apply_recovery_chain(
    connection: sqlite3.Connection,
    *,
    snapshot: ValidatedRecoveryChain,
    keyring: VersionedHMAC,
    reconcilers: Mapping[str, RecoveryRecordReconciler],
    now: float,
) -> None:
    baseline = snapshot.records[0]
    payload = baseline.payload
    accepted_facts = (
        payload["lineage_id"],
        payload["baseline_backup_id"],
        float(payload["minimum_backup_created_at"]),
        payload["manifest_sha256"],
        payload["schema_generation"],
        payload["baseline_db_checkpoint"],
    )
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT lineage_id, baseline_backup_id, minimum_backup_created_at, "
            "manifest_sha256, schema_generation, baseline_db_checkpoint FROM "
            "mobile_recovery_accepted_baselines"
        ).fetchall()
        if existing and (
            len(existing) != 1 or tuple(existing[0]) != accepted_facts
        ):
            raise BackupError("The restored accepted baseline conflicts with genesis.")
        if not existing:
            connection.execute(
                "INSERT INTO mobile_recovery_accepted_baselines "
                "(lineage_id, baseline_backup_id, minimum_backup_created_at, "
                "manifest_sha256, schema_generation, baseline_db_checkpoint, accepted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*accepted_facts, now),
            )

        journal = connection.execute(
            "SELECT lineage_id, head_etag FROM mobile_recovery_baseline_journals "
            "WHERE operation_id = ?",
            (baseline.event_id,),
        ).fetchone()
        if journal is None or str(journal[0]) != str(payload["lineage_id"]):
            raise BackupError("The restored baseline journal lineage conflicts.")
        baseline_head_etag = baseline.head_etag or journal[1] or snapshot.head_etag
        if not isinstance(baseline_head_etag, str) or not baseline_head_etag:
            raise BackupError("The restored baseline journal HEAD is unavailable.")
        connection.execute(
            "UPDATE mobile_recovery_baseline_journals SET phase='accepted', "
            "backup_id=?, backup_created_at=?, schema_generation=?, "
            "manifest_sha256=?, baseline_db_checkpoint=?, record_key=?, "
            "record_hash=?, head_etag=?, chain_hmac_key_id=?, updated_at=? "
            "WHERE operation_id=?",
            (
                payload["baseline_backup_id"],
                float(payload["minimum_backup_created_at"]),
                payload["schema_generation"],
                payload["manifest_sha256"],
                payload["baseline_db_checkpoint"],
                baseline.record_key,
                baseline.record_hash,
                baseline_head_etag,
                baseline.chain_hmac_key_id,
                now,
                baseline.event_id,
            ),
        )

        for record in snapshot.records[1:]:
            if record.kind == RecoveryRecordKind.TOKEN_REVOKE.value:
                _apply_token_revoke(connection, record, keyring)
            elif record.kind in {
                RecoveryRecordKind.PUSH_ENVIRONMENT_CUTOFF.value,
                RecoveryRecordKind.REVIEW_ACCESS_REVISION.value,
            }:
                reconcilers[record.kind](connection, record, keyring)
            else:
                raise BackupError("An unsupported recovery-fence kind was encountered.")

        head = snapshot.records[-1]
        connection.execute(
            "INSERT INTO mobile_recovery_fence_checkpoints "
            "(checkpoint_id, lineage_id, baseline_backup_id, schema_generation, "
            "head_sequence, head_record_key, head_record_hash, head_etag, "
            "chain_hmac_key_id, verified_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
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
                payload["lineage_id"],
                payload["baseline_backup_id"],
                payload["schema_generation"],
                head.sequence,
                head.record_key,
                head.record_hash,
                snapshot.head_etag,
                head.chain_hmac_key_id,
                now,
            ),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def prepare_service_restore(
    bundle_dir: Path,
    scratch_root: Path,
    *,
    ledger: object,
    keyring: VersionedHMAC,
    reconcilers: Mapping[str, RecoveryRecordReconciler] | None = None,
    now: float | None = None,
) -> ServiceRestoreResult:
    """Prepare—but never promote—one service-eligible disposable sessions tree."""

    prepared_at = time.time() if now is None else now
    evidence = retain_verified_restore_evidence(bundle_dir, scratch_root)
    mobile_state = evidence.manifest.get("mobile_state")
    recovery_fence = evidence.manifest.get("recovery_fence")
    if (
        not isinstance(mobile_state, dict)
        or mobile_state.get("generation") != 1
        or not isinstance(recovery_fence, dict)
    ):
        raise BackupError(
            "A generation-0 or pre-cutover bundle cannot be prepared for service."
        )
    working_dir = create_service_working_copy(evidence, scratch_root)
    connection = sqlite3.connect(working_dir / "swinglab.db")
    owners = dict(reconcilers or {})
    try:
        _require_key_coverage(
            connection,
            keyring=keyring,
            manifest_key_ids=mobile_state["referenced_hmac_key_ids"],
        )
        snapshot = _validated_service_chain(ledger)
        lineage_id, _baseline = _validate_candidate_lineage(evidence, snapshot)
        _validate_candidate_checkpoint_ancestry(
            connection,
            evidence=evidence,
            snapshot=snapshot,
        )
        _validate_candidate_baseline_journal(
            connection,
            evidence=evidence,
            snapshot=snapshot,
        )
        _preflight_chain_owners(snapshot.records, owners)
        _require_key_coverage(
            connection,
            keyring=keyring,
            manifest_key_ids=mobile_state["referenced_hmac_key_ids"],
            chain=snapshot.records,
        )
        reset = prepare_restored_auth_state(
            connection,
            source_backup_id=str(evidence.manifest["backup_id"]),
            source_lineage_id=lineage_id,
            now=float(prepared_at),
        )
        expected_user_epochs = _restored_user_epoch_snapshot(connection)
        _apply_recovery_chain(
            connection,
            snapshot=snapshot,
            keyring=keyring,
            reconcilers=owners,
            now=float(prepared_at),
        )
        _validate_restored_auth_reset_postconditions(
            connection,
            expected_user_epochs=expected_user_epochs,
        )
        validate_mobile_state_schema(connection)
        _require_key_coverage(
            connection,
            keyring=keyring,
            manifest_key_ids=mobile_state["referenced_hmac_key_ids"],
            chain=snapshot.records,
        )
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("The prepared service database failed integrity.")
        if connection.execute(
            "SELECT COUNT(*) FROM mobile_restore_credential_reset_markers "
            "WHERE marker_id=?",
            (reset.marker_id,),
        ).fetchone()[0] != 1:
            raise BackupError("The restore credential reset marker is missing.")
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("Service restore preparation failed closed.") from exc
    finally:
        connection.close()

    verify_bundle_files(evidence.bundle_dir, evidence.manifest)
    _verify_evidence_metadata(evidence, evidence.bundle_dir)
    _verify_evidence_metadata(evidence, evidence.restore_dir)
    if _verify_manifest_files(
        evidence.restore_dir, evidence.manifest, sessions_layout=False
    ) != evidence.file_sha256:
        raise BackupError("Retained restore evidence changed during preparation.")
    head = snapshot.records[-1]
    readiness = {
        "format": "caddieinsight-service-restore-readiness/v1",
        "backup_id": evidence.manifest["backup_id"],
        "lineage_id": lineage_id,
        "manifest_sha256": evidence.manifest_sha256,
        "baseline_db_checkpoint": snapshot.records[0].payload[
            "baseline_db_checkpoint"
        ],
        "head_sequence": head.sequence,
        "head_record_hash": head.record_hash,
        "credential_reset_marker_id": reset.marker_id,
        "prepared_at": float(prepared_at),
        "promoted": False,
    }
    ready_path = working_dir / "service-restore-ready.json"
    readiness_receipt = _durably_publish_readiness(
        ready_path,
        _canonical_json(readiness),
    )
    return ServiceRestoreResult(
        ready=True,
        backup_id=str(evidence.manifest["backup_id"]),
        lineage_id=lineage_id,
        manifest_sha256=evidence.manifest_sha256,
        baseline_db_checkpoint=str(
            snapshot.records[0].payload["baseline_db_checkpoint"]
        ),
        head_record_hash=head.record_hash,
        retained_restore_dir=evidence.restore_dir,
        working_dir=working_dir,
        credential_reset_marker_id=reset.marker_id,
        readiness_receipt=readiness_receipt,
    )


def _operator_root(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if _is_link_or_reparse(expanded):
        raise BackupError(f"The {label} cannot be a symlink or reparse point.")
    resolved = expanded.resolve()
    data_root = Path("/data").resolve()
    try:
        in_data = resolved == data_root or resolved.is_relative_to(data_root)
    except AttributeError:  # pragma: no cover - Python 3.9 compatibility
        try:
            resolved.relative_to(data_root)
            in_data = True
        except ValueError:
            in_data = False
    if in_data or not resolved.is_dir() or resolved == Path(resolved.anchor):
        raise BackupError(f"The {label} must be an existing non-/data directory.")
    return resolved


class ImmutableBundleBaselineBackupVerifier:
    """Create one lineage-fixed bundle and prove its immutable remote readback."""

    def __init__(
        self,
        *,
        sessions_dir: Path,
        bundle_root: Path,
        readback_root: Path,
        backup_settings: S3Settings,
        restore_settings: S3Settings,
        backup_client=None,
        restore_client=None,
        now_factory: Callable[[], datetime] | None = None,
    ):
        self._sessions_dir = sessions_dir.expanduser().resolve()
        self._bundle_root = _operator_root(bundle_root, label="baseline bundle root")
        self._readback_root = _operator_root(
            readback_root, label="baseline readback root"
        )
        if not isinstance(backup_settings, S3Settings) or not isinstance(
            restore_settings, S3Settings
        ):
            raise TypeError("Dedicated backup and restore S3 settings are required.")
        self._backup_settings = backup_settings
        self._restore_settings = restore_settings
        self._backup_client = backup_client
        self._restore_client = restore_client
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _lineage(value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError):
            raise BackupError("The baseline recovery lineage ID is invalid.") from None
        if str(parsed) != value:
            raise BackupError("The baseline recovery lineage ID is not canonical.")
        return value

    def _readback(self, backup_id: str) -> tuple[Path, dict[str, object]]:
        target = self._readback_root / (
            f"readback-{backup_id}-{uuid.uuid4().hex[:8]}"
        )
        manifest = download_bundle(
            backup_id,
            target,
            self._restore_settings,
            client=self._restore_client,
        )
        return target, manifest

    @staticmethod
    def _facts(
        bundle_dir: Path, manifest: dict[str, object], *, lineage_id: str
    ) -> VerifiedBackupFacts:
        metadata = _load_and_verify_manifest_snapshot(bundle_dir)
        if metadata.manifest != manifest:
            raise BackupError("The baseline bundle manifest identity changed.")
        manifest = metadata.manifest
        verify_bundle_files(bundle_dir, manifest)
        extension = manifest.get("recovery_fence")
        mobile_state = manifest.get("mobile_state")
        if (
            not isinstance(extension, dict)
            or extension.get("lineage_id") != lineage_id
            or extension.get("baseline_backup_id") != manifest.get("backup_id")
            or extension.get("baseline_manifest_sha256") is not None
            or not isinstance(mobile_state, dict)
            or mobile_state.get("generation") != 1
        ):
            raise BackupError("The baseline bundle lineage facts are invalid.")
        manifest_sha256 = metadata.manifest_sha256
        database = manifest["database"]
        assert isinstance(database, dict)
        database_sha256 = str(database["sha256"])
        if extension.get("baseline_db_checkpoint") != database_sha256:
            raise BackupError("The baseline database checkpoint does not match.")
        return VerifiedBackupFacts(
            backup_id=str(manifest["backup_id"]),
            backup_created_at=float(manifest["created_at_epoch"]),
            schema_generation=int(mobile_state["generation"]),
            manifest_sha256=manifest_sha256,
            manifest_database_sha256=database_sha256,
            baseline_db_checkpoint=database_sha256,
        )

    def create_and_verify(self, *, lineage_id: str) -> VerifiedBackupFacts:
        lineage_id = self._lineage(lineage_id)
        local_bundle = self._bundle_root / f"cutover-baseline-{lineage_id}"
        created = not local_bundle.exists()
        if created:
            captured_at = self._now_factory()
            if not isinstance(captured_at, datetime):
                raise BackupError("The baseline backup clock is invalid.")
            create_backup(
                self._sessions_dir,
                local_bundle,
                now=captured_at,
                baseline_lineage_id=lineage_id,
            )
        local_manifest = load_and_verify_manifest(local_bundle)
        local_facts = self._facts(
            local_bundle, local_manifest, lineage_id=lineage_id
        )
        claim_nonce = hashlib.sha256(
            (
                "caddieinsight-cutover-baseline-upload-claim/v1\0"
                + lineage_id
                + "\0"
                + local_facts.manifest_sha256
            ).encode("utf-8")
        ).hexdigest()

        readback: tuple[Path, dict[str, object]] | None = None
        if not created:
            try:
                readback = self._readback(local_facts.backup_id)
            except BackupError:
                # A prior attempt may have stopped before COMPLETE became
                # visible. The immutable upload claim below distinguishes an
                # exact retry from a competing writer.
                pass
        if readback is None:
            try:
                upload_bundle(
                    local_bundle,
                    self._backup_settings,
                    client=self._backup_client,
                    claim_nonce=claim_nonce,
                )
            except BackupError as upload_error:
                # COMPLETE may have committed even if its response was lost.
                try:
                    readback = self._readback(local_facts.backup_id)
                except BackupError:
                    raise upload_error
            if readback is None:
                readback = self._readback(local_facts.backup_id)
        readback_dir, readback_manifest = readback
        readback_facts = self._facts(
            readback_dir, readback_manifest, lineage_id=lineage_id
        )
        if readback_facts != local_facts:
            raise BackupError("The immutable baseline readback identity diverged.")
        return readback_facts


class ExactScratchBaselineVerifier:
    """Bind Gate 3B's scratch proof to the full Gate 3C service path."""

    def __init__(
        self,
        *,
        readback_root: Path,
        scratch_root: Path,
        restore_settings: S3Settings,
        ledger: object,
        keyring: VersionedHMAC,
        restore_client=None,
        reconcilers: Mapping[str, RecoveryRecordReconciler] | None = None,
        now_factory: Callable[[], float] | None = None,
    ):
        self._readback_root = _operator_root(
            readback_root, label="baseline scratch readback root"
        )
        self._scratch_root = _operator_root(
            scratch_root, label="baseline service scratch root"
        )
        if not isinstance(restore_settings, S3Settings):
            raise TypeError("Dedicated restore S3 settings are required.")
        if not isinstance(keyring, VersionedHMAC):
            raise TypeError("A VersionedHMAC keyring is required.")
        self._restore_settings = restore_settings
        self._restore_client = restore_client
        self._ledger = ledger
        self._keyring = keyring
        self._reconcilers = dict(reconcilers or {})
        self._now_factory = now_factory or time.time
        self._last_result: ServiceRestoreResult | None = None

    @property
    def last_result(self) -> ServiceRestoreResult | None:
        return self._last_result

    def verify_exact(
        self,
        *,
        lineage_id: str,
        facts: VerifiedBackupFacts,
        record: PublishedRecoveryRecord,
    ) -> ScratchVerificationProof:
        if not isinstance(facts, VerifiedBackupFacts) or not isinstance(
            record, PublishedRecoveryRecord
        ):
            raise BackupError("Exact baseline proof inputs are invalid.")
        if (
            record.kind != RecoveryRecordKind.CUTOVER_BASELINE.value
            or record.payload.get("lineage_id") != lineage_id
            or record.payload.get("baseline_backup_id") != facts.backup_id
            or record.payload.get("manifest_sha256") != facts.manifest_sha256
            or record.payload.get("schema_generation") != facts.schema_generation
            or record.payload.get("baseline_db_checkpoint")
            != facts.baseline_db_checkpoint
            or record.head_etag is None
        ):
            raise BackupError("The exact baseline recovery record does not match facts.")
        readback_dir = self._readback_root / (
            f"scratch-readback-{facts.backup_id}-{uuid.uuid4().hex[:8]}"
        )
        manifest = download_bundle(
            facts.backup_id,
            readback_dir,
            self._restore_settings,
            client=self._restore_client,
        )
        readback_facts = ImmutableBundleBaselineBackupVerifier._facts(
            readback_dir, manifest, lineage_id=lineage_id
        )
        if readback_facts != facts:
            raise BackupError("The scratch candidate does not match verified facts.")
        result = prepare_service_restore(
            readback_dir,
            self._scratch_root,
            ledger=self._ledger,
            keyring=self._keyring,
            reconcilers=self._reconcilers,
            now=self._now_factory(),
        )
        if (
            result.backup_id != facts.backup_id
            or result.lineage_id != lineage_id
            or result.manifest_sha256 != facts.manifest_sha256
            or result.baseline_db_checkpoint != facts.baseline_db_checkpoint
            or result.head_record_hash != record.record_hash
        ):
            raise BackupError("The service scratch proof identity diverged.")
        self._last_result = result
        return ScratchVerificationProof(
            verified=True,
            lineage_id=lineage_id,
            backup_id=facts.backup_id,
            manifest_sha256=facts.manifest_sha256,
            baseline_db_checkpoint=facts.baseline_db_checkpoint,
            record_hash=record.record_hash,
        )


class OfflineServiceRestoreOperator:
    """Explicit CLI adapter that prepares scratch state and cannot promote it."""

    def __init__(
        self,
        *,
        scratch_root: Path,
        ledger: RecoveryFenceLedger,
        keyring: VersionedHMAC,
        reconcilers: Mapping[str, RecoveryRecordReconciler] | None = None,
    ):
        self._scratch_root = scratch_root
        self._ledger = ledger
        self._keyring = keyring
        self._reconcilers = dict(reconcilers or {})

    def prepare(self, bundle_dir: Path) -> ServiceRestoreResult:
        return prepare_service_restore(
            bundle_dir,
            self._scratch_root,
            ledger=self._ledger,
            keyring=self._keyring,
            reconcilers=self._reconcilers,
        )


@dataclass(frozen=True)
class RecoveryFenceOperatorComposition:
    initializer: CutoverBaselineInitializer | None
    service_restorer: OfflineServiceRestoreOperator


def _private_operator_child(root: Path, name: str) -> Path:
    child = root / name
    if child.exists() and (_is_link_or_reparse(child) or not child.is_dir()):
        raise BackupError("An operator working directory is unsafe.")
    child.mkdir(mode=0o700, exist_ok=True)
    child.chmod(0o700)
    return child


def compose_recovery_fence_operator(args: object) -> RecoveryFenceOperatorComposition:
    """Lazily compose real offline adapters after CLI gates and approvals."""

    operator_value = getattr(args, "operator_root", None)
    sessions_value = getattr(args, "sessions_dir", None)
    command = getattr(args, "recovery_fence_command", None)
    if not isinstance(operator_value, Path) or not isinstance(sessions_value, Path):
        raise BackupError("Explicit operator and sessions roots are required.")
    if command not in {"initialize-baseline", "restore-to-service"}:
        raise BackupError("A closed recovery-fence operator command is required.")
    operator_root = _operator_root(operator_value, label="recovery operator root")
    sessions_dir = sessions_value.expanduser().resolve()
    if _is_link_or_reparse(sessions_value.expanduser()) or not sessions_dir.is_dir():
        raise BackupError("The recovery sessions directory is missing or unsafe.")
    try:
        overlap = operator_root.is_relative_to(sessions_dir) or sessions_dir.is_relative_to(
            operator_root
        )
    except AttributeError:  # pragma: no cover - Python 3.9 compatibility
        try:
            operator_root.relative_to(sessions_dir)
            overlap = True
        except ValueError:
            try:
                sessions_dir.relative_to(operator_root)
                overlap = True
            except ValueError:
                overlap = False
    if overlap:
        raise BackupError("Operator storage cannot overlap the live sessions tree.")
    db_path = sessions_dir / "swinglab.db"
    if _is_link_or_reparse(db_path) or not db_path.is_file():
        raise BackupError("The recovery sessions database is missing or unsafe.")

    scratch_root = _private_operator_child(operator_root, "service-scratch")
    keyring = VersionedHMAC.from_env(required=True)
    assert keyring is not None
    recovery_settings = RecoveryFenceStoreSettings.from_env()
    remote = RecoveryFenceRemoteStore(recovery_settings)
    ledger = RecoveryFenceLedger(
        remote_store=remote,
        keyring=keyring,
        local_root=sessions_dir,
        db_path=db_path,
    )
    service_restorer = OfflineServiceRestoreOperator(
        scratch_root=scratch_root,
        ledger=ledger,
        keyring=keyring,
    )
    if command == "restore-to-service":
        return RecoveryFenceOperatorComposition(
            initializer=None,
            service_restorer=service_restorer,
        )

    bundle_root = _private_operator_child(operator_root, "baseline-bundles")
    readback_root = _private_operator_child(operator_root, "verified-readbacks")
    backup_settings = S3Settings.from_env(role="backup")
    restore_settings = S3Settings.from_env(role="restore")
    backup_verifier = ImmutableBundleBaselineBackupVerifier(
        sessions_dir=sessions_dir,
        bundle_root=bundle_root,
        readback_root=readback_root,
        backup_settings=backup_settings,
        restore_settings=restore_settings,
    )
    scratch_verifier = ExactScratchBaselineVerifier(
        readback_root=readback_root,
        scratch_root=scratch_root,
        restore_settings=restore_settings,
        ledger=ledger,
        keyring=keyring,
    )
    initializer = CutoverBaselineInitializer(
        ledger=ledger,
        db_path=db_path,
        backup_verifier=backup_verifier,
        scratch_verifier=scratch_verifier,
    )
    return RecoveryFenceOperatorComposition(
        initializer=initializer,
        service_restorer=service_restorer,
    )
