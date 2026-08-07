"""WAL-safe SQLite snapshots and verified session-artifact backups."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from swinglab.web.mobile_schema import (
    MOBILE_STATE_GENERATIONS,
    detect_mobile_state_generation,
    mobile_state_summary,
)

FORMAT = "caddieinsight-backup/v1"
COMPLETE_FILE = "COMPLETE.json"
MANIFEST_FILE = "manifest.json"
DATABASE_BUNDLE_PATH = "database/swinglab.db"

CRITICAL_TABLES = (
    "jobs",
    "users",
    "pro_grants",
    "shopify_orders",
    "gear_orders",
    "email_codes",
    "auth_attempts",
    "shopify_sync_control",
    "shopify_privacy_event_fences",
    "shopify_redacted_order_fences",
    "shopify_privacy_requests",
    "shopify_customer_tombstones",
    "shopify_pending_customer_links",
)

# Added after the original v1 bundle format shipped.  A current database must
# contain both tables (quota receipts and the crash-recovery journal) and they
# receive the same count/digest protection as every critical ledger.  A legacy
# v1 snapshot with neither table remains restorable so upgrades do not strand
# an otherwise valid backup; a partial pair fails closed.
HISTORY_STATE_TABLES = (
    "analysis_usage_monthly",
    "history_reset_operations",
)

_BACKUP_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")


class BackupError(RuntimeError):
    """A safe-to-display backup or restore failure."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _backup_id(now: datetime) -> str:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def validate_backup_id(value: str) -> str:
    if not _BACKUP_ID_RE.fullmatch(value):
        raise BackupError("Invalid backup identifier.")
    return value


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether ``path`` is a symlink or Windows reparse point."""

    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise BackupError("A backup path could not be inspected safely.") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _copy_and_hash(source: Path, destination: Path) -> tuple[str, int]:
    if _is_link_or_reparse(source) or not source.is_file():
        raise BackupError("A required artifact is not a regular file.")
    try:
        before = source.stat()
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as src, destination.open("xb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(chunk)
                dst.write(chunk)
                size += len(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        after = source.stat()
    except (FileNotFoundError, OSError) as exc:
        raise BackupError(
            "An artifact disappeared or could not be read; no backup was completed."
        ) from exc
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or size != before.st_size
    ):
        destination.unlink(missing_ok=True)
        raise BackupError(
            "An artifact changed while it was being captured; no backup was completed."
        )
    return digest.hexdigest(), size


def _read_only_connection(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _online_sqlite_snapshot(source: Path, destination: Path) -> None:
    if _is_link_or_reparse(source) or not source.is_file():
        raise BackupError("The SQLite source database is missing or is not a regular file.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = _read_only_connection(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection, pages=1024, sleep=0.05)
        destination_connection.commit()
    except sqlite3.Error as exc:
        raise BackupError("The WAL-safe SQLite snapshot failed.") from exc
    finally:
        destination_connection.close()
        source_connection.close()


def _table_digest(connection: sqlite3.Connection, table: str) -> str:
    columns = [
        row["name"]
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]
    digest = hashlib.sha256()
    digest.update(_canonical_json(columns))
    for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
        values = []
        for value in row:
            if isinstance(value, bytes):
                values.append({"bytes_sha256": hashlib.sha256(value).hexdigest()})
            else:
                values.append(value)
        digest.update(_canonical_json(values))
    return digest.hexdigest()


def _one(connection: sqlite3.Connection, sql: str) -> int | float:
    value = connection.execute(sql).fetchone()[0]
    return 0 if value is None else value


def database_summary(db_path: Path, captured_at_epoch: float) -> dict[str, Any]:
    connection = _read_only_connection(db_path)
    try:
        integrity_rows = [
            row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        if integrity_rows != ["ok"]:
            raise BackupError("SQLite integrity_check did not return exactly 'ok'.")

        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(set(CRITICAL_TABLES) - existing_tables)
        if missing:
            raise BackupError(
                "The SQLite snapshot is missing required application tables."
            )

        history_tables_present = set(HISTORY_STATE_TABLES) & existing_tables
        user_columns = {
            row["name"]
            for row in connection.execute('PRAGMA table_info("users")')
        }
        history_epoch_present = "history_epoch" in user_columns
        history_tables_complete = history_tables_present == set(
            HISTORY_STATE_TABLES
        )
        # The epoch column and both tables were introduced as one logical
        # migration. All absent is a genuine legacy v1 database; every mixed
        # state signals schema/data loss and must not be blessed as a backup.
        if (
            bool(history_tables_present) and not history_tables_complete
        ) or history_tables_complete != history_epoch_present:
            raise BackupError(
                "The SQLite snapshot has incomplete history-reset state."
            )
        counts = {
            table: int(_one(connection, f'SELECT COUNT(*) FROM "{table}"'))
            for table in CRITICAL_TABLES
        }
        history_counts = {
            table: int(_one(connection, f'SELECT COUNT(*) FROM "{table}"'))
            for table in HISTORY_STATE_TABLES
            if table in history_tables_present
        }
        if history_counts.get("history_reset_operations", 0):
            raise BackupError(
                "History cleanup is pending; finish recovery before backing up."
            )
        digests = {
            table: _table_digest(connection, table) for table in CRITICAL_TABLES
        }
        history_digests = {
            table: _table_digest(connection, table)
            for table in HISTORY_STATE_TABLES
            if table in history_tables_present
        }

        violations = {
            "negative_pro_until": int(
                _one(connection, "SELECT COUNT(*) FROM users WHERE pro_until < 0")
            ),
            "negative_pending_grant_days": int(
                _one(connection, "SELECT COUNT(*) FROM pro_grants WHERE days < 0")
            ),
            "negative_shopify_order_days": int(
                _one(connection, "SELECT COUNT(*) FROM shopify_orders WHERE days < 0")
            ),
            "nonpositive_gear_quantity": int(
                _one(connection, "SELECT COUNT(*) FROM gear_orders WHERE quantity <= 0")
            ),
        }
        history_violations: dict[str, int] = {}
        if "analysis_usage_monthly" in history_tables_present:
            history_violations["negative_analysis_usage"] = int(
                _one(
                    connection,
                    "SELECT COUNT(*) FROM analysis_usage_monthly"
                    " WHERE coaching_eligible < 0 OR refilm_rejections < 0",
                )
            )
        if any(violations.values()) or any(history_violations.values()):
            raise BackupError("Entitlement or purchase-ledger invariants failed.")

        reconciliation = {
            "entitlements": {
                "stripe_entitled_users": int(
                    _one(
                        connection,
                        "SELECT COUNT(*) FROM users WHERE plan = 'pro' "
                        "AND subscription_status IN ('active', 'trialing', 'past_due')",
                    )
                ),
                "time_entitled_users_at_capture": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM users WHERE pro_until > ?",
                        (captured_at_epoch,),
                    ).fetchone()[0]
                ),
                "pending_grants": counts["pro_grants"],
                "pending_grant_days": float(
                    _one(connection, "SELECT COALESCE(SUM(days), 0) FROM pro_grants")
                ),
            },
            "shopify_purchase_ledger": {
                "orders": counts["shopify_orders"],
                "active_orders": int(
                    _one(
                        connection,
                        "SELECT COUNT(*) FROM shopify_orders WHERE days > 0",
                    )
                ),
                "voided_orders": int(
                    _one(
                        connection,
                        "SELECT COUNT(*) FROM shopify_orders WHERE days = 0",
                    )
                ),
                "remaining_recorded_days": float(
                    _one(
                        connection,
                        "SELECT COALESCE(SUM(days), 0) FROM shopify_orders",
                    )
                ),
            },
            "gear_purchase_ledger": {
                "rows": counts["gear_orders"],
                "orders": int(
                    _one(
                        connection,
                        "SELECT COUNT(DISTINCT order_id) FROM gear_orders",
                    )
                ),
                "quantity": int(
                    _one(
                        connection,
                        "SELECT COALESCE(SUM(quantity), 0) FROM gear_orders",
                    )
                ),
                "cancelled_rows": int(
                    _one(
                        connection,
                        "SELECT COUNT(*) FROM gear_orders "
                        "WHERE cancelled_at IS NOT NULL",
                    )
                ),
            },
        }
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_violations:
            raise BackupError("SQLite foreign_key_check reported violations.")
        summary = {
            "integrity_check": "ok",
            "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            "critical_table_counts": counts,
            "critical_table_sha256": digests,
            "reconciliation": reconciliation,
            "invariant_violations": violations,
        }
        if history_tables_present:
            # Keep the original v1 fields byte-for-byte compatible with older
            # restore readers. They ignore these additive extension fields;
            # current readers verify them when present.
            summary.update(
                {
                    "history_state_table_counts": history_counts,
                    "history_state_table_sha256": history_digests,
                    "history_state_invariant_violations": history_violations,
                }
            )
        return summary
    except sqlite3.Error as exc:
        raise BackupError("SQLite verification failed.") from exc
    finally:
        connection.close()


def _snapshot_mobile_state(db_path: Path) -> dict[str, object] | None:
    connection = _read_only_connection(db_path)
    try:
        generation = detect_mobile_state_generation(connection)
        if generation == 0:
            return None
        return mobile_state_summary(connection)
    except (RuntimeError, sqlite3.Error) as exc:
        raise BackupError(
            "The SQLite snapshot has invalid or incomplete mobile state."
        ) from exc
    finally:
        connection.close()


def _canonical_uuid(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise BackupError(f"The backup {label} is invalid.")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise BackupError(f"The backup {label} is invalid.") from None
    if str(parsed) != value:
        raise BackupError(f"The backup {label} is not canonical.")
    return value


def _snapshot_recovery_fence(
    db_path: Path,
    *,
    backup_id: str,
    database_sha256: str,
    mobile_state: dict[str, object] | None,
    baseline_lineage_id: str | None,
) -> dict[str, object] | None:
    if baseline_lineage_id is not None:
        lineage_id = _canonical_uuid(
            baseline_lineage_id, label="recovery lineage ID"
        )
        if mobile_state is None or mobile_state.get("generation") != 1:
            raise BackupError(
                "A cutover baseline requires an exact generation-1 mobile snapshot."
            )
    connection = _read_only_connection(db_path)
    try:
        if mobile_state is None:
            return None
        accepted = connection.execute(
            "SELECT lineage_id, baseline_backup_id, manifest_sha256, "
            "schema_generation, baseline_db_checkpoint FROM "
            "mobile_recovery_accepted_baselines"
        ).fetchall()
        if len(accepted) > 1:
            raise BackupError(
                "The SQLite snapshot has conflicting accepted recovery baselines."
            )
        if baseline_lineage_id is not None:
            if accepted:
                raise BackupError(
                    "An accepted recovery baseline already exists for this snapshot."
                )
            matching = connection.execute(
                "SELECT phase FROM mobile_recovery_baseline_journals "
                "WHERE lineage_id = ?",
                (lineage_id,),
            ).fetchall()
            if len(matching) != 1 or str(matching[0][0]) != "lineage_prepared":
                raise BackupError(
                    "The cutover baseline lineage is not uniquely prepared."
                )
            return {
                "lineage_id": lineage_id,
                "baseline_backup_id": backup_id,
                # The canonical baseline marker is null because embedding the
                # final manifest digest inside that manifest is impossible.
                "baseline_manifest_sha256": None,
                "baseline_schema_generation": int(mobile_state["generation"]),
                "baseline_db_checkpoint": database_sha256,
            }
        if not accepted:
            return None
        row = accepted[0]
        lineage_id = _canonical_uuid(row[0], label="recovery lineage ID")
        baseline_backup_id = validate_backup_id(str(row[1]))
        manifest_sha256 = str(row[2])
        schema_generation = row[3]
        checkpoint = str(row[4])
        if (
            re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
            or isinstance(schema_generation, bool)
            or not isinstance(schema_generation, int)
            or schema_generation not in MOBILE_STATE_GENERATIONS
            or schema_generation == 0
            or re.fullmatch(r"[0-9a-f]{64}", checkpoint) is None
        ):
            raise BackupError(
                "The SQLite snapshot accepted recovery baseline is invalid."
            )
        return {
            "lineage_id": lineage_id,
            "baseline_backup_id": baseline_backup_id,
            "baseline_manifest_sha256": manifest_sha256,
            "baseline_schema_generation": schema_generation,
            "baseline_db_checkpoint": checkpoint,
        }
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError(
            "The SQLite snapshot recovery baseline could not be verified."
        ) from exc
    finally:
        connection.close()


def _safe_relative_path(value: str) -> PurePosixPath:
    """Parse a manifest path using platform-independent POSIX semantics."""
    candidate = PurePosixPath(value)
    if (
        not value
        or candidate.is_absolute()
        or value.startswith(("/", "\\"))
        or "\\" in value
        or ":" in value
        or value != candidate.as_posix()
        or any(part in ("", ".", "..") for part in candidate.parts)
    ):
        raise BackupError("The backup contains an unsafe relative path.")
    return candidate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _join_under(root: Path, relative: PurePosixPath) -> Path:
    """Join a safe manifest path and reject link/reparse traversal."""
    resolved_root = root.resolve()
    candidate = resolved_root
    for part in relative.parts:
        candidate = candidate / part
        if _is_link_or_reparse(candidate):
            raise BackupError("Symlinks or reparse points are not allowed in backup paths.")
    candidate = candidate.resolve()
    if not _is_relative_to(candidate, resolved_root):
        raise BackupError("A backup path escapes its expected root.")
    return candidate


def _safe_job_root(sessions_dir: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", job_id):
        raise BackupError("A job identifier cannot be mapped safely to an artifact path.")
    root = _join_under(sessions_dir, PurePosixPath(job_id))
    if not _is_relative_to(root, sessions_dir):
        raise BackupError("A job artifact path escapes the sessions directory.")
    return root


def _artifact_sources(
    sessions_dir: Path, snapshot_db: Path
) -> Iterable[tuple[Path, Path]]:
    connection = _read_only_connection(snapshot_db)
    try:
        rows = connection.execute(
            "SELECT id, report_rel FROM jobs "
            "WHERE status = 'done' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        job_root = _safe_job_root(sessions_dir, str(row["id"]))
        report_rel = _safe_relative_path(str(row["report_rel"] or ""))
        report = _join_under(job_root, report_rel)
        if not _is_relative_to(report, job_root):
            raise BackupError("A report path escapes its job directory.")
        if (
            report.name != "report.html"
            or _is_link_or_reparse(report)
            or not report.is_file()
        ):
            raise BackupError("A completed job is missing its retained report.")

        deliverable_root = report.parent
        if not _is_relative_to(deliverable_root, job_root):
            raise BackupError("A deliverable path escapes its job directory.")

        selected = [report]
        metrics = deliverable_root / "metrics.json"
        if metrics.exists():
            selected.append(metrics)
        proof_cycle = deliverable_root / "proof-cycle.json"
        if proof_cycle.exists():
            selected.append(proof_cycle)
        media = deliverable_root / "media"
        if media.exists():
            if _is_link_or_reparse(media) or not media.is_dir():
                raise BackupError("A generated-media path is not a safe directory.")
            selected.extend(path for path in media.rglob("*") if path.is_file())

        for source in sorted(set(selected)):
            try:
                lexical_relative = source.relative_to(deliverable_root)
            except ValueError as exc:
                raise BackupError(
                    "An artifact escapes its deliverable directory."
                ) from exc
            resolved = _join_under(
                deliverable_root,
                PurePosixPath(*lexical_relative.parts),
            )
            relative = resolved.relative_to(sessions_dir)
            if "work" in relative.parts or relative.name.startswith("source."):
                raise BackupError("A raw upload or work artifact entered the allowlist.")
            yield resolved, relative


def create_backup(
    sessions_dir: Path,
    output_dir: Path,
    *,
    now: datetime | None = None,
    baseline_lineage_id: str | None = None,
) -> dict[str, Any]:
    """Create a complete local backup bundle without contacting object storage."""
    expanded_sessions = sessions_dir.expanduser()
    expanded_output = output_dir.expanduser()
    if _is_link_or_reparse(expanded_sessions):
        raise BackupError("The sessions directory cannot be a symlink or reparse point.")
    if _is_link_or_reparse(expanded_output):
        raise BackupError("The backup output cannot be a symlink or reparse point.")
    sessions_dir = expanded_sessions.resolve()
    output_dir = expanded_output.resolve()
    if not sessions_dir.is_dir():
        raise BackupError("The sessions directory does not exist.")
    if output_dir.exists():
        raise BackupError("The backup output directory must not already exist.")
    data_root = Path("/data").resolve()
    if output_dir == data_root or _is_relative_to(output_dir, data_root):
        raise BackupError("Backup output is never allowed in or below /data.")
    if _is_relative_to(output_dir, sessions_dir):
        raise BackupError("The backup output directory cannot be inside sessions.")
    if not output_dir.parent.is_dir():
        raise BackupError("The backup output parent directory must already exist.")

    captured_at = (now or _utc_now()).astimezone(timezone.utc)
    captured_at_epoch = captured_at.timestamp()
    backup_id = _backup_id(captured_at)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.partial-",
            dir=output_dir.parent,
        )
    )
    try:
        os.chmod(staging, 0o700)
        snapshot = staging / DATABASE_BUNDLE_PATH
        _online_sqlite_snapshot(sessions_dir / "swinglab.db", snapshot)
        sqlite_summary = database_summary(snapshot, captured_at_epoch)
        mobile_state = _snapshot_mobile_state(snapshot)
        db_sha256, db_size = _sha256_file(snapshot)
        recovery_fence = _snapshot_recovery_fence(
            snapshot,
            backup_id=backup_id,
            database_sha256=db_sha256,
            mobile_state=mobile_state,
            baseline_lineage_id=baseline_lineage_id,
        )

        artifacts = []
        artifact_bytes = 0
        seen_paths: set[str] = set()
        for source, relative in _artifact_sources(sessions_dir, snapshot):
            relative_text = relative.as_posix()
            if relative_text in seen_paths:
                raise BackupError("Duplicate artifact path in backup.")
            seen_paths.add(relative_text)
            destination = staging / "artifacts" / relative
            sha256, size = _copy_and_hash(source, destination)
            artifacts.append(
                {"path": relative_text, "size": size, "sha256": sha256}
            )
            artifact_bytes += size

        manifest = {
            "format": FORMAT,
            "backup_id": backup_id,
            "created_at": captured_at.isoformat().replace("+00:00", "Z"),
            "created_at_epoch": captured_at_epoch,
            "database": {
                "path": DATABASE_BUNDLE_PATH,
                "size": db_size,
                "sha256": db_sha256,
                "sqlite": sqlite_summary,
            },
            "artifacts": {
                "count": len(artifacts),
                "bytes": artifact_bytes,
                "files": artifacts,
            },
            "scope": {
                "included": [
                    "WAL-safe SQLite snapshot",
                    "completed-job report.html",
                    "completed-job metrics.json when present",
                    "completed-job proof-cycle.json when present",
                    "completed-job media files",
                ],
                "excluded": [
                    "live swinglab.db, swinglab.db-wal, and swinglab.db-shm files",
                    "raw source uploads",
                    "work directories",
                    "queued, processing, and failed-job files",
                ],
            },
        }
        if mobile_state is not None:
            manifest["mobile_state"] = mobile_state
        if recovery_fence is not None:
            manifest["recovery_fence"] = recovery_fence
        manifest_bytes = _canonical_json(manifest)
        manifest_path = staging / MANIFEST_FILE
        manifest_path.write_bytes(manifest_bytes)
        complete = {
            "format": FORMAT,
            "backup_id": backup_id,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        }
        (staging / COMPLETE_FILE).write_bytes(_canonical_json(complete))
        staging.replace(output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("A required backup metadata file is missing or invalid.") from exc
    if not isinstance(value, dict):
        raise BackupError("Backup metadata must be a JSON object.")
    return value


def _nonnegative_count(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _validate_group_counts(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(name, str) and name and _nonnegative_count(count)
        for name, count in value.items()
    )


def _validate_mobile_state_manifest_shape(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "generation",
        "schema_sha256",
        "table_row_counts",
        "phase_counts",
        "domain_counts",
        "referenced_hmac_key_ids",
    }:
        raise BackupError("The backup mobile state manifest shape is invalid.")
    generation = value["generation"]
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation not in MOBILE_STATE_GENERATIONS
        or generation == 0
    ):
        raise BackupError("The backup mobile state generation is unsupported.")

    expected_tables = set(MOBILE_STATE_GENERATIONS[generation].required_columns)
    schema = value["schema_sha256"]
    counts = value["table_row_counts"]
    if (
        not isinstance(schema, dict)
        or set(schema) != expected_tables
        or any(
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in schema.values()
        )
        or not isinstance(counts, dict)
        or set(counts) != expected_tables
        or any(not _nonnegative_count(count) for count in counts.values())
    ):
        raise BackupError("The backup mobile state attestation is invalid.")

    phases = value["phase_counts"]
    expected_phase_tables = {
        "mobile_auth_exchange_journals",
        "mobile_signout_journals",
        "mobile_device_revoke_journals",
        "mobile_recovery_baseline_journals",
    }
    domains = value["domain_counts"]
    if (
        not isinstance(phases, dict)
        or set(phases) != expected_phase_tables
        or any(not _validate_group_counts(group) for group in phases.values())
        or not isinstance(domains, dict)
        or set(domains) != {"mobile_rate_limit_events"}
        or not _validate_group_counts(domains["mobile_rate_limit_events"])
    ):
        raise BackupError("The backup mobile state attestation is invalid.")

    key_ids = value["referenced_hmac_key_ids"]
    if (
        not isinstance(key_ids, list)
        or any(
            not isinstance(key_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key_id) is None
            for key_id in key_ids
        )
    ):
        raise BackupError("The backup mobile state HMAC key attestation is invalid.")
    if key_ids != sorted(set(key_ids)):
        raise BackupError("The backup mobile state HMAC key attestation is invalid.")


def _validate_recovery_fence_manifest_shape(
    value: object, *, backup_id: str, database_sha256: str
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "lineage_id",
        "baseline_backup_id",
        "baseline_manifest_sha256",
        "baseline_schema_generation",
        "baseline_db_checkpoint",
    }:
        raise BackupError("The backup recovery fence manifest shape is invalid.")
    _canonical_uuid(value["lineage_id"], label="recovery lineage ID")
    try:
        baseline_backup_id = validate_backup_id(value["baseline_backup_id"])
    except (BackupError, TypeError):
        raise BackupError("The backup recovery baseline identifier is invalid.") from None
    generation = value["baseline_schema_generation"]
    checkpoint = value["baseline_db_checkpoint"]
    baseline_manifest_sha256 = value["baseline_manifest_sha256"]
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation not in MOBILE_STATE_GENERATIONS
        or generation == 0
        or not isinstance(checkpoint, str)
        or re.fullmatch(r"[0-9a-f]{64}", checkpoint) is None
    ):
        raise BackupError("The backup recovery fence manifest values are invalid.")
    if baseline_backup_id == backup_id:
        if baseline_manifest_sha256 is not None or checkpoint != database_sha256:
            raise BackupError(
                "The baseline backup recovery fence self marker is invalid."
            )
    elif (
        not isinstance(baseline_manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", baseline_manifest_sha256) is None
    ):
        raise BackupError(
            "A later backup must bind the baseline manifest SHA-256."
        )


def _verify_recovery_fence_attestation(
    db_path: Path, manifest: dict[str, Any]
) -> None:
    declared = manifest.get("recovery_fence")
    if declared is None:
        return
    connection = _read_only_connection(db_path)
    try:
        accepted = connection.execute(
            "SELECT lineage_id, baseline_backup_id, manifest_sha256, "
            "schema_generation, baseline_db_checkpoint FROM "
            "mobile_recovery_accepted_baselines"
        ).fetchall()
        if declared["baseline_backup_id"] == manifest["backup_id"]:
            matching = connection.execute(
                "SELECT phase FROM mobile_recovery_baseline_journals "
                "WHERE lineage_id = ?",
                (declared["lineage_id"],),
            ).fetchall()
            if (
                accepted
                or len(matching) != 1
                or str(matching[0][0]) != "lineage_prepared"
            ):
                raise BackupError(
                    "The baseline recovery fence does not match its snapshot lineage."
                )
            return
        expected = [
            (
                declared["lineage_id"],
                declared["baseline_backup_id"],
                declared["baseline_manifest_sha256"],
                declared["baseline_schema_generation"],
                declared["baseline_db_checkpoint"],
            )
        ]
        if [tuple(row) for row in accepted] != expected:
            raise BackupError(
                "The recovery fence manifest does not match the accepted baseline."
            )
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError("The backup recovery fence attestation is invalid.") from exc
    finally:
        connection.close()


def _verify_mobile_state_attestation(
    db_path: Path, manifest: dict[str, Any]
) -> None:
    connection = _read_only_connection(db_path)
    try:
        generation = detect_mobile_state_generation(connection)
        declared = manifest.get("mobile_state")
        if declared is None:
            if generation != 0:
                raise BackupError(
                    "The backup mobile state declaration is missing for this schema."
                )
            return
        _validate_mobile_state_manifest_shape(declared)
        if generation != declared["generation"]:
            raise BackupError(
                "The backup mobile state generation does not match its schema."
            )
        actual = mobile_state_summary(connection)
        if actual != declared:
            raise BackupError(
                "The backup mobile state attestation does not match the database."
            )
    except BackupError:
        raise
    except (RuntimeError, sqlite3.Error) as exc:
        raise BackupError(
            "The backup mobile state schema is invalid or incomplete."
        ) from exc
    finally:
        connection.close()


def load_and_verify_manifest(bundle_dir: Path) -> dict[str, Any]:
    expanded_bundle = bundle_dir.expanduser()
    if _is_link_or_reparse(expanded_bundle):
        raise BackupError("The backup bundle cannot be a symlink or reparse point.")
    bundle_dir = expanded_bundle.resolve()
    if not bundle_dir.is_dir():
        raise BackupError("The backup bundle is missing or is not a regular directory.")
    manifest_path = bundle_dir / MANIFEST_FILE
    complete_path = bundle_dir / COMPLETE_FILE
    if any(
        _is_link_or_reparse(path) or not path.is_file()
        for path in (manifest_path, complete_path)
    ):
        raise BackupError("Backup metadata is missing, linked, or not a regular file.")
    manifest = _load_json(manifest_path)
    complete = _load_json(complete_path)
    if manifest.get("format") != FORMAT or complete.get("format") != FORMAT:
        raise BackupError("Unsupported backup format.")
    backup_id = validate_backup_id(str(manifest.get("backup_id", "")))
    if complete.get("backup_id") != backup_id:
        raise BackupError("Backup completion metadata does not match the manifest.")
    manifest_sha256, _ = _sha256_file(manifest_path)
    if complete.get("manifest_sha256") != manifest_sha256:
        raise BackupError("Backup manifest checksum verification failed.")

    database = manifest.get("database")
    artifacts = manifest.get("artifacts")
    if not isinstance(database, dict) or not isinstance(artifacts, dict):
        raise BackupError("Backup manifest is missing required sections.")
    database_path = _safe_relative_path(str(database.get("path", "")))
    if database_path.as_posix() != DATABASE_BUNDLE_PATH:
        raise BackupError("Backup manifest contains an unexpected database path.")

    listed_paths: set[str] = set()
    files = artifacts.get("files")
    if not isinstance(files, list):
        raise BackupError("Backup artifact list is invalid.")
    for item in files:
        if not isinstance(item, dict):
            raise BackupError("Backup artifact entry is invalid.")
        path = _safe_relative_path(str(item.get("path", ""))).as_posix()
        if path in listed_paths:
            raise BackupError("Backup manifest contains duplicate artifact paths.")
        listed_paths.add(path)
        if (
            not isinstance(item.get("size"), int)
            or item["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
        ):
            raise BackupError("Backup artifact metadata is invalid.")

    if artifacts.get("count") != len(files) or artifacts.get("bytes") != sum(
        item["size"] for item in files
    ):
        raise BackupError("Backup artifact summary does not match its file list.")
    if "mobile_state" in manifest:
        _validate_mobile_state_manifest_shape(manifest["mobile_state"])
    if "recovery_fence" in manifest:
        _validate_recovery_fence_manifest_shape(
            manifest["recovery_fence"],
            backup_id=backup_id,
            database_sha256=str(database.get("sha256", "")),
        )
    return manifest


def verify_bundle_files(bundle_dir: Path, manifest: dict[str, Any]) -> None:
    database = manifest["database"]
    db_path = _join_under(bundle_dir, _safe_relative_path(database["path"]))
    if _is_link_or_reparse(db_path) or not db_path.is_file():
        raise BackupError("The SQLite snapshot is missing from the backup.")
    db_sha256, db_size = _sha256_file(db_path)
    if db_sha256 != database.get("sha256") or db_size != database.get("size"):
        raise BackupError("The SQLite snapshot checksum verification failed.")

    for item in manifest["artifacts"]["files"]:
        path = _join_under(
            bundle_dir,
            PurePosixPath("artifacts") / _safe_relative_path(item["path"]),
        )
        if _is_link_or_reparse(path) or not path.is_file():
            raise BackupError("A backed-up artifact is missing or unsafe.")
        sha256, size = _sha256_file(path)
        if sha256 != item["sha256"] or size != item["size"]:
            raise BackupError("Artifact checksum verification failed.")
    _verify_artifact_database_mapping(db_path, manifest)
    _verify_mobile_state_attestation(db_path, manifest)
    _verify_recovery_fence_attestation(db_path, manifest)


def _verify_artifact_database_mapping(
    db_path: Path, manifest: dict[str, Any]
) -> None:
    connection = _read_only_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT id, report_rel FROM jobs WHERE status = 'done' ORDER BY id"
        ).fetchall()
    except sqlite3.Error as exc:
        raise BackupError("Artifact-to-database reconciliation failed.") from exc
    finally:
        connection.close()

    jobs: dict[str, tuple[PurePosixPath, PurePosixPath]] = {}
    for row in rows:
        job_id = str(row["id"])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", job_id):
            raise BackupError("A restored job identifier is unsafe.")
        report_rel = _safe_relative_path(str(row["report_rel"] or ""))
        report_path = PurePosixPath(job_id) / report_rel
        jobs[job_id] = (report_path, report_path.parent)

    listed = {
        _safe_relative_path(str(item["path"]))
        for item in manifest["artifacts"]["files"]
    }
    expected_reports = {report for report, _ in jobs.values()}
    if not expected_reports.issubset(listed):
        raise BackupError("A completed job report is absent from the manifest.")

    for path in listed:
        if not path.parts or path.parts[0] not in jobs:
            raise BackupError("An artifact is not associated with a completed job.")
        report, deliverable_root = jobs[path.parts[0]]
        metrics = deliverable_root / "metrics.json"
        proof_cycle = deliverable_root / "proof-cycle.json"
        media_root = deliverable_root / "media"
        is_media = (
            len(path.parts) > len(media_root.parts)
            and path.parts[: len(media_root.parts)] == media_root.parts
        )
        if path != report and path != metrics and path != proof_cycle and not is_media:
            raise BackupError("An artifact is outside the retained deliverable allowlist.")


def _assert_safe_scratch_root(scratch_root: Path, bundle_dir: Path) -> Path:
    expanded_root = scratch_root.expanduser()
    if _is_link_or_reparse(expanded_root):
        raise BackupError("The scratch root cannot be a symlink or reparse point.")
    root = expanded_root.resolve()
    data_root = Path("/data").resolve()
    if root == data_root or _is_relative_to(root, data_root):
        raise BackupError("Restore drills are never allowed in or below /data.")
    if not root.is_dir() or _is_link_or_reparse(root):
        raise BackupError("The scratch root must be an existing regular directory.")
    if root == Path(root.anchor):
        raise BackupError("The filesystem root cannot be used as a restore scratch root.")
    if _is_relative_to(root, bundle_dir) or _is_relative_to(bundle_dir, root):
        raise BackupError("The restore scratch root cannot overlap the backup bundle.")
    if any((candidate / "swinglab.db").exists() for candidate in (root, *root.parents)):
        raise BackupError(
            "The restore scratch root cannot be inside a live sessions tree."
        )
    return root


def restore_backup(bundle_dir: Path, scratch_root: Path) -> dict[str, Any]:
    """Restore a verified bundle into a new scratch child and verify it."""
    expanded_bundle = bundle_dir.expanduser()
    manifest = load_and_verify_manifest(expanded_bundle)
    bundle_dir = expanded_bundle.resolve()
    verify_bundle_files(bundle_dir, manifest)
    root = _assert_safe_scratch_root(scratch_root, bundle_dir)
    restore_dir = root / (
        f"restore-{manifest['backup_id']}-{uuid.uuid4().hex[:8]}"
    )
    restore_dir.mkdir(mode=0o700, exist_ok=False)
    try:
        restored_db = _join_under(
            restore_dir, _safe_relative_path(DATABASE_BUNDLE_PATH)
        )
        # This source is the closed, checksummed snapshot in the bundle—not the
        # live WAL database. The live database is never copied or opened writable.
        db_sha256, db_size = _copy_and_hash(
            bundle_dir / DATABASE_BUNDLE_PATH, restored_db
        )
        database = manifest["database"]
        if db_sha256 != database["sha256"] or db_size != database["size"]:
            raise BackupError("Restored SQLite snapshot checksum verification failed.")

        for item in manifest["artifacts"]["files"]:
            relative = _safe_relative_path(item["path"])
            source = _join_under(bundle_dir, PurePosixPath("artifacts") / relative)
            destination = _join_under(
                restore_dir, PurePosixPath("artifacts") / relative
            )
            sha256, size = _copy_and_hash(source, destination)
            if sha256 != item["sha256"] or size != item["size"]:
                raise BackupError("Restored artifact checksum verification failed.")

        restored_summary = database_summary(
            restored_db, float(manifest["created_at_epoch"])
        )
        expected_summary = database["sqlite"]
        for field in (
            "integrity_check",
            "user_version",
            "critical_table_counts",
            "critical_table_sha256",
            "reconciliation",
            "invariant_violations",
        ):
            if restored_summary.get(field) != expected_summary.get(field):
                raise BackupError(
                    "Restored database reconciliation did not match the manifest."
                )
        extension_fields = (
            "history_state_table_counts",
            "history_state_table_sha256",
            "history_state_invariant_violations",
        )
        expected_has_history_state = any(
            field in expected_summary for field in extension_fields
        )
        # An old v1 writer can snapshot a database that already contains the
        # additive history tables while omitting the extension fields. The
        # database hash still attests those bytes, so a missing extension means
        # "legacy writer" and is intentionally ignored. Once any extension
        # field is present, current readers require the complete exact state.
        if expected_has_history_state and any(
            restored_summary.get(field) != expected_summary.get(field)
            for field in extension_fields
        ):
            raise BackupError(
                "Restored history state did not match the manifest."
            )

        report = {
            "format": FORMAT,
            "backup_id": manifest["backup_id"],
            "verified_at": _utc_now().isoformat().replace("+00:00", "Z"),
            "sqlite_integrity_check": "ok",
            "critical_table_counts": restored_summary["critical_table_counts"],
            "history_state_table_counts": restored_summary.get(
                "history_state_table_counts", {}
            ),
            "entitlement_and_purchase_reconciliation": "matched",
            "artifact_checksums_verified": manifest["artifacts"]["count"],
        }
        (restore_dir / "restore-report.json").write_bytes(_canonical_json(report))
        return {"restore_dir": restore_dir, "report": report}
    except Exception:
        # Keep the new scratch directory for diagnosis. It never overlaps live data,
        # and a retry always creates a different directory.
        raise
