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
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Iterator, Protocol, Sequence

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
    ReportOutcome,
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
_MAX_RECOVERY_DIRECT_ENTRIES = 4096
_MAX_RECOVERY_PLANNED_ENTRIES = 8192
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


if os.name == "nt":  # pragma: no cover - definitions are imported only on Windows
    from ctypes import wintypes

    class _WinFileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _WinByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _WinFileTime),
            ("ftLastAccessTime", _WinFileTime),
            ("ftLastWriteTime", _WinFileTime),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _WinFileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    _WIN_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _WIN_CREATE_FILE = _WIN_KERNEL32.CreateFileW
    _WIN_CREATE_FILE.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _WIN_CREATE_FILE.restype = wintypes.HANDLE
    _WIN_CLOSE_HANDLE = _WIN_KERNEL32.CloseHandle
    _WIN_CLOSE_HANDLE.argtypes = [wintypes.HANDLE]
    _WIN_CLOSE_HANDLE.restype = wintypes.BOOL
    _WIN_GET_FILE_INFO = _WIN_KERNEL32.GetFileInformationByHandle
    _WIN_GET_FILE_INFO.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WinByHandleFileInformation)]
    _WIN_GET_FILE_INFO.restype = wintypes.BOOL
    _WIN_GET_FINAL_PATH = _WIN_KERNEL32.GetFinalPathNameByHandleW
    _WIN_GET_FINAL_PATH.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _WIN_GET_FINAL_PATH.restype = wintypes.DWORD
    _WIN_SET_FILE_INFO = _WIN_KERNEL32.SetFileInformationByHandle
    _WIN_SET_FILE_INFO.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _WIN_SET_FILE_INFO.restype = wintypes.BOOL

    _WIN_FILE_READ_ATTRIBUTES = 0x80
    _WIN_DELETE = 0x00010000
    _WIN_SHARE_READ_WRITE = 0x1 | 0x2
    _WIN_OPEN_EXISTING = 3
    _WIN_FLAG_BACKUP_SEMANTICS = 0x02000000
    _WIN_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _WIN_ATTR_DIRECTORY = 0x10
    _WIN_ATTR_REPARSE_POINT = 0x400
    _WIN_FILE_DISPOSITION_INFO_CLASS = 4
    _WIN_INVALID_HANDLE = ctypes.c_void_p(-1).value


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


@dataclass
class _OwnedEntry:
    path: Path
    name: str
    parent_handle: int
    mode: int
    device: int
    inode: int
    handle: int | None = None


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


def _same_entry(info: os.stat_result, entry: _OwnedEntry) -> bool:
    return (
        not _is_reparse_info(info)
        and not stat.S_ISLNK(info.st_mode)
        and (int(info.st_dev), int(info.st_ino), stat.S_IFMT(info.st_mode))
        == (entry.device, entry.inode, stat.S_IFMT(entry.mode))
    )


def _win_open_owned(path: Path, *, delete_access: bool) -> int:
    desired = _WIN_FILE_READ_ATTRIBUTES | (_WIN_DELETE if delete_access else 0)
    handle = _WIN_CREATE_FILE(
        str(path),
        desired,
        _WIN_SHARE_READ_WRITE,
        None,
        _WIN_OPEN_EXISTING,
        _WIN_FLAG_BACKUP_SEMANTICS | _WIN_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle in (None, _WIN_INVALID_HANDLE):
        raise OSError(ctypes.get_last_error(), "cannot pin owned report entry")
    return int(handle)


def _win_close_owned(handle: int) -> None:
    if not _WIN_CLOSE_HANDLE(handle):
        raise OSError(ctypes.get_last_error(), "cannot close owned report handle")


def _win_owned_info(handle: int):
    info = _WinByHandleFileInformation()
    if not _WIN_GET_FILE_INFO(handle, ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "cannot inspect owned report handle")
    return info


def _win_owned_identity(info: object) -> tuple[int, int]:
    return (
        int(info.dwVolumeSerialNumber),
        (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
    )


def _win_owned_final_path(handle: int) -> Path:
    size = 32768
    buffer = ctypes.create_unicode_buffer(size)
    length = _WIN_GET_FINAL_PATH(handle, buffer, size, 0)
    if not length or length >= size:
        raise OSError(ctypes.get_last_error(), "cannot resolve owned report handle")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _same_windows_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
        os.path.abspath(str(right))
    )


def _win_validate_handle(
    handle: int,
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
    expected_directory: bool,
) -> tuple[int, int]:
    info = _win_owned_info(handle)
    attributes = int(info.dwFileAttributes)
    if attributes & _WIN_ATTR_REPARSE_POINT:
        raise CoreReportBundleError("owned report tree contains a reparse point")
    is_directory = bool(attributes & _WIN_ATTR_DIRECTORY)
    if is_directory != expected_directory:
        raise CoreReportBundleError("owned report entry changed type")
    identity = _win_owned_identity(info)
    if expected_identity is not None and identity != expected_identity:
        raise CoreReportBundleError("owned report entry changed identity")
    if not _same_windows_path(_win_owned_final_path(handle), path):
        raise CoreReportBundleError("owned report handle changed its final path")
    return identity


def _require_posix_delete_capabilities() -> int:
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(directory, int) or directory == 0:
        raise CoreReportBundleError("POSIX O_DIRECTORY support is required for owned deletion")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(no_follow, int) or no_follow == 0:
        raise CoreReportBundleError("POSIX O_NOFOLLOW support is required for owned deletion")
    required_dir_fd = (os.open, os.stat, os.unlink, os.rmdir)
    dir_fd_support = getattr(os, "supports_dir_fd", frozenset())
    if any(function not in dir_fd_support for function in required_dir_fd):
        raise CoreReportBundleError("POSIX dir_fd support is required for owned deletion")
    follow_support = getattr(os, "supports_follow_symlinks", frozenset())
    if os.stat not in follow_support:
        raise CoreReportBundleError(
            "POSIX no-follow stat support is required for owned deletion"
        )
    return os.O_RDONLY | directory | no_follow


def _posix_rename_to_quarantine_noreplace(
    source_parent_fd: int,
    source_name: str,
    anchor_fd: int,
    quarantine_name: str,
) -> None:
    source = os.fsencode(source_name)
    destination = os.fsencode(quarantine_name)
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise CoreReportBundleError(
                "Linux renameat2 RENAME_NOREPLACE is required for owned deletion"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        if renameat2(source_parent_fd, source, anchor_fd, destination, 1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, "descriptor-relative quarantine rename failed")
        return
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx = getattr(libc, "renameatx_np", None)
        if renameatx is None:
            raise CoreReportBundleError(
                "macOS renameatx_np RENAME_EXCL is required for owned deletion"
            )
        renameatx.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx.restype = ctypes.c_int
        if renameatx(source_parent_fd, source, anchor_fd, destination, 0x00000004) != 0:
            error = ctypes.get_errno()
            raise OSError(error, "descriptor-relative quarantine rename failed")
        return
    raise CoreReportBundleError(
        "platform has no descriptor-relative exclusive quarantine rename"
    )


class _PinnedOwnedTree:
    """Plan and delete one owned tree without releasing its ancestry."""

    def __init__(self, root: Path, *, session_anchor: Path):
        self.root = root.absolute()
        self.session_anchor = session_anchor.absolute()
        self.entries: list[_OwnedEntry] = []
        self._parent_handle: int | None = None
        self._parent_identity: tuple[int, int] | None = None
        self._posix_anchor_handle: int | None = None
        self._posix_anchor_identity: tuple[int, int] | None = None
        self._posix_ancestors: list[_OwnedEntry] = []

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    def __enter__(self) -> _PinnedOwnedTree:
        try:
            if os.name == "nt":
                self._enter_windows()
            else:
                self._enter_posix()
        except CoreReportBundleError:
            self.__exit__(None, None, None)
            raise
        except OSError as exc:
            self.__exit__(None, None, None)
            raise CoreReportBundleError("owned report tree cannot be pinned safely") from exc
        except (AttributeError, NotImplementedError, TypeError) as exc:
            self.__exit__(None, None, None)
            raise CoreReportBundleError(
                "owned report platform capabilities are unavailable"
            ) from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        for entry in reversed(self.entries):
            if entry.handle is None:
                continue
            try:
                _win_close_owned(entry.handle) if os.name == "nt" else os.close(entry.handle)
            except OSError:
                pass
            entry.handle = None
        self.entries.clear()
        for entry in reversed(self._posix_ancestors):
            if entry.handle is None:
                continue
            try:
                os.close(entry.handle)
            except OSError:
                pass
            entry.handle = None
        self._posix_ancestors.clear()
        if self._posix_anchor_handle is not None:
            try:
                os.close(self._posix_anchor_handle)
            except OSError:
                pass
        self._posix_anchor_handle = None
        self._posix_anchor_identity = None
        if self._parent_handle is not None:
            try:
                _win_close_owned(self._parent_handle) if os.name == "nt" else os.close(self._parent_handle)
            except OSError:
                pass
        self._parent_handle = None
        self._parent_identity = None

    def _check_bound(self) -> None:
        if len(self.entries) >= _MAX_OWNED_ENTRIES:
            raise CoreReportBundleError("owned report tree exceeds its traversal bound")

    def _enter_posix(self) -> None:
        flags = _require_posix_delete_capabilities()
        try:
            relative = self.root.relative_to(self.session_anchor)
        except ValueError as exc:
            raise CoreReportBundleError(
                "owned report tree is outside its trusted session anchor"
            ) from exc
        if not relative.parts:
            raise CoreReportBundleError("trusted session anchor cannot be a deletion target")

        anchor_handle = os.open(self.session_anchor, flags)
        self._posix_anchor_handle = anchor_handle
        anchor_info = os.fstat(anchor_handle)
        lexical_anchor = os.stat(self.session_anchor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(anchor_info.st_mode)
            or _is_reparse_info(anchor_info)
            or stat.S_ISLNK(lexical_anchor.st_mode)
            or _is_reparse_info(lexical_anchor)
            or (int(anchor_info.st_dev), int(anchor_info.st_ino))
            != (int(lexical_anchor.st_dev), int(lexical_anchor.st_ino))
        ):
            raise CoreReportBundleError(
                "trusted session anchor cannot be pinned as one plain directory"
            )
        self._posix_anchor_identity = (int(anchor_info.st_dev), int(anchor_info.st_ino))

        parent_handle = anchor_handle
        parent_path = self.session_anchor
        for name in relative.parts[:-1]:
            info = os.stat(name, dir_fd=parent_handle, follow_symlinks=False)
            if (
                _is_reparse_info(info)
                or stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
            ):
                raise CoreReportBundleError(
                    "owned report ancestor is not one plain directory"
                )
            handle = os.open(name, flags, dir_fd=parent_handle)
            try:
                pinned = os.fstat(handle)
            except OSError:
                os.close(handle)
                raise
            if (
                int(info.st_dev),
                int(info.st_ino),
                stat.S_IFMT(info.st_mode),
            ) != (
                int(pinned.st_dev),
                int(pinned.st_ino),
                stat.S_IFMT(pinned.st_mode),
            ):
                os.close(handle)
                raise CoreReportBundleError(
                    "owned report ancestor changed while being pinned"
                )
            ancestor = _OwnedEntry(
                parent_path / name,
                name,
                parent_handle,
                info.st_mode,
                int(info.st_dev),
                int(info.st_ino),
                handle,
            )
            self._posix_ancestors.append(ancestor)
            parent_handle = handle
            parent_path = ancestor.path

        root_name = relative.parts[-1]
        root_lexical = os.stat(root_name, dir_fd=parent_handle, follow_symlinks=False)
        if (
            _is_reparse_info(root_lexical)
            or stat.S_ISLNK(root_lexical.st_mode)
            or not stat.S_ISDIR(root_lexical.st_mode)
        ):
            raise CoreReportBundleError("owned report root is not one plain directory")
        root_handle = os.open(root_name, flags, dir_fd=parent_handle)
        try:
            root_info = os.fstat(root_handle)
        except OSError:
            os.close(root_handle)
            raise
        if (
            int(root_lexical.st_dev),
            int(root_lexical.st_ino),
            stat.S_IFMT(root_lexical.st_mode),
        ) != (
            int(root_info.st_dev),
            int(root_info.st_ino),
            stat.S_IFMT(root_info.st_mode),
        ):
            os.close(root_handle)
            raise CoreReportBundleError("owned report root changed while being pinned")
        root_entry = _OwnedEntry(
            self.root,
            root_name,
            parent_handle,
            root_info.st_mode,
            int(root_info.st_dev),
            int(root_info.st_ino),
            root_handle,
        )
        self._walk_posix(root_entry)

    def _walk_posix(self, entry: _OwnedEntry) -> None:
        if len(self.entries) >= _MAX_OWNED_ENTRIES:
            if entry.handle is not None:
                os.close(entry.handle)
                entry.handle = None
            raise CoreReportBundleError("owned report tree exceeds its traversal bound")
        if entry.handle is None or not stat.S_ISDIR(entry.mode):
            raise CoreReportBundleError("owned report directory was not pinned")
        self.entries.append(entry)
        names: list[str] = []
        with os.scandir(entry.handle) as scanned:
            for child in scanned:
                if len(self.entries) + len(names) >= _MAX_OWNED_ENTRIES:
                    raise CoreReportBundleError("owned report tree exceeds its traversal bound")
                names.append(child.name)
        for name in sorted(names):
            info = os.stat(name, dir_fd=entry.handle, follow_symlinks=False)
            if _is_reparse_info(info) or stat.S_ISLNK(info.st_mode):
                raise CoreReportBundleError("owned report tree contains a link or reparse point")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise CoreReportBundleError("owned report tree contains an unsupported entry")
            child_path = entry.path / name
            child_handle = None
            if stat.S_ISDIR(info.st_mode):
                flags = _require_posix_delete_capabilities()
                child_handle = os.open(name, flags, dir_fd=entry.handle)
                try:
                    pinned_info = os.fstat(child_handle)
                except OSError:
                    os.close(child_handle)
                    raise
                if (int(info.st_dev), int(info.st_ino)) != (
                    int(pinned_info.st_dev), int(pinned_info.st_ino)
                ):
                    os.close(child_handle)
                    raise CoreReportBundleError("owned report directory changed while being pinned")
            child_entry = _OwnedEntry(
                child_path,
                name,
                entry.handle,
                info.st_mode,
                int(info.st_dev),
                int(info.st_ino),
                child_handle,
            )
            if stat.S_ISDIR(info.st_mode):
                self._walk_posix(child_entry)
            else:
                self._check_bound()
                self.entries.append(child_entry)

    def _enter_windows(self) -> None:
        parent = self.root.parent
        self._parent_handle = _win_open_owned(parent, delete_access=False)
        self._parent_identity = _win_validate_handle(
            self._parent_handle, parent, expected_directory=True
        )
        root_handle = _win_open_owned(self.root, delete_access=True)
        identity = _win_validate_handle(root_handle, self.root, expected_directory=True)
        root_entry = _OwnedEntry(
            self.root,
            self.root.name,
            self._parent_handle,
            stat.S_IFDIR,
            identity[0],
            identity[1],
            root_handle,
        )
        self._walk_windows(root_entry)

    def _walk_windows(self, entry: _OwnedEntry) -> None:
        if len(self.entries) >= _MAX_OWNED_ENTRIES:
            if entry.handle is not None:
                _win_close_owned(entry.handle)
                entry.handle = None
            raise CoreReportBundleError("owned report tree exceeds its traversal bound")
        if entry.handle is None or not stat.S_ISDIR(entry.mode):
            raise CoreReportBundleError("owned report directory was not pinned")
        self.entries.append(entry)
        names: list[str] = []
        with os.scandir(entry.path) as scanned:
            for child in scanned:
                if len(self.entries) + len(names) >= _MAX_OWNED_ENTRIES:
                    raise CoreReportBundleError("owned report tree exceeds its traversal bound")
                names.append(child.name)
        for name in sorted(names):
            child_path = entry.path / name
            child_handle = _win_open_owned(child_path, delete_access=True)
            try:
                info = _win_owned_info(child_handle)
                attributes = int(info.dwFileAttributes)
                if attributes & _WIN_ATTR_REPARSE_POINT:
                    raise CoreReportBundleError("owned report tree contains a link or reparse point")
                is_directory = bool(attributes & _WIN_ATTR_DIRECTORY)
                identity = _win_validate_handle(
                    child_handle,
                    child_path,
                    expected_directory=is_directory,
                )
                child_entry = _OwnedEntry(
                    child_path,
                    name,
                    entry.handle,
                    stat.S_IFDIR if is_directory else stat.S_IFREG,
                    identity[0],
                    identity[1],
                    child_handle,
                )
                child_handle = None
                if is_directory:
                    self._walk_windows(child_entry)
                else:
                    self._check_bound()
                    self.entries.append(child_entry)
            finally:
                if child_handle is not None:
                    _win_close_owned(child_handle)

    def validate(self) -> None:
        if not self.entries:
            raise CoreReportBundleError("owned report tree is not pinned")
        if os.name == "nt":
            if self._parent_handle is None or self._parent_identity is None:
                raise CoreReportBundleError("owned report tree is not pinned")
        elif (
            self._posix_anchor_handle is None
            or self._posix_anchor_identity is None
        ):
            raise CoreReportBundleError("owned report tree is not pinned")
        try:
            if os.name == "nt":
                self._validate_windows()
            else:
                self._validate_posix()
        except CoreReportBundleError:
            raise
        except OSError as exc:
            raise CoreReportBundleError("owned report tree changed before deletion") from exc
        except (AttributeError, NotImplementedError, TypeError) as exc:
            raise CoreReportBundleError(
                "owned report validation capabilities are unavailable"
            ) from exc

    def _validate_posix(self) -> None:
        for entry in self.entries:
            self._validate_posix_source(entry)

    def _validate_posix_anchor(self) -> None:
        if self._posix_anchor_handle is None or self._posix_anchor_identity is None:
            raise CoreReportBundleError("trusted session anchor is not pinned")
        lexical = os.stat(self.session_anchor, follow_symlinks=False)
        pinned = os.fstat(self._posix_anchor_handle)
        if (
            _is_reparse_info(lexical)
            or stat.S_ISLNK(lexical.st_mode)
            or not stat.S_ISDIR(lexical.st_mode)
            or (int(lexical.st_dev), int(lexical.st_ino))
            != self._posix_anchor_identity
            or (int(pinned.st_dev), int(pinned.st_ino))
            != self._posix_anchor_identity
        ):
            raise CoreReportBundleError("trusted session anchor changed before deletion")

    def _posix_directory_records(self) -> dict[Path, _OwnedEntry]:
        records = {entry.path: entry for entry in self._posix_ancestors}
        records.update(
            (entry.path, entry)
            for entry in self.entries
            if stat.S_ISDIR(entry.mode)
        )
        return records

    def _validate_posix_source(self, entry: _OwnedEntry) -> None:
        self._validate_posix_anchor()
        assert self._posix_anchor_handle is not None
        try:
            relative = entry.path.relative_to(self.session_anchor)
        except ValueError as exc:
            raise CoreReportBundleError(
                "owned report entry escaped its trusted session anchor"
            ) from exc
        if not relative.parts:
            raise CoreReportBundleError("trusted session anchor cannot be deleted")
        directories = self._posix_directory_records()
        parent_handle = self._posix_anchor_handle
        current_path = self.session_anchor
        for name in relative.parts[:-1]:
            current_path = current_path / name
            record = directories.get(current_path)
            if record is None or record.handle is None:
                raise CoreReportBundleError(
                    "owned report lexical ancestor is no longer pinned"
                )
            info = os.stat(name, dir_fd=parent_handle, follow_symlinks=False)
            if not _same_entry(info, record):
                raise CoreReportBundleError(
                    "owned report lexical ancestor changed before deletion"
                )
            pinned = os.fstat(record.handle)
            if not _same_entry(pinned, record):
                raise CoreReportBundleError(
                    "pinned owned report ancestor changed before deletion"
                )
            if record.parent_handle != parent_handle:
                raise CoreReportBundleError(
                    "owned report ancestor handle chain is inconsistent"
                )
            parent_handle = record.handle
        if entry.parent_handle != parent_handle:
            raise CoreReportBundleError("owned report source parent is not its lexical parent")
        source = os.stat(entry.name, dir_fd=parent_handle, follow_symlinks=False)
        if not _same_entry(source, entry):
            raise CoreReportBundleError("owned report source changed before quarantine")
        if entry.handle is not None:
            pinned = os.fstat(entry.handle)
            if not _same_entry(pinned, entry):
                raise CoreReportBundleError(
                    "pinned owned report directory changed before quarantine"
                )

    def _validate_windows(self) -> None:
        assert self._parent_handle is not None and self._parent_identity is not None
        _win_validate_handle(
            self._parent_handle,
            self.root.parent,
            expected_identity=self._parent_identity,
            expected_directory=True,
        )
        parent_probe = _win_open_owned(self.root.parent, delete_access=False)
        try:
            _win_validate_handle(
                parent_probe,
                self.root.parent,
                expected_identity=self._parent_identity,
                expected_directory=True,
            )
        finally:
            _win_close_owned(parent_probe)
        for entry in self.entries:
            if entry.handle is None:
                raise CoreReportBundleError("owned report entry lost its pinned handle")
            identity = (entry.device, entry.inode)
            expected_directory = stat.S_ISDIR(entry.mode)
            _win_validate_handle(
                entry.handle,
                entry.path,
                expected_identity=identity,
                expected_directory=expected_directory,
            )
            probe = _win_open_owned(entry.path, delete_access=False)
            try:
                _win_validate_handle(
                    probe,
                    entry.path,
                    expected_identity=identity,
                    expected_directory=expected_directory,
                )
            finally:
                _win_close_owned(probe)

    def delete_preserving(
        self,
        keep_paths: Sequence[Path] = (),
        *,
        already_validated: bool = False,
    ) -> None:
        kept = {path.absolute() for path in keep_paths}
        known = {entry.path for entry in self.entries}
        if not kept.issubset(known):
            raise CoreReportBundleError("owned report preservation target changed before deletion")
        if not already_validated:
            self.validate()
            _after_owned_tree_validation((self,))
        for entry in reversed(self.entries):
            if entry.path in kept:
                continue
            if stat.S_ISDIR(entry.mode) and any(entry.path in path.parents for path in kept):
                continue
            try:
                if os.name == "nt":
                    if entry.handle is None:
                        raise CoreReportBundleError("owned report entry lost its pinned handle")
                    disposition = _WinFileDispositionInfo(True)
                    if not _WIN_SET_FILE_INFO(
                        entry.handle,
                        _WIN_FILE_DISPOSITION_INFO_CLASS,
                        ctypes.byref(disposition),
                        ctypes.sizeof(disposition),
                    ):
                        raise OSError(ctypes.get_last_error(), "cannot mark owned report entry for deletion")
                    _win_close_owned(entry.handle)
                    entry.handle = None
                else:
                    self._delete_posix_entry(entry)
            except CoreReportBundleError:
                raise
            except OSError as exc:
                raise CoreReportBundleError("owned report entry could not be deleted safely") from exc

    def _delete_posix_entry(self, entry: _OwnedEntry) -> None:
        self._validate_posix_source(entry)
        if self._posix_anchor_handle is None:
            raise CoreReportBundleError("trusted session anchor is not pinned")
        quarantine = f".report-delete-quarantine-{uuid.uuid4().hex}"
        # If the source parent relocates after validation, renameat still moves
        # this exact preflighted child back beneath the trusted anchor. A raced
        # replacement fails the identity check and remains as recovery evidence.
        _posix_rename_to_quarantine_noreplace(
            entry.parent_handle,
            entry.name,
            self._posix_anchor_handle,
            quarantine,
        )
        quarantined = os.stat(
            quarantine,
            dir_fd=self._posix_anchor_handle,
            follow_symlinks=False,
        )
        if not _same_entry(quarantined, entry):
            raise CoreReportBundleError(
                "quarantined report entry has an ambiguous identity; evidence was preserved"
            )
        self._validate_posix_anchor()
        if stat.S_ISDIR(entry.mode):
            os.rmdir(quarantine, dir_fd=self._posix_anchor_handle)
        else:
            os.unlink(quarantine, dir_fd=self._posix_anchor_handle)


def _after_owned_tree_plans(plans: Sequence[_PinnedOwnedTree]) -> None:
    """Test seam after all handles are pinned and before any deletion begins."""


def _after_owned_tree_validation(plans: Sequence[_PinnedOwnedTree]) -> None:
    """Test seam after validation and before the first destructive step."""


def _delete_exact_owned_file(root: Path, target: Path, *, session_anchor: Path) -> None:
    with _PinnedOwnedTree(root, session_anchor=session_anchor) as plan:
        _after_owned_tree_plans((plan,))
        matches = [entry for entry in plan.entries if entry.path == target.absolute()]
        if not matches:
            return
        if len(matches) != 1 or not stat.S_ISREG(matches[0].mode):
            raise CoreReportBundleError("partial focused evidence is not one owned regular file")
        keep = tuple(entry.path for entry in plan.entries if entry.path != target.absolute())
        plan.delete_preserving(keep)


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
    with _PinnedOwnedTree(
        attempt.staging_dir,
        session_anchor=attempt.session_dir,
    ) as plan:
        _after_owned_tree_plans((plan,))
        plan.delete_preserving()


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
    *,
    angle: str,
) -> tuple[list[dict], tuple[_GuidedMedia, ...]]:
    normalized: list[dict] = []
    records: list[_GuidedMedia] = []
    for index, original in enumerate(swings, start=1):
        swing = dict(original)
        if swing.get("overlay") is not None:
            raise CoreReportBundleError("guided report bundles reject legacy overlay media")
        if angle == "dtl" and swing.get("strip") is not None:
            raise CoreReportBundleError("DTL guided report bundles reject key-position strip media")
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

    with _PinnedOwnedTree(
        attempt.media_dir,
        session_anchor=attempt.session_dir,
    ) as plan:
        files = {entry.path for entry in plan.entries if stat.S_ISREG(entry.mode)}
        if not kept.issubset(files):
            raise CoreReportBundleError("safe capture playback changed before pruning")
        _after_owned_tree_plans((plan,))
        plan.delete_preserving((attempt.media_dir, *kept))
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
    with ExitStack() as stack:
        work = stack.enter_context(
            _PinnedOwnedTree(attempt.work_dir, session_anchor=attempt.session_dir)
        )
        media = stack.enter_context(
            _PinnedOwnedTree(attempt.media_dir, session_anchor=attempt.session_dir)
        )
        plans = (work, media)
        _after_owned_tree_plans(plans)
        for plan in plans:
            plan.validate()
        _after_owned_tree_validation(plans)
        work.delete_preserving(already_validated=True)
        if media.entry_count == 1:
            media.delete_preserving(already_validated=True)


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
        normalized_swings, media_records = _normalize_guided_media(attempt, swings, angle=angle)
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
                            _delete_exact_owned_file(
                                attempt.media_dir,
                                focused_path,
                                session_anchor=attempt.session_dir,
                            )
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
        write_metrics_json(
            metrics_path,
            video,
            final_swings,
            stats,
            session_notes,
            cfg,
            meta={
                "camera_angle": angle,
                "club": club,
                "level": level,
                "hand": hand,
                "analysis_fps": analysis_fps,
            },
            swing_pattern=(
                source.swing_pattern.as_dict()
                if source.swing_pattern is not None
                and document.view.outcome is not ReportOutcome.CAPTURE_ONLY
                else None
            ),
        )
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
        published = load_published_bundle(
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
    if (
        published.manifest,
        published.checksums,
        published.view,
    ) != (
        staged.manifest,
        staged.checksums,
        staged.view,
    ):
        raise CoreReportBundleError(
            "published report graph changed after the atomic rename; final root was left for scoped recovery"
        )
    return published


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


@dataclass
class _PreparedReportBundleCleanup:
    plans: tuple[_PinnedOwnedTree, ...]
    active: bool = True
    executed: bool = False

    def execute(self) -> int:
        if not self.active:
            raise CoreReportBundleError("prepared report cleanup is no longer pinned")
        if self.executed:
            raise CoreReportBundleError("prepared report cleanup already executed")
        self.executed = True
        for plan in self.plans:
            plan.delete_preserving(already_validated=True)
        return len(self.plans)


@contextmanager
def prepare_abandoned_report_bundle_cleanup(
    session_dir: Path,
    *,
    protected_rels: Sequence[str] | None = None,
) -> Iterator[_PreparedReportBundleCleanup]:
    session = _resolved_plain_directory(Path(session_dir), label="session root")
    protected = None if protected_rels is None else _protected_roots(protected_rels)
    child_names: list[str] = []
    try:
        with os.scandir(session) as scanned:
            for child in scanned:
                if len(child_names) >= _MAX_RECOVERY_DIRECT_ENTRIES:
                    raise CoreReportBundleError(
                        "session report recovery exceeds its direct-entry bound"
                    )
                child_names.append(child.name)
    except CoreReportBundleError:
        raise
    except OSError as exc:
        raise CoreReportBundleError("session root cannot be enumerated for report recovery") from exc
    with ExitStack() as stack:
        plans: list[_PinnedOwnedTree] = []
        cumulative_entries = 0
        for name in sorted(child_names):
            attempt_match = _ATTEMPT_RE.fullmatch(name)
            final_match = _FINAL_RE.fullmatch(name)
            if attempt_match is None and final_match is None:
                continue
            path = session / name
            info = _lstat(path, label="report recovery candidate")
            if _is_reparse(path, info) or not stat.S_ISDIR(info.st_mode):
                raise CoreReportBundleError(
                    "report recovery candidate is a link, reparse point, or non-directory"
                )
            if attempt_match is not None:
                attempt_id = attempt_match.group(1)
                _attempt_ownership(path, attempt_id)
            else:
                assert final_match is not None
                if protected is None or name in protected:
                    continue
                try:
                    manifest, _, _ = validate_staged_bundle(
                        path,
                        manifest_rel=REPORT_MANIFEST_FILENAME,
                        checksums_rel=REPORT_CHECKSUMS_FILENAME,
                    )
                except ReportArtifactValidationError as exc:
                    raise CoreReportBundleError(
                        "final report recovery candidate failed strict validation"
                    ) from exc
                if manifest.attempt_id != final_match.group(1):
                    raise CoreReportBundleError(
                        "final report manifest ID does not match its exact directory"
                    )
            plan = stack.enter_context(
                _PinnedOwnedTree(path, session_anchor=session)
            )
            cumulative_entries += plan.entry_count
            if cumulative_entries > _MAX_RECOVERY_PLANNED_ENTRIES:
                raise CoreReportBundleError(
                    "report recovery exceeds its cumulative planned-entry budget"
                )
            plans.append(plan)

        _after_owned_tree_plans(tuple(plans))
        for plan in plans:
            plan.validate()
        _after_owned_tree_validation(tuple(plans))
        prepared = _PreparedReportBundleCleanup(tuple(plans))
        try:
            yield prepared
        finally:
            prepared.active = False


def cleanup_abandoned_report_bundles(
    session_dir: Path,
    *,
    protected_rels: Sequence[str] | None = None,
) -> int:
    with prepare_abandoned_report_bundle_cleanup(
        session_dir,
        protected_rels=protected_rels,
    ) as prepared:
        return prepared.execute()
