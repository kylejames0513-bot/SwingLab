"""Strip and overlay generation from synthetic frames + landmarks, and the
white-label acceptance: config colors must land in the rendered images."""

from __future__ import annotations

import numpy as np
from PIL import Image

from swinglab.config import Config
from swinglab.report_view import Entitlement, MediaRole
from swinglab.overlay import make_overlay
from swinglab.strip import make_strip
from tests.conftest import make_landmarks


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def _contains_color(img_path, color: str) -> bool:
    arr = np.asarray(Image.open(img_path).convert("RGB"))
    return bool(np.any(np.all(arr == _hex_to_rgb(color), axis=-1)))


def _blank_frames(tmp_path, n, size=(800, 1000)):
    paths = []
    for i in range(n):
        p = tmp_path / f"frame{i}.png"
        Image.new("RGB", size, (200, 200, 200)).save(p)
        paths.append(p)
    return paths


def test_strip_uses_brand_color_and_tiles(tmp_path):
    cfg = Config()
    cfg.brand["name"] = "AceCoach"
    cfg.brand["primary_color"] = "#123456"
    frames = _blank_frames(tmp_path, 4, size=(300, 400))
    out = make_strip(frames, 1, tmp_path / "strip.png", cfg)
    img = Image.open(out)
    assert img.width > 4 * 300  # four tiles plus padding
    assert img.getpixel((5, 5)) == _hex_to_rgb("#123456")  # header bar


def test_overlay_draws_both_skeletons_in_config_colors(tmp_path):
    cfg = Config()
    cfg.overlay["captured_color"] = "#ff0180"
    cfg.overlay["corrected_color"] = "#01ff80"
    frames = _blank_frames(tmp_path, 3)
    landmarks = {
        "address": make_landmarks(),
        "top": make_landmarks(nose_x=430.0),  # 70px off centre -> arrow drawn too
        "impact": make_landmarks(nose_x=520.0),
    }
    out = make_overlay(
        {"address": frames[0], "top": frames[1], "impact": frames[2]},
        landmarks,
        target_direction=1,
        out_path=tmp_path / "overlay.png",
        cfg=cfg,
    )
    assert _contains_color(out, "#ff0180")  # captured skeleton
    assert _contains_color(out, "#01ff80")  # corrected skeleton + centerline


def test_overlay_survives_missing_top_pose(tmp_path):
    cfg = Config()
    frames = _blank_frames(tmp_path, 3)
    landmarks = {"address": make_landmarks(), "top": None, "impact": make_landmarks()}
    out = make_overlay(
        {"address": frames[0], "top": frames[1], "impact": frames[2]},
        landmarks,
        target_direction=1,
        out_path=tmp_path / "overlay.png",
        cfg=cfg,
    )
    assert out.is_file()


def test_watermark_applied_when_enabled(tmp_path):
    cfg = Config()
    cfg.brand["watermark"] = True
    frames = _blank_frames(tmp_path, 4, size=(300, 400))
    out_marked = make_strip(frames, 1, tmp_path / "marked.png", cfg)
    cfg.brand["watermark"] = False
    out_plain = make_strip(frames, 1, tmp_path / "plain.png", cfg)
    marked = np.asarray(Image.open(out_marked).convert("RGB"))
    plain = np.asarray(Image.open(out_plain).convert("RGB"))
    assert marked.shape == plain.shape
    assert np.any(marked != plain)  # the watermark changed pixels


def test_focused_artifact_media_is_core_and_hashes_saved_bytes(tmp_path):
    from swinglab.focused_evidence import FocusedEvidenceArtifact
    from hashlib import sha256
    from swinglab.report_view import MediaEntry
    path = tmp_path / "evidence.png"
    Image.new("RGB", (4, 4), "white").save(path)
    media = MediaEntry("priority-evidence", MediaRole.PRIORITY_EVIDENCE, "image/png", Entitlement.CORE, "media/evidence.png", sha256(path.read_bytes()).hexdigest())
    artifact = FocusedEvidenceArtifact(None, media, path)  # type: ignore[arg-type]
    assert artifact.media.entitlement is Entitlement.CORE
    assert artifact.media.role is MediaRole.PRIORITY_EVIDENCE
    assert artifact.media.checksum_sha256 == sha256(artifact.path.read_bytes()).hexdigest()
