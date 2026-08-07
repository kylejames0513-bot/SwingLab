"""The ankle-pinned shear and drawing helpers."""

from __future__ import annotations

import numpy as np
import pytest

from swinglab import pose
from PIL import Image, ImageDraw, ImageFont

from swinglab.drawing import draw_dashed_line, draw_labeled_timeline, draw_marker, head_radius, sheared
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


def test_focused_primitives_draw_measured_markers_boundary_and_timing_only():
    image = Image.new("RGB", (800, 220), "white")
    draw = ImageDraw.Draw(image)
    draw_marker(draw, (100, 100), "#ff6600")
    draw_dashed_line(draw, (200, 20), (200, 200), "#00aa55")
    draw_labeled_timeline(draw, [("Address", 0), ("Top", 100), ("Impact", 200), ("Finish", 300)], y=160, color="#ff6600", font=ImageFont.load_default())
    pixels = np.asarray(image)
    assert np.any(np.all(pixels == (255, 102, 0), axis=-1))
    assert np.any(np.all(pixels == (0, 170, 85), axis=-1))
