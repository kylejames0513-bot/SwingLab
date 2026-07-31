"""Optional private S3-compatible transport for complete backup bundles."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .core import (
    COMPLETE_FILE,
    DATABASE_BUNDLE_PATH,
    FORMAT,
    MANIFEST_FILE,
    BackupError,
    _canonical_json,
    _load_json,
    _join_under,
    _safe_relative_path,
    _sha256_file,
    load_and_verify_manifest,
    validate_backup_id,
    verify_bundle_files,
)

MAX_COMPLETE_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ARTIFACTS = 100_000
MAX_BUNDLE_BYTES = 2 * 1024**4
STREAM_CHUNK_BYTES = 1024 * 1024
UPLOAD_CLAIM_FILE = "CLAIM.json"
UPLOAD_CLAIM_FORMAT = "caddieinsight-upload-claim/v1"


class _ConditionalConflict(RuntimeError):
    """The provider explicitly rejected a write because the key exists."""


@dataclass(frozen=True)
class S3Settings:
    bucket: str
    prefix: str
    region: str
    endpoint_url: str | None = field(repr=False)
    addressing_style: str
    sse: str
    kms_key_id: str | None = field(repr=False)
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, *, role: str) -> "S3Settings":
        if role not in {"backup", "restore"}:
            raise BackupError("Invalid object-storage credential role.")
        credential_prefix = (
            "CADDIE_BACKUP" if role == "backup" else "CADDIE_RESTORE"
        )

        def required(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise BackupError(f"Required object-storage setting {name} is missing.")
            return value

        endpoint = os.environ.get("CADDIE_BACKUP_ENDPOINT_URL", "").strip() or None
        if endpoint:
            parsed = urlparse(endpoint)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise BackupError(
                    "The S3-compatible endpoint must be an absolute HTTPS URL "
                    "without credentials, query parameters, or fragments."
                )
        prefix = required("CADDIE_BACKUP_PREFIX").strip("/")
        if not prefix or any(part in ("", ".", "..") for part in prefix.split("/")):
            raise BackupError("The object-storage prefix is invalid.")
        addressing = os.environ.get(
            "CADDIE_BACKUP_ADDRESSING_STYLE", "auto"
        ).strip()
        if addressing not in {"auto", "path", "virtual"}:
            raise BackupError("Unsupported S3 addressing style.")
        sse = required("CADDIE_BACKUP_SSE")
        if sse not in {"AES256", "aws:kms", "provider-managed"}:
            raise BackupError("Unsupported server-side encryption mode.")
        kms_key = os.environ.get("CADDIE_BACKUP_KMS_KEY_ID", "").strip() or None
        if sse == "aws:kms" and not kms_key:
            raise BackupError("KMS encryption requires CADDIE_BACKUP_KMS_KEY_ID.")
        if sse == "aws:kms" and kms_key and not _kms_key_is_comparable(kms_key):
            raise BackupError(
                "CADDIE_BACKUP_KMS_KEY_ID must be a stable key UUID or key ARN; "
                "KMS aliases cannot be verified after S3 resolves them."
            )
        if sse != "aws:kms" and kms_key:
            raise BackupError("A KMS key may be set only with aws:kms encryption.")
        return cls(
            bucket=required("CADDIE_BACKUP_BUCKET"),
            prefix=prefix,
            region=required("CADDIE_BACKUP_REGION"),
            endpoint_url=endpoint,
            addressing_style=addressing,
            sse=sse,
            kms_key_id=kms_key,
            access_key_id=required(f"{credential_prefix}_ACCESS_KEY_ID"),
            secret_access_key=required(f"{credential_prefix}_SECRET_ACCESS_KEY"),
            session_token=os.environ.get(
                f"{credential_prefix}_SESSION_TOKEN", ""
            ).strip()
            or None,
        )

    def object_prefix(self, backup_id: str) -> str:
        return f"{self.prefix}/{validate_backup_id(backup_id)}"

    def upload_args(self, sha256: str, content_type: str) -> dict[str, Any]:
        args: dict[str, Any] = {
            "Metadata": {"sha256": sha256},
            "ContentType": content_type,
        }
        if self.sse in {"AES256", "aws:kms"}:
            args["ServerSideEncryption"] = self.sse
        if self.kms_key_id:
            args["SSEKMSKeyId"] = self.kms_key_id
        return args


def _client(settings: S3Settings):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise BackupError(
            'S3 transport is optional; install it with pip install "swinglab[backup]".'
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        region_name=settings.region,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        aws_session_token=settings.session_token,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.addressing_style},
            retries={"mode": "standard", "max_attempts": 5},
        ),
    )


def _data_objects(manifest: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Map database/artifact logical paths to opaque remote keys."""
    files = [(DATABASE_BUNDLE_PATH, "objects/00000000", "application/octet-stream")]
    files.extend(
        (
            f"artifacts/{item['path']}",
            f"objects/{index:08d}",
            "application/octet-stream",
        )
        for index, item in enumerate(manifest["artifacts"]["files"], start=1)
    )
    return files


def _upload_objects(manifest: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        *_data_objects(manifest),
        (MANIFEST_FILE, MANIFEST_FILE, "application/json"),
    ]


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    code = str(error.get("Code", "")) if isinstance(error, dict) else ""
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404


def _precondition_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    code = str(error.get("Code", "")) if isinstance(error, dict) else ""
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"412", "PreconditionFailed"} or status == 412


def _head_object(
    s3,
    settings: S3Settings,
    key: str,
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    try:
        return s3.head_object(Bucket=settings.bucket, Key=key)
    except Exception as exc:
        if allow_missing and _not_found(exc):
            return None
        raise BackupError(
            "S3-compatible metadata check failed; provider details were "
            "suppressed to prevent credential or signed-URL disclosure."
        ) from None


def _verify_remote_object(
    head: dict[str, Any],
    *,
    expected_size: int,
    expected_sha256: str,
    settings: S3Settings,
) -> None:
    metadata = head.get("Metadata")
    if (
        head.get("ContentLength") != expected_size
        or not isinstance(metadata, dict)
        or metadata.get("sha256") != expected_sha256
    ):
        raise BackupError("Remote object size or checksum metadata did not match.")
    if settings.sse in {"AES256", "aws:kms"} and head.get(
        "ServerSideEncryption"
    ) != settings.sse:
        raise BackupError("Remote object did not confirm the requested encryption.")
    if (
        settings.sse == "aws:kms"
        and settings.kms_key_id
        and _kms_key_is_comparable(settings.kms_key_id)
        and not _kms_key_matches(
            settings.kms_key_id,
            str(head.get("SSEKMSKeyId", "")),
        )
    ):
        raise BackupError("Remote object did not confirm the requested KMS key.")


def _kms_key_is_comparable(configured: str) -> bool:
    """Return whether a provider response can identify this configured key."""
    return bool(
        re.fullmatch(r"[0-9a-fA-F-]{36}", configured)
        or re.fullmatch(r"arn:[^:]+:kms:[^:]+:[^:]+:key/.+", configured)
    )


def _kms_key_matches(configured: str, returned: str) -> bool:
    if configured == returned:
        return True
    return bool(
        re.fullmatch(r"[0-9a-fA-F-]{36}", configured)
        and returned.endswith(f"/{configured}")
    )


def _verified_metadata_sha256(
    head: dict[str, Any],
    *,
    expected_size: int,
    settings: S3Settings,
) -> str:
    """Validate metadata-object headers and return their asserted digest."""
    metadata = head.get("Metadata")
    sha256 = metadata.get("sha256") if isinstance(metadata, dict) else None
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise BackupError("Remote metadata object has no valid checksum metadata.")
    _verify_remote_object(
        head,
        expected_size=expected_size,
        expected_sha256=sha256,
        settings=settings,
    )
    return sha256


def _put_if_absent(
    s3,
    settings: S3Settings,
    key: str,
    body: bytes,
    *,
    content_type: str,
) -> None:
    sha256 = hashlib.sha256(body).hexdigest()
    try:
        s3.put_object(
            Bucket=settings.bucket,
            Key=key,
            Body=body,
            ContentLength=len(body),
            IfNoneMatch="*",
            **settings.upload_args(sha256, content_type),
        )
    except Exception as exc:
        if _precondition_failed(exc):
            raise _ConditionalConflict from None
        raise BackupError(
            "The provider failed or does not support the required conditional "
            "object write; no backup was completed."
        ) from None


def _acquire_upload_claim(
    s3,
    settings: S3Settings,
    object_prefix: str,
    backup_id: str,
) -> None:
    claim_key = f"{object_prefix}/{UPLOAD_CLAIM_FILE}"
    claim_body = _canonical_json(
        {
            "format": UPLOAD_CLAIM_FORMAT,
            "backup_id": backup_id,
            "nonce": uuid.uuid4().hex,
        }
    )
    probe_body = _canonical_json(
        {
            "format": UPLOAD_CLAIM_FORMAT,
            "backup_id": backup_id,
            "nonce": uuid.uuid4().hex,
        }
    )
    try:
        _put_if_absent(
            s3,
            settings,
            claim_key,
            claim_body,
            content_type="application/json",
        )
    except _ConditionalConflict:
        raise BackupError(
            "This backup identifier is already claimed by another writer."
        ) from None

    # Prove that the provider enforces If-None-Match before any backup body is
    # uploaded. A silent success would overwrite the claim and fails closed.
    try:
        _put_if_absent(
            s3,
            settings,
            claim_key,
            probe_body,
            content_type="application/json",
        )
    except _ConditionalConflict:
        pass
    else:
        raise BackupError(
            "The provider did not enforce the required conditional write; "
            "the claimed backup identifier cannot be used."
        )

    claim_sha256 = hashlib.sha256(claim_body).hexdigest()
    claim_head = _head_object(s3, settings, claim_key)
    assert claim_head is not None
    _verify_remote_object(
        claim_head,
        expected_size=len(claim_body),
        expected_sha256=claim_sha256,
        settings=settings,
    )


def _expected_upload_files(
    bundle_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, tuple[int, str]]:
    expected: dict[str, tuple[int, str]] = {
        DATABASE_BUNDLE_PATH: (
            int(manifest["database"]["size"]),
            str(manifest["database"]["sha256"]),
        )
    }
    expected.update(
        {
            f"artifacts/{item['path']}": (
                int(item["size"]),
                str(item["sha256"]),
            )
            for item in manifest["artifacts"]["files"]
        }
    )

    manifest_path = _join_under(bundle_dir, _safe_relative_path(MANIFEST_FILE))
    if _load_json(manifest_path) != manifest:
        raise BackupError("The local manifest changed after bundle verification.")
    manifest_sha256, manifest_size = _sha256_file(manifest_path)
    complete_path = _join_under(bundle_dir, _safe_relative_path(COMPLETE_FILE))
    complete = _load_json(complete_path)
    if complete.get("manifest_sha256") != manifest_sha256:
        raise BackupError("The local completion marker no longer matches its manifest.")
    complete_sha256, complete_size = _sha256_file(complete_path)
    expected[MANIFEST_FILE] = (manifest_size, manifest_sha256)
    expected[COMPLETE_FILE] = (complete_size, complete_sha256)
    return expected


def _object_pin(head: dict[str, Any]) -> tuple[dict[str, str], str, str]:
    version_id = head.get("VersionId")
    if isinstance(version_id, str) and version_id and version_id != "null":
        return {"VersionId": version_id}, "VersionId", version_id

    etag = head.get("ETag")
    if (
        isinstance(etag, str)
        and 2 < len(etag) <= 1024
        and etag.startswith('"')
        and etag.endswith('"')
        and "\r" not in etag
        and "\n" not in etag
    ):
        return {"IfMatch": etag}, "ETag", etag
    raise BackupError(
        "The provider supplied neither a usable object version nor immutable ETag."
    )


def _get_pinned_object(
    s3,
    settings: S3Settings,
    key: str,
    head: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    request_pin, response_field, expected_pin = _object_pin(head)
    try:
        response = s3.get_object(
            Bucket=settings.bucket,
            Key=key,
            **request_pin,
        )
    except Exception as exc:
        if _precondition_failed(exc):
            raise BackupError(
                "A remote object changed between inspection and download."
            ) from None
        raise BackupError(
            "S3-compatible download failed; provider details were suppressed "
            "to prevent credential or signed-URL disclosure."
        ) from None
    if not isinstance(response, dict) or response.get(response_field) != expected_pin:
        body = response.get("Body") if isinstance(response, dict) else None
        if body is not None and hasattr(body, "close"):
            body.close()
        raise BackupError("The provider did not honor the inspected object pin.")
    head_etag = head.get("ETag")
    if isinstance(head_etag, str) and response.get("ETag") != head_etag:
        body = response.get("Body")
        if body is not None and hasattr(body, "close"):
            body.close()
        raise BackupError("The remote object identity changed during download.")
    return response, response.get("Body")


def _stream_pinned_object(
    s3,
    settings: S3Settings,
    key: str,
    destination: Path,
    head: dict[str, Any],
    *,
    expected_size: int,
    expected_sha256: str,
    hard_limit: int,
) -> None:
    if expected_size < 0 or expected_size > hard_limit:
        raise BackupError("The remote object exceeds its enforced byte limit.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise BackupError("A download destination unexpectedly already exists.")
    partial = destination.with_name(
        f".{destination.name}.part-{uuid.uuid4().hex}"
    )
    response: dict[str, Any] | None = None
    body = None
    try:
        response, body = _get_pinned_object(s3, settings, key, head)
        if body is None or not hasattr(body, "read"):
            raise BackupError("The provider returned no readable object stream.")
        _verify_remote_object(
            response,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            settings=settings,
        )

        digest = hashlib.sha256()
        written = 0
        with partial.open("xb") as handle:
            while True:
                chunk = body.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise BackupError("The provider returned an invalid object stream.")
                chunk_size = len(chunk)
                if (
                    written + chunk_size > expected_size
                    or written + chunk_size > hard_limit
                ):
                    raise BackupError(
                        "The remote object stream exceeded its declared byte limit."
                    )
                handle.write(chunk)
                digest.update(chunk)
                written += chunk_size
            handle.flush()
            os.fsync(handle.fileno())
        if written != expected_size or digest.hexdigest() != expected_sha256:
            raise BackupError("The downloaded object size or checksum did not match.")
        partial.replace(destination)
    except BackupError:
        raise
    except Exception:
        raise BackupError(
            "S3-compatible streaming download failed; provider details were "
            "suppressed to prevent credential or signed-URL disclosure."
        ) from None
    finally:
        if body is not None and hasattr(body, "close"):
            try:
                body.close()
            except Exception:
                pass
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                raise BackupError("Partial object cleanup failed.") from None


def upload_bundle(
    bundle_dir: Path,
    settings: S3Settings,
    *,
    client=None,
) -> str:
    bundle_dir = bundle_dir.expanduser().resolve()
    manifest = load_and_verify_manifest(bundle_dir)
    verify_bundle_files(bundle_dir, manifest)
    expected = _expected_upload_files(bundle_dir, manifest)
    backup_id = manifest["backup_id"]
    object_prefix = settings.object_prefix(backup_id)
    s3 = client or _client(settings)
    complete_key = f"{object_prefix}/{COMPLETE_FILE}"
    if _head_object(s3, settings, complete_key, allow_missing=True) is not None:
        raise BackupError(
            "This immutable backup identifier is already complete; refusing "
            "to overwrite any remote object."
        )
    _acquire_upload_claim(s3, settings, object_prefix, backup_id)
    try:
        for local_relative, remote_relative, content_type in _upload_objects(manifest):
            path = _join_under(bundle_dir, _safe_relative_path(local_relative))
            sha256, size = _sha256_file(path)
            expected_size, expected_sha256 = expected[local_relative]
            if (size, sha256) != (expected_size, expected_sha256):
                raise BackupError(
                    "A local backup file changed after bundle verification."
                )
            key = f"{object_prefix}/{remote_relative}"
            s3.upload_file(
                str(path),
                settings.bucket,
                key,
                ExtraArgs=settings.upload_args(sha256, content_type),
            )
            uploaded_sha256, uploaded_size = _sha256_file(path)
            if (uploaded_size, uploaded_sha256) != (
                expected_size,
                expected_sha256,
            ):
                raise BackupError(
                    "A local backup file changed while it was being uploaded."
                )
            head = _head_object(s3, settings, key)
            assert head is not None
            _verify_remote_object(
                head,
                expected_size=size,
                expected_sha256=sha256,
                settings=settings,
            )

        # COMPLETE.json is captured into memory, conditionally written, and
        # verified last. A partial or racing prefix is never restorable.
        complete_path = _join_under(
            bundle_dir,
            _safe_relative_path(COMPLETE_FILE),
        )
        complete_body = complete_path.read_bytes()
        complete_size, complete_sha256 = expected[COMPLETE_FILE]
        if (
            len(complete_body) != complete_size
            or hashlib.sha256(complete_body).hexdigest() != complete_sha256
        ):
            raise BackupError(
                "The local completion marker changed after bundle verification."
            )
        try:
            _put_if_absent(
                s3,
                settings,
                complete_key,
                complete_body,
                content_type="application/json",
            )
        except _ConditionalConflict:
            raise BackupError(
                "The remote completion marker already exists; refusing overwrite."
            ) from None
        complete_head = _head_object(s3, settings, complete_key)
        assert complete_head is not None
        _verify_remote_object(
            complete_head,
            expected_size=complete_size,
            expected_sha256=complete_sha256,
            settings=settings,
        )
    except BackupError:
        raise
    except Exception:
        raise BackupError(
            "S3-compatible upload failed; provider details were suppressed "
            "to prevent credential or signed-URL disclosure."
        ) from None
    return backup_id


def download_bundle(
    backup_id: str,
    output_dir: Path,
    settings: S3Settings,
    *,
    client=None,
) -> dict[str, Any]:
    backup_id = validate_backup_id(backup_id)
    output_dir = output_dir.expanduser().resolve()
    data_root = Path("/data").resolve()
    try:
        output_dir.relative_to(data_root)
        in_data = True
    except ValueError:
        in_data = output_dir == data_root
    if in_data:
        raise BackupError("Download output is never allowed in or below /data.")
    if output_dir.exists():
        raise BackupError("The download output directory must not already exist.")
    if not output_dir.parent.is_dir():
        raise BackupError("The download output parent directory must already exist.")
    prefix = settings.object_prefix(backup_id)
    s3 = client or _client(settings)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.partial-",
            dir=output_dir.parent,
        )
    )
    try:
        os.chmod(staging, 0o700)
        complete_key = f"{prefix}/{COMPLETE_FILE}"
        complete_head = _head_object(s3, settings, complete_key)
        assert complete_head is not None
        complete_size = complete_head.get("ContentLength")
        if (
            not isinstance(complete_size, int)
            or complete_size <= 0
            or complete_size > MAX_COMPLETE_BYTES
        ):
            raise BackupError("Remote completion metadata exceeds its safe size limit.")
        complete_sha256 = _verified_metadata_sha256(
            complete_head,
            expected_size=complete_size,
            settings=settings,
        )
        _stream_pinned_object(
            s3,
            settings,
            complete_key,
            staging / COMPLETE_FILE,
            complete_head,
            expected_size=complete_size,
            expected_sha256=complete_sha256,
            hard_limit=MAX_COMPLETE_BYTES,
        )

        manifest_key = f"{prefix}/{MANIFEST_FILE}"
        manifest_head = _head_object(s3, settings, manifest_key)
        assert manifest_head is not None
        manifest_size = manifest_head.get("ContentLength")
        if (
            not isinstance(manifest_size, int)
            or manifest_size <= 0
            or manifest_size > MAX_MANIFEST_BYTES
        ):
            raise BackupError("Remote manifest exceeds its safe size limit.")
        manifest_metadata_sha256 = _verified_metadata_sha256(
            manifest_head,
            expected_size=manifest_size,
            settings=settings,
        )
        _stream_pinned_object(
            s3,
            settings,
            manifest_key,
            staging / MANIFEST_FILE,
            manifest_head,
            expected_size=manifest_size,
            expected_sha256=manifest_metadata_sha256,
            hard_limit=MAX_MANIFEST_BYTES,
        )
        complete = _load_json(staging / COMPLETE_FILE)
        manifest_sha256, _ = _sha256_file(staging / MANIFEST_FILE)
        if (
            complete.get("backup_id") != backup_id
            or complete.get("manifest_sha256") != manifest_sha256
        ):
            raise BackupError("Remote completion metadata did not validate.")
        manifest = load_and_verify_manifest_metadata_only(staging)
        total_bytes = int(manifest["database"]["size"]) + int(
            manifest["artifacts"]["bytes"]
        )
        if total_bytes > MAX_BUNDLE_BYTES:
            raise BackupError("Remote backup exceeds the configured safety limit.")
        free_bytes = shutil.disk_usage(output_dir.parent).free
        required_bytes = total_bytes + max(64 * 1024 * 1024, total_bytes // 20)
        if free_bytes < required_bytes:
            raise BackupError("Insufficient free space for the verified download.")

        expected = {
            DATABASE_BUNDLE_PATH: (
                int(manifest["database"]["size"]),
                str(manifest["database"]["sha256"]),
            )
        }
        expected.update(
            {
                f"artifacts/{item['path']}": (
                    int(item["size"]),
                    str(item["sha256"]),
                )
                for item in manifest["artifacts"]["files"]
            }
        )
        for local_relative, remote_relative, _ in _data_objects(manifest):
            key = f"{prefix}/{remote_relative}"
            head = _head_object(s3, settings, key)
            assert head is not None
            size, sha256 = expected[local_relative]
            _verify_remote_object(
                head,
                expected_size=size,
                expected_sha256=sha256,
                settings=settings,
            )
            _stream_pinned_object(
                s3,
                settings,
                key,
                _join_under(staging, _safe_relative_path(local_relative)),
                head,
                expected_size=size,
                expected_sha256=sha256,
                hard_limit=size,
            )
        verify_bundle_files(staging, manifest)
        staging.replace(output_dir)
        return manifest
    except Exception:
        try:
            shutil.rmtree(staging)
        except FileNotFoundError:
            pass
        except OSError:
            raise BackupError("Partial download cleanup failed.") from None
        if staging.exists():
            raise BackupError("Partial download cleanup failed.")
        raise


def load_and_verify_manifest_metadata_only(bundle_dir: Path) -> dict[str, Any]:
    """Validate remote metadata before the referenced bodies have downloaded."""
    manifest = _load_json(bundle_dir / MANIFEST_FILE)
    complete = _load_json(bundle_dir / COMPLETE_FILE)
    if manifest.get("format") != FORMAT or complete.get("format") != FORMAT:
        raise BackupError("Remote backup format metadata does not match.")
    backup_id = validate_backup_id(str(manifest.get("backup_id", "")))
    if complete.get("backup_id") != backup_id:
        raise BackupError("Remote backup identifier metadata does not match.")

    # Validate only bounded structural metadata here. The database and
    # artifacts are downloaded next, then the strict bundle verifier hashes
    # their actual bodies before the staging directory can be published.
    database = manifest.get("database")
    artifacts = manifest.get("artifacts")
    if not isinstance(database, dict) or not isinstance(artifacts, dict):
        raise BackupError("Remote manifest is missing required sections.")
    if (
        _safe_relative_path(str(database.get("path", ""))).as_posix()
        != DATABASE_BUNDLE_PATH
        or not isinstance(database.get("size"), int)
        or database["size"] < 0
        or not re.fullmatch(r"[0-9a-f]{64}", str(database.get("sha256", "")))
    ):
        raise BackupError("Remote database metadata is invalid.")
    files = artifacts.get("files")
    if not isinstance(files, list):
        raise BackupError("Remote artifact list is invalid.")
    if len(files) > MAX_ARTIFACTS:
        raise BackupError("Remote artifact count exceeds the safety limit.")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise BackupError("Remote artifact entry is invalid.")
        path = _safe_relative_path(str(item.get("path", ""))).as_posix()
        if path in seen:
            raise BackupError("Remote manifest contains duplicate artifact paths.")
        seen.add(path)
        if (
            not isinstance(item.get("size"), int)
            or item["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
        ):
            raise BackupError("Remote artifact metadata is invalid.")
    if artifacts.get("count") != len(files) or artifacts.get("bytes") != sum(
        item["size"] for item in files
    ):
        raise BackupError("Remote artifact summary is invalid.")
    return manifest
