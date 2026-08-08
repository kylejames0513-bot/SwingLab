"""Typed FFmpeg failure classification.

The safe native classifier consumes ``.kind`` off typed subclasses; existing
CLI/web callers keep the human-readable ``FFmpegError`` base. Classification is
by call site, return code, signaled status, timeout, and explicit ``errno`` —
never by parsing command/stderr/path text.
"""

from __future__ import annotations

import errno as errno_module
import subprocess
from pathlib import Path

import pytest

from swinglab import ffmpeg
from swinglab.ffmpeg import (
    FFmpegError,
    FFmpegMediaError,
    FFmpegRuntimeError,
    FFmpegStorageError,
    classify_os_error,
)


def test_typed_errors_share_ffmpeg_error_base() -> None:
    assert issubclass(FFmpegMediaError, FFmpegError)
    assert issubclass(FFmpegRuntimeError, FFmpegError)
    assert issubclass(FFmpegStorageError, FFmpegError)


def test_media_error_kinds_are_closed() -> None:
    for kind in ("decode_failed", "no_video_stream", "invalid_metadata"):
        assert FFmpegMediaError("boom", kind=kind).kind == kind
    with pytest.raises(ValueError):
        FFmpegMediaError("boom", kind="not_a_kind")


def test_runtime_error_kinds_are_closed() -> None:
    for kind in (
        "missing_binary",
        "process_start_failed",
        "process_timeout",
        "process_signaled",
    ):
        assert FFmpegRuntimeError("boom", kind=kind).kind == kind
    with pytest.raises(ValueError):
        FFmpegRuntimeError("boom", kind="nope")


def test_storage_error_kinds_are_closed() -> None:
    for kind in ("temporary_io", "disk_unavailable"):
        assert FFmpegStorageError("boom", kind=kind).kind == kind
    with pytest.raises(ValueError):
        FFmpegStorageError("boom", kind="nope")


def test_require_binaries_missing_raises_runtime_missing_binary(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    with pytest.raises(FFmpegRuntimeError) as info:
        ffmpeg.require_binaries()
    assert info.value.kind == "missing_binary"


def test_run_nonzero_returncode_is_media_decode_failed(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    with pytest.raises(FFmpegMediaError) as info:
        ffmpeg.run(["ffmpeg", "-i", "x"])
    assert info.value.kind == "decode_failed"


def test_run_signaled_returncode_is_process_signaled(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, -9, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    with pytest.raises(FFmpegRuntimeError) as info:
        ffmpeg.run(["ffmpeg"])
    assert info.value.kind == "process_signaled"


def test_run_timeout_is_process_timeout(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        raise subprocess.TimeoutExpired(cmd, timeout or 1)

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    with pytest.raises(FFmpegRuntimeError) as info:
        ffmpeg.run(["ffmpeg"], timeout=1)
    assert info.value.kind == "process_timeout"


def test_run_missing_binary_at_start_is_runtime(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        raise FileNotFoundError(errno_module.ENOENT, "no ffmpeg")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    with pytest.raises(FFmpegRuntimeError) as info:
        ffmpeg.run(["ffmpeg"])
    assert info.value.kind == "missing_binary"


def test_run_disk_full_at_start_is_storage(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        raise OSError(errno_module.ENOSPC, "no space")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    with pytest.raises(FFmpegStorageError) as info:
        ffmpeg.run(["ffmpeg"])
    assert info.value.kind == "disk_unavailable"


def test_run_transient_io_at_start_is_storage(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        raise OSError(errno_module.EIO, "io error")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    with pytest.raises(FFmpegStorageError) as info:
        ffmpeg.run(["ffmpeg"])
    assert info.value.kind == "temporary_io"


def test_run_generic_oserror_at_start_is_process_start_failed(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        raise OSError(errno_module.EACCES, "denied")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    with pytest.raises(FFmpegRuntimeError) as info:
        ffmpeg.run(["ffmpeg"])
    assert info.value.kind == "process_start_failed"


def test_classify_os_error_maps_errno() -> None:
    assert isinstance(
        classify_os_error(OSError(errno_module.ENOSPC, "x")), FFmpegStorageError
    )
    assert classify_os_error(OSError(errno_module.ENOSPC, "x")).kind == "disk_unavailable"
    assert classify_os_error(OSError(errno_module.EDQUOT, "x")).kind == "disk_unavailable"
    for transient in (errno_module.EIO, errno_module.EAGAIN, errno_module.EBUSY):
        assert classify_os_error(OSError(transient, "x")).kind == "temporary_io"


def test_error_messages_never_needed_for_classification() -> None:
    # A media error carries a safe kind independent of any embedded text.
    err = FFmpegMediaError("path=/secret/file.mov failed", kind="decode_failed")
    assert err.kind == "decode_failed"
    assert isinstance(err, FFmpegError)


@pytest.mark.skipif(
    __import__("shutil").which("ffprobe") is None, reason="ffprobe not installed"
)
def test_probe_missing_file_raises_media_error(tmp_path: Path) -> None:
    with pytest.raises(FFmpegMediaError):
        ffmpeg.probe(tmp_path / "does-not-exist.mp4")
