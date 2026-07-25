"""Quarter-speed slow motion.

Two gotchas are baked into this exact filter chain and must stay that way:
1. Interpolate at NATIVE speed up to a high frame rate FIRST, then stretch
   with setpts — stretching first would interpolate already-slowed footage.
2. The trim (-ss/-t) stays on the INPUT side; on the output side -t caps the
   output duration and silently truncates the stretched clip.
"""

from __future__ import annotations

from pathlib import Path

from . import frames
from .config import Config
from .ffmpeg import run


def make_slowmo(
    video: str | Path, strike_s: float, out_path: str | Path, cfg: Config,
    fast: bool = False,
) -> Path:
    """Render the slow-motion clip for one strike.

    ``fast=True`` skips motion interpolation — by far the most expensive step
    of the whole pipeline — and stretches the source frames directly. The clip
    is less silky (source frames are just held longer) but renders in seconds
    instead of a minute.
    """
    sm = cfg.slowmo
    factor = int(sm["factor"])
    interp_fps = 30 * factor  # interpolate up so 30fps output stays smooth after the stretch
    start = max(0.0, strike_s - sm["pre_s"])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fast:
        vf = f"scale=-2:{sm['height']},setpts={factor}*PTS"
    else:
        vf = (
            f"scale=-2:{sm['height']},"
            f"minterpolate=fps={interp_fps}:mi_mode=mci:mc_mode=aobmc,"
            f"setpts={factor}*PTS"
        )
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{sm['duration_s']:.3f}",
            "-i",
            str(video),
            "-vf",
            vf,
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


def extract_replay_frames(
    video: str | Path, strike_s: float, workdir: str | Path, cfg: Config
) -> frames.FrameSet:
    """Discrete frames for the annotated replay (annotate.make_replay).

    Same window as the slow-mo clip ([strike - slowmo.pre_s, + duration_s]),
    at analysis.fps, scaled to -2:slowmo.height (even dimensions for x264).
    The -ss/-t trim stays on the INPUT side (see the module docstring and
    frames.py). Writes r%04d.png into ``workdir``; returns a FrameSet with
    start_s = max(0, strike_s - pre_s) and fps = analysis.fps.
    """
    sm = cfg.slowmo
    start = max(0.0, strike_s - sm["pre_s"])
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    pattern = workdir / "r%04d.png"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            # input-side trim: keep -ss/-t before -i (see module docstring)
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{sm['duration_s']:.3f}",
            "-i",
            str(video),
            "-vf",
            f"fps={cfg.analysis['fps']},scale=-2:{sm['height']}",
            str(pattern),
        ]
    )
    paths = sorted(workdir.glob("r*.png"))
    return frames.FrameSet(paths=paths, start_s=start, fps=float(cfg.analysis["fps"]))
