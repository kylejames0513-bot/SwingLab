"""Frame extraction around each strike.

Critical gotcha, hit once in the prototype and not to be reintroduced: -ss and
-t must come BEFORE -i so they trim the INPUT. Placed after -i, -t caps the
OUTPUT duration instead, which silently truncates any clip whose timestamps are
later stretched (the slow-motion filter does exactly that).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .ffmpeg import run

# Auto-fps (analysis.auto_fps): sources filmed at or above AUTO_FPS_MIN_SOURCE
# get their analysis windows extracted at min(source_fps, AUTO_FPS_CAP)
# instead of analysis.fps. The downswing is only 7-8 frames at 30 fps, so
# tempo carries a ~13% quantization error there; 60 fps halves it. The cap
# keeps 120/240 fps slow-motion sources from quadrupling pose-tracking cost
# for precision the metrics can't use.
AUTO_FPS_MIN_SOURCE = 50.0
AUTO_FPS_CAP = 60.0


def pick_analysis_fps(cfg: Config, source_fps: float) -> float:
    """The frame rate to extract analysis windows at, for one video.

    analysis.fps is the floor (and the answer whenever analysis.auto_fps is
    off or the source is below AUTO_FPS_MIN_SOURCE); a high-fps source lifts
    it to min(source_fps, AUTO_FPS_CAP). Never below analysis.fps, so an
    operator who deliberately configured a higher rate keeps it.
    """
    base = float(cfg.analysis["fps"])
    if cfg.analysis.get("auto_fps") and source_fps >= AUTO_FPS_MIN_SOURCE:
        return max(base, min(float(source_fps), AUTO_FPS_CAP))
    return base


@dataclass
class FrameSet:
    """Analysis frames for one swing window, with the time mapping."""

    paths: list[Path]
    start_s: float  # video time of the first frame
    fps: float

    def time_of(self, index: int) -> float:
        return self.start_s + index / self.fps

    def index_near(self, t: float) -> int:
        idx = round((t - self.start_s) * self.fps)
        return max(0, min(len(self.paths) - 1, idx))


def extract_window(
    video: str | Path,
    strike_s: float,
    workdir: str | Path,
    swing_no: int,
    cfg: Config,
    fps: float | None = None,
) -> FrameSet:
    """Extract the analysis window (strike-1.8s .. strike+0.8s) at working
    resolution. ``fps`` overrides analysis.fps (the auto-fps path — see
    pick_analysis_fps); the returned FrameSet carries whichever rate was
    actually used, and all downstream timing derives from it."""
    ana = cfg.analysis
    fps = float(fps or ana["fps"])
    start = max(0.0, strike_s - ana["window_pre_s"])
    duration = (strike_s - start) + ana["window_post_s"]
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    pattern = workdir / f"s{swing_no}_%03d.png"
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
            f"{duration:.3f}",
            "-i",
            str(video),
            "-vf",
            f"fps={fps:.6g},scale={ana['analysis_width']}:-2",
            str(pattern),
        ]
    )
    paths = sorted(workdir.glob(f"s{swing_no}_*.png"))
    return FrameSet(paths=paths, start_s=start, fps=fps)


def extract_fullres_frame(
    video: str | Path, t: float, out_path: str | Path, cfg: Config
) -> Path:
    """Extract a single full-resolution frame (deliverables only)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, t):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale=-2:{cfg.analysis['fullres_height']}",
            str(out_path),
        ]
    )
    return out_path
