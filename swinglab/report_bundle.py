"""Same-volume staging, publication, and scoped recovery for guided reports."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Protocol, Sequence

from .config import Config
from .evidence import EvidenceSnapshot
from .ffmpeg import VideoInfo
from .focused_evidence import (
    FocusedEvidenceRenderError,
    UnsupportedFocusedEvidence,
    build_unavailable_evidence,
    render_focused_evidence,
    select_focused_evidence,
)
from .report import write_metrics_json
from .report_artifacts import (
    REPORT_CHECKSUMS_FILENAME,
    REPORT_CHECKSUMS_FORMAT,
    REPORT_MANIFEST_FILENAME,
    REPORT_MANIFEST_FORMAT,
    REPORT_VIEW_FILENAME,
    ChecksumEntry,
    ManifestArtifact,
    PublishedReportBundle,
    ReportArtifactValidationError,
    ReportBundleChecksums,
    ReportBundleManifest,
    load_published_bundle,
    load_report_manifest,
    validate_staged_bundle,
    write_report_checksums,
    write_report_manifest,
)
from .report_presenter import (
    ReportDocument,
    ReportPresentationInput,
    ReportSwingSource,
    UnsupportedPriorityEvidence,
    build_report_document,
    measurement_detail,
    prepare_report_input,
    priority_evidence_rule,
)
from .report_view import (
    GUIDED_REPORT_PRESENTATION_VERSION,
    Entitlement,
    MediaEntry,
    MediaRole,
    ReasonCode,
    ReportViewV1,
    write_report_view,
)


ATTEMPT_FORMAT = "report-bundle-attempt-v1"
ATTEMPT_OWNER_FILENAME = ".report-attempt-owner.json"
REPORT_FILENAME = "report.html"
METRICS_FILENAME = "metrics.json"
_ATTEMPT_RE = re.compile(r"\.report-attempt-([0-9a-f]{32})\Z")
_FINAL_RE = re.compile(r"report-bundle-([0-9a-f]{32})\Z")
_ATTEMPT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_OWNER_READ_LIMIT = 4096
_MAX_OWNED_ENTRIES = 4096
_FATAL_CAPTURE_REASONS = frozenset(
    {
        ReasonCode.CAMERA_ANGLE_MISMATCH,
        ReasonCode.TRACKING_UNSTABLE,
        ReasonCode.INSUFFICIENT_POSE_FRAMES,
        ReasonCode.NO_READABLE_SWING,
        ReasonCode.NO_RELIABLE_STRIKE_EVENT,
        ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE,
    }
)


class CoreReportBundleError(RuntimeError):
    """A required bundle build/publication/recovery invariant failed."""


class GuidedReportRendererUnavailable(CoreReportBundleError):
    """No production-composed guided HTML writer reached the boundary."""


class ReportHtmlWriter(Protocol):
    def __call__(
        self,
        out_path: Path,
        document: ReportDocument,
        *,
        cfg: Config,
    ) -> Path: ...


@dataclass(frozen=True)
class ReportBundleAttempt:
    attempt_id: str
    session_dir: Path
    staging_dir: Path
    work_dir: Path
    media_dir: Path


@dataclass(frozen=True)
class StagedReportBundle:
    attempt: ReportBundleAttempt
    document: ReportDocument
    report_path: Path
    report_view_path: Path
    manifest_path: Path
    checksums_path: Path
    view: ReportViewV1
    manifest: ReportBundleManifest
    checksums: ReportBundleChecksums


@dataclass(frozen=True)
class _OwnedEntry:
    path: Path
    mode: int
    device: int
    inode: int


@dataclass(frozen=True)
class _GuidedMedia:
    swing_index: int
    field: str
    path: Path
    relative_path: str
    size: int
    sha256: str


def _is_reparse_info(info: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(int(getattr(info, "st_file_attributes", 0)) & attribute)


def _lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise CoreReportBundleError(f"{label} cannot be inspected") from exc


def _is_reparse(path: Path, info: os.stat_result | None = None) -> bool:
    inspected = info if info is not None else _lstat(path, label="path")
    return stat.S_ISLNK(inspected.st_mode) or _is_reparse_info(inspected)


def _resolved_plain_directory(path: Path, *, label: str) -> Path:
    info = _lstat(path, label=label)
    if _is_reparse(path, info):
        raise CoreReportBundleError(f"{label} cannot be a link or reparse point")
    if not stat.S_ISDIR(info.st_mode):
        raise CoreReportBundleError(f"{label} must be a directory")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CoreReportBundleError(f"{label} cannot be resolved") from exc
    if resolved != path.absolute():
        raise CoreReportBundleError(f"{label} has an ambiguous resolved identity")
    return resolved


def _owner_bytes(attempt_id: str) -> bytes:
    return (
        f'{{"attempt_id":"{attempt_id}","format":"{ATTEMPT_FORMAT}"}}\n'
    ).encode("ascii")


def _validate_attempt_id(attempt_id: str) -> str:
    if not isinstance(attempt_id, str) or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise CoreReportBundleError("report bundle attempt ID must be 32 lowercase hex characters")
    return attempt_id


def _read_exact_owner(path: Path, attempt_id: str) -> None:
    info = _lstat(path, label="report attempt owner marker")
    if _is_reparse(path, info) or not stat.S_ISREG(info.st_mode):
        raise CoreReportBundleError("report attempt owner marker is not a plain file")
    if info.st_size > _OWNER_READ_LIMIT:
        raise CoreReportBundleError("report attempt owner marker exceeds its bound")
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read(_OWNER_READ_LIMIT + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise CoreReportBundleError("report attempt owner marker cannot be read") from exc
    if (
        len(raw) > _OWNER_READ_LIMIT
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or raw != _owner_bytes(attempt_id)
    ):
        raise CoreReportBundleError("report attempt ownership marker is malformed or mismatched")


def _validate_attempt_descriptor(attempt: ReportBundleAttempt) -> Path:
    if not isinstance(attempt, ReportBundleAttempt):
        raise CoreReportBundleError("expected ReportBundleAttempt")
    attempt_id = _validate_attempt_id(attempt.attempt_id)
    session = _resolved_plain_directory(attempt.session_dir, label="session root")
    expected = session / f".report-attempt-{attempt_id}"
    if attempt.staging_dir.absolute() != expected:
        raise CoreReportBundleError("attempt staging directory is not its exact owned direct child")
    if attempt.work_dir.absolute() != expected / "work" or attempt.media_dir.absolute() != expected / "media":
        raise CoreReportBundleError("attempt work/media roots are not canonical")
    return session


def begin_report_bundle(
    session_dir: Path,
    *,
    attempt_id: str | None = None,
) -> ReportBundleAttempt:
    session = _resolved_plain_directory(Path(session_dir), label="session root")
    selected = _validate_attempt_id(attempt_id if attempt_id is not None else uuid.uuid4().hex)
    staging = session / f".report-attempt-{selected}"
    owner = staging / ATTEMPT_OWNER_FILENAME
    try:
        staging.mkdir(exist_ok=False)
        owner.write_bytes(_owner_bytes(selected))
        (staging / "work").mkdir(exist_ok=False)
        (staging / "media").mkdir(exist_ok=False)
    except Exception as exc:
        try:
            if owner.exists() and owner.is_file():
                owner.unlink()
            for child in (staging / "media", staging / "work"):
                if child.exists() and child.is_dir():
                    child.rmdir()
            if staging.exists() and staging.is_dir():
                staging.rmdir()
        except OSError:
            pass
        raise CoreReportBundleError("report bundle attempt could not be initialized") from exc
    return ReportBundleAttempt(selected, session, staging, staging / "work", staging / "media")


def _preflight_owned_tree(root: Path) -> tuple[_OwnedEntry, ...]:
    root_info = _lstat(root, label="owned report tree")
    if _is_reparse(root, root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise CoreReportBundleError("owned report target is a link, reparse point, or non-directory")
    entries: list[_OwnedEntry] = []

    def walk(path: Path, info: os.stat_result) -> None:
        if len(entries) >= _MAX_OWNED_ENTRIES:
            raise CoreReportBundleError("owned report tree exceeds its traversal bound")
        if _is_reparse(path, info):
            raise CoreReportBundleError("owned report tree contains a link or reparse point")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise CoreReportBundleError("owned report tree contains an unsupported entry")
        entries.append(_OwnedEntry(path, info.st_mode, int(info.st_dev), int(info.st_ino)))
        if stat.S_ISDIR(info.st_mode):
            try:
                with os.scandir(path) as scanned:
                    children = sorted(scanned, key=lambda item: item.name)
                    for child in children:
                        child_path = Path(child.path)
                        # On Windows, DirEntry.stat may expose zeroed file IDs
                        # while lstat returns the stable volume/file identity.
                        walk(child_path, os.lstat(child_path))
            except CoreReportBundleError:
                raise
            except OSError as exc:
                raise CoreReportBundleError("owned report tree cannot be enumerated") from exc

    walk(root, root_info)
    return tuple(entries)


def _delete_preflighted_tree(entries: Sequence[_OwnedEntry]) -> None:
    for entry in reversed(entries):
        info = _lstat(entry.path, label="owned report entry")
        if (
            _is_reparse(entry.path, info)
            or (int(info.st_dev), int(info.st_ino), stat.S_IFMT(info.st_mode))
            != (entry.device, entry.inode, stat.S_IFMT(entry.mode))
        ):
            raise CoreReportBundleError("owned report entry changed before deletion")
        try:
            if stat.S_ISDIR(entry.mode):
                entry.path.rmdir()
            else:
                entry.path.unlink()
        except OSError as exc:
            raise CoreReportBundleError("owned report entry could not be deleted") from exc


def _attempt_ownership(staging: Path, attempt_id: str) -> None:
    manifest_path = staging / REPORT_MANIFEST_FILENAME
    owner_path = staging / ATTEMPT_OWNER_FILENAME
    if manifest_path.exists():
        try:
            manifest = load_report_manifest(manifest_path)
        except ReportArtifactValidationError as exc:
            raise CoreReportBundleError("report attempt manifest ownership is malformed") from exc
        if manifest.attempt_id != attempt_id:
            raise CoreReportBundleError("report attempt manifest ownership is mismatched")
        if owner_path.exists():
            _read_exact_owner(owner_path, attempt_id)
        return
    if not owner_path.exists():
        raise CoreReportBundleError("report attempt has no exact ownership proof")
    _read_exact_owner(owner_path, attempt_id)


def discard_report_bundle_attempt(attempt: ReportBundleAttempt) -> None:
    _validate_attempt_descriptor(attempt)
    info = _lstat(attempt.staging_dir, label="report attempt")
    if _is_reparse(attempt.staging_dir, info) or not stat.S_ISDIR(info.st_mode):
        raise CoreReportBundleError("report attempt target is not an owned plain directory")
    _attempt_ownership(attempt.staging_dir, attempt.attempt_id)
    entries = _preflight_owned_tree(attempt.staging_dir)
    _delete_preflighted_tree(entries)


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise CoreReportBundleError("report artifact cannot be hashed") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse_info(before)
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise CoreReportBundleError("report artifact changed while hashing")
    return int(after.st_size), digest.hexdigest()


def _safe_guided_media_path(attempt: ReportBundleAttempt, value: object) -> Path:
    if not isinstance(value, Path):
        raise CoreReportBundleError("guided media inputs must be owned Path values")
    media_root = _resolved_plain_directory(attempt.media_dir, label="attempt media root")
    candidate = value if value.is_absolute() else attempt.staging_dir / value
    try:
        lexical = candidate.absolute()
        lexical.relative_to(media_root)
    except (OSError, ValueError) as exc:
        raise CoreReportBundleError("guided media input is outside the owned attempt media root") from exc
    current = media_root
    for part in lexical.relative_to(media_root).parts:
        current = current / part
        info = _lstat(current, label="guided media input")
        if _is_reparse(current, info):
            raise CoreReportBundleError("guided media input is a link or reparse point")
    info = _lstat(lexical, label="guided media input")
    if not stat.S_ISREG(info.st_mode):
        raise CoreReportBundleError("guided media input must be a regular file")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(media_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CoreReportBundleError("guided media input does not resolve inside its attempt") from exc
    return resolved


def _normalize_guided_media(
    attempt: ReportBundleAttempt,
    swings: Sequence[dict],
) -> tuple[list[dict], tuple[_GuidedMedia, ...]]:
    normalized: list[dict] = []
    records: list[_GuidedMedia] = []
    for index, original in enumerate(swings, start=1):
        swing = dict(original)
        if swing.get("overlay") is not None:
            raise CoreReportBundleError("guided report bundles reject legacy overlay media")
        swing.pop("overlay", None)
        for field in ("strip", "slowmo", "replay"):
            value = swing.get(field)
            if value is None:
                swing.pop(field, None)
                continue
            path = _safe_guided_media_path(attempt, value)
            relative = path.relative_to(attempt.staging_dir).as_posix()
            safe = PurePosixPath(relative)
            if safe.is_absolute() or ".." in safe.parts or "\\" in relative or ":" in relative:
                raise CoreReportBundleError("guided media input has an unsafe canonical path")
            size, digest = _hash_file(path)
            records.append(_GuidedMedia(index, field, path, relative, size, digest))
            swing[field] = relative
        normalized.append(swing)
    return normalized, tuple(records)


def _mime_for(record: _GuidedMedia) -> str:
    suffix = record.path.suffix.lower()
    if record.field == "strip":
        return "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return "video/mp4"


def _coaching_media(records: Sequence[_GuidedMedia]) -> tuple[MediaEntry, ...]:
    rows: list[MediaEntry] = []
    for record in records:
        role, entitlement, prefix = {
            "strip": (MediaRole.KEY_POSITIONS, Entitlement.CORE, "key-positions"),
            "slowmo": (MediaRole.SLOW_MOTION, Entitlement.CORE, "slow-motion"),
            "replay": (MediaRole.COACH_REPLAY, Entitlement.PRO, "coach-replay"),
        }[record.field]
        rows.append(
            MediaEntry(
                f"{prefix}-s{record.swing_index}",
                role,
                _mime_for(record),
                entitlement,
                record.relative_path,
                record.sha256,
            )
        )
    return tuple(rows)


def _capture_media_and_prune(
    attempt: ReportBundleAttempt,
    swings: Sequence[dict],
    records: Sequence[_GuidedMedia],
) -> tuple[list[dict], tuple[MediaEntry, ...], tuple[str, ...]]:
    by_swing = {(record.swing_index, record.field): record for record in records}
    kept: set[Path] = set()
    media: list[MediaEntry] = []
    safe_keys: list[str] = []
    normalized: list[dict] = []
    for index, original in enumerate(swings, start=1):
        swing = {key: value for key, value in original.items() if key not in {"strip", "overlay", "slowmo", "replay"}}
        slow = by_swing.get((index, "slowmo"))
        if slow is not None:
            key = f"capture-playback-s{index}"
            kept.add(slow.path)
            safe_keys.append(key)
            swing["slowmo"] = slow.relative_path
            media.append(
                MediaEntry(
                    key,
                    MediaRole.CAPTURE_PLAYBACK,
                    "video/mp4",
                    Entitlement.CORE,
                    slow.relative_path,
                    slow.sha256,
                )
            )
        normalized.append(swing)

    entries = _preflight_owned_tree(attempt.media_dir)
    files = {entry.path for entry in entries if stat.S_ISREG(entry.mode)}
    if not kept.issubset(files):
        raise CoreReportBundleError("safe capture playback changed before pruning")
    for entry in reversed(entries):
        if entry.path == attempt.media_dir or entry.path in kept:
            continue
        if stat.S_ISDIR(entry.mode) and any(path == entry.path or entry.path in path.parents for path in kept):
            continue
        info = _lstat(entry.path, label="coaching-only media")
        if (
            _is_reparse(entry.path, info)
            or (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
            != (entry.device, entry.inode, stat.S_IFMT(entry.mode))
        ):
            raise CoreReportBundleError("coaching-only media changed before pruning")
        try:
            entry.path.rmdir() if stat.S_ISDIR(entry.mode) else entry.path.unlink()
        except OSError as exc:
            raise CoreReportBundleError("coaching-only media could not be pruned safely") from exc
    return normalized, tuple(media), tuple(safe_keys)


def _capture_swing_sources(
    source: ReportPresentationInput,
    media: Sequence[MediaEntry],
) -> tuple[ReportSwingSource, ...]:
    by_path = {entry.relative_path: entry.key for entry in media}
    return tuple(
        replace(
            swing,
            key_positions_media_key=None,
            key_positions_alt_text=None,
            slow_motion_media_key=(
                by_path.get(next((entry.relative_path for entry in media if entry.key == f"capture-playback-s{index}"), ""))
            ),
            coach_replay_media_key=None,
            coach_replay_caption=None,
            locked_replay_explanation=None,
            video_poster_media_key=None,
            video_poster_alt_text=None,
        )
        for index, swing in enumerate(source.swings, start=1)
    )


def _has_fatal(reasons: Sequence[ReasonCode]) -> bool:
    return any(reason in _FATAL_CAPTURE_REASONS for reason in reasons)


def _append_reason(reasons: Sequence[ReasonCode], reason: ReasonCode) -> tuple[ReasonCode, ...]:
    return tuple(dict.fromkeys((*reasons, reason)))


def _remove_work_and_empty_media(attempt: ReportBundleAttempt) -> None:
    if attempt.work_dir.exists():
        _delete_preflighted_tree(_preflight_owned_tree(attempt.work_dir))
    if attempt.media_dir.exists():
        try:
            next(attempt.media_dir.iterdir())
        except StopIteration:
            attempt.media_dir.rmdir()
        except OSError as exc:
            raise CoreReportBundleError("attempt media root cannot be inspected") from exc


def _manifest_artifacts(view: ReportViewV1) -> tuple[ManifestArtifact, ...]:
    rows = [
        ManifestArtifact(REPORT_FILENAME, "report", None, None, True),
        ManifestArtifact(REPORT_VIEW_FILENAME, "report_view", None, None, True),
        ManifestArtifact(METRICS_FILENAME, "metrics", None, None, True),
    ]
    rows.extend(
        ManifestArtifact(
            media.relative_path,
            "media",
            media.key,
            media.entitlement,
            media.entitlement is Entitlement.CORE,
        )
        for media in view.media
    )
    return tuple(sorted(rows, key=lambda row: row.relative_path))


def _checksum_entries(root: Path, manifest: ReportBundleManifest) -> tuple[ChecksumEntry, ...]:
    paths = [row.relative_path for row in manifest.artifacts]
    paths.append(REPORT_MANIFEST_FILENAME)
    rows = []
    for relative in sorted(paths):
        size, digest = _hash_file(root / Path(*PurePosixPath(relative).parts))
        rows.append(ChecksumEntry(relative, size, digest))
    return tuple(rows)


def build_report_bundle(
    attempt: ReportBundleAttempt,
    *,
    html_writer: ReportHtmlWriter,
    video: VideoInfo,
    swings: list[dict],
    stats: dict,
    session_notes: list[str],
    hand: str,
    cfg: Config,
    angle: str,
    club: str | None,
    level: str | None,
    analysis_fps: float | None,
    replay_locked: bool,
    evidence_snapshots: Sequence[EvidenceSnapshot],
    reason_codes: Sequence[ReasonCode],
) -> StagedReportBundle:
    """Build and strictly validate one complete unpublished directory."""
    _validate_attempt_descriptor(attempt)
    _attempt_ownership(attempt.staging_dir, attempt.attempt_id)
    if html_writer is None or not callable(html_writer):
        try:
            discard_report_bundle_attempt(attempt)
        except CoreReportBundleError:
            pass
        raise GuidedReportRendererUnavailable("guided report HTML writer is unavailable")

    try:
        normalized_swings, media_records = _normalize_guided_media(attempt, swings)
        initial_media = _coaching_media(media_records)
        source = prepare_report_input(
            video,
            normalized_swings,
            stats,
            session_notes,
            hand,
            cfg,
            angle=angle,
            club=club,
            level=level,
            analysis_fps=analysis_fps,
            replay_locked=replay_locked,
            media=initial_media,
            reason_codes=tuple(reason_codes),
        )
        reasons = tuple(reason_codes)
        visual = None
        final_media: tuple[MediaEntry, ...] = initial_media
        final_swings = normalized_swings
        safe_media_keys: tuple[str, ...] = ()

        if not _has_fatal(reasons):
            if source.brief is None:
                reasons = _append_reason(reasons, ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE)
            else:
                try:
                    rule = priority_evidence_rule(source.brief, source.issues, angle=source.context.angle, cfg=cfg)
                    selection = select_focused_evidence(rule=rule, snapshots=evidence_snapshots, stats=source.stats)
                except (UnsupportedPriorityEvidence, ValueError):
                    reasons = _append_reason(reasons, ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE)
                else:
                    if selection.fatal_reason is not None:
                        reasons = _append_reason(reasons, selection.fatal_reason)
                    elif selection.snapshot is None:
                        reasons = _append_reason(reasons, ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE)
                    elif selection.snapshot.tracking_quality.poor:
                        reasons = _append_reason(reasons, ReasonCode.TRACKING_UNSTABLE)
                    else:
                        focused_path = attempt.media_dir / "priority-evidence.png"
                        try:
                            focused = render_focused_evidence(
                                selection,
                                out_path=focused_path,
                                relative_path="media/priority-evidence.png",
                                cfg=cfg,
                                angle=source.context.angle,
                            )
                        except FocusedEvidenceRenderError:
                            visual = build_unavailable_evidence(
                                selection,
                                observation="The selected measurement remains usable, but its focused visual is unavailable.",
                                supporting_measurement=measurement_detail(
                                    rule.metric_id,
                                    source.swings,
                                    source.stats,
                                    cfg,
                                    angle=source.context.angle,
                                ),
                            )
                            reasons = _append_reason(reasons, ReasonCode.FOCUSED_MEDIA_RENDER_FAILED)
                        except UnsupportedFocusedEvidence as exc:
                            raise CoreReportBundleError("guided focused evidence is unsupported for this angle") from exc
                        else:
                            visual = focused.evidence
                            final_media = (*final_media, focused.media)

        if _has_fatal(reasons):
            final_swings, final_media, safe_media_keys = _capture_media_and_prune(
                attempt, normalized_swings, media_records
            )
            source = replace(
                source,
                swings=_capture_swing_sources(source, final_media),
                visual_evidence=None,
                media=final_media,
                reason_codes=reasons,
                safe_media_keys=safe_media_keys,
            )
        else:
            source = replace(
                source,
                visual_evidence=visual,
                media=final_media,
                reason_codes=reasons,
            )

        document = build_report_document(source, cfg)
        report_path = attempt.staging_dir / REPORT_FILENAME
        report_view_path = attempt.staging_dir / REPORT_VIEW_FILENAME
        metrics_path = attempt.staging_dir / METRICS_FILENAME
        manifest_path = attempt.staging_dir / REPORT_MANIFEST_FILENAME
        checksums_path = attempt.staging_dir / REPORT_CHECKSUMS_FILENAME

        write_report_view(report_view_path, document.view)
        write_metrics_json(metrics_path, video, final_swings, stats, session_notes, cfg)
        written_report = html_writer(report_path, document, cfg=cfg)
        if not isinstance(written_report, Path) or written_report.absolute() != report_path.absolute():
            raise CoreReportBundleError("guided HTML writer returned a noncanonical report path")
        report_info = _lstat(report_path, label="guided report HTML")
        if _is_reparse(report_path, report_info) or not stat.S_ISREG(report_info.st_mode):
            raise CoreReportBundleError("guided HTML writer did not create a plain report file")

        _remove_work_and_empty_media(attempt)
        manifest = ReportBundleManifest(
            REPORT_MANIFEST_FORMAT,
            attempt.attempt_id,
            GUIDED_REPORT_PRESENTATION_VERSION,
            document.view.outcome,
            _manifest_artifacts(document.view),
        )
        write_report_manifest(manifest_path, manifest)
        owner_path = attempt.staging_dir / ATTEMPT_OWNER_FILENAME
        _read_exact_owner(owner_path, attempt.attempt_id)
        owner_path.unlink()
        manifest_size, manifest_digest = _hash_file(manifest_path)
        checksums = ReportBundleChecksums(
            REPORT_CHECKSUMS_FORMAT,
            manifest_digest,
            _checksum_entries(attempt.staging_dir, manifest),
        )
        if not any(
            row.relative_path == REPORT_MANIFEST_FILENAME
            and row.size_bytes == manifest_size
            and row.sha256 == manifest_digest
            for row in checksums.files
        ):
            raise CoreReportBundleError("manifest checksum construction failed")
        write_report_checksums(checksums_path, checksums)
        parsed_manifest, parsed_checksums, parsed_view = validate_staged_bundle(
            attempt.staging_dir,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )
        return StagedReportBundle(
            attempt,
            replace(document, view=parsed_view),
            report_path,
            report_view_path,
            manifest_path,
            checksums_path,
            parsed_view,
            parsed_manifest,
            parsed_checksums,
        )
    except Exception as exc:
        try:
            if attempt.staging_dir.exists() or os.path.lexists(attempt.staging_dir):
                discard_report_bundle_attempt(attempt)
        except CoreReportBundleError as cleanup_exc:
            raise CoreReportBundleError(
                "report bundle failed and its owned tree contains an ambiguous link or reparse entry; attempt was preserved"
            ) from cleanup_exc
        if isinstance(exc, CoreReportBundleError):
            raise
        raise CoreReportBundleError("core report bundle build failed") from exc


def _rename_report_bundle_noreplace(source: Path, destination: Path) -> None:
    """Perform exactly one platform-exclusive same-filesystem directory rename."""
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel32.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int
        if not move(str(source), str(destination), 0):
            error = ctypes.get_last_error()
            raise CoreReportBundleError(f"exclusive Windows report bundle rename failed ({error})")
        return
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise CoreReportBundleError("Linux renameat2 RENAME_NOREPLACE is unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
            error = ctypes.get_errno()
            raise CoreReportBundleError(f"exclusive Linux report bundle rename failed ({error})")
        return
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex = getattr(libc, "renamex_np", None)
        if renamex is None:
            raise CoreReportBundleError("macOS renamex_np RENAME_EXCL is unavailable")
        renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex.restype = ctypes.c_int
        if renamex(os.fsencode(source), os.fsencode(destination), 0x00000004) != 0:
            error = ctypes.get_errno()
            raise CoreReportBundleError(f"exclusive macOS report bundle rename failed ({error})")
        return
    raise CoreReportBundleError("platform has no supported exclusive report bundle rename")


def publish_report_bundle(staged: StagedReportBundle) -> PublishedReportBundle:
    if not isinstance(staged, StagedReportBundle):
        raise CoreReportBundleError("expected StagedReportBundle")
    attempt = staged.attempt
    session = _validate_attempt_descriptor(attempt)
    source = _resolved_plain_directory(attempt.staging_dir, label="staged report bundle")
    if source.parent != session:
        raise CoreReportBundleError("staged report source is not a resolved session sibling")
    destination = session / f"report-bundle-{attempt.attempt_id}"
    if os.path.lexists(destination):
        raise CoreReportBundleError("final report bundle destination already exists")
    source_info = _lstat(source, label="staged report bundle")
    session_info = _lstat(session, label="session root")
    if int(source_info.st_dev) != int(session_info.st_dev):
        raise CoreReportBundleError("staged and final report roots are not on one filesystem")

    try:
        manifest, checksums, view = validate_staged_bundle(
            source,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )
    except ReportArtifactValidationError as exc:
        raise CoreReportBundleError("staged report validation failed immediately before publication") from exc
    if (manifest, checksums, view) != (staged.manifest, staged.checksums, staged.view):
        raise CoreReportBundleError("staged report contracts changed before publication")

    try:
        _rename_report_bundle_noreplace(source, destination)
    except CoreReportBundleError:
        raise
    except OSError as exc:
        raise CoreReportBundleError("exclusive report bundle publication failed") from exc

    root_rel = destination.name
    try:
        return load_published_bundle(
            session,
            report_rel=f"{root_rel}/{REPORT_FILENAME}",
            report_view_rel=f"{root_rel}/{REPORT_VIEW_FILENAME}",
            manifest_rel=f"{root_rel}/{REPORT_MANIFEST_FILENAME}",
            checksums_rel=f"{root_rel}/{REPORT_CHECKSUMS_FILENAME}",
        )
    except ReportArtifactValidationError as exc:
        raise CoreReportBundleError(
            "published report readback failed; final root was left for scoped recovery"
        ) from exc


def _safe_protected_path(value: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise CoreReportBundleError("protected report rel must be a string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in path.parts)
    ):
        raise CoreReportBundleError("protected report rel is unsafe")
    return path


def _protected_roots(protected_rels: Sequence[str]) -> frozenset[str]:
    values = tuple(protected_rels)
    if len(values) != len(set(values)) or len(values) != len({value.casefold() for value in values}):
        raise CoreReportBundleError("protected report rels are duplicated")
    groups: dict[str, set[str]] = {}
    expected = {
        REPORT_FILENAME,
        REPORT_VIEW_FILENAME,
        REPORT_MANIFEST_FILENAME,
        REPORT_CHECKSUMS_FILENAME,
    }
    for value in values:
        path = _safe_protected_path(value)
        if len(path.parts) != 2 or _FINAL_RE.fullmatch(path.parts[0]) is None:
            raise CoreReportBundleError("protected report rels must use one direct final root")
        groups.setdefault(path.parts[0], set()).add(path.parts[1])
    if any(files != expected for files in groups.values()):
        raise CoreReportBundleError("protected report rels must form complete canonical four-rel groups")
    return frozenset(groups)


def cleanup_abandoned_report_bundles(
    session_dir: Path,
    *,
    protected_rels: Sequence[str] | None = None,
) -> int:
    session = _resolved_plain_directory(Path(session_dir), label="session root")
    protected = None if protected_rels is None else _protected_roots(protected_rels)
    plans: list[tuple[_OwnedEntry, ...]] = []
    try:
        children = sorted(os.scandir(session), key=lambda entry: entry.name)
    except OSError as exc:
        raise CoreReportBundleError("session root cannot be enumerated for report recovery") from exc
    for child in children:
        attempt_match = _ATTEMPT_RE.fullmatch(child.name)
        final_match = _FINAL_RE.fullmatch(child.name)
        if attempt_match is None and final_match is None:
            continue
        path = Path(child.path)
        info = child.stat(follow_symlinks=False)
        if child.is_symlink() or _is_reparse_info(info) or not stat.S_ISDIR(info.st_mode):
            raise CoreReportBundleError("report recovery candidate is a link, reparse point, or non-directory")
        if attempt_match is not None:
            attempt_id = attempt_match.group(1)
            if path.parent.resolve(strict=True) != session:
                raise CoreReportBundleError("report recovery attempt is not a direct session child")
            _attempt_ownership(path, attempt_id)
            plans.append(_preflight_owned_tree(path))
            continue
        assert final_match is not None
        if protected is None or child.name in protected:
            continue
        try:
            manifest, _, _ = validate_staged_bundle(
                path,
                manifest_rel=REPORT_MANIFEST_FILENAME,
                checksums_rel=REPORT_CHECKSUMS_FILENAME,
            )
        except ReportArtifactValidationError as exc:
            raise CoreReportBundleError("final report recovery candidate failed strict validation") from exc
        if manifest.attempt_id != final_match.group(1):
            raise CoreReportBundleError("final report manifest ID does not match its exact directory")
        plans.append(_preflight_owned_tree(path))

    for entries in plans:
        _delete_preflighted_tree(entries)
    return len(plans)
