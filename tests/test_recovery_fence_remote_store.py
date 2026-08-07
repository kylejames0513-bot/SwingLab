from __future__ import annotations

import hashlib
import io
import threading
import tomllib
from pathlib import Path

import pytest

from swinglab.backups.core import BackupError
from swinglab.backups import store as store_module


def _api():
    settings_type = getattr(store_module, "RecoveryFenceStoreSettings", None)
    remote_type = getattr(store_module, "RecoveryFenceRemoteStore", None)
    conflict_type = getattr(store_module, "RecoveryFenceCASConflict", None)
    error_type = getattr(store_module, "RecoveryFenceStoreError", None)
    assert settings_type is not None, "RecoveryFenceStoreSettings is missing"
    assert remote_type is not None, "RecoveryFenceRemoteStore is missing"
    assert conflict_type is not None, "RecoveryFenceCASConflict is missing"
    assert error_type is not None, "RecoveryFenceStoreError is missing"
    return settings_type, remote_type, conflict_type, error_type


class _FakeS3:
    class Missing(Exception):
        response = {
            "Error": {"Code": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }

    class Precondition(Exception):
        response = {
            "Error": {"Code": "PreconditionFailed"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        }

    def __init__(self):
        self.objects: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str, str | None, str | None]] = []
        self.lock = threading.RLock()
        self.fail_with: str | None = None
        self.mutate_before_get: dict[str, bytes] = {}
        self.ignore_conditions = False
        self.unsupported_conditions = False

    @staticmethod
    def _etag(body: bytes) -> str:
        return f'"{hashlib.sha256(body).hexdigest()}"'

    def _store(self, key: str, body: bytes, kwargs: dict) -> dict[str, object]:
        result = {
            "body": body,
            "head": {
                "ContentLength": len(body),
                "Metadata": dict(kwargs.get("Metadata") or {}),
                "ContentType": kwargs.get("ContentType"),
                "ServerSideEncryption": kwargs.get("ServerSideEncryption"),
                "SSEKMSKeyId": kwargs.get("SSEKMSKeyId"),
                "ETag": self._etag(body),
            },
        }
        self.objects[key] = result
        return dict(result["head"])

    def put_object(self, **kwargs):
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        key = kwargs["Key"]
        body = kwargs["Body"]
        if hasattr(body, "read"):
            body = body.read()
        body = bytes(body)
        with self.lock:
            self.calls.append(
                (
                    "put",
                    key,
                    kwargs.get("IfNoneMatch"),
                    kwargs.get("IfMatch"),
                )
            )
            existing = self.objects.get(key)
            if self.unsupported_conditions and (
                kwargs.get("IfNoneMatch") is not None
                or kwargs.get("IfMatch") is not None
            ):
                raise RuntimeError("synthetic unsupported condition")
            if (
                not self.ignore_conditions
                and kwargs.get("IfNoneMatch") == "*"
                and existing is not None
            ):
                raise self.Precondition()
            if not self.ignore_conditions and kwargs.get("IfMatch") is not None:
                if (
                    existing is None
                    or existing["head"]["ETag"] != kwargs["IfMatch"]
                ):
                    raise self.Precondition()
            return self._store(key, body, kwargs)

    def head_object(self, *, Bucket, Key):
        del Bucket
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        with self.lock:
            self.calls.append(("head", Key, None, None))
            try:
                head = self.objects[Key]["head"]
            except KeyError:
                raise self.Missing() from None
            return {**head, "Metadata": dict(head.get("Metadata") or {})}

    def get_object(self, **kwargs):
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        key = kwargs["Key"]
        with self.lock:
            self.calls.append(("get", key, None, kwargs.get("IfMatch")))
            try:
                current = self.objects[key]
            except KeyError:
                raise self.Missing() from None
            if key in self.mutate_before_get:
                replacement = self.mutate_before_get.pop(key)
                self._store(
                    key,
                    replacement,
                    {"Metadata": {"sha256": hashlib.sha256(replacement).hexdigest()}},
                )
                current = self.objects[key]
            head = current["head"]
            if kwargs.get("IfMatch") != head["ETag"]:
                raise self.Precondition()
            return {
                **head,
                "Metadata": dict(head.get("Metadata") or {}),
                "Body": io.BytesIO(current["body"]),
            }


def _settings(secret: str = "synthetic-recovery-secret"):
    settings_type, *_ = _api()
    return settings_type(
        bucket="synthetic-recovery-bucket",
        prefix="caddieinsight/recovery-fence",
        region="synthetic-region",
        endpoint_url="https://objects.example.invalid",
        addressing_style="path",
        sse="AES256",
        kms_key_id=None,
        access_key_id="synthetic-recovery-access",
        secret_access_key=secret,
    )


def test_settings_use_only_dedicated_recovery_environment(monkeypatch):
    settings_type, *_ = _api()
    backup_only = {
        "CADDIE_BACKUP_BUCKET": "must-not-be-used",
        "CADDIE_BACKUP_PREFIX": "must/not/be/used",
        "CADDIE_BACKUP_REGION": "must-not-be-used",
        "CADDIE_BACKUP_SSE": "AES256",
        "CADDIE_BACKUP_ACCESS_KEY_ID": "must-not-be-used",
        "CADDIE_BACKUP_SECRET_ACCESS_KEY": "must-not-be-used",
    }
    for name in tuple(backup_only) + (
        "CADDIE_RECOVERY_FENCE_BUCKET",
        "CADDIE_RECOVERY_FENCE_PREFIX",
        "CADDIE_RECOVERY_FENCE_REGION",
        "CADDIE_RECOVERY_FENCE_SSE",
        "CADDIE_RECOVERY_FENCE_ACCESS_KEY_ID",
        "CADDIE_RECOVERY_FENCE_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in backup_only.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(BackupError, match="CADDIE_RECOVERY_FENCE_BUCKET"):
        settings_type.from_env()

    dedicated = {
        "CADDIE_RECOVERY_FENCE_BUCKET": "recovery-only",
        "CADDIE_RECOVERY_FENCE_PREFIX": "fences/production",
        "CADDIE_RECOVERY_FENCE_REGION": "synthetic-region",
        "CADDIE_RECOVERY_FENCE_SSE": "AES256",
        "CADDIE_RECOVERY_FENCE_ACCESS_KEY_ID": "recovery-access",
        "CADDIE_RECOVERY_FENCE_SECRET_ACCESS_KEY": "recovery-secret",
    }
    for name, value in dedicated.items():
        monkeypatch.setenv(name, value)
    loaded = settings_type.from_env()
    assert loaded.bucket == "recovery-only"
    assert loaded.prefix == "fences/production"
    assert "recovery-secret" not in repr(loaded)


def test_settings_reject_backup_prefix_overlap_in_same_bucket(monkeypatch):
    settings_type, *_ = _api()
    values = {
        "CADDIE_RECOVERY_FENCE_BUCKET": "shared-private-bucket",
        "CADDIE_RECOVERY_FENCE_PREFIX": "caddieinsight/recovery",
        "CADDIE_RECOVERY_FENCE_REGION": "synthetic-region",
        "CADDIE_RECOVERY_FENCE_SSE": "AES256",
        "CADDIE_RECOVERY_FENCE_ACCESS_KEY_ID": "recovery-access",
        "CADDIE_RECOVERY_FENCE_SECRET_ACCESS_KEY": "recovery-secret",
        "CADDIE_BACKUP_BUCKET": "shared-private-bucket",
        "CADDIE_BACKUP_PREFIX": "caddieinsight",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(BackupError, match="overlap|dedicated"):
        settings_type.from_env()


def test_settings_limit_keys_to_head_and_content_addressed_records():
    settings = _settings()
    digest = "a" * 64

    assert settings.head_key == "caddieinsight/recovery-fence/HEAD"
    key = settings.record_key(7, digest)
    assert key == (
        "caddieinsight/recovery-fence/records/7-" + digest + ".json"
    )
    assert settings.validate_record_key(key) == (7, digest)
    for invalid in (
        "caddieinsight/recovery-fence/records/07-" + digest + ".json",
        "caddieinsight/recovery-fence/records/7-" + "A" * 64 + ".json",
        "caddieinsight/recovery-fence/other",
        "caddieinsight/backups/HEAD",
    ):
        with pytest.raises(BackupError):
            settings.validate_record_key(invalid)


def test_remote_store_immutably_puts_and_pinned_reads_back_record():
    _, remote_type, *_ = _api()
    settings = _settings()
    fake = _FakeS3()
    remote = remote_type(settings, client=fake)
    body = b'{"record_hash":"' + b"a" * 64 + b'"}\n'
    key = settings.record_key(1, "a" * 64)

    stored = remote.put_immutable_record(key, body)

    assert stored.body == body
    assert stored.key == key
    assert fake.calls[0] == ("put", key, "*", None)
    assert fake.calls[-1][0:2] == ("get", key)
    assert fake.calls[-1][3] == stored.etag
    assert not hasattr(remote, "list")
    assert not hasattr(remote, "delete")


def test_immutable_exact_retry_succeeds_but_different_body_fails_closed():
    _, remote_type, _, error_type = _api()
    settings = _settings()
    fake = _FakeS3()
    remote = remote_type(settings, client=fake)
    key = settings.record_key(3, "b" * 64)
    body = b'{"record_hash":"' + b"b" * 64 + b'"}\n'

    first = remote.put_immutable_record(key, body)
    second = remote.put_immutable_record(key, body)
    assert second.body == first.body
    with pytest.raises(error_type, match="immutable record"):
        remote.put_immutable_record(key, body.replace(b"b", b"c", 1))


def test_head_compare_and_swap_uses_expected_etag_and_reads_back():
    _, remote_type, conflict_type, _ = _api()
    settings = _settings()
    fake = _FakeS3()
    remote = remote_type(settings, client=fake)
    first_body = b'{"record_hash":"' + b"1" * 64 + b'","sequence":1}\n'
    second_body = b'{"record_hash":"' + b"2" * 64 + b'","sequence":2}\n'

    first = remote.compare_and_swap_head(first_body, expected_etag=None)
    second = remote.compare_and_swap_head(second_body, expected_etag=first.etag)
    assert second.body == second_body
    assert ("put", settings.head_key, "*", None) in fake.calls
    assert ("put", settings.head_key, None, first.etag) in fake.calls

    with pytest.raises(conflict_type):
        remote.compare_and_swap_head(first_body, expected_etag=first.etag)
    assert remote.read_head().body == second_body


def test_missing_record_bad_readback_and_provider_error_fail_closed_without_secret():
    _, remote_type, _, error_type = _api()
    sentinel = "SENTINEL-RECOVERY-SECRET"
    settings = _settings(secret=sentinel)
    fake = _FakeS3()
    remote = remote_type(settings, client=fake)
    missing_key = settings.record_key(1, "d" * 64)

    with pytest.raises(error_type, match="missing"):
        remote.read_record(missing_key)

    body = b'{"record_hash":"' + b"e" * 64 + b'"}\n'
    key = settings.record_key(1, "e" * 64)
    fake.mutate_before_get[key] = body + b" "
    with pytest.raises(error_type, match="changed|readback"):
        remote.put_immutable_record(key, body)

    fake.fail_with = f"provider leaked {sentinel}"
    with pytest.raises(error_type) as caught:
        remote.read_head()
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(settings)


def test_store_negatively_proves_immutable_if_none_match_support():
    _, remote_type, _, error_type = _api()
    settings = _settings()
    fake = _FakeS3()
    fake.ignore_conditions = True
    remote = remote_type(settings, client=fake)
    body = b'{"record_hash":"' + b"f" * 64 + b'"}\n'
    key = settings.record_key(1, "f" * 64)

    with pytest.raises(error_type, match="enforce|conditional"):
        remote.put_immutable_record(key, body)


def test_store_negatively_proves_genesis_if_none_match_support():
    _, remote_type, _, error_type = _api()
    fake = _FakeS3()
    fake.ignore_conditions = True
    remote = remote_type(_settings(), client=fake)
    body = b'{"record_hash":"' + b"1" * 64 + b'","sequence":1}\n'

    with pytest.raises(error_type, match="enforce|conditional"):
        remote.compare_and_swap_head(body, expected_etag=None)


def test_store_negatively_proves_stale_if_match_rejection_before_replace():
    _, remote_type, _, error_type = _api()
    settings = _settings()
    fake = _FakeS3()
    remote = remote_type(settings, client=fake)
    first_body = b'{"record_hash":"' + b"1" * 64 + b'","sequence":1}\n'
    second_body = b'{"record_hash":"' + b"2" * 64 + b'","sequence":2}\n'
    first = remote.compare_and_swap_head(first_body, expected_etag=None)
    fake.ignore_conditions = True

    with pytest.raises(error_type, match="If-Match|conditional|enforce"):
        remote.compare_and_swap_head(second_body, expected_etag=first.etag)
    assert fake.objects[settings.head_key]["body"] == first_body


def test_store_fails_closed_when_conditional_headers_are_unsupported():
    _, remote_type, _, error_type = _api()
    settings = _settings()
    fake = _FakeS3()
    fake.unsupported_conditions = True
    remote = remote_type(settings, client=fake)
    body = b'{"record_hash":"' + b"9" * 64 + b'"}\n'

    with pytest.raises(error_type, match="conditional|write"):
        remote.put_immutable_record(settings.record_key(9, "9" * 64), body)


def test_production_web_and_operator_backup_extras_both_include_vetted_boto3():
    metadata = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    extras = metadata["project"]["optional-dependencies"]
    for name in ("web", "backup"):
        assert "boto3>=1.37.32,<2" in extras[name]
