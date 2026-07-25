"""Annotated coach replay: skeleton + fading hand-path trace + event chips.

Renders ``replay_sN.mp4`` beside the slow-mo clip: the same window rebuilt
from discrete frames (slowmo.extract_replay_frames) with Pillow-burned
overlays — the live skeleton in the "captured" identity, a trace of the HAND
path (wrist centroid, a 2D image-plane measurement — never a club-path or 3D
claim), and metric chips that appear at the swing events and persist.

The replay is NEVER motion-interpolated: interpolating burned-in text smears
it, and skipping minterpolate keeps the render at seconds per swing. Each
source frame is simply held ``slowmo.factor`` output frames — the same 4x
stretch and duration as slowmo_sN.mp4, with crisp text. Consequently
``--fast`` does not change the replay at all (it only skips the minterpolate
slow-mo path); ``slowmo.annotated: false`` is the switch that disables the
replay entirely.

Everything except ``encode_frames`` / ``extract_replay_frames`` is pure
Pillow/NumPy and runs without ffmpeg — that is the testable seam. Importing
this module never spawns ffmpeg; only calling ``encode_frames`` does.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from . import drawing, frames, pose
from .config import Config
from .events import SwingEvents
from .ffmpeg import run
from .metrics import SwingMetrics

# Chip layout (fixed drawing constants from the replay spec, not tunables).
CHIP_MARGIN = 12  # px from the top-left corner
CHIP_GAP = 6  # px between stacked chips
CHIP_BG_ALPHA = 230  # brand primary at this alpha, text white
TRAIL_MAX_ALPHA = 200  # a fresh trail segment starts at this alpha

# (x, y, age_s) hand-centroid history entry, oldest first.
TrailPoint = tuple[float, float, float]


def _fmt_chip(head: str, parts: list[str | None]) -> str:
    """Join an event name with its metric fragments, dropping NaN-born Nones."""
    return " · ".join([head] + [p for p in parts if p])


def chip_schedule(
    analysis_frames: frames.FrameSet,
    events: SwingEvents,
    m: SwingMetrics,
) -> list[tuple[float, str]]:
    """[(appear_time_s, text)] in event order (source time, not slowed time).

    A chip appears at its event time and persists to the end of the replay.
    NaN metrics drop their fragment; an event whose fragments are all NaN
    degrades to the bare event name.
    """

    def num(value: float, fmt: str) -> str | None:
        return None if math.isnan(value) else fmt.format(value)

    return [
        (analysis_frames.time_of(events.address_idx), "Setup"),
        (
            events.top_s,
            _fmt_chip("Top", [num(m.backswing_s, "backswing {:.2f} s")]),
        ),
        (
            events.impact_s,
            _fmt_chip(
                "Impact",
                [
                    num(m.lead_arm_angle_deg, "lead arm {:.0f}\N{DEGREE SIGN}"),
                    num(m.head_sway_downswing_sw, "sway T\N{RIGHTWARDS ARROW}I {:+.2f} SW"),
                ],
            ),
        ),
        (
            events.finish_s,
            _fmt_chip("Finish", [num(m.finish_balance_sw, "balance {:.2f} SW")]),
        ),
    ]


def chips_at(t: float, schedule: list[tuple[float, str]]) -> list[str]:
    """Chip texts active at source time ``t`` (cumulative, in event order)."""
    return [text for appear_s, text in schedule if t >= appear_s - 1e-9]


def replay_landmarks(
    replay_frames: frames.FrameSet,
    analysis_frames: frames.FrameSet,
    tracked: list[pose.Landmarks | None],
    scale: float,
) -> list[pose.Landmarks | None]:
    """Per replay frame: the nearest analysis landmarks scaled to replay pixels.

    The replay window extends past the analysis window; frames further than
    0.75/fps from their nearest analysis frame get None (never a reuse of the
    clamped last frame).
    """
    tol = 0.75 / analysis_frames.fps
    out: list[pose.Landmarks | None] = []
    for i in range(len(replay_frames.paths)):
        t_i = replay_frames.time_of(i)
        j = analysis_frames.index_near(t_i)
        lm = tracked[j] if abs(analysis_frames.time_of(j) - t_i) <= tol else None
        out.append(None if lm is None else {k: v * scale for k, v in lm.items()})
    return out


def trail_layer(
    size: tuple[int, int], trail: list[TrailPoint], cfg: Config
) -> Image.Image:
    """Transparent RGBA layer with the fading hand trail drawn on it.

    Segment alpha fades with the OLDER endpoint's age over slowmo.trail_fade_s;
    fully-faded segments are skipped. A solid accent dot marks the newest point
    (skipped once that point itself has fully faded).
    """
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if not trail:
        return layer
    fade_s = float(cfg.slowmo["trail_fade_s"])
    accent = cfg.brand["accent_color"]
    h = size[1]
    line_w = max(3, h // 240)
    draw = ImageDraw.Draw(layer)
    for (x0, y0, age0), (x1, y1, _age1) in zip(trail, trail[1:]):
        alpha = round(TRAIL_MAX_ALPHA * min(max(1.0 - age0 / fade_s, 0.0), 1.0))
        if alpha <= 0:
            continue
        draw.line(
            [(x0, y0), (x1, y1)],
            fill=drawing.hex_to_rgba(accent, alpha),
            width=line_w,
        )
    x, y, age = trail[-1]
    if age < fade_s:
        r = max(4, h // 180)
        draw.ellipse(
            [x - r, y - r, x + r, y + r], fill=drawing.hex_to_rgba(accent, 255)
        )
    return layer


def annotate_frame(
    img: Image.Image,
    lm: pose.Landmarks | None,
    shoulder_width_px: float,
    trail: list[TrailPoint],
    chips: list[str],
    centerline_x: float | None,
    cfg: Config,
) -> Image.Image:
    """Burn the replay overlays into one frame (pure Pillow; ffmpeg-free).

    ``lm`` and ``shoulder_width_px`` are already scaled to ``img`` pixels.
    Draw order: centerline, hand trail, skeleton, chips, watermark. Sizes
    scale with the frame height (reference 720). The input image is not
    mutated; a new RGB image is returned.
    """
    h = img.height
    base = img.convert("RGBA")
    draw = ImageDraw.Draw(base)

    # reference centerline — same visual identity as the overlay image
    if centerline_x is not None:
        drawing.draw_dashed_vline(
            draw,
            centerline_x,
            0,
            h,
            cfg.overlay["corrected_color"],
            line_w=max(2, h // 360),
        )

    if trail:
        base = Image.alpha_composite(base, trail_layer(base.size, trail, cfg))
        draw = ImageDraw.Draw(base)

    if lm is not None:
        drawing.draw_skeleton(
            draw,
            lm,
            cfg.overlay["captured_color"],
            shoulder_width_px,
            line_w=max(3, h // 240),
        )

    if chips:
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        font = drawing.load_font(max(14, h // 36))
        bg = drawing.hex_to_rgba(cfg.brand["primary_color"], CHIP_BG_ALPHA)
        y = CHIP_MARGIN
        for text in chips:
            _, chip_h = drawing.draw_chip(layer, (CHIP_MARGIN, y), text, bg, font)
            y += chip_h + CHIP_GAP
        base = Image.alpha_composite(base, layer)

    out = base.convert("RGB")
    if cfg.brand["watermark"]:
        out = drawing.apply_watermark(out, cfg.brand["name"])
    return out


def make_replay(
    replay_frames: frames.FrameSet,
    analysis_frames: frames.FrameSet,
    tracked: list[pose.Landmarks | None],
    events: SwingEvents,
    m: SwingMetrics,
    out_path: str | Path,
    cfg: Config,
) -> Path:
    """Annotate every replay frame and encode ``replay_sN.mp4``.

    Annotated frames are written as annot%04d.png next to the source frames
    (under work/, so the existing keep_work cleanup covers them).
    """
    if not replay_frames.paths:
        raise ValueError("no replay frames")
    with Image.open(replay_frames.paths[0]) as first_replay:
        w_replay = first_replay.width
    with Image.open(analysis_frames.paths[0]) as first_analysis:
        w_analysis = first_analysis.width
    scale = w_replay / w_analysis

    sw_px = events.shoulder_width_px * scale
    lms = replay_landmarks(replay_frames, analysis_frames, tracked, scale)
    schedule = chip_schedule(analysis_frames, events, m)

    setup_s = analysis_frames.time_of(events.address_idx)
    address_lm = tracked[events.address_idx]
    center_x = (
        float(pose.head_center(address_lm)[0]) * scale
        if address_lm is not None
        else None
    )

    fade_s = float(cfg.slowmo["trail_fade_s"])
    frames_dir = replay_frames.paths[0].parent
    hand_pts: list[tuple[float, float, float]] = []  # (x, y, source time)
    for i, src in enumerate(replay_frames.paths):
        t_i = replay_frames.time_of(i)
        lm = lms[i]
        if lm is not None:
            hc = pose.hand_centroid(lm)
            hand_pts.append((float(hc[0]), float(hc[1]), t_i))
        trail = [
            (x, y, t_i - t_j) for x, y, t_j in hand_pts if t_i - t_j <= fade_s
        ]
        chips = chips_at(t_i, schedule)
        centerline_x = center_x if t_i >= setup_s - 1e-9 else None
        with Image.open(src) as im:
            annotated = annotate_frame(
                im.convert("RGB"), lm, sw_px, trail, chips, centerline_x, cfg
            )
        annotated.save(frames_dir / f"annot{i:04d}.png")

    return encode_frames(frames_dir, "annot%04d.png", Path(out_path), cfg)


def encode_frames(
    frames_dir: Path, pattern: str, out_path: Path, cfg: Config
) -> Path:
    """Encode annotated frames to mp4 — the only ffmpeg touchpoint here.

    Input framerate analysis.fps/slowmo.factor (7.5 with defaults), output
    30 fps: each source frame is held ``factor`` output frames — the same 4x
    stretch and duration as slowmo_sN.mp4, with crisp text (no interpolation,
    by design; see the module docstring).
    """
    sm = cfg.slowmo
    factor = int(sm["factor"])
    in_fps = float(cfg.analysis["fps"]) / factor
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-framerate",
            f"{in_fps:.6g}",
            "-i",
            str(Path(frames_dir) / pattern),
            "-r",
            "30",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            str(sm["crf"]),
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ]
    )
    return out_path
