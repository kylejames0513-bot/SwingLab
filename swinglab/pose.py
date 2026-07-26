"""Pose tracking via mediapipe's tasks API.

Gotcha: mediapipe pip wheels 0.10.30+ ship ONLY the tasks API — mp.solutions
does not exist. The pose landmarker model is downloaded once on first run and
cached next to the package.

Landmark indices used throughout: 0 nose, 7/8 ears, 11/12 shoulders,
13/14 elbows, 15/16 wrists, 23/24 hips, 25/26 knees, 27/28 ankles.
Coordinates handed out by this module are in image pixels (normalized values
multiplied by width/height).
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)
MODEL_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "pose_landmarker_lite.task"

NOSE = 0
LEFT_EAR, RIGHT_EAR = 7, 8
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_ELBOW, RIGHT_ELBOW = 13, 14
LEFT_WRIST, RIGHT_WRIST = 15, 16
LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_KNEE, RIGHT_KNEE = 25, 26
LEFT_ANKLE, RIGHT_ANKLE = 27, 28

TRACKED = (0, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28)

# Core body landmarks for confidence checks: shoulders, hips, ankles. They
# move slowly (unlike the hands, which cross several shoulder widths per
# frame through the downswing), so a big inter-frame jump means the tracker
# jumped — to another person, or to garbage — not that the golfer did.
CORE_LANDMARKS = (
    LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP, LEFT_ANKLE, RIGHT_ANKLE,
)

# Frames whose core landmarks the model itself doubts are dropped like the
# upright-sanity failures — an occluded or out-of-frame body produces
# hallucinated coordinates that would otherwise flow silently into metrics.
# 0.5 = the model considers the landmark more likely visible than not;
# clearly-tracked golfers score far higher, so this drops only genuine doubt.
CORE_VISIBILITY_FLOOR = 0.5

# Per-swing tracking-quality heuristic (see tracking_quality). Conservative
# on purpose — the low-confidence coaching note should fire on genuinely
# unstable tracking, never on a routine clean swing:
# - pose jitter on a stable subject is ~0.02 SW/frame; a core landmark
#   moving a full shoulder width in one frame (1/30 s) is physically not the
#   same person standing there.
# - the events layer already needs ~1/3 of the window tracked; losing more
#   than 40% of frames means the numbers rest on sparse data.
QUALITY_MAX_DROPPED_FRACTION = 0.40
QUALITY_MAX_CORE_JUMP_SW = 1.0

Landmarks = dict[int, np.ndarray]  # index -> [x_px, y_px]


def ensure_model(path: Path = MODEL_PATH) -> Path:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".download")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
    return path


def midpoint(lm: Landmarks, a: int, b: int) -> np.ndarray:
    return (lm[a] + lm[b]) / 2.0


def hand_centroid(lm: Landmarks) -> np.ndarray:
    return midpoint(lm, LEFT_WRIST, RIGHT_WRIST)


def head_center(lm: Landmarks) -> np.ndarray:
    return (lm[NOSE] + lm[LEFT_EAR] + lm[RIGHT_EAR]) / 3.0


def upright_sanity(lm: Landmarks) -> bool:
    """Nose above shoulders above hips above ankles, in image coordinates."""
    nose_y = lm[NOSE][1]
    shoulder_y = midpoint(lm, LEFT_SHOULDER, RIGHT_SHOULDER)[1]
    hip_y = midpoint(lm, LEFT_HIP, RIGHT_HIP)[1]
    ankle_y = midpoint(lm, LEFT_ANKLE, RIGHT_ANKLE)[1]
    return nose_y < shoulder_y < hip_y < ankle_y


def core_visibility_ok(visibility: dict[int, float]) -> bool:
    """False when any core landmark (shoulders/hips/ankles) scores below
    CORE_VISIBILITY_FLOOR. Missing scores count as visible — when the model
    doesn't say, don't drop."""
    return all(
        (visibility.get(i) is None or visibility[i] >= CORE_VISIBILITY_FLOOR)
        for i in CORE_LANDMARKS
    )


@dataclass(frozen=True)
class TrackingQuality:
    """Per-swing tracking-confidence summary (see tracking_quality)."""

    dropped_fraction: float  # frames with no usable pose / all frames
    max_core_jump_sw: float  # biggest one-frame core-landmark move, in SW
    poor: bool  # True = add the honest low-confidence note


def tracking_quality(
    tracked: list[Landmarks | None], shoulder_width_px: float
) -> TrackingQuality:
    """How trustworthy one swing's pose track is.

    Two signals, both cheap and both conservative: the fraction of frames
    that produced no usable pose (detection failed, upright check failed,
    or core visibility too low), and the largest single-frame jump of any
    core landmark between ADJACENT tracked frames, in shoulder widths — the
    signature of the detector locking onto another person mid-swing. Jumps
    across a dropout gap are NOT counted (re-acquiring after a gap looks
    like a jump even when tracking is fine).
    """
    n = len(tracked)
    valid = [i for i, lm in enumerate(tracked) if lm is not None]
    dropped = 1.0 - (len(valid) / n) if n else 1.0
    max_jump = 0.0
    for a, b in zip(valid, valid[1:]):
        if b - a != 1:
            continue
        jump = max(
            float(np.linalg.norm(tracked[b][i] - tracked[a][i]))
            for i in CORE_LANDMARKS
        )
        max_jump = max(max_jump, jump)
    max_jump_sw = max_jump / shoulder_width_px if shoulder_width_px > 0 else 0.0
    return TrackingQuality(
        dropped_fraction=round(dropped, 3),
        max_core_jump_sw=round(max_jump_sw, 3),
        poor=(
            dropped > QUALITY_MAX_DROPPED_FRACTION
            or max_jump_sw > QUALITY_MAX_CORE_JUMP_SW
        ),
    )


class PoseTracker:
    """Wraps a PoseLandmarker in IMAGE mode; one instance per session."""

    def __init__(self, model_path: Path | None = None):
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision

        self._mp = mp
        model = ensure_model(model_path or MODEL_PATH)
        self._landmarker = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.IMAGE,
            )
        )

    def detect(self, frame_path: str | Path) -> Landmarks | None:
        """Landmarks in pixels for one frame, or None if no (sane) pose found."""
        img = self._mp.Image.create_from_file(str(frame_path))
        res = self._landmarker.detect(img)
        if not res.pose_landmarks:
            return None
        raw = res.pose_landmarks[0]
        visibility = {
            i: getattr(raw[i], "visibility", None) for i in CORE_LANDMARKS
        }
        if not core_visibility_ok(visibility):
            return None
        w, h = img.width, img.height
        lm: Landmarks = {
            i: np.array([raw[i].x * w, raw[i].y * h], dtype=np.float64)
            for i in TRACKED
        }
        if not upright_sanity(lm):
            return None
        return lm

    def close(self) -> None:
        self._landmarker.close()
