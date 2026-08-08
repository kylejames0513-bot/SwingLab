"""Thin wrappers around the system ffmpeg / ffprobe binaries.

ffmpeg stays an external subprocess on purpose: it keeps licensing simple and
matches how the pipeline was proven. Rotation metadata in phone .mov files is
applied automatically by ffmpeg during any filter or frame extraction — never
rotate manually or frames come out double-rotated.

Failure taxonomy: ``FFmpegError`` remains the human-readable compatibility base
that existing CLI/web callers catch. The safe native analysis classifier
instead consumes the typed subclasses' ``.kind`` — and those kinds are decided
from the *call site*, process return/signaled status, timeout, and explicit
``OSError.errno`` only. We never parse command, stderr, or path text to decide
a kind, so nothing operator- or user-sensitive leaks into the native API.
"""

from __future__ import annotations

import errno as _errno
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    """Human-readable base kept for existing CLI/web error surfaces."""


_MEDIA_KINDS = frozenset({"decode_failed", "no_video_stream", "invalid_metadata"})
_RUNTIME_KINDS = frozenset(
    {"missing_binary", "process_start_failed", "process_timeout", "process_signaled"}
)
_STORAGE_KINDS = frozenset({"temporary_io", "disk_unavailable"})

# errno values that mean the volume itself is unusable (permanent for this
# attempt) versus transient conditions worth a bounded retry.
_DISK_UNAVAILABLE_ERRNOS = frozenset(
    number
    for number in (
        getattr(_errno, name, None)
        for name in ("ENOSPC", "EDQUOT", "EROFS", "EFBIG")
    )
    if number is not None
)
_TEMPORARY_IO_ERRNOS = frozenset(
    number
    for number in (
        getattr(_errno, name, None)
        for name in ("EIO", "EAGAIN", "EBUSY", "EINTR", "ETIMEDOUT", "ENFILE", "EMFILE")
    )
    if number is not None
)


class FFmpegMediaError(FFmpegError):
    """The input media could not be decoded / lacks a usable stream/metadata."""

    def __init__(self, message: str, *, kind: str) -> None:
        if kind not in _MEDIA_KINDS:
            raise ValueError(f"unknown media error kind: {kind!r}")
        super().__init__(message)
        self.kind = kind


class FFmpegRuntimeError(FFmpegError):
    """The ffmpeg binary/process itself could not run to completion."""

    def __init__(self, message: str, *, kind: str) -> None:
        if kind not in _RUNTIME_KINDS:
            raise ValueError(f"unknown runtime error kind: {kind!r}")
        super().__init__(message)
        self.kind = kind


class FFmpegStorageError(FFmpegError):
    """A filesystem/storage condition prevented ffmpeg from running."""

    def __init__(self, message: str, *, kind: str) -> None:
        if kind not in _STORAGE_KINDS:
            raise ValueError(f"unknown storage error kind: {kind!r}")
        super().__init__(message)
        self.kind = kind


def classify_os_error(exc: OSError) -> FFmpegError:
    """Map an ``OSError`` to a typed FFmpeg error purely by ``errno``.

    ``ENOENT`` is treated as a missing binary at process start; storage-full and
    transient-I/O errnos become the matching storage kinds; anything else is a
    generic process-start failure.
    """
    number = exc.errno
    if number == _errno.ENOENT:
        return FFmpegRuntimeError(str(exc), kind="missing_binary")
    if number in _DISK_UNAVAILABLE_ERRNOS:
        return FFmpegStorageError(str(exc), kind="disk_unavailable")
    if number in _TEMPORARY_IO_ERRNOS:
        return FFmpegStorageError(str(exc), kind="temporary_io")
    return FFmpegRuntimeError(str(exc), kind="process_start_failed")


def require_binaries() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise FFmpegRuntimeError(
            f"Required binaries not found on PATH: {', '.join(missing)}. "
            "Install ffmpeg (which provides both) and retry.",
            kind="missing_binary",
        )


def run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegRuntimeError(
            f"Command timed out after {timeout}s: {' '.join(cmd)}",
            kind="process_timeout",
        ) from exc
    except FileNotFoundError as exc:
        raise FFmpegRuntimeError(
            f"Command binary not found: {' '.join(cmd)}",
            kind="missing_binary",
        ) from exc
    except OSError as exc:
        raise classify_os_error(exc) from exc
    if proc.returncode < 0:
        # Negative return code == terminated by a signal (OOM killer, timeout
        # via SIGKILL, operator kill): a runtime condition, not bad media.
        raise FFmpegRuntimeError(
            f"Command terminated by signal {-proc.returncode}: {' '.join(cmd)}\n"
            f"{proc.stderr.strip()}",
            kind="process_signaled",
        )
    if proc.returncode != 0:
        # A clean nonzero exit from ffmpeg/ffprobe on a caller-provided file
        # means the media could not be processed — a decode failure from this
        # call site. We never read stderr to decide this.
        raise FFmpegMediaError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"{proc.stderr.strip()}",
            kind="decode_failed",
        )
    return proc


@dataclass
class VideoInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    fps: float
    rotation: int
    creation_time: str | None
    has_audio: bool

    @property
    def display_width(self) -> int:
        """Width after ffmpeg applies rotation metadata."""
        return self.height if self.rotation % 180 == 90 else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation % 180 == 90 else self.height


def probe(video: str | Path) -> VideoInfo:
    """ffprobe the input; rotation is read from metadata only (never applied here)."""
    video = Path(video)
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video),
        ]
    )
    try:
        info = json.loads(proc.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise FFmpegMediaError(
            f"{video}: could not parse ffprobe output", kind="invalid_metadata"
        ) from exc

    vstream = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if vstream is None:
        raise FFmpegMediaError(f"{video}: no video stream found", kind="no_video_stream")
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))

    rotation = 0
    for side in vstream.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(side["rotation"])
    if not rotation:
        rotation = int(vstream.get("tags", {}).get("rotate", 0) or 0)
    rotation %= 360

    num, _, den = (vstream.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0

    fmt = info.get("format", {})
    duration = float(fmt.get("duration") or vstream.get("duration") or 0.0)
    creation = fmt.get("tags", {}).get("creation_time") or vstream.get("tags", {}).get(
        "creation_time"
    )

    try:
        width = int(vstream["width"])
        height = int(vstream["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FFmpegMediaError(
            f"{video}: video stream is missing width/height", kind="invalid_metadata"
        ) from exc

    return VideoInfo(
        path=video,
        duration_s=duration,
        width=width,
        height=height,
        fps=fps,
        rotation=rotation,
        creation_time=creation,
        has_audio=has_audio,
    )
