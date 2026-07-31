"""Stage 0B backup/restore tests use synthetic data and local fake clients only."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
from swinglab.web.throttle import _SCHEMA as THROTTLE_SCHEMA
from swinglab.web.users import _SCHEMA as USERS_SCHEMA


CAPTURED_AT = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


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
    restored.close()
    assert (sessions / "swinglab.db").is_file()


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
