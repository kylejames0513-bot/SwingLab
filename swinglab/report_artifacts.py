"""Canonical manifests and attack-resistant loading for guided report bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from .report_view import (
    GUIDED_REPORT_PRESENTATION_VERSION,
    MAX_REPORT_VIEW_BYTES,
    CoachingReportView,
    Entitlement,
    MediaEntry,
    MediaRole,
    OptionalSectionId,
    RenderedEvidence,
    ReportOutcome,
    ReportViewV1,
    ReportViewValidationError,
    UnsupportedReportViewVersion,
    report_view_from_dict,
)


REPORT_MANIFEST_FORMAT = "report-bundle-v1"
REPORT_CHECKSUMS_FORMAT = "report-bundle-checksums-v1"
REPORT_VIEW_FILENAME = "report-view.json"
REPORT_MANIFEST_FILENAME = "report-bundle-manifest.json"
REPORT_CHECKSUMS_FILENAME = "report-bundle-checksums.json"

MAX_REPORT_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_REPORT_CHECKSUMS_BYTES = 2 * 1024 * 1024
MAX_REPORT_ENTITLEMENTS_BYTES = 4 * 1024
MAX_REPORT_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_REPORT_FILENAME = "report.html"
_METRICS_FILENAME = "metrics.json"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ReportArtifactValidationError(ValueError):
    """A report bundle failed canonical, path, schema, or integrity validation."""


@dataclass(frozen=True)
class ReportEntitlementSnapshot:
    coach_replay: Literal["available", "locked", "disabled"]

    def __post_init__(self) -> None:
        if self.coach_replay not in ("available", "locked", "disabled"):
            raise ReportArtifactValidationError("invalid coach replay entitlement")

    def to_json(self) -> str:
        return report_entitlements_to_json(self)

    @classmethod
    def from_json(cls, value: str) -> ReportEntitlementSnapshot:
        return report_entitlements_from_json(value)


@dataclass(frozen=True)
class ManifestArtifact:
    relative_path: str
    kind: Literal["report", "report_view", "metrics", "media"]
    media_key: str | None
    entitlement: Entitlement | None
    required: bool


@dataclass(frozen=True)
class ReportBundleManifest:
    format: Literal["report-bundle-v1"]
    attempt_id: str
    presentation_version: str
    outcome: ReportOutcome
    artifacts: tuple[ManifestArtifact, ...]


@dataclass(frozen=True)
class ChecksumEntry:
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ReportBundleChecksums:
    format: Literal["report-bundle-checksums-v1"]
    manifest_sha256: str
    files: tuple[ChecksumEntry, ...]


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    created_ns: int


@dataclass(frozen=True)
class PublishedReportBundle:
    root: Path
    report_path: Path
    report_view_path: Path
    manifest_path: Path
    checksums_path: Path
    view: ReportViewV1
    manifest: ReportBundleManifest
    checksums: ReportBundleChecksums
    _media_identities: tuple[tuple[str, _FileIdentity], ...] = field(
        default=(), repr=False, compare=False
    )


def _err(message: str) -> None:
    raise ReportArtifactValidationError(message)


def _canonical_json(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ReportArtifactValidationError("value is not canonical JSON") from exc
    return (encoded + "\n").encode("utf-8")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _err(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, label: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except ReportArtifactValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportArtifactValidationError(f"invalid {label} JSON") from exc


def _expect_object(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _err(f"{label} must be an object")
    return value


def _expect_keys(value: dict[str, object], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        _err(f"{label} fields are invalid")


def _expect_nonempty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        _err(f"{label} must be a nonempty string")
    return value


def _safe_relative_path(value: str) -> PurePosixPath:
    """Parse every untrusted path using one platform-independent policy."""
    if not isinstance(value, str):
        _err("report bundle path must be a string")
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
        _err("report bundle contains an unsafe relative path")
    return candidate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _resolved_directory(path: Path, *, label: str) -> Path:
    if _is_link(path):
        _err(f"{label} cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ReportArtifactValidationError(f"{label} does not exist") from exc
    if not resolved.is_dir():
        _err(f"{label} must be a directory")
    return resolved


def _join_under(root: Path, relative: PurePosixPath) -> Path:
    """Join a safe path, rejecting every symlink/junction segment before use."""
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if _is_link(candidate):
            _err("symlinks are not allowed in report bundle paths")
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ReportArtifactValidationError("report bundle path cannot be resolved") from exc
    if not _is_relative_to(resolved, root):
        _err("report bundle path escapes its expected root")
    return resolved


def _identity(info: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(getattr(info, "st_birthtime_ns", info.st_ctime_ns)),
    )


def _regular_file_identity(path: Path) -> _FileIdentity:
    if _is_link(path):
        _err("symlinks are not allowed in report bundle paths")
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReportArtifactValidationError("declared report artifact is missing") from exc
    if not stat.S_ISREG(info.st_mode):
        _err("declared report artifact must be a regular file")
    return _identity(info)


def _hash_declared_file(
    root: Path,
    relative: PurePosixPath,
    *,
    expected_size: int | None = None,
) -> tuple[Path, int, str, _FileIdentity]:
    path = _join_under(root, relative)
    before = _regular_file_identity(path)
    if before.size > MAX_REPORT_ARTIFACT_BYTES:
        _err("declared report artifact exceeds maximum size")
    if expected_size is not None and before.size != expected_size:
        _err("declared report artifact size does not match checksums")

    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            opened_before = _identity(os.fstat(handle.fileno()))
            if opened_before != before:
                _err("declared report artifact changed before it was opened")
            while True:
                chunk = handle.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_REPORT_ARTIFACT_BYTES:
                    _err("declared report artifact exceeds maximum size")
                if expected_size is not None and total > expected_size:
                    _err("declared report artifact exceeds its declared size")
                digest.update(chunk)
            opened_after = _identity(os.fstat(handle.fileno()))
    except ReportArtifactValidationError:
        raise
    except OSError as exc:
        raise ReportArtifactValidationError("declared report artifact cannot be read") from exc

    after = _regular_file_identity(path)
    if before != opened_after or before != after:
        _err("declared report artifact changed while it was read")
    if expected_size is not None and total != expected_size:
        _err("declared report artifact size does not match checksums")
    return path, total, digest.hexdigest(), after


def _read_bounded_path(path: Path, *, limit: int, label: str) -> bytes:
    before = _regular_file_identity(path)
    if before.size > limit:
        _err(f"{label} exceeds maximum size")
    try:
        with path.open("rb") as handle:
            opened_before = _identity(os.fstat(handle.fileno()))
            if opened_before != before:
                _err(f"{label} changed before it was opened")
            raw = handle.read(limit + 1)
            opened_after = _identity(os.fstat(handle.fileno()))
    except ReportArtifactValidationError:
        raise
    except OSError as exc:
        raise ReportArtifactValidationError(f"{label} cannot be read") from exc
    if len(raw) > limit:
        _err(f"{label} exceeds maximum size")
    after = _regular_file_identity(path)
    if before != opened_after or before != after or len(raw) != before.size:
        _err(f"{label} changed while it was read")
    return raw


def _parse_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        _err(f"{label} must be a lowercase SHA-256")
    return value


def report_entitlements_to_json(snapshot: ReportEntitlementSnapshot) -> str:
    if not isinstance(snapshot, ReportEntitlementSnapshot):
        _err("expected ReportEntitlementSnapshot")
    return _canonical_json({"coach_replay": snapshot.coach_replay}).decode("utf-8")


def report_entitlements_from_json(value: str) -> ReportEntitlementSnapshot:
    if not isinstance(value, str):
        _err("report entitlements JSON must be a string")
    raw = value.encode("utf-8")
    if len(raw) > MAX_REPORT_ENTITLEMENTS_BYTES:
        _err("report entitlements JSON exceeds maximum size")
    payload = _expect_object(_decode_json(raw, label="report entitlements"), label="report entitlements")
    _expect_keys(payload, {"coach_replay"}, label="report entitlements")
    snapshot = ReportEntitlementSnapshot(cast(Any, payload["coach_replay"]))
    if raw != _canonical_json({"coach_replay": snapshot.coach_replay}):
        _err("report entitlements JSON is not canonical")
    return snapshot


def report_entitlements_json(snapshot: ReportEntitlementSnapshot) -> str:
    """Compatibility spelling for the persisted entitlement serializer."""
    return report_entitlements_to_json(snapshot)


def _artifact_to_dict(artifact: ManifestArtifact) -> dict[str, object]:
    relative = _safe_relative_path(artifact.relative_path).as_posix()
    if artifact.kind not in ("report", "report_view", "metrics", "media"):
        _err("invalid manifest artifact kind")
    if not isinstance(artifact.required, bool):
        _err("manifest artifact required must be boolean")
    if artifact.kind == "media":
        media_key = _expect_nonempty_string(artifact.media_key, label="media key")
        if not isinstance(artifact.entitlement, Entitlement):
            try:
                entitlement = Entitlement(artifact.entitlement)
            except (TypeError, ValueError) as exc:
                raise ReportArtifactValidationError("invalid manifest entitlement") from exc
        else:
            entitlement = artifact.entitlement
        entitlement_value: str | None = entitlement.value
    else:
        if artifact.media_key is not None or artifact.entitlement is not None:
            _err("non-media manifest artifacts cannot carry media fields")
        media_key = None
        entitlement_value = None
    return {
        "relative_path": relative,
        "kind": artifact.kind,
        "media_key": media_key,
        "entitlement": entitlement_value,
        "required": artifact.required,
    }


def report_manifest_to_dict(manifest: ReportBundleManifest) -> dict[str, object]:
    if not isinstance(manifest, ReportBundleManifest):
        _err("expected ReportBundleManifest")
    if manifest.format != REPORT_MANIFEST_FORMAT:
        _err("unsupported report manifest format")
    attempt_id = _expect_nonempty_string(manifest.attempt_id, label="attempt_id")
    presentation = _expect_nonempty_string(
        manifest.presentation_version, label="presentation_version"
    )
    try:
        outcome = ReportOutcome(manifest.outcome)
    except (TypeError, ValueError) as exc:
        raise ReportArtifactValidationError("invalid report outcome") from exc
    rows = [_artifact_to_dict(row) for row in manifest.artifacts]
    _validate_unique_paths([str(row["relative_path"]) for row in rows], label="manifest artifact")
    rows.sort(key=lambda row: str(row["relative_path"]))
    return {
        "format": REPORT_MANIFEST_FORMAT,
        "attempt_id": attempt_id,
        "presentation_version": presentation,
        "outcome": outcome.value,
        "artifacts": rows,
    }


def report_manifest_from_dict(payload: object) -> ReportBundleManifest:
    data = _expect_object(payload, label="report manifest")
    _expect_keys(
        data,
        {"format", "attempt_id", "presentation_version", "outcome", "artifacts"},
        label="report manifest",
    )
    if data["format"] != REPORT_MANIFEST_FORMAT:
        _err("unsupported report manifest format")
    attempt_id = _expect_nonempty_string(data["attempt_id"], label="attempt_id")
    presentation = _expect_nonempty_string(
        data["presentation_version"], label="presentation_version"
    )
    try:
        outcome = ReportOutcome(data["outcome"])
    except (TypeError, ValueError) as exc:
        raise ReportArtifactValidationError("invalid report outcome") from exc
    raw_rows = data["artifacts"]
    if not isinstance(raw_rows, list):
        _err("manifest artifacts must be an array")
    rows: list[ManifestArtifact] = []
    for raw_row in raw_rows:
        row = _expect_object(raw_row, label="manifest artifact")
        _expect_keys(
            row,
            {"relative_path", "kind", "media_key", "entitlement", "required"},
            label="manifest artifact",
        )
        relative_path = _safe_relative_path(
            _expect_nonempty_string(row["relative_path"], label="relative_path")
        ).as_posix()
        kind = row["kind"]
        if kind not in ("report", "report_view", "metrics", "media"):
            _err("invalid manifest artifact kind")
        required = row["required"]
        if not isinstance(required, bool):
            _err("manifest artifact required must be boolean")
        if kind == "media":
            media_key = _expect_nonempty_string(row["media_key"], label="media key")
            try:
                entitlement = Entitlement(row["entitlement"])
            except (TypeError, ValueError) as exc:
                raise ReportArtifactValidationError("invalid manifest entitlement") from exc
        else:
            if row["media_key"] is not None or row["entitlement"] is not None:
                _err("non-media manifest artifacts cannot carry media fields")
            media_key = None
            entitlement = None
        rows.append(
            ManifestArtifact(
                relative_path,
                cast(Literal["report", "report_view", "metrics", "media"], kind),
                media_key,
                entitlement,
                required,
            )
        )
    paths = [row.relative_path for row in rows]
    _validate_unique_paths(paths, label="manifest artifact")
    if paths != sorted(paths):
        _err("manifest artifacts are not canonically ordered")
    return ReportBundleManifest(
        REPORT_MANIFEST_FORMAT,
        attempt_id,
        presentation,
        outcome,
        tuple(rows),
    )


def _checksum_to_dict(entry: ChecksumEntry) -> dict[str, object]:
    relative_path = _safe_relative_path(entry.relative_path).as_posix()
    if isinstance(entry.size_bytes, bool) or not isinstance(entry.size_bytes, int):
        _err("checksum size must be an integer")
    if entry.size_bytes < 0 or entry.size_bytes > MAX_REPORT_ARTIFACT_BYTES:
        _err("checksum size is outside the supported range")
    sha256 = _parse_sha256(entry.sha256, label="checksum sha256")
    return {
        "relative_path": relative_path,
        "size_bytes": entry.size_bytes,
        "sha256": sha256,
    }


def report_checksums_to_dict(checksums: ReportBundleChecksums) -> dict[str, object]:
    if not isinstance(checksums, ReportBundleChecksums):
        _err("expected ReportBundleChecksums")
    if checksums.format != REPORT_CHECKSUMS_FORMAT:
        _err("unsupported report checksums format")
    manifest_sha256 = _parse_sha256(
        checksums.manifest_sha256, label="manifest_sha256"
    )
    rows = [_checksum_to_dict(row) for row in checksums.files]
    _validate_unique_paths([str(row["relative_path"]) for row in rows], label="checksum")
    rows.sort(key=lambda row: str(row["relative_path"]))
    return {
        "format": REPORT_CHECKSUMS_FORMAT,
        "manifest_sha256": manifest_sha256,
        "files": rows,
    }


def report_checksums_from_dict(payload: object) -> ReportBundleChecksums:
    data = _expect_object(payload, label="report checksums")
    _expect_keys(data, {"format", "manifest_sha256", "files"}, label="report checksums")
    if data["format"] != REPORT_CHECKSUMS_FORMAT:
        _err("unsupported report checksums format")
    manifest_sha256 = _parse_sha256(data["manifest_sha256"], label="manifest_sha256")
    raw_rows = data["files"]
    if not isinstance(raw_rows, list):
        _err("checksum files must be an array")
    rows: list[ChecksumEntry] = []
    for raw_row in raw_rows:
        row = _expect_object(raw_row, label="checksum entry")
        _expect_keys(row, {"relative_path", "size_bytes", "sha256"}, label="checksum entry")
        relative_path = _safe_relative_path(
            _expect_nonempty_string(row["relative_path"], label="relative_path")
        ).as_posix()
        size = row["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int):
            _err("checksum size must be an integer")
        if size < 0 or size > MAX_REPORT_ARTIFACT_BYTES:
            _err("checksum size is outside the supported range")
        rows.append(
            ChecksumEntry(
                relative_path,
                size,
                _parse_sha256(row["sha256"], label="checksum sha256"),
            )
        )
    paths = [row.relative_path for row in rows]
    _validate_unique_paths(paths, label="checksum")
    if paths != sorted(paths):
        _err("checksum entries are not canonically ordered")
    return ReportBundleChecksums(
        REPORT_CHECKSUMS_FORMAT,
        manifest_sha256,
        tuple(rows),
    )


def _validate_unique_paths(paths: list[str], *, label: str) -> None:
    if len(paths) != len(set(paths)):
        _err(f"duplicate {label} path")
    folded = [path.casefold() for path in paths]
    if len(folded) != len(set(folded)):
        _err(f"case-colliding {label} path")


def write_report_manifest(path: Path, manifest: ReportBundleManifest) -> Path:
    path.write_bytes(_canonical_json(report_manifest_to_dict(manifest)))
    return path


def write_report_checksums(path: Path, checksums: ReportBundleChecksums) -> Path:
    path.write_bytes(_canonical_json(report_checksums_to_dict(checksums)))
    return path


def load_report_manifest(path: Path) -> ReportBundleManifest:
    raw = _read_bounded_path(path, limit=MAX_REPORT_MANIFEST_BYTES, label="report manifest")
    payload = _decode_json(raw, label="report manifest")
    manifest = report_manifest_from_dict(payload)
    if raw != _canonical_json(report_manifest_to_dict(manifest)):
        _err("report manifest is not canonical JSON")
    return manifest


def load_report_checksums(path: Path) -> ReportBundleChecksums:
    raw = _read_bounded_path(path, limit=MAX_REPORT_CHECKSUMS_BYTES, label="report checksums")
    payload = _decode_json(raw, label="report checksums")
    checksums = report_checksums_from_dict(payload)
    if raw != _canonical_json(report_checksums_to_dict(checksums)):
        _err("report checksums are not canonical JSON")
    return checksums


def _filesystem_files(root: Path) -> set[str]:
    files: set[str] = set()
    try:
        for current, directories, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            for directory in tuple(directories):
                child = current_path / directory
                if _is_link(child):
                    _err("symlinks are not allowed in report bundles")
            for name in names:
                child = current_path / name
                if _is_link(child):
                    _err("symlinks are not allowed in report bundles")
                relative = child.relative_to(root).as_posix()
                _safe_relative_path(relative)
                _regular_file_identity(child)
                files.add(relative)
    except ReportArtifactValidationError:
        raise
    except OSError as exc:
        raise ReportArtifactValidationError("report bundle cannot be enumerated") from exc
    return files


def _load_view_from_validated_file(path: Path) -> ReportViewV1:
    raw = _read_bounded_path(path, limit=MAX_REPORT_VIEW_BYTES, label="report view")
    payload = _decode_json(raw, label="report view")
    try:
        return report_view_from_dict(payload)
    except UnsupportedReportViewVersion:
        raise
    except ReportViewValidationError as exc:
        raise ReportArtifactValidationError("report view failed schema validation") from exc


def _single_kind(manifest: ReportBundleManifest, kind: str) -> ManifestArtifact:
    rows = tuple(row for row in manifest.artifacts if row.kind == kind)
    if len(rows) != 1:
        _err(f"manifest must declare exactly one {kind} artifact")
    row = rows[0]
    if not row.required:
        _err(f"manifest {kind} artifact must be required")
    return row


def _validate_manifest_relationships(manifest: ReportBundleManifest) -> None:
    report = _single_kind(manifest, "report")
    report_view = _single_kind(manifest, "report_view")
    metrics = _single_kind(manifest, "metrics")
    if report.relative_path != _REPORT_FILENAME:
        _err("manifest report artifact must use the canonical filename")
    if report_view.relative_path != REPORT_VIEW_FILENAME:
        _err("manifest report view artifact must use the canonical filename")
    if metrics.relative_path != _METRICS_FILENAME:
        _err("manifest metrics artifact must use the canonical filename")
    reserved = {REPORT_MANIFEST_FILENAME, REPORT_CHECKSUMS_FILENAME}
    if any(row.relative_path in reserved for row in manifest.artifacts):
        _err("manifest cannot declare manifest or checksum files as artifacts")
    media_keys = [
        cast(str, row.media_key) for row in manifest.artifacts if row.kind == "media"
    ]
    if len(media_keys) != len(set(media_keys)):
        _err("duplicate manifest media key")


def _optional_replay_locked(view: ReportViewV1) -> bool:
    replay = tuple(
        section
        for section in view.optional_sections
        if section.id == OptionalSectionId.REPLAY
    )
    if len(replay) > 1:
        _err("duplicate replay optional section")
    return bool(replay and (replay[0].locked or not replay[0].available))


def _validate_media_relationships(
    view: ReportViewV1,
    manifest: ReportBundleManifest,
    checksum_by_path: dict[str, ChecksumEntry],
) -> None:
    view_keys = [entry.key for entry in view.media]
    if len(view_keys) != len(set(view_keys)):
        _err("duplicate report view media key")
    view_paths = [entry.relative_path for entry in view.media]
    _validate_unique_paths(view_paths, label="report view media")

    manifest_media = tuple(row for row in manifest.artifacts if row.kind == "media")
    by_key: dict[str, ManifestArtifact] = {}
    for row in manifest_media:
        assert row.media_key is not None
        if row.media_key in by_key:
            _err("duplicate manifest media key")
        by_key[row.media_key] = row
    if set(view_keys) != set(by_key):
        _err("manifest and report view media keys differ")

    for media in view.media:
        _safe_relative_path(media.relative_path)
        artifact = by_key[media.key]
        if artifact.relative_path != media.relative_path:
            _err("manifest and report view media paths differ")
        if artifact.entitlement != media.entitlement:
            _err("manifest and report view media entitlements differ")
        checksum = checksum_by_path.get(media.relative_path)
        if checksum is None or checksum.sha256 != media.checksum_sha256:
            _err("report view media checksum differs from checksum artifact")

    focused = tuple(entry for entry in view.media if entry.role == MediaRole.PRIORITY_EVIDENCE)
    if isinstance(view, CoachingReportView) and isinstance(view.visual_evidence, RenderedEvidence):
        if len(focused) != 1 or focused[0].key != view.visual_evidence.media_key:
            _err("rendered coaching requires exactly one focused evidence file")
        focused_manifest = by_key[focused[0].key]
        if (
            focused[0].entitlement != Entitlement.CORE
            or focused_manifest.entitlement != Entitlement.CORE
            or not focused_manifest.required
            or not view.capabilities.focused_evidence
        ):
            _err("focused evidence must be required core media")
    elif focused or view.capabilities.focused_evidence:
        _err("unrendered report cannot declare focused evidence media")

    replay_media = tuple(entry for entry in view.media if entry.role == MediaRole.COACH_REPLAY)
    if replay_media and (
        not view.capabilities.coach_replay or _optional_replay_locked(view)
    ):
        _err("locked or unavailable coach replay cannot be declared as a file")


def validate_staged_bundle(
    staging_dir: Path,
    *,
    manifest_rel: str,
    checksums_rel: str,
) -> tuple[ReportBundleManifest, ReportBundleChecksums, ReportViewV1]:
    root = _resolved_directory(staging_dir, label="report bundle root")
    manifest_relative = _safe_relative_path(manifest_rel)
    checksums_relative = _safe_relative_path(checksums_rel)
    if manifest_relative.as_posix() != REPORT_MANIFEST_FILENAME:
        _err("report manifest must use its canonical bundle path")
    if checksums_relative.as_posix() != REPORT_CHECKSUMS_FILENAME:
        _err("report checksums must use their canonical bundle path")

    manifest_path = _join_under(root, manifest_relative)
    checksums_path = _join_under(root, checksums_relative)
    manifest = load_report_manifest(manifest_path)
    checksums = load_report_checksums(checksums_path)
    _validate_manifest_relationships(manifest)
    if manifest.presentation_version != GUIDED_REPORT_PRESENTATION_VERSION:
        _err("unsupported report presentation version")

    declared_paths = {row.relative_path for row in manifest.artifacts}
    checksum_paths = {row.relative_path for row in checksums.files}
    expected_checksum_paths = declared_paths | {REPORT_MANIFEST_FILENAME}
    if checksum_paths != expected_checksum_paths:
        _err("checksums must cover the manifest and every declared artifact exactly once")
    if REPORT_CHECKSUMS_FILENAME in checksum_paths:
        _err("checksum file cannot include itself")

    expected_files = expected_checksum_paths | {REPORT_CHECKSUMS_FILENAME}
    actual_files = _filesystem_files(root)
    if actual_files != expected_files:
        _err("report bundle contains undeclared or missing files")

    checksum_by_path = {row.relative_path: row for row in checksums.files}
    identities: dict[str, _FileIdentity] = {}
    digests: dict[str, str] = {}
    for relative_path in sorted(expected_checksum_paths):
        checksum = checksum_by_path[relative_path]
        _, _, digest, identity = _hash_declared_file(
            root,
            _safe_relative_path(relative_path),
            expected_size=checksum.size_bytes,
        )
        if digest != checksum.sha256:
            _err("declared report artifact hash does not match checksums")
        identities[relative_path] = identity
        digests[relative_path] = digest

    manifest_digest = digests[REPORT_MANIFEST_FILENAME]
    if checksums.manifest_sha256 != manifest_digest:
        _err("manifest_sha256 does not match the report manifest")

    view_path = _join_under(root, _safe_relative_path(REPORT_VIEW_FILENAME))
    view = _load_view_from_validated_file(view_path)
    if view.presentation_version != manifest.presentation_version:
        _err("manifest and report view presentation versions differ")
    if view.outcome != manifest.outcome:
        _err("manifest and report view outcomes differ")
    _validate_media_relationships(view, manifest, checksum_by_path)
    return manifest, checksums, view


def load_published_bundle(
    session_dir: Path,
    *,
    report_rel: str,
    report_view_rel: str,
    manifest_rel: str,
    checksums_rel: str,
) -> PublishedReportBundle:
    session_root = _resolved_directory(session_dir, label="session root")
    relative_values = {
        "report": _safe_relative_path(report_rel),
        "report_view": _safe_relative_path(report_view_rel),
        "manifest": _safe_relative_path(manifest_rel),
        "checksums": _safe_relative_path(checksums_rel),
    }
    paths = {
        name: _join_under(session_root, relative)
        for name, relative in relative_values.items()
    }
    roots = {path.parent for path in paths.values()}
    if len(roots) != 1:
        _err("published report paths must refer to one bundle root")
    root = next(iter(roots))
    if root == session_root:
        _err("published report must live in a dedicated bundle root")
    if paths["report_view"].name != REPORT_VIEW_FILENAME:
        _err("published report view path is not canonical")
    if paths["manifest"].name != REPORT_MANIFEST_FILENAME:
        _err("published manifest path is not canonical")
    if paths["checksums"].name != REPORT_CHECKSUMS_FILENAME:
        _err("published checksums path is not canonical")

    manifest, checksums, view = validate_staged_bundle(
        root,
        manifest_rel=REPORT_MANIFEST_FILENAME,
        checksums_rel=REPORT_CHECKSUMS_FILENAME,
    )
    report_artifact = _single_kind(manifest, "report")
    view_artifact = _single_kind(manifest, "report_view")
    canonical_report = _join_under(root, _safe_relative_path(report_artifact.relative_path))
    canonical_view = _join_under(root, _safe_relative_path(view_artifact.relative_path))
    if paths["report"] != canonical_report or paths["report_view"] != canonical_view:
        _err("published paths do not match their manifest-declared identities")
    if paths["manifest"] != root / REPORT_MANIFEST_FILENAME:
        _err("published manifest path does not match its canonical identity")
    if paths["checksums"] != root / REPORT_CHECKSUMS_FILENAME:
        _err("published checksums path does not match its canonical identity")

    checksums_by_path = {entry.relative_path: entry for entry in checksums.files}
    media_identities: list[tuple[str, _FileIdentity]] = []
    for media in view.media:
        checksum = checksums_by_path[media.relative_path]
        _, _, digest, identity = _hash_declared_file(
            root,
            _safe_relative_path(media.relative_path),
            expected_size=checksum.size_bytes,
        )
        if digest != checksum.sha256 or digest != media.checksum_sha256:
            _err("published media changed during bundle loading")
        media_identities.append((media.key, identity))

    return PublishedReportBundle(
        root,
        paths["report"],
        paths["report_view"],
        paths["manifest"],
        paths["checksums"],
        view,
        manifest,
        checksums,
        tuple(media_identities),
    )


def resolve_media_path(bundle: PublishedReportBundle, media_key: str) -> Path:
    """Resolve an opaque media key and revalidate its identity and content."""
    if not isinstance(bundle, PublishedReportBundle):
        _err("expected PublishedReportBundle")
    key = _expect_nonempty_string(media_key, label="media key")
    view_rows = tuple(entry for entry in bundle.view.media if entry.key == key)
    if len(view_rows) != 1:
        _err("media key is unknown or duplicated")
    media: MediaEntry = view_rows[0]

    manifest_rows = tuple(
        row for row in bundle.manifest.artifacts if row.media_key == key
    )
    if len(manifest_rows) != 1 or manifest_rows[0].kind != "media":
        _err("media key does not identify one manifest media artifact")
    artifact = manifest_rows[0]
    if (
        artifact.relative_path != media.relative_path
        or artifact.entitlement != media.entitlement
    ):
        _err("media metadata no longer matches the validated manifest")

    checksum_rows = tuple(
        row for row in bundle.checksums.files if row.relative_path == media.relative_path
    )
    if len(checksum_rows) != 1:
        _err("media path does not identify one checksum entry")
    checksum = checksum_rows[0]
    expected_identities = tuple(
        identity for stored_key, identity in bundle._media_identities if stored_key == key
    )
    if len(expected_identities) != 1:
        _err("media key does not have one validated file identity")

    root = _resolved_directory(bundle.root, label="published report bundle root")
    path, _, digest, identity = _hash_declared_file(
        root,
        _safe_relative_path(media.relative_path),
        expected_size=checksum.size_bytes,
    )
    if identity != expected_identities[0]:
        _err("published media file identity changed after bundle loading")
    if digest != checksum.sha256 or digest != media.checksum_sha256:
        _err("published media hash changed after bundle loading")
    return path
