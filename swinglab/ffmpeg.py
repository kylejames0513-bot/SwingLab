"""Thin wrappers around the system ffmpeg / ffprobe binaries.

ffmpeg stays an external subprocess on purpose: it keeps licensing simple and
matches how the pipeline was proven. Rotation metadata in phone .mov files is
applied automatically by ffmpeg during any filter or frame extraction — never
rotate manually or frames come out double-rotated.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def require_binaries() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise FFmpegError(
            f"Required binaries not found on PATH: {', '.join(missing)}. "
            "Install ffmpeg (which provides both) and retry."
        )


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc


@dataclass
class VideoInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    fps: float
    rotation: int
    creation_time: str | None
    has_audio: bool

    @property
    def display_width(self) -> int:
        """Width after ffmpeg applies rotation metadata."""
        return self.height if self.rotation % 180 == 90 else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation % 180 == 90 else self.height


def probe(video: str | Path) -> VideoInfo:
    """ffprobe the input; rotation is read from metadata only (never applied here)."""
    video = Path(video)
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video),
        ]
    )
    info = json.loads(proc.stdout)

    vstream = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if vstream is None:
        raise FFmpegError(f"{video}: no video stream found")
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))

    rotation = 0
    for side in vstream.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(side["rotation"])
    if not rotation:
        rotation = int(vstream.get("tags", {}).get("rotate", 0) or 0)
    rotation %= 360

    num, _, den = (vstream.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0

    fmt = info.get("format", {})
    duration = float(fmt.get("duration") or vstream.get("duration") or 0.0)
    creation = fmt.get("tags", {}).get("creation_time") or vstream.get("tags", {}).get(
        "creation_time"
    )

    return VideoInfo(
        path=video,
        duration_s=duration,
        width=int(vstream["width"]),
        height=int(vstream["height"]),
        fps=fps,
        rotation=rotation,
        creation_time=creation,
        has_audio=has_audio,
    )
