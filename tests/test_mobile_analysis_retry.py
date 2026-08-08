"""Typed analysis-failure classification and its closed native contract.

This module covers the pure classifier (exception -> closed ``AnalysisFailureCode``
plus retryability and a safe customer message). Route-level retry admission is
exercised by the resumable-upload suites; here we prove the mapping table, the
attempt-cap collapse to non-retryable, and the no-leak guarantee.
"""

from __future__ import annotations

import errno as errno_module

import pytest

from swinglab.api.contracts import AnalysisFailureCode
from swinglab.events import EventError
from swinglab.ffmpeg import (
    FFmpegError,
    FFmpegMediaError,
    FFmpegRuntimeError,
    FFmpegStorageError,
)
from swinglab.pipeline import VideoTooLongError, ZeroStrikesError
from swinglab.web.analysis_failures import (
    classify_analysis_failure,
    effective_retryable,
)


@pytest.mark.parametrize(
    "exc,expected_code,expected_retryable",
    [
        (VideoTooLongError("300s"), AnalysisFailureCode.video_too_long, False),
        (ZeroStrikesError("no strike"), AnalysisFailureCode.capture_no_strike, False),
        (EventError("bad pose"), AnalysisFailureCode.capture_pose_unusable, False),
        (
            FFmpegMediaError("x", kind="decode_failed"),
            AnalysisFailureCode.media_decode_failed,
            False,
        ),
        (
            FFmpegMediaError("x", kind="no_video_stream"),
            AnalysisFailureCode.media_decode_failed,
            False,
        ),
        (
            FFmpegMediaError("x", kind="invalid_metadata"),
            AnalysisFailureCode.media_decode_failed,
            False,
        ),
        (
            FFmpegRuntimeError("x", kind="missing_binary"),
            AnalysisFailureCode.analysis_runtime_unavailable,
            True,
        ),
        (
            FFmpegRuntimeError("x", kind="process_timeout"),
            AnalysisFailureCode.analysis_runtime_unavailable,
            True,
        ),
        (
            FFmpegRuntimeError("x", kind="process_signaled"),
            AnalysisFailureCode.analysis_runtime_unavailable,
            True,
        ),
        (
            FFmpegStorageError("x", kind="temporary_io"),
            AnalysisFailureCode.analysis_storage_unavailable,
            True,
        ),
        (
            FFmpegStorageError("x", kind="disk_unavailable"),
            AnalysisFailureCode.analysis_storage_unavailable,
            True,
        ),
        (
            OSError(errno_module.EIO, "io"),
            AnalysisFailureCode.analysis_storage_unavailable,
            True,
        ),
        (
            OSError(errno_module.ENOSPC, "full"),
            AnalysisFailureCode.analysis_storage_unavailable,
            True,
        ),
        (
            OSError(errno_module.EACCES, "denied"),
            AnalysisFailureCode.analysis_internal_error,
            True,
        ),
        (RuntimeError("surprise"), AnalysisFailureCode.analysis_internal_error, True),
        (ValueError("surprise"), AnalysisFailureCode.analysis_internal_error, True),
        (FFmpegError("bare base"), AnalysisFailureCode.analysis_internal_error, True),
    ],
)
def test_classifier_mapping(exc, expected_code, expected_retryable) -> None:
    classified = classify_analysis_failure(exc)
    assert classified.code is expected_code
    assert classified.retryable is expected_retryable


def test_interrupted_restart_is_not_classified_as_terminal() -> None:
    # KeyboardInterrupt / SystemExit represent process restarts, never a
    # terminal analysis failure. The classifier declines to own them.
    with pytest.raises(BaseException):
        classify_analysis_failure(KeyboardInterrupt())


def test_effective_retryable_collapses_at_cap() -> None:
    classified = classify_analysis_failure(RuntimeError("boom"))
    assert classified.retryable is True
    assert effective_retryable(classified, remaining_attempts=1) is True
    assert effective_retryable(classified, remaining_attempts=0) is False
    permanent = classify_analysis_failure(VideoTooLongError("x"))
    assert effective_retryable(permanent, remaining_attempts=5) is False


def test_customer_message_never_leaks_details() -> None:
    secret = "/srv/uploads/user-42/source.mov exit 1 traceback"
    for exc in (
        FFmpegMediaError(secret, kind="decode_failed"),
        FFmpegRuntimeError(secret, kind="process_signaled"),
        FFmpegStorageError(secret, kind="temporary_io"),
        RuntimeError(secret),
        ValueError(secret),
    ):
        classified = classify_analysis_failure(exc)
        assert secret not in classified.message
        assert "/srv" not in classified.message
        assert "traceback" not in classified.message.lower()
        assert "exit" not in classified.message.lower()


def test_all_codes_have_a_message() -> None:
    from swinglab.web.analysis_failures import CUSTOMER_MESSAGES

    for code in AnalysisFailureCode:
        assert code in CUSTOMER_MESSAGES
        assert CUSTOMER_MESSAGES[code]


def test_failure_code_openapi_literals_are_closed() -> None:
    assert {code.value for code in AnalysisFailureCode} == {
        "video_too_long",
        "capture_no_strike",
        "capture_pose_unusable",
        "media_decode_failed",
        "analysis_runtime_unavailable",
        "analysis_storage_unavailable",
        "analysis_internal_error",
    }
