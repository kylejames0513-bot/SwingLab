"""Canonical manifests and attack-resistant loading for guided report bundles."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterator, Literal, cast

from .report_view import (
    GUIDED_REPORT_PRESENTATION_VERSION,
    MAX_REPORT_VIEW_BYTES,
    CaptureOnlyReportView,
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
    report_view_to_dict,
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
MAX_REPORT_HTML_BYTES = 8 * 1024 * 1024
MAX_REPORT_METRICS_BYTES = 8 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_REPORT_FILENAME = "report.html"
_METRICS_FILENAME = "metrics.json"
_REPORT_HTML_FORMAT = "caddie-brief-v1"
_REPORT_HEADER_BYTES = 8192
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_PUBLISHED_ROOT_PATTERN = re.compile(r"report-bundle-([0-9a-f]{32})\Z")
_JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WINDOWS_RESERVED_SEGMENTS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_MAX_PATH_CHARACTERS = 4096
_MAX_PATH_SEGMENTS = 128
_MAX_DIRECTORY_ENTRIES = 4096


if os.name == "nt":  # pragma: no cover - imported only on Windows
    import ctypes
    import msvcrt
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

    class _NtUnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _NtIoStatusValue(ctypes.Union):
        _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]

    class _NtIoStatusBlock(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = [
            ("value", _NtIoStatusValue),
            ("Information", ctypes.c_size_t),
        ]

    class _NtObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_NtUnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

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
    _WIN_GET_FILE_INFO_EX = _WIN_KERNEL32.GetFileInformationByHandleEx
    _WIN_GET_FILE_INFO_EX.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _WIN_GET_FILE_INFO_EX.restype = wintypes.BOOL
    _WIN_GET_FINAL_PATH = _WIN_KERNEL32.GetFinalPathNameByHandleW
    _WIN_GET_FINAL_PATH.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _WIN_GET_FINAL_PATH.restype = wintypes.DWORD
    _WIN_NTDLL = ctypes.WinDLL("ntdll")
    _WIN_NT_OPEN_FILE = _WIN_NTDLL.NtOpenFile
    _WIN_NT_OPEN_FILE.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_NtObjectAttributes),
        ctypes.POINTER(_NtIoStatusBlock),
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    _WIN_NT_OPEN_FILE.restype = wintypes.LONG
    _WIN_NTSTATUS_TO_DOS_ERROR = _WIN_NTDLL.RtlNtStatusToDosError
    _WIN_NTSTATUS_TO_DOS_ERROR.argtypes = [wintypes.LONG]
    _WIN_NTSTATUS_TO_DOS_ERROR.restype = wintypes.ULONG

    _WIN_GENERIC_READ = 0x80000000
    _WIN_FILE_LIST_DIRECTORY = 0x1
    _WIN_FILE_READ_DATA = 0x1
    _WIN_FILE_TRAVERSE = 0x20
    _WIN_FILE_READ_ATTRIBUTES = 0x80
    _WIN_SYNCHRONIZE = 0x00100000
    _WIN_SHARE_READ_WRITE = 0x1 | 0x2
    _WIN_OPEN_EXISTING = 3
    _WIN_FLAG_BACKUP_SEMANTICS = 0x02000000
    _WIN_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _WIN_FILE_DIRECTORY_FILE = 0x1
    _WIN_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
    _WIN_FILE_NON_DIRECTORY_FILE = 0x40
    _WIN_FILE_OPEN_REPARSE_POINT = 0x00200000
    _WIN_ATTR_DIRECTORY = 0x10
    _WIN_ATTR_REPARSE_POINT = 0x400
    _WIN_INVALID_HANDLE = ctypes.c_void_p(-1).value
    _WIN_FILE_FULL_DIRECTORY_INFO = 14
    _WIN_FILE_FULL_DIRECTORY_RESTART_INFO = 15
    _WIN_ERROR_NO_MORE_FILES = 18
    _WIN_DIRECTORY_INFO_BYTES = 64 * 1024
    _WIN_DIRECTORY_NAME_OFFSET = 68


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


def _unbound_job_bundle_verifier() -> None:
    _err("job report bundle is not pinned")


@dataclass(frozen=True)
class PinnedJobReportBundle:
    bundle: PublishedReportBundle
    report_rels: tuple[str, str, str, str]
    _verifier: Callable[[], None] = field(
        default=_unbound_job_bundle_verifier,
        repr=False,
        compare=False,
    )

    def verify_lexical_identity(self) -> None:
        self._verifier()


@dataclass(frozen=True)
class _ParsedJobReportPaths:
    analysis_child: str
    bundle_name: str
    full_rels: tuple[str, str, str, str]
    direct_rels: tuple[str, str, str, str]


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
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda value: _err(f"invalid non-finite JSON value: {value}"),
        )
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
    parts = candidate.parts
    if (
        not value
        or len(value) > _MAX_PATH_CHARACTERS
        or len(parts) > _MAX_PATH_SEGMENTS
        or candidate.is_absolute()
        or value.startswith(("/", "\\"))
        or "\\" in value
        or ":" in value
        or value != candidate.as_posix()
        or any(part in ("", ".", "..") for part in parts)
        or any(part.endswith((".", " ")) for part in parts)
        or any(part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_SEGMENTS for part in parts)
    ):
        _err("report bundle contains an unsafe relative path")
    return candidate


def _safe_job_id(value: object) -> str:
    if not isinstance(value, str) or _JOB_ID_PATTERN.fullmatch(value) is None:
        _err("structured report job id is unsafe")
    if (
        value.endswith((".", " "))
        or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_SEGMENTS
    ):
        _err("structured report job id is unsafe")
    return value


def _parse_job_report_paths(
    *,
    report_rel: object,
    report_view_rel: object,
    manifest_rel: object,
    checksums_rel: object,
) -> _ParsedJobReportPaths:
    values = (report_rel, report_view_rel, manifest_rel, checksums_rel)
    if any(not isinstance(value, str) for value in values):
        _err("structured report paths must be strings")
    strings = cast(tuple[str, str, str, str], values)
    if len(set(strings)) != 4 or len({value.casefold() for value in strings}) != 4:
        _err("structured report paths must be distinct")

    expected_names = (
        _REPORT_FILENAME,
        REPORT_VIEW_FILENAME,
        REPORT_MANIFEST_FILENAME,
        REPORT_CHECKSUMS_FILENAME,
    )
    parsed = tuple(_safe_relative_path(value) for value in strings)
    for path, expected_name in zip(parsed, expected_names):
        if (
            len(path.parts) != 4
            or path.parts[0] != "out"
            or path.parts[3] != expected_name
        ):
            _err("structured report path is not canonical")

    children = {path.parts[1] for path in parsed}
    bundle_names = {path.parts[2] for path in parsed}
    if len(children) != 1 or len(bundle_names) != 1:
        _err("structured report paths must share one analysis bundle")
    child = next(iter(children))
    if len(_safe_relative_path(child).parts) != 1:
        _err("structured report analysis child is unsafe")
    bundle_name = next(iter(bundle_names))
    if _PUBLISHED_ROOT_PATTERN.fullmatch(bundle_name) is None:
        _err("structured report bundle root is not canonical")

    full_rels = cast(
        tuple[str, str, str, str],
        tuple(path.as_posix() for path in parsed),
    )
    direct_rels = cast(
        tuple[str, str, str, str],
        tuple(PurePosixPath(bundle_name, name).as_posix() for name in expected_names),
    )
    return _ParsedJobReportPaths(child, bundle_name, full_rels, direct_rels)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return path.is_symlink()
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if int(getattr(info, "st_file_attributes", 0)) & reparse_attribute:
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _assert_exact_child_name(parent: Path, name: str) -> None:
    """Reject case and Win32-normalization aliases before any resolution."""
    try:
        with os.scandir(parent) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > _MAX_DIRECTORY_ENTRIES:
                    _err("report bundle directory exceeds the traversal limit")
                if entry.name == name:
                    return
            _err("report bundle path does not use the stored canonical name")
    except ReportArtifactValidationError:
        raise
    except OSError as exc:
        raise ReportArtifactValidationError("report bundle path cannot be enumerated") from exc


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
        _assert_exact_child_name(candidate, part)
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


@dataclass(frozen=True)
class _ScannedEntry:
    name: str
    mode: int
    reparse: bool


@dataclass(frozen=True)
class _ReadArtifact:
    path: Path
    size: int
    digest: str
    identity: _FileIdentity
    raw: bytes | None


def _entry_is_reparse(info: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(int(getattr(info, "st_file_attributes", 0)) & attribute)


if os.name == "nt":

    def _win_open(path: Path, *, directory: bool) -> int:
        access = (
            _WIN_FILE_LIST_DIRECTORY
            | _WIN_FILE_TRAVERSE
            | _WIN_FILE_READ_ATTRIBUTES
            | _WIN_SYNCHRONIZE
            if directory
            else _WIN_GENERIC_READ
        )
        handle = _WIN_CREATE_FILE(
            str(path),
            access,
            _WIN_SHARE_READ_WRITE,
            None,
            _WIN_OPEN_EXISTING,
            _WIN_FLAG_OPEN_REPARSE_POINT | _WIN_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == _WIN_INVALID_HANDLE:
            raise OSError(ctypes.get_last_error(), f"cannot open {path}")
        return int(handle)


    def _win_iter_directory(handle: int) -> Iterator[_ScannedEntry]:
        buffer = (ctypes.c_longlong * (_WIN_DIRECTORY_INFO_BYTES // 8))()
        information_class = _WIN_FILE_FULL_DIRECTORY_RESTART_INFO
        while True:
            ctypes.set_last_error(0)
            if not _WIN_GET_FILE_INFO_EX(
                handle,
                information_class,
                ctypes.byref(buffer),
                ctypes.sizeof(buffer),
            ):
                error = ctypes.get_last_error()
                if error == _WIN_ERROR_NO_MORE_FILES:
                    return
                raise OSError(error, "cannot enumerate open directory handle")
            information_class = _WIN_FILE_FULL_DIRECTORY_INFO
            base = ctypes.addressof(buffer)
            offset = 0
            while True:
                next_offset = wintypes.ULONG.from_address(base + offset).value
                attributes = wintypes.ULONG.from_address(base + offset + 56).value
                name_bytes = wintypes.ULONG.from_address(base + offset + 60).value
                name_start = offset + _WIN_DIRECTORY_NAME_OFFSET
                name_end = name_start + name_bytes
                if (
                    name_bytes % ctypes.sizeof(ctypes.c_wchar)
                    or name_end > ctypes.sizeof(buffer)
                ):
                    raise OSError("open directory returned malformed entry data")
                name = ctypes.wstring_at(
                    base + name_start,
                    name_bytes // ctypes.sizeof(ctypes.c_wchar),
                )
                if name not in (".", ".."):
                    mode = (
                        stat.S_IFDIR
                        if attributes & _WIN_ATTR_DIRECTORY
                        else stat.S_IFREG
                    )
                    yield _ScannedEntry(
                        name,
                        mode,
                        bool(attributes & _WIN_ATTR_REPARSE_POINT),
                    )
                if next_offset == 0:
                    break
                if (
                    next_offset % 8
                    or next_offset < _WIN_DIRECTORY_NAME_OFFSET
                    or offset + next_offset >= ctypes.sizeof(buffer)
                ):
                    raise OSError("open directory returned malformed entry offsets")
                offset += next_offset


    def _win_scan_directory(handle: int, *, limit: int) -> tuple[_ScannedEntry, ...]:
        if limit <= 0:
            raise OSError("directory traversal limit must be positive")
        rows: list[_ScannedEntry] = []
        for entry in _win_iter_directory(handle):
            if len(rows) >= limit:
                _err("report bundle contains too many filesystem entries")
            rows.append(entry)
        return tuple(rows)


    def _win_has_exact_child(handle: int, name: str) -> bool:
        # An exact lookup cannot share the capped bundle-topology scan: a sessions
        # directory may legitimately outgrow that cap. Streaming to a match, or to
        # the native end-of-directory marker for absence, keeps memory usage fixed.
        return any(entry.name == name for entry in _win_iter_directory(handle))


    def _win_open_relative(
        parent_handle: int,
        name: str,
        *,
        directory: bool,
    ) -> int:
        if (
            not isinstance(name, str)
            or not name
            or name in (".", "..")
            or "/" in name
            or "\\" in name
        ):
            raise OSError("relative Windows child name is invalid")
        if not _win_has_exact_child(parent_handle, name):
            raise FileNotFoundError(f"exact Windows child name does not exist: {name}")

        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name = _NtUnicodeString(
            ctypes.sizeof(name_buffer) - ctypes.sizeof(ctypes.c_wchar),
            ctypes.sizeof(name_buffer),
            ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        attributes = _NtObjectAttributes(
            ctypes.sizeof(_NtObjectAttributes),
            wintypes.HANDLE(parent_handle),
            ctypes.pointer(unicode_name),
            0,
            None,
            None,
        )
        io_status = _NtIoStatusBlock()
        result = wintypes.HANDLE()
        access = (
            _WIN_FILE_LIST_DIRECTORY
            | _WIN_FILE_TRAVERSE
            | _WIN_FILE_READ_ATTRIBUTES
            | _WIN_SYNCHRONIZE
            if directory
            else _WIN_FILE_READ_DATA
            | _WIN_FILE_READ_ATTRIBUTES
            | _WIN_SYNCHRONIZE
        )
        options = (
            _WIN_FILE_DIRECTORY_FILE if directory else _WIN_FILE_NON_DIRECTORY_FILE
        ) | _WIN_FILE_SYNCHRONOUS_IO_NONALERT | _WIN_FILE_OPEN_REPARSE_POINT
        status = int(
            _WIN_NT_OPEN_FILE(
                ctypes.byref(result),
                access,
                ctypes.byref(attributes),
                ctypes.byref(io_status),
                _WIN_SHARE_READ_WRITE,
                options,
            )
        )
        if status != 0:
            if result.value not in (None, _WIN_INVALID_HANDLE):
                _win_close(int(result.value))
            error = int(_WIN_NTSTATUS_TO_DOS_ERROR(status))
            raise OSError(error, f"cannot open relative Windows child: {name}")
        if result.value in (None, _WIN_INVALID_HANDLE):
            raise OSError("relative Windows open returned an invalid handle")
        handle = int(result.value)
        try:
            if _win_final_path(handle).name != name:
                raise OSError("relative Windows child name is not exact")
        except Exception:
            _win_close(handle)
            raise
        return handle


    def _win_close(handle: int) -> None:
        _WIN_CLOSE_HANDLE(handle)


    def _win_info(handle: int) -> _WinByHandleFileInformation:
        info = _WinByHandleFileInformation()
        if not _WIN_GET_FILE_INFO(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "cannot inspect open file handle")
        return info


    def _win_final_path(handle: int) -> Path:
        size = 32768
        buffer = ctypes.create_unicode_buffer(size)
        length = _WIN_GET_FINAL_PATH(handle, buffer, size, 0)
        if not length or length >= size:
            raise OSError(ctypes.get_last_error(), "cannot resolve open file handle")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)


    def _win_identity(info: _WinByHandleFileInformation) -> tuple[int, int]:
        return (
            int(info.dwVolumeSerialNumber),
            (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        )


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(root))))
    except ValueError:
        return False
    return common == os.path.normcase(str(root))


def _absolute_lexical(path: object, *, label: str) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError, OSError) as exc:
        raise ReportArtifactValidationError(f"{label} is invalid") from exc


def _posix_directory_flags() -> int:
    directory = getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
    supports_follow = getattr(os, "supports_follow_symlinks", frozenset())
    if (
        not directory
        or not no_follow
        or os.open not in supports_dir_fd
        or os.stat not in supports_dir_fd
        or os.stat not in supports_follow
    ):
        _err("POSIX no-follow descriptor traversal is unavailable")
    return os.O_RDONLY | int(directory) | int(no_follow)


def _directory_stat_identity(info: os.stat_result, *, label: str) -> tuple[int, int]:
    if stat.S_ISLNK(info.st_mode) or _entry_is_reparse(info):
        _err(f"{label} cannot be a link or reparse point")
    if not stat.S_ISDIR(info.st_mode):
        _err(f"{label} must be a directory")
    return int(info.st_dev), int(info.st_ino)


def _close_directory_handle(handle: int) -> None:
    if os.name == "nt":
        _win_close(handle)
    else:
        os.close(handle)


def _open_pinned_directory(
    path: Path,
    *,
    parent_handle: int | None,
    name: str | None,
    label: str,
) -> tuple[int, tuple[int, int]]:
    handle: int | None = None
    try:
        if os.name == "nt":
            lexical_inode: int | None = None
            if parent_handle is None:
                lexical = os.lstat(path)
                _directory_stat_identity(lexical, label=label)
                lexical_inode = int(lexical.st_ino)
                handle = _win_open(path, directory=True)
            else:
                if name is None or path.name != name:
                    _err(f"{label} path is not canonical")
                handle = _win_open_relative(
                    parent_handle,
                    name,
                    directory=True,
                )
            opened = _win_info(handle)
            if opened.dwFileAttributes & _WIN_ATTR_REPARSE_POINT:
                _err(f"{label} cannot be a reparse point")
            if not opened.dwFileAttributes & _WIN_ATTR_DIRECTORY:
                _err(f"{label} must be a directory")
            identity = _win_identity(opened)
            if lexical_inode is not None and lexical_inode != identity[1]:
                _err(f"{label} changed while it was pinned")
            final = _win_final_path(handle)
            if os.path.normcase(os.path.abspath(str(final))) != os.path.normcase(
                os.path.abspath(str(path))
            ):
                _err(f"{label} handle resolved to an unexpected path")
            return handle, identity

        flags = _posix_directory_flags()
        if parent_handle is None:
            lexical = os.stat(path, follow_symlinks=False)
            handle = os.open(path, flags)
        else:
            if name is None:
                _err(f"{label} child name is missing")
            lexical = os.stat(
                name,
                dir_fd=parent_handle,
                follow_symlinks=False,
            )
            handle = os.open(name, flags, dir_fd=parent_handle)
        lexical_identity = _directory_stat_identity(lexical, label=label)
        opened = os.fstat(handle)
        opened_identity = _directory_stat_identity(opened, label=label)
        if lexical_identity != opened_identity:
            _err(f"{label} changed while it was pinned")
        return handle, opened_identity
    except ReportArtifactValidationError:
        if handle is not None:
            _close_directory_handle(handle)
        raise
    except OSError as exc:
        if handle is not None:
            _close_directory_handle(handle)
        raise ReportArtifactValidationError(f"{label} cannot be pinned") from exc


@dataclass(frozen=True)
class _PinnedDirectoryEntry:
    path: Path
    name: str | None
    handle: int
    identity: tuple[int, int]


class _PinnedJobDirectoryChain:
    """Own the read-only sessions/job/out/analysis handle chain."""

    def __init__(self, sessions_dir: Path, *, job_id: object, analysis_child: str):
        self.sessions_path = _absolute_lexical(sessions_dir, label="sessions root")
        self.job_id = _safe_job_id(job_id)
        child_path = _safe_relative_path(analysis_child)
        if len(child_path.parts) != 1:
            _err("structured report analysis child is unsafe")
        self.analysis_child = analysis_child
        self._entries: list[_PinnedDirectoryEntry] = []

    @property
    def analysis_path(self) -> Path:
        if len(self._entries) != 4:
            _err("structured report analysis directory is not pinned")
        return self._entries[-1].path

    @property
    def analysis_handle(self) -> int:
        if len(self._entries) != 4:
            _err("structured report analysis directory is not pinned")
        return self._entries[-1].handle

    def __enter__(self) -> _PinnedJobDirectoryChain:
        paths = (
            (self.sessions_path, None, "sessions root"),
            (self.sessions_path / self.job_id, self.job_id, "structured job root"),
            (
                self.sessions_path / self.job_id / "out",
                "out",
                "structured job out root",
            ),
            (
                self.sessions_path / self.job_id / "out" / self.analysis_child,
                self.analysis_child,
                "structured analysis child",
            ),
        )
        try:
            for path, name, label in paths:
                parent_handle = self._entries[-1].handle if self._entries else None
                handle, identity = _open_pinned_directory(
                    path,
                    parent_handle=parent_handle,
                    name=name,
                    label=label,
                )
                self._entries.append(
                    _PinnedDirectoryEntry(path, name, handle, identity)
                )
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        while self._entries:
            entry = self._entries.pop()
            try:
                _close_directory_handle(entry.handle)
            except OSError:  # cleanup cannot invalidate a committed publication
                pass

    def verify_lexical_identity(self) -> None:
        if len(self._entries) != 4:
            _err("structured report directory chain is not pinned")
        for index, entry in enumerate(self._entries):
            parent_handle = self._entries[index - 1].handle if index else None
            verification_handle: int | None = None
            try:
                verification_handle, lexical_identity = _open_pinned_directory(
                    entry.path,
                    parent_handle=parent_handle,
                    name=entry.name,
                    label="published structured directory",
                )
                if os.name == "nt":
                    pinned_identity = _win_identity(_win_info(entry.handle))
                else:
                    pinned_identity = _directory_stat_identity(
                        os.fstat(entry.handle),
                        label="published structured directory",
                    )
                if (
                    lexical_identity != entry.identity
                    or pinned_identity != entry.identity
                ):
                    _err("published structured directory lexical identity changed")
            finally:
                if verification_handle is not None:
                    _close_directory_handle(verification_handle)


class _PinnedBundleRoot:
    """Keep the root and each traversed ancestor open while a file is consumed."""

    def __init__(
        self,
        root: Path,
        *,
        parent_handle: int | None = None,
        root_name: str | None = None,
    ):
        self.path = _absolute_lexical(root, label="report bundle root")
        self._parent_handle = parent_handle
        self._root_name = root_name
        self._root_handle: int | None = None
        self._root_identity: tuple[int, int] | None = None

    def __enter__(self) -> _PinnedBundleRoot:
        try:
            handle, identity = _open_pinned_directory(
                self.path,
                parent_handle=self._parent_handle,
                name=self._root_name,
                label="report bundle root",
            )
            self._root_handle = handle
            self._root_identity = identity
        except ReportArtifactValidationError:
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._root_handle is None:
            return
        handle = self._root_handle
        try:
            try:
                _close_directory_handle(handle)
            except OSError:  # cleanup cannot invalidate a committed publication
                pass
        finally:
            self._root_handle = None
            self._root_identity = None

    def verify_lexical_identity(self) -> None:
        """Confirm the pinned directory still owns its lexical bundle path."""
        if self._root_handle is None or self._root_identity is None:
            _err("report bundle root is not pinned")
        lexical_handle: int | None = None
        try:
            lexical_handle, lexical_identity = _open_pinned_directory(
                self.path,
                parent_handle=self._parent_handle,
                name=self._root_name,
                label="published report bundle root",
            )
            if os.name == "nt":
                pinned_identity = _win_identity(_win_info(self._root_handle))
            else:
                pinned_identity = _directory_stat_identity(
                    os.fstat(self._root_handle),
                    label="published report bundle root",
                )
            if (
                lexical_identity != self._root_identity
                or pinned_identity != self._root_identity
            ):
                _err("published report bundle lexical identity changed")
        except ReportArtifactValidationError:
            raise
        except OSError as exc:
            raise ReportArtifactValidationError(
                "published report bundle lexical identity cannot be verified"
            ) from exc
        finally:
            if lexical_handle is not None:
                _close_directory_handle(lexical_handle)

    @contextmanager
    def _open_directory(self, relative: PurePosixPath | None) -> Iterator[tuple[int, Path]]:
        if self._root_handle is None:
            _err("report bundle root is not pinned")
        parts = () if relative is None else relative.parts
        handles: list[int] = []
        current_path = self.path
        current_handle = self._root_handle
        try:
            for part in parts:
                current_path = current_path / part
                if os.name == "nt":
                    child = _win_open_relative(
                        current_handle,
                        part,
                        directory=True,
                    )
                    handles.append(child)
                    info = _win_info(child)
                    if info.dwFileAttributes & _WIN_ATTR_REPARSE_POINT:
                        _err("report bundle directory cannot be a reparse point")
                    if not info.dwFileAttributes & _WIN_ATTR_DIRECTORY:
                        _err("report bundle path expected a directory")
                    final = _win_final_path(child)
                    if not _path_is_under(final, self.path):
                        _err("report bundle directory handle escaped its root")
                else:
                    _assert_exact_child_name(current_path.parent, part)
                    flags = _posix_directory_flags()
                    child = os.open(part, flags, dir_fd=current_handle)
                    handles.append(child)
                    if not stat.S_ISDIR(os.fstat(child).st_mode):
                        _err("report bundle path expected a directory")
                current_handle = child
            yield current_handle, current_path
        except ReportArtifactValidationError:
            raise
        except OSError as exc:
            raise ReportArtifactValidationError("report bundle directory cannot be opened safely") from exc
        finally:
            for handle in reversed(handles):
                if os.name == "nt":
                    _win_close(handle)
                else:
                    os.close(handle)

    @contextmanager
    def open_file(self, relative: PurePosixPath) -> Iterator[tuple[BinaryIO, Path]]:
        if _safe_relative_path(relative.as_posix()) != relative:
            _err("declared report artifact path is not canonical")
        # Path inspection remains defense in depth only. The component opens
        # below are authoritative and stay relative to pinned parent handles.
        _join_under(self.path, relative)
        parent_relative = (
            PurePosixPath(*relative.parts[:-1]) if len(relative.parts) > 1 else None
        )
        with self._open_directory(parent_relative) as (parent_handle, parent_path):
            name = relative.parts[-1]
            if os.name != "nt":
                _assert_exact_child_name(parent_path, name)
            path = parent_path / name
            raw_handle: int | None = None
            descriptor: int | None = None
            try:
                if os.name == "nt":
                    raw_handle = _win_open_relative(
                        parent_handle,
                        name,
                        directory=False,
                    )
                    info = _win_info(raw_handle)
                    if info.dwFileAttributes & _WIN_ATTR_REPARSE_POINT:
                        _err("declared report artifact cannot be a reparse point")
                    if info.dwFileAttributes & _WIN_ATTR_DIRECTORY:
                        _err("declared report artifact must be a regular file")
                    final = _win_final_path(raw_handle)
                    if not _path_is_under(final, self.path):
                        _err("declared report artifact handle escaped its root")
                    descriptor = msvcrt.open_osfhandle(
                        raw_handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                    )
                    raw_handle = None
                else:
                    _posix_directory_flags()
                    flags = os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0))
                    descriptor = os.open(name, flags, dir_fd=parent_handle)
                handle = os.fdopen(descriptor, "rb")
                descriptor = None
                try:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or _entry_is_reparse(info):
                        _err("declared report artifact must be a regular file")
                    yield handle, path
                finally:
                    handle.close()
            except ReportArtifactValidationError:
                raise
            except OSError as exc:
                raise ReportArtifactValidationError(
                    "declared report artifact cannot be opened safely"
                ) from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if raw_handle is not None:
                    _win_close(raw_handle)

    def scan_directory(
        self, relative: PurePosixPath | None, *, limit: int
    ) -> tuple[_ScannedEntry, ...]:
        rows: list[_ScannedEntry] = []
        with self._open_directory(relative) as (handle, path):
            try:
                if os.name == "nt":
                    return _win_scan_directory(handle, limit=limit)
                with os.scandir(handle) as entries:
                    for entry in entries:
                        if len(rows) >= limit:
                            _err("report bundle contains too many filesystem entries")
                        info = entry.stat(follow_symlinks=False)
                        rows.append(
                            _ScannedEntry(
                                entry.name,
                                info.st_mode,
                                entry.is_symlink() or _entry_is_reparse(info),
                            )
                        )
            except ReportArtifactValidationError:
                raise
            except OSError as exc:
                raise ReportArtifactValidationError(
                    "report bundle cannot be enumerated safely"
                ) from exc
        return tuple(rows)


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
    with _PinnedBundleRoot(root) as pinned:
        result = _read_and_hash_pinned(
            pinned,
            relative,
            expected_size=expected_size,
        )
    return result.path, result.size, result.digest, result.identity


def _read_and_hash_pinned(
    pinned: _PinnedBundleRoot,
    relative: PurePosixPath,
    *,
    expected_size: int | None = None,
    capture_limit: int | None = None,
    label: str = "declared report artifact",
) -> _ReadArtifact:
    digest = hashlib.sha256()
    total = 0
    captured: list[bytes] | None = [] if capture_limit is not None else None
    with pinned.open_file(relative) as (handle, path):
        before = _identity(os.fstat(handle.fileno()))
        if before.size > MAX_REPORT_ARTIFACT_BYTES:
            _err("declared report artifact exceeds maximum size")
        if expected_size is not None and before.size != expected_size:
            _err("declared report artifact size does not match checksums")
        if capture_limit is not None and before.size > capture_limit:
            _err(f"{label} exceeds maximum size")
        try:
            while True:
                chunk = handle.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_REPORT_ARTIFACT_BYTES:
                    _err("declared report artifact exceeds maximum size")
                if expected_size is not None and total > expected_size:
                    _err("declared report artifact exceeds its declared size")
                if capture_limit is not None and total > capture_limit:
                    _err(f"{label} exceeds maximum size")
                digest.update(chunk)
                if captured is not None:
                    captured.append(chunk)
            after = _identity(os.fstat(handle.fileno()))
        except ReportArtifactValidationError:
            raise
        except OSError as exc:
            raise ReportArtifactValidationError(f"{label} cannot be read") from exc
    if before != after or total != before.size:
        _err(f"{label} changed while it was read")
    if expected_size is not None and total != expected_size:
        _err("declared report artifact size does not match checksums")
    return _ReadArtifact(
        path,
        total,
        digest.hexdigest(),
        after,
        b"".join(captured) if captured is not None else None,
    )


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
    return _parse_report_manifest(raw)


def _parse_report_manifest(raw: bytes) -> ReportBundleManifest:
    payload = _decode_json(raw, label="report manifest")
    manifest = report_manifest_from_dict(payload)
    if raw != _canonical_json(report_manifest_to_dict(manifest)):
        _err("report manifest is not canonical JSON")
    return manifest


def load_report_checksums(path: Path) -> ReportBundleChecksums:
    raw = _read_bounded_path(path, limit=MAX_REPORT_CHECKSUMS_BYTES, label="report checksums")
    return _parse_report_checksums(raw)


def _parse_report_checksums(raw: bytes) -> ReportBundleChecksums:
    payload = _decode_json(raw, label="report checksums")
    checksums = report_checksums_from_dict(payload)
    if raw != _canonical_json(report_checksums_to_dict(checksums)):
        _err("report checksums are not canonical JSON")
    return checksums


def _validate_expected_topology(
    pinned: _PinnedBundleRoot, expected_files: set[str]
) -> None:
    """Traverse only the expected graph and stop at the first extra entry."""
    tree: dict[str, object] = {}
    expected_directories = 0
    for value in sorted(expected_files):
        relative = _safe_relative_path(value)
        node = tree
        for index, part in enumerate(relative.parts):
            final = index == len(relative.parts) - 1
            existing = node.get(part)
            if final:
                if isinstance(existing, dict):
                    _err("report bundle topology uses one path as both file and directory")
                node[part] = None
            else:
                if existing is None and part in node:
                    _err("report bundle topology uses one path as both file and directory")
                if existing is None:
                    existing = {}
                    node[part] = existing
                    expected_directories += 1
                node = cast(dict[str, object], existing)

    maximum_entries = len(expected_files) + expected_directories
    visited = 0

    def walk(node: dict[str, object], relative: PurePosixPath | None) -> None:
        nonlocal visited
        remaining = maximum_entries - visited + 1
        entries = pinned.scan_directory(relative, limit=remaining)
        for entry in entries:
            visited += 1
            if visited > maximum_entries:
                _err("report bundle contains too many filesystem entries")
            if entry.reparse:
                _err("links and reparse points are not allowed in report bundles")
            expected = node.get(entry.name, ...)
            if expected is ...:
                _err("report bundle contains an unexpected filesystem entry")
            if isinstance(expected, dict):
                if not stat.S_ISDIR(entry.mode):
                    _err("report bundle expected a directory")
            elif not stat.S_ISREG(entry.mode):
                _err("report bundle expected a regular file")
        actual_names = {entry.name for entry in entries}
        if actual_names != set(node):
            _err("report bundle is missing an expected filesystem entry")
        for name, child in node.items():
            if isinstance(child, dict):
                child_relative = (
                    PurePosixPath(name)
                    if relative is None
                    else PurePosixPath(*relative.parts, name)
                )
                walk(child, child_relative)

    walk(tree, None)


def _parse_report_view(raw: bytes) -> ReportViewV1:
    payload = _decode_json(raw, label="report view")
    try:
        view = report_view_from_dict(payload)
    except UnsupportedReportViewVersion as exc:
        raise ReportArtifactValidationError(
            "unsupported report view version"
        ) from exc
    except ReportViewValidationError as exc:
        raise ReportArtifactValidationError("report view failed schema validation") from exc
    try:
        canonical = _canonical_json(report_view_to_dict(view))
    except ReportViewValidationError as exc:
        raise ReportArtifactValidationError("report view failed canonical encoding") from exc
    if raw != canonical:
        _err("report view is not canonical JSON")
    return view


def _load_view_from_validated_file(path: Path) -> ReportViewV1:
    raw = _read_bounded_path(path, limit=MAX_REPORT_VIEW_BYTES, label="report view")
    return _parse_report_view(raw)


def _validate_report_html(
    raw: bytes, *, presentation_version: str, outcome: ReportOutcome
) -> None:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReportArtifactValidationError("report HTML must be UTF-8") from exc
    expected = {
        "caddieinsight-report-format": _REPORT_HTML_FORMAT,
        "caddieinsight-report-presentation": presentation_version,
        "caddieinsight-report-outcome": outcome.value,
    }
    header = raw[:_REPORT_HEADER_BYTES]
    folded = raw.lower()
    for name, expected_value in expected.items():
        name_bytes = name.encode("ascii")
        marker = f'name="{name}" content="{expected_value}"'.encode("ascii")
        if folded.count(name_bytes) != 1 or header.count(marker) != 1:
            _err(
                f"report HTML {name} marker is late, noncanonical, duplicated, or inconsistent"
            )


def _validate_metrics(raw: bytes, *, view: ReportViewV1) -> None:
    payload = _expect_object(_decode_json(raw, label="metrics"), label="metrics")
    required = {
        "generator",
        "video",
        "swings",
        "session_stats",
        "session_notes",
        "disclaimer",
    }
    if set(payload) not in (required, required | {"meta"}):
        _err("metrics fields are invalid")
    generator = _expect_object(payload["generator"], label="metrics generator")
    _expect_keys(generator, {"name", "swinglab_version"}, label="metrics generator")
    _expect_nonempty_string(generator["name"], label="metrics generator name")
    _expect_nonempty_string(
        generator["swinglab_version"], label="metrics generator version"
    )
    video = _expect_object(payload["video"], label="metrics video")
    _expect_keys(
        video,
        {
            "path",
            "duration_s",
            "width",
            "height",
            "fps",
            "rotation",
            "creation_time",
        },
        label="metrics video",
    )
    _expect_nonempty_string(video["path"], label="metrics video path")
    for field in ("duration_s", "fps"):
        value = video[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            _err(f"metrics video {field} must be a nonnegative finite number")
    for field in ("width", "height"):
        value = video[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            _err(f"metrics video {field} must be a positive integer")
    rotation = video["rotation"]
    if isinstance(rotation, bool) or not isinstance(rotation, int):
        _err("metrics video rotation must be an integer")
    creation_time = video["creation_time"]
    if creation_time is not None and not isinstance(creation_time, str):
        _err("metrics video creation_time must be a string or null")
    if "meta" in payload and not isinstance(payload["meta"], dict):
        _err("metrics meta must be an object")
    swings = payload["swings"]
    if not isinstance(swings, list):
        _err("metrics swings must be an array")
    if not isinstance(payload["session_stats"], dict):
        _err("metrics session_stats must be an object")
    notes = payload["session_notes"]
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        _err("metrics session_notes must be an array of strings")
    if not isinstance(payload["disclaimer"], str):
        _err("metrics disclaimer must be a string")

    expected_roles = (
        {
            "strip": MediaRole.KEY_POSITIONS,
            "slowmo": MediaRole.SLOW_MOTION,
            "replay": MediaRole.COACH_REPLAY,
        }
        if isinstance(view, CoachingReportView)
        else {"slowmo": MediaRole.CAPTURE_PLAYBACK}
    )
    media_by_path = {entry.relative_path: entry for entry in view.media}
    for raw_swing in swings:
        swing = _expect_object(raw_swing, label="metrics swing")
        _expect_keys(swing, {"metrics", "notes", "deliverables"}, label="metrics swing")
        if not isinstance(swing["metrics"], dict):
            _err("metrics swing metrics must be an object")
        swing_notes = swing["notes"]
        if not isinstance(swing_notes, list):
            _err("metrics swing notes must be an array")
        deliverables = _expect_object(
            swing["deliverables"], label="metrics swing deliverables"
        )
        if "overlay" in deliverables:
            _err("guided metrics cannot declare overlay deliverables")
        if not set(deliverables).issubset(expected_roles):
            _err("metrics swing deliverable fields are invalid")
        for key, value in deliverables.items():
            relative = _safe_relative_path(
                _expect_nonempty_string(value, label="metrics deliverable path")
            ).as_posix()
            media = media_by_path.get(relative)
            if media is None or media.role != expected_roles[key]:
                _err("metrics deliverable does not match its report media role")


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


_ROLE_ENTITLEMENTS = {
    MediaRole.PRIORITY_EVIDENCE: Entitlement.CORE,
    MediaRole.DRILL_ILLUSTRATION: Entitlement.FREE,
    MediaRole.KEY_POSITIONS: Entitlement.CORE,
    MediaRole.SLOW_MOTION: Entitlement.CORE,
    MediaRole.COACH_REPLAY: Entitlement.PRO,
    MediaRole.VIDEO_POSTER: Entitlement.CORE,
    MediaRole.CAPTURE_PLAYBACK: Entitlement.CORE,
}
_IMAGE_ROLES = {
    MediaRole.PRIORITY_EVIDENCE,
    MediaRole.DRILL_ILLUSTRATION,
    MediaRole.KEY_POSITIONS,
    MediaRole.VIDEO_POSTER,
}
_VIDEO_ROLES = {
    MediaRole.SLOW_MOTION,
    MediaRole.COACH_REPLAY,
    MediaRole.CAPTURE_PLAYBACK,
}


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
        expected_entitlement = _ROLE_ENTITLEMENTS[media.role]
        if media.entitlement != expected_entitlement:
            _err("report view media role has the wrong entitlement")
        if artifact.required != (expected_entitlement == Entitlement.CORE):
            _err("manifest media required state conflicts with its role entitlement")
        if media.role in _IMAGE_ROLES and not media.mime_type.startswith("image/"):
            _err("report view image role has a non-image MIME type")
        if media.role in _VIDEO_ROLES and not media.mime_type.startswith("video/"):
            _err("report view video role has a non-video MIME type")

    optional_by_id: dict[OptionalSectionId, Any] = {}
    for section in view.optional_sections:
        if section.id in optional_by_id:
            _err("duplicate optional report section")
        optional_by_id[section.id] = section
        if section.id == OptionalSectionId.REPLAY and section.locked:
            if section.available or section.item_count != 0:
                _err("locked replay section cannot claim rendered items")
        elif section.locked:
            _err("only the replay section can be locked")
        elif section.available != (section.item_count > 0):
            _err("optional section availability must match its item count")

    def require_content_section(
        section_id: OptionalSectionId,
        capability_name: str,
        expected_count: int,
        *,
        label: str,
    ) -> Any:
        section = optional_by_id.get(section_id)
        expected_available = expected_count > 0
        if (
            section is None
            or section.locked
            or section.available != expected_available
            or section.item_count != expected_count
            or getattr(view.capabilities, capability_name) != expected_available
        ):
            _err(f"{label} section must match report content")
        return section

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
    replay_section = optional_by_id.get(OptionalSectionId.REPLAY)
    if view.capabilities.coach_replay != bool(replay_media):
        _err("coach-replay capability and rendered media must agree")
    if replay_media:
        if (
            replay_section is None
            or not replay_section.available
            or replay_section.locked
            or replay_section.item_count != len(replay_media)
        ):
            _err("coach-replay section must match rendered replay media")
    elif replay_section is not None and (
        replay_section.available or replay_section.item_count != 0
    ):
        _err("coach-replay section cannot claim absent media")

    key_positions = tuple(
        entry for entry in view.media if entry.role == MediaRole.KEY_POSITIONS
    )
    if isinstance(view, CoachingReportView):
        every_swing_section = require_content_section(
            OptionalSectionId.EVERY_SWING,
            "every_swing",
            view.context.detected_swings,
            label="every-swing",
        )
        if len(key_positions) > every_swing_section.item_count:
            _err("key-position media exceeds the available every-swing section")

    slow_motion = tuple(
        entry for entry in view.media if entry.role == MediaRole.SLOW_MOTION
    )
    if view.capabilities.slow_motion != bool(slow_motion):
        _err("slow-motion capability and rendered media must agree")

    drill_keys = {
        entry.key for entry in view.media if entry.role == MediaRole.DRILL_ILLUSTRATION
    }
    if isinstance(view, CoachingReportView):
        expected_drill_keys = (
            {view.practice.illustration_media_key}
            if view.practice.illustration_media_key is not None
            else set()
        )
        if drill_keys != expected_drill_keys:
            _err("practice illustration references must use drill-illustration media")
        if any(entry.role == MediaRole.CAPTURE_PLAYBACK for entry in view.media):
            _err("coaching reports cannot contain capture-playback media")
        measurement_count = sum(len(phase.measurements) for phase in view.phases)
        require_content_section(
            OptionalSectionId.ALTERNATIVE_DRILLS,
            "alternative_drills",
            len(view.practice.alternatives),
            label="alternative-drill",
        )
        require_content_section(
            OptionalSectionId.MEASUREMENTS,
            "measurements",
            measurement_count,
            label="measurement",
        )
        gear = optional_by_id.get(OptionalSectionId.GEAR)
        gear_available = bool(gear is not None and gear.available)
        if view.capabilities.gear != gear_available:
            _err("gear capability must match its optional section")
    elif drill_keys:
        _err("capture-only reports cannot contain drill illustrations")

    capture_keys = {
        entry.key for entry in view.media if entry.role == MediaRole.CAPTURE_PLAYBACK
    }
    if isinstance(view, CaptureOnlyReportView):
        if view.optional_sections:
            _err("capture-only reports cannot expose coaching optional sections")
        if any(
            (
                view.capabilities.focused_evidence,
                view.capabilities.every_swing,
                view.capabilities.slow_motion,
                view.capabilities.coach_replay,
                view.capabilities.measurements,
                view.capabilities.alternative_drills,
                view.capabilities.gear,
            )
        ):
            _err("capture-only reports cannot expose coaching capabilities")
        safe_media_keys = view.capture_guidance.safe_media_keys
        if len(safe_media_keys) != len(set(safe_media_keys)):
            _err("capture guidance contains duplicate safe media keys")
        if capture_keys != set(safe_media_keys):
            _err("capture guidance keys must identify capture-playback media")
        forbidden = {
            MediaRole.PRIORITY_EVIDENCE,
            MediaRole.KEY_POSITIONS,
            MediaRole.SLOW_MOTION,
            MediaRole.COACH_REPLAY,
        }
        if any(entry.role in forbidden for entry in view.media):
            _err("capture-only reports contain an incompatible media role")


def validate_staged_bundle(
    staging_dir: Path,
    *,
    manifest_rel: str,
    checksums_rel: str,
    _pinned_root: _PinnedBundleRoot | None = None,
) -> tuple[ReportBundleManifest, ReportBundleChecksums, ReportViewV1]:
    root = (
        _absolute_lexical(staging_dir, label="report bundle root")
        if _pinned_root is not None
        else _resolved_directory(staging_dir, label="report bundle root")
    )
    manifest_relative = _safe_relative_path(manifest_rel)
    checksums_relative = _safe_relative_path(checksums_rel)
    if manifest_relative.as_posix() != REPORT_MANIFEST_FILENAME:
        _err("report manifest must use its canonical bundle path")
    if checksums_relative.as_posix() != REPORT_CHECKSUMS_FILENAME:
        _err("report checksums must use their canonical bundle path")

    def validate_with_pinned(
        pinned: _PinnedBundleRoot,
    ) -> tuple[ReportBundleManifest, ReportBundleChecksums, ReportViewV1]:
        manifest_read = _read_and_hash_pinned(
            pinned,
            manifest_relative,
            capture_limit=MAX_REPORT_MANIFEST_BYTES,
            label="report manifest",
        )
        checksums_read = _read_and_hash_pinned(
            pinned,
            checksums_relative,
            capture_limit=MAX_REPORT_CHECKSUMS_BYTES,
            label="report checksums",
        )
        assert manifest_read.raw is not None and checksums_read.raw is not None
        manifest = _parse_report_manifest(manifest_read.raw)
        checksums = _parse_report_checksums(checksums_read.raw)
        _validate_manifest_relationships(manifest)
        if manifest.presentation_version != GUIDED_REPORT_PRESENTATION_VERSION:
            _err("unsupported report presentation version")

        declared_paths = {row.relative_path for row in manifest.artifacts}
        checksum_paths = {row.relative_path for row in checksums.files}
        expected_checksum_paths = declared_paths | {REPORT_MANIFEST_FILENAME}
        if checksum_paths != expected_checksum_paths:
            _err(
                "checksums must cover the manifest and every declared artifact exactly once"
            )
        if REPORT_CHECKSUMS_FILENAME in checksum_paths:
            _err("checksum file cannot include itself")

        expected_files = expected_checksum_paths | {REPORT_CHECKSUMS_FILENAME}
        _validate_expected_topology(pinned, expected_files)

        checksum_by_path = {row.relative_path: row for row in checksums.files}
        manifest_checksum = checksum_by_path[REPORT_MANIFEST_FILENAME]
        if (
            manifest_read.size != manifest_checksum.size_bytes
            or manifest_read.digest != manifest_checksum.sha256
        ):
            _err("report manifest does not match its checksum entry")
        if checksums.manifest_sha256 != manifest_read.digest:
            _err("manifest_sha256 does not match the report manifest")

        raw_by_path: dict[str, bytes] = {}
        capture_limits = {
            _REPORT_FILENAME: MAX_REPORT_HTML_BYTES,
            _METRICS_FILENAME: MAX_REPORT_METRICS_BYTES,
            REPORT_VIEW_FILENAME: MAX_REPORT_VIEW_BYTES,
        }
        for relative_path in sorted(expected_checksum_paths - {REPORT_MANIFEST_FILENAME}):
            checksum = checksum_by_path[relative_path]
            result = _read_and_hash_pinned(
                pinned,
                _safe_relative_path(relative_path),
                expected_size=checksum.size_bytes,
                capture_limit=capture_limits.get(relative_path),
                label={
                    _REPORT_FILENAME: "report HTML",
                    _METRICS_FILENAME: "metrics",
                    REPORT_VIEW_FILENAME: "report view",
                }.get(relative_path, "declared report artifact"),
            )
            if result.digest != checksum.sha256:
                _err("declared report artifact hash does not match checksums")
            if result.raw is not None:
                raw_by_path[relative_path] = result.raw

        view = _parse_report_view(raw_by_path[REPORT_VIEW_FILENAME])
        if view.presentation_version != manifest.presentation_version:
            _err("manifest and report view presentation versions differ")
        if view.outcome != manifest.outcome:
            _err("manifest and report view outcomes differ")
        _validate_report_html(
            raw_by_path[_REPORT_FILENAME],
            presentation_version=manifest.presentation_version,
            outcome=manifest.outcome,
        )
        _validate_metrics(
            raw_by_path[_METRICS_FILENAME],
            view=view,
        )
        _validate_media_relationships(view, manifest, checksum_by_path)
        return manifest, checksums, view

    if _pinned_root is not None:
        if _pinned_root.path != root or _pinned_root._root_handle is None:
            _err("provided pinned report root does not match the staging directory")
        return validate_with_pinned(_pinned_root)
    with _PinnedBundleRoot(root) as pinned:
        return validate_with_pinned(pinned)


def _load_published_bundle_from_pinned(
    pinned: _PinnedBundleRoot,
    *,
    paths: dict[str, Path],
    attempt_id: str,
) -> PublishedReportBundle:
    root = pinned.path
    manifest, checksums, view = validate_staged_bundle(
        root,
        manifest_rel=REPORT_MANIFEST_FILENAME,
        checksums_rel=REPORT_CHECKSUMS_FILENAME,
        _pinned_root=pinned,
    )
    if manifest.attempt_id != attempt_id:
        _err("published report bundle root does not match its manifest attempt")
    report_artifact = _single_kind(manifest, "report")
    view_artifact = _single_kind(manifest, "report_view")
    report_relative = _safe_relative_path(report_artifact.relative_path)
    view_relative = _safe_relative_path(view_artifact.relative_path)
    canonical_report = root.joinpath(*report_relative.parts)
    canonical_view = root.joinpath(*view_relative.parts)
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
        result = _read_and_hash_pinned(
            pinned,
            _safe_relative_path(media.relative_path),
            expected_size=checksum.size_bytes,
        )
        if result.digest != checksum.sha256 or result.digest != media.checksum_sha256:
            _err("published media changed during bundle loading")
        media_identities.append((media.key, result.identity))

    pinned.verify_lexical_identity()
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
    try:
        root_relative = root.relative_to(session_root)
    except ValueError as exc:  # pragma: no cover - _join_under already proves this
        raise ReportArtifactValidationError(
            "published report bundle root escapes the session"
        ) from exc
    if len(root_relative.parts) != 1:
        _err("published report bundle root must be a direct session child")
    root_match = _PUBLISHED_ROOT_PATTERN.fullmatch(root_relative.name)
    if root_match is None:
        _err("published report bundle root is not canonical")
    if paths["report_view"].name != REPORT_VIEW_FILENAME:
        _err("published report view path is not canonical")
    if paths["manifest"].name != REPORT_MANIFEST_FILENAME:
        _err("published manifest path is not canonical")
    if paths["checksums"].name != REPORT_CHECKSUMS_FILENAME:
        _err("published checksums path is not canonical")

    with _PinnedBundleRoot(root) as pinned:
        return _load_published_bundle_from_pinned(
            pinned,
            paths=paths,
            attempt_id=root_match.group(1),
        )


@contextmanager
def open_job_published_bundle(
    sessions_dir: Path,
    *,
    job_id: object,
    report_rel: object,
    report_view_rel: object,
    manifest_rel: object,
    checksums_rel: object,
) -> Iterator[PinnedJobReportBundle]:
    parsed = _parse_job_report_paths(
        report_rel=report_rel,
        report_view_rel=report_view_rel,
        manifest_rel=manifest_rel,
        checksums_rel=checksums_rel,
    )
    safe_job_id = _safe_job_id(job_id)
    root_match = _PUBLISHED_ROOT_PATTERN.fullmatch(parsed.bundle_name)
    if root_match is None:  # pragma: no cover - parser establishes this invariant
        _err("structured report bundle root is not canonical")

    with _PinnedJobDirectoryChain(
        sessions_dir,
        job_id=safe_job_id,
        analysis_child=parsed.analysis_child,
    ) as chain:
        root = chain.analysis_path / parsed.bundle_name
        with _PinnedBundleRoot(
            root,
            parent_handle=chain.analysis_handle,
            root_name=parsed.bundle_name,
        ) as pinned_root:
            filenames = (
                _REPORT_FILENAME,
                REPORT_VIEW_FILENAME,
                REPORT_MANIFEST_FILENAME,
                REPORT_CHECKSUMS_FILENAME,
            )
            paths = {
                name: root / filename
                for name, filename in zip(
                    ("report", "report_view", "manifest", "checksums"),
                    filenames,
                )
            }
            bundle = _load_published_bundle_from_pinned(
                pinned_root,
                paths=paths,
                attempt_id=root_match.group(1),
            )

            def verify() -> None:
                chain.verify_lexical_identity()
                pinned_root.verify_lexical_identity()

            pinned = PinnedJobReportBundle(bundle, parsed.full_rels, verify)
            pinned.verify_lexical_identity()
            yield pinned


def validate_persisted_report_policy(
    bundle: PublishedReportBundle,
    *,
    report_presentation_version: object,
    report_entitlements_json: object,
) -> ReportEntitlementSnapshot:
    """Bind one canonical persisted guided policy to one validated bundle."""

    if not isinstance(bundle, PublishedReportBundle):
        _err("expected PublishedReportBundle")
    if report_presentation_version != GUIDED_REPORT_PRESENTATION_VERSION:
        _err("structured report policy must use the guided presentation")
    if (
        bundle.manifest.presentation_version != report_presentation_version
        or bundle.view.presentation_version != report_presentation_version
    ):
        _err("persisted presentation does not match the report bundle")
    if not isinstance(report_entitlements_json, str):
        _err("structured report entitlement policy is missing")
    snapshot = report_entitlements_from_json(report_entitlements_json)

    replay_media = tuple(
        entry for entry in bundle.view.media if entry.role is MediaRole.COACH_REPLAY
    )
    replay_sections = tuple(
        section
        for section in bundle.view.optional_sections
        if section.id is OptionalSectionId.REPLAY
    )
    if isinstance(bundle.view, CaptureOnlyReportView):
        if replay_media or replay_sections or bundle.view.capabilities.coach_replay:
            _err("capture-only report cannot expose coach replay")
        return snapshot
    if len(replay_sections) != 1:
        _err("coaching report must declare one replay policy section")
    replay_section = replay_sections[0]
    if snapshot.coach_replay == "locked":
        if (
            not replay_section.locked
            or replay_section.available
            or replay_section.item_count != 0
            or replay_media
            or bundle.view.capabilities.coach_replay
        ):
            _err("locked coach replay policy disagrees with the report bundle")
    elif replay_section.locked:
        _err("unlocked coach replay policy cannot publish a locked section")
    elif snapshot.coach_replay == "disabled" and (
        replay_media
        or replay_section.available
        or replay_section.item_count != 0
        or bundle.view.capabilities.coach_replay
    ):
        _err("disabled coach replay policy cannot publish replay content")
    return snapshot


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
