"""The single owner of native analysis-failure classification.

Maps a raised analysis exception to the closed :class:`AnalysisFailureCode`, a
retryable-in-principle flag, and a bounded customer-safe message. Nothing here
reads exception text to decide a code — classification is by exception *type*
(and, for bare ``OSError``, by ``errno``) so no command, path, stderr, or
traceback can ever reach the native API.

Retryable-in-principle is separate from the attempt cap: a naturally retryable
failure becomes non-retryable once its bounded attempts are exhausted. Callers
combine the two via :func:`effective_retryable`.
"""

from __future__ import annotations

import errno as _errno
from dataclasses import dataclass

from ..api.contracts import AnalysisFailureCode
from ..events import EventError
from ..ffmpeg import (
    FFmpegError,
    FFmpegMediaError,
    FFmpegRuntimeError,
    FFmpegStorageError,
)
from ..pipeline import VideoTooLongError, ZeroStrikesError

Code = AnalysisFailureCode

CUSTOMER_MESSAGES: dict[AnalysisFailureCode, str] = {
    Code.video_too_long: (
        "This video is longer than we can analyze. Trim it to your swings and "
        "upload again."
    ),
    Code.capture_no_strike: (
        "We couldn't find a swing in this clip. Re-film with the ball and impact "
        "in frame and try again."
    ),
    Code.capture_pose_unusable: (
        "We couldn't track your body clearly. Re-film with your whole body in "
        "frame and good lighting."
    ),
    Code.media_decode_failed: (
        "We couldn't read this video file. Export it again from your camera roll "
        "and re-upload."
    ),
    Code.analysis_runtime_unavailable: (
        "Analysis is temporarily unavailable. We'll retry your swing shortly."
    ),
    Code.analysis_storage_unavailable: (
        "Analysis is temporarily unavailable. We'll retry your swing shortly."
    ),
    Code.analysis_internal_error: (
        "Something went wrong while analyzing your swing. We'll retry shortly."
    ),
}

# Once retries are exhausted the retryable codes surface this steadier copy.
EXHAUSTED_MESSAGE = (
    "We weren't able to analyze this swing. Please re-film and upload again."
)

_TRANSIENT_ERRNOS = frozenset(
    number
    for number in (
        getattr(_errno, name, None)
        for name in (
            "EIO",
            "EAGAIN",
            "EBUSY",
            "EINTR",
            "ETIMEDOUT",
            "ENFILE",
            "EMFILE",
            "ENOSPC",
            "EDQUOT",
            "EROFS",
        )
    )
    if number is not None
)


@dataclass(frozen=True)
class ClassifiedAnalysisFailure:
    code: AnalysisFailureCode
    retryable: bool
    message: str


def _make(code: AnalysisFailureCode, retryable: bool) -> ClassifiedAnalysisFailure:
    return ClassifiedAnalysisFailure(
        code=code, retryable=retryable, message=CUSTOMER_MESSAGES[code]
    )


def classify_analysis_failure(exc: BaseException) -> ClassifiedAnalysisFailure:
    """Classify a terminal analysis exception into its closed native contract.

    Raises ``BaseException`` types that represent an interrupted restart
    (``KeyboardInterrupt``/``SystemExit``) straight through: those are not
    terminal analysis failures and must leave the job queued/recovered.
    """
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise exc

    if isinstance(exc, VideoTooLongError):
        return _make(Code.video_too_long, retryable=False)
    if isinstance(exc, ZeroStrikesError):
        return _make(Code.capture_no_strike, retryable=False)
    if isinstance(exc, EventError):
        return _make(Code.capture_pose_unusable, retryable=False)
    if isinstance(exc, FFmpegMediaError):
        return _make(Code.media_decode_failed, retryable=False)
    if isinstance(exc, FFmpegRuntimeError):
        return _make(Code.analysis_runtime_unavailable, retryable=True)
    if isinstance(exc, FFmpegStorageError):
        return _make(Code.analysis_storage_unavailable, retryable=True)
    if isinstance(exc, FFmpegError):
        # A bare compatibility-base FFmpegError carries no typed kind; treat it
        # as an unexpected internal condition rather than guessing from text.
        return _make(Code.analysis_internal_error, retryable=True)
    if isinstance(exc, OSError):
        if exc.errno in _TRANSIENT_ERRNOS:
            return _make(Code.analysis_storage_unavailable, retryable=True)
        return _make(Code.analysis_internal_error, retryable=True)
    return _make(Code.analysis_internal_error, retryable=True)


def effective_retryable(
    classified: ClassifiedAnalysisFailure, *, remaining_attempts: int
) -> bool:
    """A failure is retryable only if its kind allows it and attempts remain."""
    return classified.retryable and remaining_attempts > 0


def customer_message(
    classified: ClassifiedAnalysisFailure, *, remaining_attempts: int
) -> str:
    """Surface the exhausted copy for a retryable kind that has run out."""
    if classified.retryable and remaining_attempts <= 0:
        return EXHAUSTED_MESSAGE
    return classified.message
