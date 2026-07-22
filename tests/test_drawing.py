"""The ankle-pinned shear and drawing helpers."""

from __future__ import annotations

import numpy as np
import pytest

from swinglab import pose
from swinglab.drawing import head_radius, sheared
from tests.conftest import make_landmarks


def test_shear_pins_ankles_and_moves_head():
    lm = make_landmarks()
    dx = 40.0
    out = sheared(lm, dx)
    # ankles stay planted
    np.testing.assert_allclose(out[pose.LEFT_ANKLE], lm[pose.LEFT_ANKLE])
    np.testing.assert_allclose(out[pose.RIGHT_ANKLE], lm[pose.RIGHT_ANKLE])
    # nose (head height) moves the full correction, opposite the error
    assert out[pose.NOSE][0] == pytest.approx(lm[pose.NOSE][0] - dx)
    # no vertical movement anywhere
    for k in lm:
        assert out[k][1] == lm[k][1]
    # correction grows with height: hips move less than shoulders
    hip_shift = lm[pose.LEFT_HIP][0] - out[pose.LEFT_HIP][0]
    shoulder_shift = lm[pose.LEFT_SHOULDER][0] - out[pose.LEFT_SHOULDER][0]
    assert 0 < hip_shift < shoulder_shift < dx + 1e-9


def test_head_radius_floor():
    lm = make_landmarks()
    # nose-to-ear distance ~25px -> 1.5x = 37.5; floor is 0.30*SW
    assert head_radius(lm, shoulder_width_px=100.0) == pytest.approx(37.5, abs=1.0)
    # tiny ears: floor kicks in
    assert head_radius(lm, shoulder_width_px=1000.0) == pytest.approx(300.0)
