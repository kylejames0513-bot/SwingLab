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
