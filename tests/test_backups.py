"""Stage 0B backup/restore tests use synthetic data and local fake clients only."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from swinglab.backups import core as core_module
from swinglab.backups import store as store_module
from swinglab.backups.core import (
    COMPLETE_FILE,
    DATABASE_BUNDLE_PATH,
    MANIFEST_FILE,
    BackupError,
    create_backup,
    restore_backup,
)
from swinglab.backups.store import S3Settings, download_bundle, upload_bundle
from swinglab.cli import main
from swinglab.web.jobs import _SCHEMA as JOBS_SCHEMA
from swinglab.web.mobile_schema import (
    MOBILE_STATE_GENERATIONS,
    VersionedHMAC,
    ensure_mobile_state_schema,
    mobile_state_summary,
)
from swinglab.web.throttle import _SCHEMA as THROTTLE_SCHEMA
from swinglab.web.users import _SCHEMA as USERS_SCHEMA


CAPTURED_AT = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
BASELINE_LINEAGE_ID = "11111111-2222-4333-8444-555555555555"


def _canonical(value) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def _rewrite_completion(bundle: Path, manifest: dict) -> None:
    manifest_bytes = _canonical(manifest)
    (bundle / MANIFEST_FILE).write_bytes(manifest_bytes)
    complete = {
        "format": manifest["format"],
        "backup_id": manifest["backup_id"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    (bundle / COMPLETE_FILE).write_bytes(_canonical(complete))


@pytest.fixture
def synthetic_sessions(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    connection = sqlite3.connect(db_path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript(USERS_SCHEMA + JOBS_SCHEMA + THROTTLE_SCHEMA)
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    connection.execute(
        "INSERT INTO users "
        "(id, email, password_hash, created_at, plan, subscription_status, "
        "pro_until, digest_opt_in) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "user-synthetic",
            "synthetic@example.invalid",
            "synthetic-hash",
            1.0,
            "pro",
            "active",
            CAPTURED_AT.timestamp() + 86400,
            0,
        ),
    )
    connection.execute(
        "INSERT INTO pro_grants (email, days) VALUES (?, ?)",
        ("future@example.invalid", 31.0),
    )
    connection.execute(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at) "
        "VALUES (?, ?, ?, ?)",
        ("order-synthetic", "synthetic@example.invalid", 31.0, 2.0),
    )
    connection.execute(
        "INSERT INTO gear_orders "
        "(order_id, sku, title, quantity, email, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "gear-synthetic",
            "SKU-SYNTHETIC",
            "Synthetic training aid",
            2,
            "synthetic@example.invalid",
            3.0,
        ),
    )
    connection.execute(
        "INSERT INTO email_codes "
        "(email, purpose, code_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("synthetic@example.invalid", "signin", "synthetic-code", 4.0, 5.0),
    )
    connection.execute(
        "INSERT INTO auth_attempts (bucket, key, ts) VALUES (?, ?, ?)",
        ("login", "synthetic-key", 6.0),
    )
    connection.execute(
        "INSERT INTO analysis_usage_monthly"
        " (user_hash, month_start, coaching_eligible, refilm_rejections,"
        "  expires_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ef" * 32, 1782864000, 1, 1, 1785542400.0, 10.0),
    )
    connection.execute(
        "UPDATE shopify_sync_control SET order_fence_secret = ? WHERE id = 1",
        ("ab" * 32,),
    )
    connection.execute(
        "INSERT INTO shopify_redacted_order_fences"
        " (order_key, redacted_at) VALUES (?, ?)",
        ("cd" * 32, 7.0),
    )
    connection.execute(
        "INSERT INTO shopify_customer_tombstones"
        " (customer_id, redacted, deleted_at)"
        " VALUES (?, 1, ?)",
        ("customer-synthetic", 8.0),
    )
    connection.execute(
        "INSERT INTO shopify_pending_customer_links"
        " (customer_id, email, created_at)"
        " VALUES (?, ?, ?)",
        (
            "pending-customer-synthetic",
            "pending-link@example.invalid",
            9.0,
        ),
    )
    connection.execute(
        "INSERT INTO shopify_privacy_requests"
        " (request_id, shop_domain, status, snapshot_json,"
        "  snapshot_sha256, record_count, snapshot_bytes,"
        "  created_at, completed_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?, 0, 2, ?, ?, ?)",
        (
            "spr_synthetic",
            "synthetic.myshopify.com",
            "ready",
            "{}",
            hashlib.sha256(b"{}").hexdigest(),
            10.0,
            10.0,
            100.0,
        ),
    )
    connection.execute(
        "INSERT INTO jobs "
        "(id, status, created_at, updated_at, report_rel, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "jobdone",
            "done",
            1.0,
            2.0,
            "out/source/report.html",
            "user-synthetic",
        ),
    )
    connection.execute(
        "INSERT INTO jobs (id, status, created_at, updated_at, user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("jobactive", "processing", 3.0, 4.0, "user-synthetic"),
    )
    connection.execute(
        "INSERT INTO jobs (id, status, created_at, updated_at, user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("jobfailed", "failed", 5.0, 6.0, "user-synthetic"),
    )
    connection.commit()
    assert (sessions / "swinglab.db-wal").is_file()

    deliverables = sessions / "jobdone" / "out" / "source"
    (deliverables / "media").mkdir(parents=True)
    (deliverables / "work").mkdir()
    (deliverables / "report.html").write_text("<h1>Synthetic report</h1>")
    (deliverables / "metrics.json").write_text('{"synthetic":true}')
    (deliverables / "proof-cycle.json").write_text('{"synthetic":true}')
    (deliverables / "media" / "strip_s1.png").write_bytes(b"synthetic-png")
    (deliverables / "media" / "replay_s1.mp4").write_bytes(b"synthetic-mp4")
    (deliverables / "work" / "frame.png").write_bytes(b"temporary")
    (sessions / "jobdone" / "source.mov").write_bytes(b"synthetic-source")
    failed = sessions / "jobfailed"
    failed.mkdir()
    (failed / "source.mp4").write_bytes(b"failed-source")
    active = sessions / "jobactive"
    active.mkdir()
    (active / "source.mp4").write_bytes(b"active-source")

    try:
        yield sessions, connection
    finally:
        connection.close()


def _create_bundle(tmp_path: Path, sessions: Path) -> tuple[Path, dict]:
    bundle = tmp_path / "bundle"
    manifest = create_backup(sessions, bundle, now=CAPTURED_AT)
    return bundle, manifest


def test_wal_safe_snapshot_and_artifact_allowlist(tmp_path, synthetic_sessions):
    sessions, live_connection = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)

    # This row was committed after a WAL truncate while the writer stayed open.
    snapshot = sqlite3.connect(bundle / DATABASE_BUNDLE_PATH)
    assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert snapshot.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    snapshot.close()
    assert live_connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1

    paths = [item["path"] for item in manifest["artifacts"]["files"]]
    assert paths == sorted(paths)
    assert paths == [
        "jobdone/out/source/media/replay_s1.mp4",
        "jobdone/out/source/media/strip_s1.png",
        "jobdone/out/source/metrics.json",
        "jobdone/out/source/proof-cycle.json",
        "jobdone/out/source/report.html",
    ]
    serialized = (bundle / MANIFEST_FILE).read_text()
    assert "synthetic@example.invalid" not in serialized
    assert "order-synthetic" not in serialized
    assert "source.mov" not in serialized
    assert not (bundle / "artifacts/jobdone/out/source/work").exists()
    assert manifest["database"]["sqlite"]["critical_table_counts"] == {
        "jobs": 3,
        "users": 1,
        "pro_grants": 1,
        "shopify_orders": 1,
        "gear_orders": 1,
        "email_codes": 1,
        "auth_attempts": 1,
        "shopify_sync_control": 1,
        "shopify_privacy_event_fences": 0,
        "shopify_redacted_order_fences": 1,
        "shopify_privacy_requests": 1,
        "shopify_customer_tombstones": 1,
        "shopify_pending_customer_links": 1,
    }
    assert manifest["database"]["sqlite"]["history_state_table_counts"] == {
        "analysis_usage_monthly": 1,
        "history_reset_operations": 0,
    }


def test_restore_drill_uses_new_scratch_and_reconciles(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    result = restore_backup(bundle, scratch)
    restore_dir = result["restore_dir"]
    assert restore_dir.parent == scratch
    assert restore_dir != sessions
    assert result["report"]["sqlite_integrity_check"] == "ok"
    assert result["report"]["entitlement_and_purchase_reconciliation"] == "matched"
    assert (
        result["report"]["artifact_checksums_verified"]
        == manifest["artifacts"]["count"]
    )
    assert (restore_dir / "restore-report.json").is_file()
    restored = sqlite3.connect(restore_dir / DATABASE_BUNDLE_PATH)
    assert restored.execute("SELECT COUNT(*) FROM shopify_orders").fetchone()[0] == 1
    assert restored.execute("SELECT COUNT(*) FROM gear_orders").fetchone()[0] == 1
    assert restored.execute(
        "SELECT order_fence_secret FROM shopify_sync_control WHERE id = 1"
    ).fetchone()[0] == "ab" * 32
    assert restored.execute(
        "SELECT COUNT(*) FROM shopify_redacted_order_fences"
    ).fetchone()[0] == 1
    assert restored.execute(
        "SELECT COUNT(*) FROM shopify_privacy_requests"
    ).fetchone()[0] == 1
    assert restored.execute(
        "SELECT COUNT(*) FROM shopify_customer_tombstones"
    ).fetchone()[0] == 1
    assert restored.execute(
        "SELECT COUNT(*) FROM shopify_pending_customer_links"
    ).fetchone()[0] == 1
    assert restored.execute(
        "SELECT coaching_eligible, refilm_rejections"
        " FROM analysis_usage_monthly WHERE user_hash = ?",
        ("ef" * 32,),
    ).fetchone() == (1, 1)
    restored.close()
    assert (sessions / "swinglab.db").is_file()


def test_legacy_v1_database_without_history_tables_remains_restorable(
    tmp_path, synthetic_sessions
):
    sessions, connection = synthetic_sessions
    connection.execute("DROP TABLE analysis_usage_monthly")
    connection.execute("DROP TABLE history_reset_operations")
    connection.execute("ALTER TABLE users DROP COLUMN history_epoch")
    connection.commit()

    bundle, manifest = _create_bundle(tmp_path, sessions)
    assert "analysis_usage_monthly" not in manifest["database"]["sqlite"][
        "critical_table_counts"
    ]
    scratch = tmp_path / "scratch-legacy"
    scratch.mkdir()
    restored = restore_backup(bundle, scratch)

    assert restored["report"]["sqlite_integrity_check"] == "ok"


def test_new_bundle_preserves_the_original_v1_reader_contract(
    tmp_path, synthetic_sessions, monkeypatch
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    monkeypatch.setattr(core_module, "HISTORY_STATE_TABLES", ())

    legacy_summary = core_module.database_summary(
        bundle / DATABASE_BUNDLE_PATH,
        CAPTURED_AT.timestamp(),
    )

    for field in (
        "integrity_check",
        "user_version",
        "critical_table_counts",
        "critical_table_sha256",
        "reconciliation",
        "invariant_violations",
    ):
        assert legacy_summary[field] == manifest["database"]["sqlite"][field]
    assert "history_state_table_counts" not in legacy_summary


def test_current_reader_accepts_old_writer_manifest_over_new_schema(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    for field in (
        "history_state_table_counts",
        "history_state_table_sha256",
        "history_state_invariant_violations",
    ):
        manifest["database"]["sqlite"].pop(field)
    _rewrite_completion(bundle, manifest)
    scratch = tmp_path / "scratch-old-writer-new-schema"
    scratch.mkdir()

    restored = restore_backup(bundle, scratch)

    assert restored["report"]["sqlite_integrity_check"] == "ok"


def test_partial_history_state_schema_and_pending_cleanup_block_backup(
    tmp_path, synthetic_sessions
):
    sessions, connection = synthetic_sessions
    connection.execute("DROP TABLE history_reset_operations")
    connection.commit()
    with pytest.raises(BackupError, match="incomplete history-reset state"):
        create_backup(sessions, tmp_path / "partial", now=CAPTURED_AT)

    connection.executescript(
        "CREATE TABLE history_reset_operations ("
        " operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_hash TEXT,"
        " state TEXT NOT NULL, job_ids_json TEXT NOT NULL, created_at REAL NOT NULL,"
        " updated_at REAL NOT NULL);"
    )
    connection.execute(
        "INSERT INTO history_reset_operations VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("a" * 32, "user_reset", "b" * 64, "committed", "[]", 1.0, 2.0),
    )
    connection.commit()
    with pytest.raises(BackupError, match="History cleanup is pending"):
        create_backup(sessions, tmp_path / "pending", now=CAPTURED_AT)


def test_history_epoch_marker_blocks_backup_after_both_state_tables_are_lost(
    tmp_path, synthetic_sessions
):
    sessions, connection = synthetic_sessions
    connection.execute(
        "UPDATE users SET history_epoch = 1 WHERE id = 'user-synthetic'"
    )
    connection.execute("DROP TABLE analysis_usage_monthly")
    connection.execute("DROP TABLE history_reset_operations")
    connection.commit()

    with pytest.raises(BackupError, match="incomplete history-reset state"):
        create_backup(sessions, tmp_path / "lost-history-state", now=CAPTURED_AT)


def test_history_tables_without_epoch_marker_are_not_treated_as_legacy(
    tmp_path, synthetic_sessions
):
    sessions, connection = synthetic_sessions
    connection.execute("ALTER TABLE users DROP COLUMN history_epoch")
    connection.commit()

    with pytest.raises(BackupError, match="incomplete history-reset state"):
        create_backup(sessions, tmp_path / "missing-history-marker", now=CAPTURED_AT)


def test_artifact_corruption_blocks_restore(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    artifact = next((bundle / "artifacts").rglob("*.png"))
    artifact.write_bytes(b"corrupt")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="Artifact checksum"):
        restore_backup(bundle, scratch)
    assert not list(scratch.iterdir())


def test_database_reconciliation_detects_logical_mutation(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    snapshot_path = bundle / DATABASE_BUNDLE_PATH
    mutated = sqlite3.connect(snapshot_path)
    mutated.execute("DELETE FROM shopify_orders")
    mutated.commit()
    mutated.close()
    database_bytes = snapshot_path.read_bytes()
    manifest["database"]["sha256"] = hashlib.sha256(database_bytes).hexdigest()
    manifest["database"]["size"] = len(database_bytes)
    _rewrite_completion(bundle, manifest)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="reconciliation"):
        restore_backup(bundle, scratch)


@pytest.mark.parametrize(
    "malicious_path",
    [
        "../escape",
        "/escape",
        r"\escape",
        "C:/escape",
        "C:escape",
        "a:b",
        r"server\share\escape",
    ],
)
def test_manifest_traversal_is_rejected(
    tmp_path, synthetic_sessions, malicious_path
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    manifest["artifacts"]["files"][0]["path"] = malicious_path
    _rewrite_completion(bundle, manifest)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="unsafe relative path"):
        restore_backup(bundle, scratch)


def test_symlinked_bundle_parent_is_rejected(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    original = bundle / "artifacts/jobdone"
    outside = tmp_path / "outside-artifacts"
    original.rename(outside)
    try:
        original.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this test host.")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="Symlinks"):
        restore_backup(bundle, scratch)


def test_restore_rejects_a_symlinked_bundle_root(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    alias = tmp_path / "bundle-alias"
    try:
        alias.symlink_to(bundle, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this test host.")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="symlink|reparse"):
        restore_backup(alias, scratch)

    assert not list(scratch.iterdir())


def test_upload_rejects_a_symlinked_bundle_root(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    alias = tmp_path / "upload-bundle-alias"
    try:
        alias.symlink_to(bundle, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this test host.")
    fake = _RecordingS3()

    with pytest.raises(BackupError, match="symlink|reparse"):
        upload_bundle(alias, _settings(), client=fake)

    assert fake.objects == {}


@pytest.mark.parametrize("metadata_name", [MANIFEST_FILE, COMPLETE_FILE])
def test_restore_rejects_symlinked_bundle_metadata(
    tmp_path, synthetic_sessions, metadata_name
):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    metadata = bundle / metadata_name
    target = tmp_path / f"outside-{metadata_name}"
    metadata.replace(target)
    try:
        metadata.symlink_to(target)
    except OSError:
        pytest.skip("File symlinks are unavailable on this test host.")
    scratch = tmp_path / f"metadata-scratch-{metadata_name}"
    scratch.mkdir()

    with pytest.raises(BackupError, match="metadata|symlink|reparse"):
        restore_backup(bundle, scratch)

    assert not list(scratch.iterdir())


def test_create_backup_rejects_a_broken_symlink_output(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    outside = tmp_path / "unexpected-baseline-target"
    alias = tmp_path / "broken-baseline-alias"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this test host.")

    with pytest.raises(BackupError, match="symlink|reparse"):
        create_backup(sessions, alias, now=CAPTURED_AT)

    assert not outside.exists()


def test_symlinked_live_report_is_rejected(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    report = sessions / "jobdone/out/source/report.html"
    target = sessions / "jobdone/out/source/other.html"
    target.write_text("<h1>Not the report</h1>")
    report.unlink()
    try:
        report.symlink_to(target)
    except OSError:
        pytest.skip("File symlinks are unavailable on this test host.")

    with pytest.raises(BackupError, match="Symlinks"):
        create_backup(sessions, tmp_path / "symlink-bundle", now=CAPTURED_AT)


def test_path_guard_recognizes_windows_reparse_points(monkeypatch):
    class SyntheticReparseStat:
        st_mode = stat.S_IFDIR
        st_file_attributes = 0x400

    monkeypatch.setattr(
        core_module.os,
        "lstat",
        lambda _path: SyntheticReparseStat(),
    )

    assert core_module._is_link_or_reparse(Path("synthetic-reparse"))


def test_artifact_manifest_must_match_completed_jobs(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    report_item = next(
        item
        for item in manifest["artifacts"]["files"]
        if item["path"].endswith("/report.html")
    )
    old_path = bundle / "artifacts" / report_item["path"]
    report_item["path"] = "jobactive/out/source/report.html"
    new_path = bundle / "artifacts" / report_item["path"]
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)
    _rewrite_completion(bundle, manifest)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="completed job|associated"):
        restore_backup(bundle, scratch)


def test_missing_report_completes_no_bundle(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    (sessions / "jobdone/out/source/report.html").unlink()
    output = tmp_path / "incomplete"

    with pytest.raises(BackupError, match="missing its retained report"):
        create_backup(sessions, output, now=CAPTURED_AT)
    assert not output.exists()
    assert not list(tmp_path.glob(".incomplete.partial-*"))


def test_create_rejects_data_output_before_writing(synthetic_sessions):
    sessions, _ = synthetic_sessions

    with pytest.raises(BackupError, match="/data"):
        create_backup(sessions, Path("/data/backup-output"), now=CAPTURED_AT)


def test_cli_is_inert_without_explicit_enable_flag(
    tmp_path, synthetic_sessions, monkeypatch, capsys
):
    sessions, _ = synthetic_sessions
    monkeypatch.delenv("CADDIE_BACKUP_ENABLED", raising=False)
    output = tmp_path / "never-created"

    result = main(
        [
            "backup",
            "create",
            "--sessions-dir",
            str(sessions),
            "--output-dir",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "CADDIE_BACKUP_ENABLED=true" in captured.err
    assert not output.exists()


class _RecordingS3:
    class _Missing(Exception):
        response = {
            "Error": {"Code": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }

    class _Precondition(Exception):
        response = {
            "Error": {"Code": "PreconditionFailed"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        }

    class _Unsupported(Exception):
        response = {
            "Error": {"Code": "NotImplemented"},
            "ResponseMetadata": {"HTTPStatusCode": 501},
        }

    class _Body:
        def __init__(self, body: bytes, *, fail_after_first_read: bool = False):
            self._body = body
            self._position = 0
            self._reads = 0
            self._fail_after_first_read = fail_after_first_read
            self.closed = False

        def read(self, amount: int) -> bytes:
            if self._fail_after_first_read and self._reads:
                raise RuntimeError("synthetic stream failure")
            self._reads += 1
            if self._position >= len(self._body):
                return b""
            end = min(len(self._body), self._position + amount)
            chunk = self._body[self._position : end]
            self._position = end
            return chunk

        def close(self) -> None:
            self.closed = True

    def __init__(
        self,
        failure: str | None = None,
        *,
        conditional_mode: str = "enforce",
        versioned: bool = False,
        claim_barrier: threading.Barrier | None = None,
    ):
        self.keys = []
        self.failure = failure
        self.conditional_mode = conditional_mode
        self.versioned = versioned
        self.claim_barrier = claim_barrier
        self.objects = {}
        self.versions = {}
        self.put_calls = []
        self.get_calls = []
        self.mutate_before_get = {}
        self.oversize_stream_keys = set()
        self.failing_stream_keys = set()
        self._version_counter = 0
        self._claim_threads = set()
        self._lock = threading.RLock()

    @staticmethod
    def _etag(body: bytes) -> str:
        return f'"{hashlib.sha256(body).hexdigest()}"'

    def _store(self, key: str, body: bytes, args: dict) -> dict:
        self._version_counter += 1
        head = {
            "ContentLength": len(body),
            "Metadata": dict(args.get("Metadata") or {}),
            "ServerSideEncryption": args.get("ServerSideEncryption"),
            "SSEKMSKeyId": args.get("SSEKMSKeyId"),
            "ETag": self._etag(body),
        }
        if self.versioned:
            head["VersionId"] = f"synthetic-v{self._version_counter}"
        record = {"body": body, "head": head}
        self.objects[key] = record
        if self.versioned:
            self.versions.setdefault(key, {})[head["VersionId"]] = {
                "body": body,
                "head": dict(head),
            }
        return record

    def replace_object(self, key: str, body: bytes) -> None:
        with self._lock:
            existing = self.objects[key]["head"]
            args = {
                "Metadata": dict(existing.get("Metadata") or {}),
                "ServerSideEncryption": existing.get("ServerSideEncryption"),
                "SSEKMSKeyId": existing.get("SSEKMSKeyId"),
            }
            self._store(key, body, args)

    def head_object(self, Bucket, Key):
        with self._lock:
            if Key not in self.objects:
                raise self._Missing()
            head = dict(self.objects[Key]["head"])
            head["Metadata"] = dict(head.get("Metadata") or {})
            return head

    def upload_file(self, filename, bucket, key, ExtraArgs):
        if self.failure:
            raise RuntimeError(self.failure)
        body = Path(filename).read_bytes()
        with self._lock:
            self.keys.append(key)
            self._store(key, body, ExtraArgs)

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        if (
            self.claim_barrier is not None
            and key.endswith("/CLAIM.json")
            and kwargs.get("IfNoneMatch") == "*"
        ):
            thread_id = threading.get_ident()
            with self._lock:
                first_claim = thread_id not in self._claim_threads
                self._claim_threads.add(thread_id)
            if first_claim:
                self.claim_barrier.wait(timeout=5)

        body = kwargs["Body"]
        if hasattr(body, "read"):
            body = body.read()
        body = bytes(body)
        with self._lock:
            self.put_calls.append(
                {
                    "key": key,
                    "if_none_match": kwargs.get("IfNoneMatch"),
                }
            )
            if kwargs.get("IfNoneMatch") == "*":
                if self.conditional_mode == "unsupported":
                    raise self._Unsupported()
                if self.conditional_mode == "enforce" and key in self.objects:
                    raise self._Precondition()
            self.keys.append(key)
            return self._store(key, body, kwargs)["head"]

    def get_object(self, **kwargs):
        key = kwargs["Key"]
        with self._lock:
            if key not in self.objects:
                raise self._Missing()
            if key in self.mutate_before_get:
                replacement = self.mutate_before_get.pop(key)
                self.replace_object(key, replacement)

            if kwargs.get("VersionId"):
                version_id = kwargs["VersionId"]
                try:
                    record = self.versions[key][version_id]
                except KeyError:
                    raise self._Missing() from None
            else:
                record = self.objects[key]
            if kwargs.get("IfMatch") and (
                kwargs["IfMatch"] != record["head"]["ETag"]
            ):
                raise self._Precondition()

            head = dict(record["head"])
            head["Metadata"] = dict(head.get("Metadata") or {})
            body = record["body"]
            if key in self.oversize_stream_keys:
                body += b"x"
            stream = self._Body(
                body,
                fail_after_first_read=key in self.failing_stream_keys,
            )
            self.get_calls.append(
                {
                    "key": key,
                    "if_match": kwargs.get("IfMatch"),
                    "version_id": kwargs.get("VersionId"),
                }
            )
            return {**head, "Body": stream}


def _settings(
    secret: str = "not-a-real-secret",
    endpoint: str = "https://objects.example.invalid",
) -> S3Settings:
    return S3Settings(
        bucket="synthetic-private-bucket",
        prefix="caddieinsight/backups",
        region="synthetic-region",
        endpoint_url=endpoint,
        addressing_style="path",
        sse="AES256",
        kms_key_id=None,
        access_key_id="synthetic-access-key",
        secret_access_key=secret,
    )


def test_object_upload_marks_complete_last(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    fake = _RecordingS3()

    assert upload_bundle(bundle, _settings(), client=fake) == manifest["backup_id"]
    assert fake.keys[-1].endswith(f"/{COMPLETE_FILE}")
    assert fake.keys[-2].endswith(f"/{MANIFEST_FILE}")
    assert all("jobdone" not in key for key in fake.keys)
    assert any("/objects/00000000" in key for key in fake.keys)
    assert fake.keys[0].endswith("/CLAIM.json")
    assert fake.put_calls[-1] == {
        "key": fake.keys[-1],
        "if_none_match": "*",
    }


def test_concurrent_uploads_allow_exactly_one_writer(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3(claim_barrier=threading.Barrier(2))

    def attempt():
        try:
            return ("ok", upload_bundle(bundle, settings, client=fake))
        except BackupError as exc:
            return ("error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))

    assert [result[0] for result in results].count("ok") == 1
    assert [result[0] for result in results].count("error") == 1
    assert any("claimed by another writer" in result[1] for result in results)
    complete_key = (
        f"{settings.object_prefix(manifest['backup_id'])}/{COMPLETE_FILE}"
    )
    assert fake.keys.count(complete_key) == 1


def test_upload_fails_when_conditional_put_is_unsupported(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    fake = _RecordingS3(conditional_mode="unsupported")

    with pytest.raises(BackupError, match="conditional object write"):
        upload_bundle(bundle, _settings(), client=fake)
    assert fake.keys == []


def test_upload_fails_when_provider_ignores_if_none_match(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    fake = _RecordingS3(conditional_mode="ignore")

    with pytest.raises(BackupError, match="did not enforce"):
        upload_bundle(bundle, _settings(), client=fake)
    assert fake.keys
    assert all(key.endswith("/CLAIM.json") for key in fake.keys)


def test_object_upload_rejects_same_size_local_mutation(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()

    class MutatingS3(_RecordingS3):
        def upload_file(self, filename, bucket, key, ExtraArgs):
            path = Path(filename)
            path.write_bytes(b"x" * path.stat().st_size)
            super().upload_file(filename, bucket, key, ExtraArgs)

    fake = MutatingS3()
    complete_key = (
        f"{settings.object_prefix(manifest['backup_id'])}/{COMPLETE_FILE}"
    )

    with pytest.raises(BackupError, match="changed while"):
        upload_bundle(bundle, settings, client=fake)
    assert complete_key not in fake.objects


def test_local_mutation_after_initial_validation_blocks_completion(
    tmp_path, synthetic_sessions, monkeypatch
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    original_verify = store_module.verify_bundle_files

    def verify_then_mutate(bundle_dir, frozen_manifest):
        original_verify(bundle_dir, frozen_manifest)
        database = bundle_dir / DATABASE_BUNDLE_PATH
        body = database.read_bytes()
        database.write_bytes(body[:-1] + bytes([body[-1] ^ 1]))

    monkeypatch.setattr(
        store_module,
        "verify_bundle_files",
        verify_then_mutate,
    )
    fake = _RecordingS3()

    with pytest.raises(BackupError, match="changed after bundle verification"):
        upload_bundle(bundle, settings, client=fake)
    complete_key = (
        f"{settings.object_prefix(manifest['backup_id'])}/{COMPLETE_FILE}"
    )
    assert complete_key not in fake.objects
    assert not any("/objects/" in key for key in fake.keys)


def test_completed_remote_prefix_is_never_overwritten(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3()
    complete_key = (
        f"{settings.object_prefix(manifest['backup_id'])}/{COMPLETE_FILE}"
    )
    fake.objects[complete_key] = {
        "body": b"existing",
        "head": {
            "ContentLength": 8,
            "Metadata": {"sha256": "0" * 64},
            "ServerSideEncryption": "AES256",
        },
    }

    with pytest.raises(BackupError, match="already complete"):
        upload_bundle(bundle, settings, client=fake)
    assert fake.keys == []


def test_object_client_failure_never_exposes_credentials(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    sentinel = "SENTINEL-SECRET-DO-NOT-LOG"
    fake = _RecordingS3(failure=f"provider URL contained {sentinel}")

    with pytest.raises(BackupError) as caught:
        upload_bundle(bundle, _settings(sentinel), client=fake)
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(_settings(sentinel))


def test_endpoint_userinfo_is_rejected_without_leaking_it(monkeypatch):
    sentinel = "SENTINEL-URL-PASSWORD"
    values = {
        "CADDIE_BACKUP_BUCKET": "synthetic-private-bucket",
        "CADDIE_BACKUP_PREFIX": "caddieinsight/backups",
        "CADDIE_BACKUP_REGION": "synthetic-region",
        "CADDIE_BACKUP_ENDPOINT_URL": (
            f"https://user:{sentinel}@objects.example.invalid"
        ),
        "CADDIE_BACKUP_SSE": "AES256",
        "CADDIE_BACKUP_ACCESS_KEY_ID": "synthetic-access",
        "CADDIE_BACKUP_SECRET_ACCESS_KEY": "synthetic-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(BackupError) as caught:
        S3Settings.from_env(role="backup")
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(
        _settings(endpoint=f"https://user:{sentinel}@objects.example.invalid")
    )


def test_kms_alias_is_rejected_because_head_resolves_it(monkeypatch):
    values = {
        "CADDIE_BACKUP_BUCKET": "synthetic-private-bucket",
        "CADDIE_BACKUP_PREFIX": "caddieinsight/backups",
        "CADDIE_BACKUP_REGION": "synthetic-region",
        "CADDIE_BACKUP_SSE": "aws:kms",
        "CADDIE_BACKUP_KMS_KEY_ID": (
            "arn:aws:kms:us-east-1:123456789012:alias/synthetic"
        ),
        "CADDIE_BACKUP_ACCESS_KEY_ID": "synthetic-access",
        "CADDIE_BACKUP_SECRET_ACCESS_KEY": "synthetic-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(BackupError, match="aliases cannot be verified"):
        S3Settings.from_env(role="backup")


def test_fake_s3_round_trip_download_and_restore(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3()
    upload_bundle(bundle, settings, client=fake)
    downloaded = tmp_path / "downloaded"

    result = download_bundle(
        manifest["backup_id"], downloaded, settings, client=fake
    )
    assert result["backup_id"] == manifest["backup_id"]
    assert (downloaded / COMPLETE_FILE).is_file()
    scratch = tmp_path / "round-trip-scratch"
    scratch.mkdir()
    restored = restore_backup(downloaded, scratch)
    assert restored["report"]["entitlement_and_purchase_reconciliation"] == "matched"


def test_download_rejects_object_mutated_between_head_and_get_and_cleans_partial(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3()
    upload_bundle(bundle, settings, client=fake)
    complete_key = (
        f"{settings.object_prefix(manifest['backup_id'])}/{COMPLETE_FILE}"
    )
    original = fake.objects[complete_key]["body"]
    fake.mutate_before_get[complete_key] = b"x" * len(original)
    output = tmp_path / "mutated-download"

    with pytest.raises(BackupError, match="changed between inspection"):
        download_bundle(manifest["backup_id"], output, settings, client=fake)
    assert not output.exists()
    assert not list(tmp_path.glob(".mutated-download.partial-*"))


def test_version_pinned_download_reads_the_inspected_version(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3(versioned=True)
    upload_bundle(bundle, settings, client=fake)
    complete_key = (
        f"{settings.object_prefix(manifest['backup_id'])}/{COMPLETE_FILE}"
    )
    original = fake.objects[complete_key]["body"]
    fake.mutate_before_get[complete_key] = b"x" * len(original)
    output = tmp_path / "version-download"

    downloaded = download_bundle(
        manifest["backup_id"],
        output,
        settings,
        client=fake,
    )
    assert downloaded["backup_id"] == manifest["backup_id"]
    complete_get = next(
        call for call in fake.get_calls if call["key"] == complete_key
    )
    assert complete_get["version_id"]
    assert complete_get["if_match"] is None


def test_download_rejects_stream_larger_than_declared_before_excess_write(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3()
    upload_bundle(bundle, settings, client=fake)
    complete_key = (
        f"{settings.object_prefix(manifest['backup_id'])}/{COMPLETE_FILE}"
    )
    fake.oversize_stream_keys.add(complete_key)
    output = tmp_path / "oversize-download"

    with pytest.raises(BackupError, match="declared byte limit"):
        download_bundle(manifest["backup_id"], output, settings, client=fake)
    assert not output.exists()
    assert not list(tmp_path.glob(".oversize-download.partial-*"))


def test_download_stream_failure_removes_partial_output(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3()
    upload_bundle(bundle, settings, client=fake)
    complete_key = (
        f"{settings.object_prefix(manifest['backup_id'])}/{COMPLETE_FILE}"
    )
    fake.failing_stream_keys.add(complete_key)
    output = tmp_path / "stream-failure"

    with pytest.raises(BackupError, match="streaming download failed"):
        download_bundle(manifest["backup_id"], output, settings, client=fake)
    assert not output.exists()
    assert not list(tmp_path.glob(".stream-failure.partial-*"))


@pytest.mark.parametrize("metadata_name", [COMPLETE_FILE, MANIFEST_FILE])
def test_download_requires_encryption_confirmation_for_metadata_objects(
    tmp_path, synthetic_sessions, metadata_name
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3()
    upload_bundle(bundle, settings, client=fake)
    key = f"{settings.object_prefix(manifest['backup_id'])}/{metadata_name}"
    fake.objects[key]["head"]["ServerSideEncryption"] = None

    with pytest.raises(BackupError, match="requested encryption"):
        download_bundle(
            manifest["backup_id"],
            tmp_path / f"unencrypted-{metadata_name}",
            settings,
            client=fake,
        )


def test_download_rejects_unsupported_remote_format(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    settings = _settings()
    fake = _RecordingS3()
    upload_bundle(bundle, settings, client=fake)
    prefix = settings.object_prefix(manifest["backup_id"])
    remote_manifest = json.loads(
        fake.objects[f"{prefix}/{MANIFEST_FILE}"]["body"]
    )
    remote_manifest["format"] = "unsupported/v999"
    manifest_bytes = _canonical(remote_manifest)
    fake.objects[f"{prefix}/{MANIFEST_FILE}"]["body"] = manifest_bytes
    fake.objects[f"{prefix}/{MANIFEST_FILE}"]["head"]["ContentLength"] = len(
        manifest_bytes
    )
    fake.objects[f"{prefix}/{MANIFEST_FILE}"]["head"]["Metadata"]["sha256"] = (
        hashlib.sha256(manifest_bytes).hexdigest()
    )
    remote_complete = {
        "format": "unsupported/v999",
        "backup_id": manifest["backup_id"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    complete_bytes = _canonical(remote_complete)
    fake.objects[f"{prefix}/{COMPLETE_FILE}"]["body"] = complete_bytes
    fake.objects[f"{prefix}/{COMPLETE_FILE}"]["head"]["ContentLength"] = len(
        complete_bytes
    )
    fake.objects[f"{prefix}/{COMPLETE_FILE}"]["head"]["Metadata"]["sha256"] = (
        hashlib.sha256(complete_bytes).hexdigest()
    )

    with pytest.raises(BackupError, match="format"):
        download_bundle(
            manifest["backup_id"],
            tmp_path / "unsupported-download",
            settings,
            client=fake,
        )


def test_download_rejects_data_root_before_network(synthetic_sessions):
    sessions, _ = synthetic_sessions
    manifest = create_backup(
        sessions,
        sessions.parent / "outside-bundle",
        now=CAPTURED_AT,
    )
    fake = _RecordingS3()

    with pytest.raises(BackupError, match="/data"):
        download_bundle(
            manifest["backup_id"], Path("/data/download"), _settings(), client=fake
        )
    assert fake.keys == []


def test_restore_rejects_data_root_before_writing(tmp_path, synthetic_sessions):
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)

    with pytest.raises(BackupError, match="/data"):
        restore_backup(bundle, Path("/data"))


def test_generation_zero_bundle_omits_mobile_state_and_remains_restorable(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)

    assert "mobile_state" not in manifest
    scratch = tmp_path / "generation-zero-scratch"
    scratch.mkdir()
    assert restore_backup(bundle, scratch)["report"]["sqlite_integrity_check"] == "ok"


def test_generation_one_manifest_attests_the_frozen_snapshot_not_live_writer(
    tmp_path, synthetic_sessions, monkeypatch
):
    sessions, live_connection = synthetic_sessions
    ensure_mobile_state_schema(live_connection)
    live_connection.execute(
        "INSERT INTO mobile_rate_limit_events "
        "(domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
        ("auth-start-client-ip", "old-key", "a" * 64, 10.0),
    )
    live_connection.commit()
    original_snapshot = core_module._online_sqlite_snapshot

    def snapshot_then_advance_writer(source, destination):
        original_snapshot(source, destination)
        live_connection.execute(
            "INSERT INTO mobile_rate_limit_events "
            "(domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
            ("auth-start-client-ip", "new-key", "b" * 64, 11.0),
        )
        live_connection.commit()

    monkeypatch.setattr(
        core_module,
        "_online_sqlite_snapshot",
        snapshot_then_advance_writer,
    )

    _, manifest = _create_bundle(tmp_path, sessions)

    assert set(manifest["mobile_state"]) == {
        "generation",
        "schema_sha256",
        "table_row_counts",
        "phase_counts",
        "domain_counts",
        "referenced_hmac_key_ids",
    }
    assert manifest["mobile_state"]["generation"] == 1
    assert manifest["mobile_state"]["table_row_counts"][
        "mobile_rate_limit_events"
    ] == 1
    assert manifest["mobile_state"]["domain_counts"] == {
        "mobile_rate_limit_events": {"auth-start-client-ip": 1}
    }
    assert manifest["mobile_state"]["referenced_hmac_key_ids"] == ["old-key"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest: manifest.pop("mobile_state"), "mobile state"),
        (
            lambda manifest: manifest["mobile_state"].__setitem__("generation", 2),
            "generation",
        ),
        (
            lambda manifest: manifest["mobile_state"]["table_row_counts"].__setitem__(
                "mobile_rate_limit_events", 99
            ),
            "attestation",
        ),
        (
            lambda manifest: manifest["mobile_state"].__setitem__("extra", True),
            "shape",
        ),
        (
            lambda manifest: manifest["mobile_state"].__setitem__(
                "referenced_hmac_key_ids", ["valid-key", {}]
            ),
            "HMAC key",
        ),
    ],
)
def test_generation_one_restore_rejects_missing_future_or_tampered_attestation(
    tmp_path, synthetic_sessions, mutation, message
):
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.commit()
    bundle, manifest = _create_bundle(tmp_path, sessions)
    if "mobile_state" not in manifest:
        snapshot = sqlite3.connect(bundle / DATABASE_BUNDLE_PATH)
        try:
            manifest["mobile_state"] = mobile_state_summary(snapshot)
        finally:
            snapshot.close()
    mutation(manifest)
    _rewrite_completion(bundle, manifest)
    scratch = tmp_path / "mobile-state-rejection-scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match=message):
        restore_backup(bundle, scratch)

    assert not list(scratch.iterdir())


def test_partial_generation_one_schema_cannot_be_backed_up(
    tmp_path, synthetic_sessions
):
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.execute("DROP INDEX mobile_auth_challenges_active_ip")
    connection.commit()

    with pytest.raises(BackupError, match="mobile state"):
        create_backup(sessions, tmp_path / "partial-mobile", now=CAPTURED_AT)


def test_generation_zero_rejects_an_unknown_future_mobile_table(
    synthetic_sessions,
):
    _sessions, connection = synthetic_sessions
    connection.execute(
        "CREATE TABLE mobile_future_credentials (credential_id TEXT PRIMARY KEY)"
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="unknown|unsupported"):
        core_module.detect_mobile_state_generation(connection)


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE mobile_future_credentials (credential_id TEXT PRIMARY KEY)",
        "CREATE TABLE Mobile_Future_Credentials (credential_id TEXT PRIMARY KEY)",
        "CREATE INDEX future_token_lookup ON mobile_api_tokens(user_id)",
        "CREATE TRIGGER future_token_cleanup AFTER DELETE ON mobile_api_tokens "
        "BEGIN SELECT 1; END",
        "CREATE VIEW mobile_future_credentials_view AS "
        "SELECT selector FROM mobile_api_tokens",
    ],
    ids=[
        "table",
        "case-folded-table",
        "coupled-index",
        "coupled-trigger",
        "mobile-view",
    ],
)
def test_generation_one_rejects_unregistered_mobile_schema_objects(
    tmp_path, synthetic_sessions, ddl
):
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.execute(ddl)
    connection.commit()

    with pytest.raises(RuntimeError, match="unknown|unsupported"):
        core_module.detect_mobile_state_generation(connection)
    with pytest.raises(BackupError, match="mobile state"):
        create_backup(
            sessions,
            tmp_path / "unknown-mobile-object-bundle",
            now=CAPTURED_AT,
        )


def test_known_mobile_generations_remain_detectable(synthetic_sessions):
    _sessions, connection = synthetic_sessions

    assert core_module.detect_mobile_state_generation(connection) == 0
    ensure_mobile_state_schema(connection)
    assert core_module.detect_mobile_state_generation(connection) == 1


def test_registered_mobile_index_must_keep_its_declared_shape(synthetic_sessions):
    _sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.executescript(
        "DROP INDEX mobile_api_tokens_user_active;"
        "CREATE INDEX mobile_api_tokens_user_active ON mobile_api_tokens(user_id);"
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="unknown|unsupported"):
        core_module.detect_mobile_state_generation(connection)


def _insert_baseline_journal(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO mobile_recovery_baseline_journals "
        "(operation_id, phase, request_hash, lineage_id, created_at, updated_at) "
        "VALUES (?, 'lineage_prepared', ?, ?, ?, ?)",
        (
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "c" * 64,
            BASELINE_LINEAGE_ID,
            1.0,
            1.0,
        ),
    )


def test_baseline_manifest_uses_explicit_null_self_hash_and_exact_snapshot_facts(
    tmp_path, synthetic_sessions
):
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    _insert_baseline_journal(connection)
    connection.commit()

    bundle = tmp_path / "baseline-bundle"
    manifest = create_backup(
        sessions,
        bundle,
        now=CAPTURED_AT,
        baseline_lineage_id=BASELINE_LINEAGE_ID,
    )

    assert manifest["recovery_fence"] == {
        "lineage_id": BASELINE_LINEAGE_ID,
        "baseline_backup_id": manifest["backup_id"],
        "baseline_manifest_sha256": None,
        "baseline_schema_generation": 1,
        "baseline_db_checkpoint": manifest["database"]["sha256"],
    }
    manifest_sha256 = hashlib.sha256((bundle / MANIFEST_FILE).read_bytes()).hexdigest()
    assert manifest_sha256 not in json.dumps(manifest["recovery_fence"])


def test_baseline_restore_requires_the_snapshot_journal_to_remain_prepared(
    tmp_path, synthetic_sessions
):
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    _insert_baseline_journal(connection)
    connection.commit()
    bundle = tmp_path / "tampered-baseline-phase"
    manifest = create_backup(
        sessions,
        bundle,
        now=CAPTURED_AT,
        baseline_lineage_id=BASELINE_LINEAGE_ID,
    )
    snapshot = sqlite3.connect(bundle / DATABASE_BUNDLE_PATH)
    try:
        snapshot.execute(
            "UPDATE mobile_recovery_baseline_journals SET phase='backup_verified'"
        )
        snapshot.commit()
        manifest["mobile_state"] = mobile_state_summary(snapshot)
    finally:
        snapshot.close()
    database_bytes = (bundle / DATABASE_BUNDLE_PATH).read_bytes()
    database_sha256 = hashlib.sha256(database_bytes).hexdigest()
    manifest["database"]["sha256"] = database_sha256
    manifest["database"]["size"] = len(database_bytes)
    manifest["recovery_fence"]["baseline_db_checkpoint"] = database_sha256
    _rewrite_completion(bundle, manifest)
    scratch = tmp_path / "tampered-baseline-scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="snapshot lineage"):
        restore_backup(bundle, scratch)

    assert not list(scratch.iterdir())


def test_later_manifest_binds_the_accepted_genesis_manifest_hash(
    tmp_path, synthetic_sessions
):
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.execute(
        "INSERT INTO mobile_recovery_accepted_baselines "
        "(lineage_id, baseline_backup_id, minimum_backup_created_at, "
        "manifest_sha256, schema_generation, baseline_db_checkpoint, accepted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            BASELINE_LINEAGE_ID,
            "20260727T110000Z-aaaaaaaaaaaa",
            CAPTURED_AT.timestamp() - 3600,
            "d" * 64,
            1,
            "e" * 64,
            CAPTURED_AT.timestamp() - 3500,
        ),
    )
    connection.commit()

    _, manifest = _create_bundle(tmp_path, sessions)

    assert manifest["recovery_fence"] == {
        "lineage_id": BASELINE_LINEAGE_ID,
        "baseline_backup_id": "20260727T110000Z-aaaaaaaaaaaa",
        "baseline_manifest_sha256": "d" * 64,
        "baseline_schema_generation": 1,
        "baseline_db_checkpoint": "e" * 64,
    }


def test_generation_zero_cannot_be_declared_as_a_cutover_baseline(
    tmp_path, synthetic_sessions
):
    sessions, _ = synthetic_sessions

    with pytest.raises(BackupError, match="generation-1"):
        create_backup(
            sessions,
            tmp_path / "invalid-baseline",
            now=CAPTURED_AT,
            baseline_lineage_id=BASELINE_LINEAGE_ID,
        )


def _restore_service_module():
    try:
        from swinglab.backups import restore_service
    except ImportError as exc:  # pragma: no cover - RED-only guard
        pytest.fail(f"restore_service is missing: {exc}")
    return restore_service


def _listed_restore_hashes(root: Path, manifest: dict) -> dict[str, str]:
    paths = [DATABASE_BUNDLE_PATH]
    paths.extend(f"artifacts/{item['path']}" for item in manifest["artifacts"]["files"])
    return {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in paths
    }


def test_retained_evidence_is_read_only_and_second_copy_is_uniquely_migrated(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, _ = synthetic_sessions
    bundle, manifest = _create_bundle(tmp_path, sessions)
    original_hashes = {
        MANIFEST_FILE: hashlib.sha256((bundle / MANIFEST_FILE).read_bytes()).hexdigest(),
        COMPLETE_FILE: hashlib.sha256((bundle / COMPLETE_FILE).read_bytes()).hexdigest(),
        **_listed_restore_hashes(bundle, manifest),
    }
    scratch = tmp_path / "service-scratch"
    scratch.mkdir()

    evidence = module.retain_verified_restore_evidence(bundle, scratch)
    retained_hashes = _listed_restore_hashes(evidence.restore_dir, manifest)
    working_one = module.create_service_working_copy(evidence, scratch)
    working_two = module.create_service_working_copy(evidence, scratch)

    assert working_one != working_two != evidence.restore_dir
    assert _listed_restore_hashes(evidence.restore_dir, manifest) == retained_hashes
    assert (evidence.restore_dir / DATABASE_BUNDLE_PATH).stat().st_mode & stat.S_IWUSR == 0
    retained_connection = sqlite3.connect(evidence.restore_dir / DATABASE_BUNDLE_PATH)
    working_connection = sqlite3.connect(working_one / "swinglab.db")
    try:
        assert core_module.detect_mobile_state_generation(retained_connection) == 0
        assert core_module.detect_mobile_state_generation(working_connection) == 1
    finally:
        retained_connection.close()
        working_connection.close()
    assert all(
        (working_one / item["path"]).is_file()
        for item in manifest["artifacts"]["files"]
    )
    assert {
        MANIFEST_FILE: hashlib.sha256((bundle / MANIFEST_FILE).read_bytes()).hexdigest(),
        COMPLETE_FILE: hashlib.sha256((bundle / COMPLETE_FILE).read_bytes()).hexdigest(),
        **_listed_restore_hashes(bundle, manifest),
    } == original_hashes


def test_working_copy_rejects_changed_retained_manifest_metadata(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, _ = synthetic_sessions
    bundle, _manifest = _create_bundle(tmp_path, sessions)
    scratch = tmp_path / "retained-metadata-scratch"
    scratch.mkdir()
    evidence = module.retain_verified_restore_evidence(bundle, scratch)
    retained_manifest = evidence.restore_dir / MANIFEST_FILE
    retained_manifest.chmod(0o600)
    retained_manifest.write_text("{}\n", encoding="utf-8")

    with pytest.raises(BackupError, match="metadata"):
        module.create_service_working_copy(evidence, scratch)

    assert not list(scratch.glob("service-working-*"))


def test_manifest_parse_and_checksum_use_one_authenticated_byte_snapshot(
    tmp_path, synthetic_sessions, monkeypatch
):
    sessions, _ = synthetic_sessions
    bundle, manifest_a = _create_bundle(tmp_path, sessions)
    manifest_b = json.loads(json.dumps(manifest_a))
    manifest_b["created_at"] = "2026-07-27T12:00:01+00:00"
    manifest_b_bytes = _canonical(manifest_b)
    complete_b_bytes = _canonical(
        {
            "format": manifest_b["format"],
            "backup_id": manifest_b["backup_id"],
            "manifest_sha256": hashlib.sha256(manifest_b_bytes).hexdigest(),
        }
    )
    original_loads = core_module.json.loads
    parse_count = 0

    def replace_bundle_after_manifest_parse(*args, **kwargs):
        nonlocal parse_count
        value = original_loads(*args, **kwargs)
        parse_count += 1
        if parse_count == 1:
            (bundle / MANIFEST_FILE).write_bytes(manifest_b_bytes)
            (bundle / COMPLETE_FILE).write_bytes(complete_b_bytes)
        return value

    monkeypatch.setattr(core_module.json, "loads", replace_bundle_after_manifest_parse)

    with pytest.raises(BackupError, match="checksum"):
        core_module.load_and_verify_manifest(bundle)


def _readiness_is_accepted(module, ready_path: Path, receipt: str | None) -> bool:
    acceptance = getattr(module, "_accept_service_restore_readiness", None)
    if acceptance is None:
        return ready_path.is_file()
    try:
        acceptance(ready_path, receipt)
    except (BackupError, TypeError, ValueError):
        return False
    return True


def test_readiness_publication_returns_a_restart_stable_acceptance_receipt(
    tmp_path,
):
    module = _restore_service_module()
    ready_path = tmp_path / "service-restore-ready.json"
    body = b'{"ready":true}\n'

    receipt = module._durably_publish_readiness(ready_path, body)

    assert isinstance(receipt, str) and re.fullmatch(r"[0-9a-f]{64}", receipt)
    commit_path = tmp_path / "service-restore-ready.commit.json"
    assert commit_path.is_file()
    assert receipt.encode("ascii") not in ready_path.read_bytes()
    assert receipt.encode("ascii") not in commit_path.read_bytes()
    restarted_module = importlib.reload(module)
    assert _readiness_is_accepted(restarted_module, ready_path, receipt)
    replay_root = tmp_path / "replayed-candidate"
    replay_root.mkdir()
    replay_path = replay_root / ready_path.name
    replay_commit_path = replay_root / commit_path.name
    replay_path.write_bytes(ready_path.read_bytes())
    replay_commit_path.write_bytes(commit_path.read_bytes())
    assert not _readiness_is_accepted(restarted_module, replay_path, receipt)


def test_readiness_publisher_leaves_no_authoritative_marker_on_file_fsync_failure(
    tmp_path, monkeypatch
):
    module = _restore_service_module()
    ready_path = tmp_path / "service-restore-ready.json"

    def fail_fsync(_descriptor):
        raise OSError("synthetic readiness fsync failure")

    monkeypatch.setattr(module.os, "fsync", fail_fsync)

    with pytest.raises(BackupError, match="readiness"):
        module._durably_publish_readiness(ready_path, b'{"ready":true}\n')

    assert not ready_path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_readiness_publisher_denies_marker_after_parent_fsync_failure(
    tmp_path, monkeypatch
):
    module = _restore_service_module()
    from swinglab.web import recovery_fence_ledger as ledger_module

    ready_path = tmp_path / "service-restore-ready.json"
    existed_when_fsync_failed = []

    def fail_parent_fsync(_path):
        existed_when_fsync_failed.append(ready_path.exists())
        raise ledger_module.RecoveryFenceError(
            "synthetic post-rename directory fsync failure"
        )

    monkeypatch.setattr(ledger_module, "_fsync_directory", fail_parent_fsync)

    receipt = None
    with pytest.raises(BackupError, match="readiness"):
        module._durably_publish_readiness(ready_path, b'{"ready":true}\n')

    assert existed_when_fsync_failed == [True]
    assert not _readiness_is_accepted(module, ready_path, receipt)


def test_readiness_denial_survives_unlink_and_quarantine_failures(
    tmp_path, monkeypatch
):
    module = _restore_service_module()
    from swinglab.web import recovery_fence_ledger as ledger_module

    ready_path = tmp_path / "service-restore-ready.json"
    original_unlink = Path.unlink
    original_replace = module.os.replace
    unlink_attempted = []
    quarantine_attempted = []

    def fail_parent_fsync(_path):
        raise ledger_module.RecoveryFenceError("synthetic post-rename fsync failure")

    def fail_ready_unlink(path, *args, **kwargs):
        if path == ready_path:
            unlink_attempted.append(path)
            raise OSError("synthetic readiness unlink failure")
        return original_unlink(path, *args, **kwargs)

    def fail_quarantine_replace(source, destination):
        if Path(source) == ready_path and ".failed-" in Path(destination).name:
            quarantine_attempted.append(Path(source))
            raise OSError("synthetic readiness quarantine failure")
        return original_replace(source, destination)

    monkeypatch.setattr(ledger_module, "_fsync_directory", fail_parent_fsync)
    monkeypatch.setattr(Path, "unlink", fail_ready_unlink)
    monkeypatch.setattr(module.os, "replace", fail_quarantine_replace)

    with pytest.raises(BackupError, match="readiness"):
        module._durably_publish_readiness(ready_path, b'{"ready":true}\n')

    assert ready_path.exists()
    assert unlink_attempted == [ready_path]
    assert quarantine_attempted == [ready_path]
    assert not _readiness_is_accepted(module, ready_path, None)


def test_readiness_denial_survives_cleanup_fsync_reappearance(
    tmp_path, monkeypatch
):
    module = _restore_service_module()
    from swinglab.web import recovery_fence_ledger as ledger_module

    ready_path = tmp_path / "service-restore-ready.json"
    body = b'{"ready":true}\n'
    cleanup_fsync_attempted = []

    def fail_publish_fsync(_path):
        raise ledger_module.RecoveryFenceError("synthetic post-rename fsync failure")

    def fail_cleanup_fsync(_path):
        cleanup_fsync_attempted.append(_path)
        ready_path.write_bytes(body)
        raise ledger_module.RecoveryFenceError("synthetic cleanup fsync failure")

    monkeypatch.setattr(ledger_module, "_fsync_directory", fail_publish_fsync)
    monkeypatch.setattr(module, "_fsync_directory", fail_cleanup_fsync, raising=False)

    with pytest.raises(BackupError, match="readiness"):
        module._durably_publish_readiness(ready_path, body)

    assert ready_path.exists()
    assert cleanup_fsync_attempted == [tmp_path]
    assert not _readiness_is_accepted(module, ready_path, None)


def test_readiness_rejects_a_preexisting_broken_link(tmp_path):
    module = _restore_service_module()
    ready_path = tmp_path / "service-restore-ready.json"
    try:
        ready_path.symlink_to(tmp_path / "missing-readiness-target")
    except OSError:
        pytest.skip("File symlinks are unavailable on this test host.")

    with pytest.raises(BackupError, match="readiness"):
        module._durably_publish_readiness(ready_path, b'{"ready":true}\n')

    assert not _readiness_is_accepted(module, ready_path, None)


def test_commit_post_rename_failure_rejects_leftovers_after_module_restart(
    tmp_path, monkeypatch
):
    module = _restore_service_module()
    from swinglab.web import recovery_fence_ledger as ledger_module

    ready_path = tmp_path / "service-restore-ready.json"
    commit_path = tmp_path / "service-restore-ready.commit.json"
    original_fsync_directory = ledger_module._fsync_directory
    original_unlink = Path.unlink
    original_replace = module.os.replace
    directory_fsync_calls = 0
    cleanup_unlink_attempts = []
    cleanup_quarantine_attempts = []

    def fail_commit_parent_fsync(path):
        nonlocal directory_fsync_calls
        directory_fsync_calls += 1
        if directory_fsync_calls == 2:
            raise ledger_module.RecoveryFenceError(
                "synthetic commit post-rename fsync failure"
            )
        return original_fsync_directory(path)

    def fail_readiness_unlink(path, *args, **kwargs):
        if path in {ready_path, commit_path}:
            cleanup_unlink_attempts.append(path)
            raise OSError("synthetic readiness cleanup unlink failure")
        return original_unlink(path, *args, **kwargs)

    def fail_readiness_quarantine(source, destination):
        if Path(source) in {ready_path, commit_path} and ".failed-" in Path(
            destination
        ).name:
            cleanup_quarantine_attempts.append(Path(source))
            raise OSError("synthetic readiness cleanup quarantine failure")
        return original_replace(source, destination)

    monkeypatch.setattr(
        ledger_module,
        "_fsync_directory",
        fail_commit_parent_fsync,
    )
    monkeypatch.setattr(Path, "unlink", fail_readiness_unlink)
    monkeypatch.setattr(module.os, "replace", fail_readiness_quarantine)

    with pytest.raises(BackupError, match="readiness"):
        module._durably_publish_readiness(ready_path, b'{"ready":true}\n')

    assert ready_path.exists()
    assert commit_path.exists()
    assert cleanup_unlink_attempts == [ready_path, commit_path]
    assert cleanup_quarantine_attempts == [ready_path, commit_path]
    restarted_module = importlib.reload(module)
    assert not _readiness_is_accepted(restarted_module, ready_path, "0" * 64)


def _insert_dummy_required_row(connection: sqlite3.Connection, table: str) -> None:
    connection.execute("PRAGMA ignore_check_constraints=ON")
    columns = []
    values = []
    for row in connection.execute(f"PRAGMA table_info({table})"):
        name, declared_type, not_null, default_value, primary_key = (
            str(row[1]),
            str(row[2]).upper(),
            bool(row[3]),
            row[4],
            bool(row[5]),
        )
        if not (not_null or primary_key) or default_value is not None:
            continue
        columns.append(name)
        if "INT" in declared_type:
            values.append(1)
        elif "REAL" in declared_type or "FLOA" in declared_type:
            values.append(1.0)
        else:
            values.append(f"dummy-{table}-{name}")
    quoted = ", ".join(f'"{name}"' for name in columns)
    placeholders = ", ".join("?" for _ in values)
    connection.execute(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', values
    )


def _seed_registered_credential_rows(connection, table_names) -> None:
    for table in table_names:
        if connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0:
            _insert_dummy_required_row(connection, table)


def test_restore_credential_registry_is_owned_by_the_schema_generation():
    module = _restore_service_module()

    registry = module.RestoredCredentialTableRegistry(1)

    assert registry.generation == 1
    assert (
        registry.table_names
        == MOBILE_STATE_GENERATIONS[1].restored_credential_tables
    )
    assert {
        "mobile_api_tokens",
        "mobile_auth_challenges",
        "email_codes",
        "signup_intents",
        "shopify_customer_account_oauth_states",
        "shopify_customer_account_browser_sessions",
    }.issubset(registry.table_names)


def test_empty_credential_extensions_still_purge_and_audit_mandatory_rows(
    synthetic_sessions,
):
    module = _restore_service_module()
    _sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    credential_tables = MOBILE_STATE_GENERATIONS[1].restored_credential_tables
    _seed_registered_credential_rows(connection, credential_tables)
    connection.commit()

    module.prepare_restored_auth_state(
        connection,
        source_backup_id="20260727T120000Z-aaaaaaaaaaaa",
        source_lineage_id=BASELINE_LINEAGE_ID,
        now=CAPTURED_AT.timestamp(),
    )

    assert all(
        connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        for table in credential_tables
    )
    expected_epochs = module._restored_user_epoch_snapshot(connection)
    _insert_dummy_required_row(connection, "mobile_api_tokens")
    connection.commit()
    with pytest.raises(BackupError, match="credential reset"):
        module._validate_restored_auth_reset_postconditions(
            connection,
            expected_user_epochs=expected_epochs,
        )


def test_non_generation_credential_table_cannot_gain_deletion_authority(
    synthetic_sessions,
):
    module = _restore_service_module()
    _sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.execute(
        "CREATE TABLE future_auth_sessions (session_id TEXT PRIMARY KEY)"
    )
    connection.execute("INSERT INTO future_auth_sessions VALUES ('preserved')")
    connection.commit()

    with pytest.raises(ValueError, match="generation-owned"):
        module.RestoredCredentialTableRegistry(("future_auth_sessions",))

    module.prepare_restored_auth_state(
        connection,
        source_backup_id="20260727T120000Z-aaaaaaaaaaaa",
        source_lineage_id=BASELINE_LINEAGE_ID,
        now=CAPTURED_AT.timestamp(),
    )

    assert connection.execute(
        "SELECT COUNT(*) FROM future_auth_sessions"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM mobile_api_tokens"
    ).fetchone()[0] == 0
@pytest.mark.parametrize(
    "protected_table",
    [
        "users",
        "jobs",
        "pro_grants",
        "shopify_orders",
        "gear_orders",
        "auth_attempts",
        "shopify_sync_control",
        "shopify_privacy_event_fences",
        "shopify_redacted_order_fences",
        "shopify_privacy_requests",
        "shopify_customer_tombstones",
        "shopify_pending_customer_links",
        "shopify_customer_backfill_binding",
        "analysis_usage_monthly",
        "history_reset_operations",
        "golfer_profiles",
        "practice_checkins",
        "product_events",
        "proof_cycle_practice_evidence",
        "proof_cycle_transfer_checks",
        "lifecycle_email_sends",
        "mobile_recovery_fence_checkpoints",
        "mobile_recovery_baseline_journals",
        "mobile_recovery_accepted_baselines",
        "mobile_rate_limit_events",
        "mobile_restore_credential_reset_markers",
        "mobile_api_tokens",
        "sqlite_sequence",
    ],
)
def test_caller_names_cannot_enter_generation_owned_credential_registry(
    protected_table,
):
    module = _restore_service_module()

    with pytest.raises(ValueError, match="protected|mandatory|generation-owned"):
        module.RestoredCredentialTableRegistry((protected_table,))


def test_mixed_case_mandatory_credential_table_is_purged_and_audited(
    synthetic_sessions,
):
    module = _restore_service_module()
    _sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.executescript(
        'ALTER TABLE email_codes RENAME TO email_codes_temporary;'
        'ALTER TABLE email_codes_temporary RENAME TO "Email_Codes";'
    )
    _insert_dummy_required_row(connection, "Email_Codes")
    connection.commit()

    module.prepare_restored_auth_state(
        connection,
        source_backup_id="20260727T120000Z-aaaaaaaaaaaa",
        source_lineage_id=BASELINE_LINEAGE_ID,
        now=CAPTURED_AT.timestamp(),
    )

    assert connection.execute("SELECT COUNT(*) FROM email_codes").fetchone()[0] == 0
    expected_epochs = module._restored_user_epoch_snapshot(connection)
    _insert_dummy_required_row(connection, "Email_Codes")
    connection.commit()
    with pytest.raises(BackupError, match="credential reset"):
        module._validate_restored_auth_reset_postconditions(
            connection,
            expected_user_epochs=expected_epochs,
        )


def test_prepare_restored_auth_state_is_transactional_idempotent_and_preserving(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.execute(
        "UPDATE users SET email_verified_at = ?, auth_epoch = 7, history_epoch = 3 "
        "WHERE id = 'user-synthetic'",
        (20.0,),
    )
    connection.execute(
        "CREATE TABLE shopify_customer_backfill_binding ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), store_domain TEXT NOT NULL, "
        "shop_gid TEXT NOT NULL, bound_at REAL NOT NULL)"
    )
    connection.execute(
        "INSERT INTO shopify_customer_backfill_binding VALUES (1, ?, ?, ?)",
        ("example.myshopify.com", "gid://shopify/Shop/1", 19.0),
    )
    credential_tables = (
        "mobile_api_tokens",
        "mobile_auth_challenges",
        "mobile_review_auth_challenges",
        "mobile_auth_exchange_journals",
        "mobile_auth_exchange_receipts",
        "mobile_signout_journals",
        "mobile_signout_receipts",
        "mobile_device_revoke_journals",
        "mobile_device_revoke_receipts",
        "signup_intents",
        "shopify_customer_account_oauth_states",
        "shopify_customer_account_browser_sessions",
    )
    for table in credential_tables:
        _insert_dummy_required_row(connection, table)
    connection.commit()
    before_preserved = connection.execute(
        "SELECT email, email_verified_at, plan, subscription_status, pro_until, "
        "history_epoch FROM users WHERE id = 'user-synthetic'"
    ).fetchone()
    before_history = connection.execute(
        "SELECT coaching_eligible, refilm_rejections FROM analysis_usage_monthly"
    ).fetchone()

    first = module.prepare_restored_auth_state(
        connection,
        source_backup_id="20260727T120000Z-aaaaaaaaaaaa",
        source_lineage_id=BASELINE_LINEAGE_ID,
        now=CAPTURED_AT.timestamp(),
    )
    second = module.prepare_restored_auth_state(
        connection,
        source_backup_id="20260727T120000Z-aaaaaaaaaaaa",
        source_lineage_id=BASELINE_LINEAGE_ID,
        now=CAPTURED_AT.timestamp() + 10,
    )

    assert first.marker_id == second.marker_id
    assert connection.execute(
        "SELECT password_hash, auth_epoch FROM users WHERE id = 'user-synthetic'"
    ).fetchone() == ("", 8)
    assert connection.execute(
        "SELECT email, email_verified_at, plan, subscription_status, pro_until, "
        "history_epoch FROM users WHERE id = 'user-synthetic'"
    ).fetchone() == before_preserved
    assert connection.execute(
        "SELECT coaching_eligible, refilm_rejections FROM analysis_usage_monthly"
    ).fetchone() == before_history
    assert connection.execute("SELECT COUNT(*) FROM shopify_orders").fetchone()[0] == 1
    assert connection.execute(
        "SELECT store_domain, shop_gid, bound_at "
        "FROM shopify_customer_backfill_binding WHERE id = 1"
    ).fetchone() == (
        "example.myshopify.com",
        "gid://shopify/Shop/1",
        19.0,
    )
    assert connection.execute("SELECT COUNT(*) FROM email_codes").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM auth_attempts").fetchone()[0] == 1
    assert all(
        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        for table in credential_tables
    )
    assert connection.execute(
        "SELECT source_backup_id, source_lineage_id, prepared_at FROM "
        "mobile_restore_credential_reset_markers"
    ).fetchall() == [
        (
            "20260727T120000Z-aaaaaaaaaaaa",
            BASELINE_LINEAGE_ID,
            CAPTURED_AT.timestamp(),
        )
    ]


def test_prepare_restored_auth_state_rolls_back_every_change_on_delete_failure(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    _, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    _insert_dummy_required_row(connection, "signup_intents")
    connection.executescript(
        "CREATE TRIGGER fail_restore_delete BEFORE DELETE ON signup_intents "
        "BEGIN SELECT RAISE(ABORT, 'synthetic restore failure'); END;"
    )
    connection.commit()

    with pytest.raises(BackupError, match="credential reset"):
        module.prepare_restored_auth_state(
            connection,
            source_backup_id="20260727T120000Z-aaaaaaaaaaaa",
            source_lineage_id=BASELINE_LINEAGE_ID,
            now=CAPTURED_AT.timestamp(),
        )

    assert connection.execute(
        "SELECT password_hash, auth_epoch FROM users WHERE id = 'user-synthetic'"
    ).fetchone() == ("synthetic-hash", 0)
    assert connection.execute("SELECT COUNT(*) FROM signup_intents").fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM mobile_restore_credential_reset_markers"
    ).fetchone()[0] == 0


class _StaticValidatedLedger:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def load_chain_snapshot(self):
        self.calls += 1
        return self.snapshot


def _baseline_chain_snapshot(
    manifest: dict,
    manifest_sha256: str,
    *,
    include_token_revoke: bool = True,
    include_reserved: bool = False,
    genesis_backup_id: str | None = None,
    genesis_manifest_sha256: str | None = None,
    genesis_db_checkpoint: str | None = None,
    minimum_backup_created_at: float | None = None,
):
    from swinglab.web.recovery_fence_ledger import (
        PublishedRecoveryRecord,
        ValidatedRecoveryChain,
    )

    baseline_backup_id = genesis_backup_id or manifest["backup_id"]
    baseline_manifest_sha256 = genesis_manifest_sha256 or manifest_sha256
    baseline_db_checkpoint = genesis_db_checkpoint or manifest["database"]["sha256"]
    minimum_created_at = (
        float(manifest["created_at_epoch"])
        if minimum_backup_created_at is None
        else minimum_backup_created_at
    )
    baseline = PublishedRecoveryRecord(
        sequence=1,
        previous_record_key=None,
        previous_record_hash=None,
        kind="cutover_baseline",
        event_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        cutoff_at=minimum_created_at,
        payload={
            "lineage_id": BASELINE_LINEAGE_ID,
            "minimum_backup_created_at": minimum_created_at,
            "baseline_backup_id": baseline_backup_id,
            "manifest_sha256": baseline_manifest_sha256,
            "schema_generation": 1,
            "baseline_db_checkpoint": baseline_db_checkpoint,
        },
        chain_hmac_key_id="chain-key",
        chain_hmac="1" * 64,
        record_hash="2" * 64,
        record_key="fence/records/1-" + "2" * 64 + ".json",
        body=b"baseline-record",
    )
    records = [baseline]
    if include_token_revoke:
        records.append(
            PublishedRecoveryRecord(
                sequence=2,
                previous_record_key=baseline.record_key,
                previous_record_hash=baseline.record_hash,
                kind="token_revoke",
                event_id="bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
                cutoff_at=float(manifest["created_at_epoch"]) + 1,
                payload={
                    "selector_hmac_key_id": "old-key",
                    "selector_hmac": "3" * 64,
                    "token_verifier_hmac_key_id": "old-key",
                    "token_verifier_hmac": "4" * 64,
                },
                chain_hmac_key_id="chain-key",
                chain_hmac="5" * 64,
                record_hash="6" * 64,
                record_key="fence/records/2-" + "6" * 64 + ".json",
                body=b"token-record",
            )
        )
    if include_reserved:
        previous = records[-1]
        records.append(
            PublishedRecoveryRecord(
                sequence=len(records) + 1,
                previous_record_key=previous.record_key,
                previous_record_hash=previous.record_hash,
                kind="push_environment_cutoff",
                event_id="cccccccc-dddd-4eee-8fff-000000000000",
                cutoff_at=float(manifest["created_at_epoch"]) + 2,
                payload={"state": "closed"},
                chain_hmac_key_id="chain-key",
                chain_hmac="7" * 64,
                record_hash="8" * 64,
                record_key=f"fence/records/{len(records) + 1}-" + "8" * 64 + ".json",
                body=b"reserved-record",
            )
        )
    records[-1] = records[-1].__class__(
        **{
            **records[-1].__dict__,
            "head_etag": '"validated-head"',
        }
    )
    return ValidatedRecoveryChain(
        head_etag='"validated-head"', records=tuple(records)
    )


def _service_keyring(*, include_old: bool = True) -> VersionedHMAC:
    keys = {"active-key": b"a" * 32, "chain-key": b"c" * 32}
    if include_old:
        keys["old-key"] = b"o" * 32
    return VersionedHMAC("active-key", keys)


def _create_service_baseline_bundle(tmp_path, synthetic_sessions):
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    _insert_baseline_journal(connection)
    connection.execute(
        "INSERT INTO mobile_rate_limit_events "
        "(domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
        ("auth-start-client-ip", "old-key", "9" * 64, 5.0),
    )
    connection.execute(
        "INSERT INTO mobile_api_tokens "
        "(selector, token_hash, user_id, auth_epoch, label, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("restored-selector", "restored-token-hash", "user-synthetic", 0, "Phone", 1.0, 9999999999.0),
    )
    connection.commit()
    bundle = tmp_path / "service-baseline-bundle"
    manifest = create_backup(
        sessions,
        bundle,
        now=CAPTURED_AT,
        baseline_lineage_id=BASELINE_LINEAGE_ID,
    )
    manifest_sha256 = hashlib.sha256((bundle / MANIFEST_FILE).read_bytes()).hexdigest()
    return bundle, manifest, manifest_sha256


def test_service_restore_prepares_only_disposable_copy_and_reconciles_full_chain(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    bundle, manifest, manifest_sha256 = _create_service_baseline_bundle(
        tmp_path, synthetic_sessions
    )
    chain = _baseline_chain_snapshot(manifest, manifest_sha256)
    ledger = _StaticValidatedLedger(chain)
    scratch = tmp_path / "service-prepare-scratch"
    scratch.mkdir()

    result = module.prepare_service_restore(
        bundle,
        scratch,
        ledger=ledger,
        keyring=_service_keyring(),
        now=CAPTURED_AT.timestamp() + 100,
    )

    assert result.ready is True
    assert result.manifest_sha256 == manifest_sha256
    assert result.lineage_id == BASELINE_LINEAGE_ID
    assert result.head_record_hash == chain.records[-1].record_hash
    assert result.retained_restore_dir != result.working_dir
    ready_path = result.working_dir / "service-restore-ready.json"
    assert ready_path.is_file()
    assert (result.working_dir / "service-restore-ready.commit.json").is_file()
    assert _readiness_is_accepted(module, ready_path, result.readiness_receipt)
    retained = sqlite3.connect(result.retained_restore_dir / DATABASE_BUNDLE_PATH)
    working = sqlite3.connect(result.working_dir / "swinglab.db")
    try:
        assert retained.execute(
            "SELECT password_hash, auth_epoch FROM users WHERE id='user-synthetic'"
        ).fetchone() == ("synthetic-hash", 0)
        assert retained.execute("SELECT COUNT(*) FROM mobile_api_tokens").fetchone()[0] == 1
        assert working.execute(
            "SELECT password_hash, auth_epoch FROM users WHERE id='user-synthetic'"
        ).fetchone() == ("", 1)
        assert working.execute("SELECT COUNT(*) FROM mobile_api_tokens").fetchone()[0] == 0
        assert working.execute("SELECT COUNT(*) FROM email_codes").fetchone()[0] == 0
        assert working.execute("SELECT COUNT(*) FROM auth_attempts").fetchone()[0] == 1
        assert working.execute("SELECT COUNT(*) FROM shopify_orders").fetchone()[0] == 1
        assert working.execute(
            "SELECT phase, backup_id, manifest_sha256, record_hash, head_etag "
            "FROM mobile_recovery_baseline_journals"
        ).fetchone() == (
            "accepted",
            manifest["backup_id"],
            manifest_sha256,
            chain.records[0].record_hash,
            chain.head_etag,
        )
        assert working.execute(
            "SELECT lineage_id, baseline_backup_id, minimum_backup_created_at, "
            "manifest_sha256, schema_generation, baseline_db_checkpoint FROM "
            "mobile_recovery_accepted_baselines"
        ).fetchone() == (
            BASELINE_LINEAGE_ID,
            manifest["backup_id"],
            CAPTURED_AT.timestamp(),
            manifest_sha256,
            1,
            manifest["database"]["sha256"],
        )
        assert working.execute(
            "SELECT head_sequence, head_record_hash, head_etag, chain_hmac_key_id "
            "FROM mobile_recovery_fence_checkpoints WHERE checkpoint_id=1"
        ).fetchone() == (
            len(chain.records),
            chain.records[-1].record_hash,
            chain.head_etag,
            "chain-key",
        )
    finally:
        retained.close()
        working.close()
    assert ledger.calls == 1


def test_service_restore_requires_the_remote_genesis_journal_before_auth_reset(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    bundle, manifest, _manifest_sha256 = _create_service_baseline_bundle(
        tmp_path, synthetic_sessions
    )
    snapshot_db = sqlite3.connect(bundle / DATABASE_BUNDLE_PATH)
    try:
        snapshot_db.execute(
            "UPDATE mobile_recovery_baseline_journals SET operation_id=?",
            ("99999999-aaaa-4bbb-8ccc-dddddddddddd",),
        )
        snapshot_db.commit()
        manifest["mobile_state"] = mobile_state_summary(snapshot_db)
    finally:
        snapshot_db.close()
    database_bytes = (bundle / DATABASE_BUNDLE_PATH).read_bytes()
    database_sha256 = hashlib.sha256(database_bytes).hexdigest()
    manifest["database"]["sha256"] = database_sha256
    manifest["database"]["size"] = len(database_bytes)
    manifest["recovery_fence"]["baseline_db_checkpoint"] = database_sha256
    _rewrite_completion(bundle, manifest)
    manifest_sha256 = hashlib.sha256((bundle / MANIFEST_FILE).read_bytes()).hexdigest()
    chain = _baseline_chain_snapshot(
        manifest,
        manifest_sha256,
        include_token_revoke=False,
    )
    scratch = tmp_path / "wrong-genesis-journal-scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="genesis journal|baseline journal"):
        module.prepare_service_restore(
            bundle,
            scratch,
            ledger=_StaticValidatedLedger(chain),
            keyring=_service_keyring(),
            now=CAPTURED_AT.timestamp() + 100,
        )

    working = sqlite3.connect(next(scratch.glob("service-working-*")) / "swinglab.db")
    try:
        assert working.execute(
            "SELECT password_hash, auth_epoch FROM users WHERE id='user-synthetic'"
        ).fetchone() == ("synthetic-hash", 0)
        assert working.execute(
            "SELECT COUNT(*) FROM mobile_restore_credential_reset_markers"
        ).fetchone()[0] == 0
    finally:
        working.close()


def test_service_restore_rejects_generation_zero_before_working_copy_migration(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, _ = synthetic_sessions
    bundle, _ = _create_bundle(tmp_path, sessions)
    scratch = tmp_path / "generation-zero-service-scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="generation-0|cutover"):
        module.prepare_service_restore(
            bundle,
            scratch,
            ledger=object(),
            keyring=_service_keyring(),
            now=CAPTURED_AT.timestamp(),
        )

    assert not list(scratch.glob("service-working-*"))


def test_service_restore_requires_manifest_live_and_chain_hmac_keys_before_reset(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    bundle, manifest, manifest_sha256 = _create_service_baseline_bundle(
        tmp_path, synthetic_sessions
    )
    scratch = tmp_path / "missing-key-service-scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="HMAC key|key ID"):
        module.prepare_service_restore(
            bundle,
            scratch,
            ledger=_StaticValidatedLedger(
                _baseline_chain_snapshot(manifest, manifest_sha256)
            ),
            keyring=_service_keyring(include_old=False),
            now=CAPTURED_AT.timestamp() + 100,
        )

    working_dirs = list(scratch.glob("service-working-*"))
    assert len(working_dirs) == 1
    connection = sqlite3.connect(working_dirs[0] / "swinglab.db")
    try:
        assert connection.execute(
            "SELECT password_hash, auth_epoch FROM users WHERE id='user-synthetic'"
        ).fetchone() == ("synthetic-hash", 0)
        assert connection.execute(
            "SELECT COUNT(*) FROM mobile_restore_credential_reset_markers"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_service_restore_requires_an_explicit_owner_for_reserved_chain_kinds(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    bundle, manifest, manifest_sha256 = _create_service_baseline_bundle(
        tmp_path, synthetic_sessions
    )
    scratch = tmp_path / "reserved-kind-service-scratch"
    scratch.mkdir()
    snapshot = _baseline_chain_snapshot(
        manifest,
        manifest_sha256,
        include_reserved=True,
    )

    with pytest.raises(BackupError, match="reconciler|owned"):
        module.prepare_service_restore(
            bundle,
            scratch,
            ledger=_StaticValidatedLedger(snapshot),
            keyring=_service_keyring(),
            now=CAPTURED_AT.timestamp() + 100,
        )

    working_dir = next(scratch.glob("service-working-*"))
    connection = sqlite3.connect(working_dir / "swinglab.db")
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM mobile_restore_credential_reset_markers"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_baseline_backup_verifier_uses_exact_immutable_transport_readback_and_retry(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    _insert_baseline_journal(connection)
    connection.commit()
    bundle_root = tmp_path / "baseline-verifier-bundles"
    readback_root = tmp_path / "baseline-verifier-readbacks"
    bundle_root.mkdir()
    readback_root.mkdir()
    fake = _RecordingS3()
    verifier = module.ImmutableBundleBaselineBackupVerifier(
        sessions_dir=sessions,
        bundle_root=bundle_root,
        readback_root=readback_root,
        backup_settings=_settings(),
        restore_settings=_settings(),
        backup_client=fake,
        restore_client=fake,
        now_factory=lambda: CAPTURED_AT,
    )

    first = verifier.create_and_verify(lineage_id=BASELINE_LINEAGE_ID)
    recreated = module.ImmutableBundleBaselineBackupVerifier(
        sessions_dir=sessions,
        bundle_root=bundle_root,
        readback_root=readback_root,
        backup_settings=_settings(),
        restore_settings=_settings(),
        backup_client=fake,
        restore_client=fake,
        now_factory=lambda: CAPTURED_AT,
    )
    second = recreated.create_and_verify(lineage_id=BASELINE_LINEAGE_ID)

    assert first == second
    local_bundle = bundle_root / f"cutover-baseline-{BASELINE_LINEAGE_ID}"
    manifest = json.loads((local_bundle / MANIFEST_FILE).read_text())
    assert first.backup_id == manifest["backup_id"]
    assert first.backup_created_at == CAPTURED_AT.timestamp()
    assert first.schema_generation == 1
    assert first.manifest_sha256 == hashlib.sha256(
        (local_bundle / MANIFEST_FILE).read_bytes()
    ).hexdigest()
    assert first.manifest_database_sha256 == manifest["database"]["sha256"]
    assert first.baseline_db_checkpoint == manifest["database"]["sha256"]
    complete_key = f"{_settings().object_prefix(first.backup_id)}/{COMPLETE_FILE}"
    assert complete_key in fake.objects
    assert len(list(readback_root.glob(f"readback-{first.backup_id}-*"))) == 2


def test_baseline_backup_verifier_resumes_after_a_partial_claimed_upload(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    _insert_baseline_journal(connection)
    connection.commit()
    bundle_root = tmp_path / "resumable-baseline-bundles"
    readback_root = tmp_path / "resumable-baseline-readbacks"
    bundle_root.mkdir()
    readback_root.mkdir()
    fake = _RecordingS3(failure="synthetic interrupted upload")

    def verifier():
        return module.ImmutableBundleBaselineBackupVerifier(
            sessions_dir=sessions,
            bundle_root=bundle_root,
            readback_root=readback_root,
            backup_settings=_settings(),
            restore_settings=_settings(),
            backup_client=fake,
            restore_client=fake,
            now_factory=lambda: CAPTURED_AT,
        )

    with pytest.raises(BackupError, match="upload failed|upload"):
        verifier().create_and_verify(lineage_id=BASELINE_LINEAGE_ID)
    fake.failure = None

    facts = verifier().create_and_verify(lineage_id=BASELINE_LINEAGE_ID)

    complete_key = f"{_settings().object_prefix(facts.backup_id)}/{COMPLETE_FILE}"
    assert complete_key in fake.objects
    claim_key = f"{_settings().object_prefix(facts.backup_id)}/CLAIM.json"
    assert sum(call["key"] == claim_key for call in fake.put_calls) >= 3


def test_exact_scratch_baseline_verifier_returns_proof_from_service_restore_path(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    _insert_baseline_journal(connection)
    connection.execute(
        "INSERT INTO mobile_rate_limit_events "
        "(domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
        ("auth-start-client-ip", "old-key", "9" * 64, 5.0),
    )
    connection.commit()
    bundle_root = tmp_path / "scratch-verifier-bundles"
    readback_root = tmp_path / "scratch-verifier-readbacks"
    service_scratch = tmp_path / "scratch-verifier-service"
    bundle_root.mkdir()
    readback_root.mkdir()
    service_scratch.mkdir()
    fake = _RecordingS3()
    backup_verifier = module.ImmutableBundleBaselineBackupVerifier(
        sessions_dir=sessions,
        bundle_root=bundle_root,
        readback_root=readback_root,
        backup_settings=_settings(),
        restore_settings=_settings(),
        backup_client=fake,
        restore_client=fake,
        now_factory=lambda: CAPTURED_AT,
    )
    facts = backup_verifier.create_and_verify(lineage_id=BASELINE_LINEAGE_ID)
    local_bundle = bundle_root / f"cutover-baseline-{BASELINE_LINEAGE_ID}"
    manifest = json.loads((local_bundle / MANIFEST_FILE).read_text())
    snapshot = _baseline_chain_snapshot(
        manifest,
        facts.manifest_sha256,
        include_token_revoke=False,
    )
    record = snapshot.records[0]
    scratch_verifier = module.ExactScratchBaselineVerifier(
        readback_root=readback_root,
        scratch_root=service_scratch,
        restore_settings=_settings(),
        restore_client=fake,
        ledger=_StaticValidatedLedger(snapshot),
        keyring=_service_keyring(),
        now_factory=lambda: CAPTURED_AT.timestamp() + 100,
    )

    proof = scratch_verifier.verify_exact(
        lineage_id=BASELINE_LINEAGE_ID,
        facts=facts,
        record=record,
    )

    assert proof.verified is True
    assert proof.lineage_id == BASELINE_LINEAGE_ID
    assert proof.backup_id == facts.backup_id
    assert proof.manifest_sha256 == facts.manifest_sha256
    assert proof.baseline_db_checkpoint == facts.baseline_db_checkpoint
    assert proof.record_hash == record.record_hash
    assert scratch_verifier.last_result is not None
    assert scratch_verifier.last_result.ready is True
    assert scratch_verifier.last_result.head_record_hash == record.record_hash


def test_restore_operator_composition_does_not_load_backup_transport_roles(
    tmp_path, synthetic_sessions, monkeypatch
):
    module = _restore_service_module()
    sessions, _ = synthetic_sessions
    operator_root = tmp_path / "restore-only-operator"
    operator_root.mkdir()
    keyring = _service_keyring()

    monkeypatch.setattr(
        module.VersionedHMAC,
        "from_env",
        classmethod(lambda _cls, *, required: keyring),
    )
    monkeypatch.setattr(
        module.RecoveryFenceStoreSettings,
        "from_env",
        classmethod(lambda _cls: object()),
    )
    monkeypatch.setattr(
        module,
        "RecoveryFenceRemoteStore",
        lambda _settings: object(),
    )

    def reject_backup_role(_cls, *, role):
        raise AssertionError(f"restore-only composition loaded the {role} role")

    monkeypatch.setattr(
        module.S3Settings,
        "from_env",
        classmethod(reject_backup_role),
    )

    composition = module.compose_recovery_fence_operator(
        SimpleNamespace(
            recovery_fence_command="restore-to-service",
            operator_root=operator_root,
            sessions_dir=sessions,
        )
    )

    assert composition.initializer is None
    assert isinstance(composition.service_restorer, module.OfflineServiceRestoreOperator)
    assert not (operator_root / "baseline-bundles").exists()
    assert not (operator_root / "verified-readbacks").exists()


def test_retained_backup_key_usage_audit_is_verified_and_deterministic(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, connection = synthetic_sessions
    ensure_mobile_state_schema(connection)
    connection.execute(
        "INSERT INTO mobile_rate_limit_events "
        "(domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
        ("auth-start-client-ip", "old-key", "a" * 64, 1.0),
    )
    connection.commit()
    first_bundle = tmp_path / "audit-first"
    first_manifest = create_backup(sessions, first_bundle, now=CAPTURED_AT)
    connection.execute(
        "INSERT INTO mobile_rate_limit_events "
        "(domain, key_id, key_digest, occurred_at) VALUES (?, ?, ?, ?)",
        ("auth-start-client-ip", "active-key", "b" * 64, 2.0),
    )
    connection.commit()
    second_bundle = tmp_path / "audit-second"
    second_manifest = create_backup(
        sessions,
        second_bundle,
        now=datetime(2026, 7, 27, 12, 1, tzinfo=timezone.utc),
    )

    audit = module.audit_retained_backup_key_usage(
        [second_bundle, first_bundle]
    )

    assert audit.by_backup_id == (
        (first_manifest["backup_id"], ("old-key",)),
        (second_manifest["backup_id"], ("active-key", "old-key")),
    )
    assert audit.by_key_id == (
        ("active-key", (second_manifest["backup_id"],)),
        (
            "old-key",
            (first_manifest["backup_id"], second_manifest["backup_id"]),
        ),
    )


def _seed_later_service_candidate(
    connection: sqlite3.Connection,
    *,
    checkpoint_hash: str = "2" * 64,
    minimum_created_at: float = CAPTURED_AT.timestamp() - 3600,
) -> tuple[str, str, str]:
    baseline_backup_id = "20260727T110000Z-aaaaaaaaaaaa"
    baseline_manifest_sha256 = "d" * 64
    baseline_db_checkpoint = "e" * 64
    ensure_mobile_state_schema(connection)
    connection.execute(
        "INSERT INTO mobile_recovery_accepted_baselines "
        "(lineage_id, baseline_backup_id, minimum_backup_created_at, "
        "manifest_sha256, schema_generation, baseline_db_checkpoint, accepted_at) "
        "VALUES (?, ?, ?, ?, 1, ?, ?)",
        (
            BASELINE_LINEAGE_ID,
            baseline_backup_id,
            minimum_created_at,
            baseline_manifest_sha256,
            baseline_db_checkpoint,
            minimum_created_at + 1,
        ),
    )
    connection.execute(
        "INSERT INTO mobile_recovery_baseline_journals "
        "(operation_id, phase, request_hash, lineage_id, backup_id, "
        "backup_created_at, schema_generation, manifest_sha256, "
        "baseline_db_checkpoint, record_key, record_hash, head_etag, "
        "chain_hmac_key_id, created_at, updated_at) "
        "VALUES (?, 'accepted', ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "c" * 64,
            BASELINE_LINEAGE_ID,
            baseline_backup_id,
            minimum_created_at,
            baseline_manifest_sha256,
            baseline_db_checkpoint,
            "fence/records/1-" + "2" * 64 + ".json",
            "2" * 64,
            '"genesis-head"',
            "chain-key",
            minimum_created_at,
            minimum_created_at + 1,
        ),
    )
    connection.execute(
        "INSERT INTO mobile_recovery_fence_checkpoints "
        "(checkpoint_id, lineage_id, baseline_backup_id, schema_generation, "
        "head_sequence, head_record_key, head_record_hash, head_etag, "
        "chain_hmac_key_id, verified_at) VALUES (1, ?, ?, 1, 1, ?, ?, ?, ?, ?)",
        (
            BASELINE_LINEAGE_ID,
            baseline_backup_id,
            "fence/records/1-" + checkpoint_hash + ".json",
            checkpoint_hash,
            '"old-head"',
            "chain-key",
            minimum_created_at + 1,
        ),
    )
    connection.commit()
    return baseline_backup_id, baseline_manifest_sha256, baseline_db_checkpoint


@pytest.mark.parametrize(
    "invalid_created_at",
    [str(CAPTURED_AT.timestamp()), float("nan"), float("inf")],
    ids=["numeric-string", "nan", "infinity"],
)
def test_later_candidate_requires_a_finite_numeric_creation_time(
    tmp_path, synthetic_sessions, invalid_created_at
):
    module = _restore_service_module()
    sessions, connection = synthetic_sessions
    baseline_id, baseline_sha, baseline_checkpoint = _seed_later_service_candidate(
        connection
    )
    bundle = tmp_path / "invalid-created-at-candidate"
    manifest = create_backup(sessions, bundle, now=CAPTURED_AT)
    manifest_sha256 = hashlib.sha256((bundle / MANIFEST_FILE).read_bytes()).hexdigest()
    snapshot = _baseline_chain_snapshot(
        manifest,
        manifest_sha256,
        genesis_backup_id=baseline_id,
        genesis_manifest_sha256=baseline_sha,
        genesis_db_checkpoint=baseline_checkpoint,
        minimum_backup_created_at=CAPTURED_AT.timestamp() - 3600,
    )
    manifest["created_at_epoch"] = invalid_created_at
    evidence = module.RetainedRestoreEvidence(
        bundle_dir=bundle,
        restore_dir=bundle,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        complete_sha256="",
        file_sha256=(),
    )

    with pytest.raises(BackupError, match="creation time"):
        module._validate_candidate_lineage(evidence, snapshot)


def test_later_matching_lineage_candidate_advances_a_stale_checkpoint(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, connection = synthetic_sessions
    baseline_id, baseline_sha, baseline_checkpoint = _seed_later_service_candidate(
        connection
    )
    bundle = tmp_path / "later-service-candidate"
    manifest = create_backup(sessions, bundle, now=CAPTURED_AT)
    snapshot = _baseline_chain_snapshot(
        manifest,
        hashlib.sha256((bundle / MANIFEST_FILE).read_bytes()).hexdigest(),
        genesis_backup_id=baseline_id,
        genesis_manifest_sha256=baseline_sha,
        genesis_db_checkpoint=baseline_checkpoint,
        minimum_backup_created_at=CAPTURED_AT.timestamp() - 3600,
    )
    scratch = tmp_path / "later-service-scratch"
    scratch.mkdir()

    result = module.prepare_service_restore(
        bundle,
        scratch,
        ledger=_StaticValidatedLedger(snapshot),
        keyring=_service_keyring(),
        now=CAPTURED_AT.timestamp() + 100,
    )

    prepared = sqlite3.connect(result.working_dir / "swinglab.db")
    try:
        assert prepared.execute(
            "SELECT head_sequence, head_record_hash FROM "
            "mobile_recovery_fence_checkpoints WHERE checkpoint_id=1"
        ).fetchone() == (2, snapshot.records[-1].record_hash)
    finally:
        prepared.close()


def test_later_candidate_rejects_divergent_checkpoint_before_auth_reset(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    sessions, connection = synthetic_sessions
    baseline_id, baseline_sha, baseline_checkpoint = _seed_later_service_candidate(
        connection,
        checkpoint_hash="f" * 64,
    )
    bundle = tmp_path / "divergent-service-candidate"
    manifest = create_backup(sessions, bundle, now=CAPTURED_AT)
    snapshot = _baseline_chain_snapshot(
        manifest,
        hashlib.sha256((bundle / MANIFEST_FILE).read_bytes()).hexdigest(),
        genesis_backup_id=baseline_id,
        genesis_manifest_sha256=baseline_sha,
        genesis_db_checkpoint=baseline_checkpoint,
        minimum_backup_created_at=CAPTURED_AT.timestamp() - 3600,
    )
    scratch = tmp_path / "divergent-service-scratch"
    scratch.mkdir()

    with pytest.raises(BackupError, match="checkpoint|ancestry"):
        module.prepare_service_restore(
            bundle,
            scratch,
            ledger=_StaticValidatedLedger(snapshot),
            keyring=_service_keyring(),
            now=CAPTURED_AT.timestamp() + 100,
        )

    working = sqlite3.connect(next(scratch.glob("service-working-*")) / "swinglab.db")
    try:
        assert working.execute(
            "SELECT password_hash, auth_epoch FROM users WHERE id='user-synthetic'"
        ).fetchone() == ("synthetic-hash", 0)
        assert working.execute(
            "SELECT COUNT(*) FROM mobile_restore_credential_reset_markers"
        ).fetchone()[0] == 0
    finally:
        working.close()


def test_service_restore_executes_an_explicit_reserved_kind_owner(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    bundle, manifest, manifest_sha256 = _create_service_baseline_bundle(
        tmp_path, synthetic_sessions
    )
    snapshot = _baseline_chain_snapshot(
        manifest,
        manifest_sha256,
        include_reserved=True,
    )
    scratch = tmp_path / "owned-reserved-scratch"
    scratch.mkdir()
    calls = []

    def reconcile(connection, record, _keyring):
        calls.append(record.record_hash)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS synthetic_owned_reconciliation (value TEXT)"
        )
        connection.execute(
            "INSERT INTO synthetic_owned_reconciliation VALUES (?)",
            (record.record_hash,),
        )

    result = module.prepare_service_restore(
        bundle,
        scratch,
        ledger=_StaticValidatedLedger(snapshot),
        keyring=_service_keyring(),
        reconcilers={"push_environment_cutoff": reconcile},
        now=CAPTURED_AT.timestamp() + 100,
    )

    assert calls == [snapshot.records[-1].record_hash]
    prepared = sqlite3.connect(result.working_dir / "swinglab.db")
    try:
        assert prepared.execute(
            "SELECT value FROM synthetic_owned_reconciliation"
        ).fetchone() == (snapshot.records[-1].record_hash,)
    finally:
        prepared.close()


def test_service_restore_rejects_a_reconciler_that_reintroduces_credentials(
    tmp_path, synthetic_sessions
):
    module = _restore_service_module()
    bundle, manifest, manifest_sha256 = _create_service_baseline_bundle(
        tmp_path, synthetic_sessions
    )
    snapshot = _baseline_chain_snapshot(
        manifest,
        manifest_sha256,
        include_reserved=True,
    )
    scratch = tmp_path / "credential-reintroduction-scratch"
    scratch.mkdir()

    def unsafe_reconciler(connection, _record, _keyring):
        connection.execute(
            "UPDATE users SET password_hash='resurrected', auth_epoch=0"
        )

    with pytest.raises(BackupError, match="credential reset|credentials"):
        module.prepare_service_restore(
            bundle,
            scratch,
            ledger=_StaticValidatedLedger(snapshot),
            keyring=_service_keyring(),
            reconcilers={"push_environment_cutoff": unsafe_reconciler},
            now=CAPTURED_AT.timestamp() + 100,
        )

    working_dir = next(scratch.glob("service-working-*"))
    assert not (working_dir / "service-restore-ready.json").exists()
