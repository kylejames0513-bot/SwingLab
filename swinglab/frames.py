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
    video: str | Path, strike_s: float, workdir: str | Path, swing_no: int, cfg: Config
) -> FrameSet:
    """Extract the analysis window (strike-1.8s .. strike+0.8s) at working resolution."""
    ana = cfg.analysis
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
            f"fps={ana['fps']},scale={ana['analysis_width']}:-2",
            str(pattern),
        ]
    )
    paths = sorted(workdir.glob(f"s{swing_no}_*.png"))
    return FrameSet(paths=paths, start_s=start, fps=float(ana["fps"]))


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
