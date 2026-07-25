"""Cross-session progress: per-metric time series from a user's finished
sessions, honest deltas, flag frequencies, and the one-line trend sentence
the conversion moments and the weekly digest reuse.

Everything here reads what the pipeline already wrote — each finished
session's metrics.json — and never invents a number: a metric appears only
when at least one session actually measured it, "best" only exists for
metrics with a direction worth calling best, and the trend sentence only
exists once two sessions have real values of the same metric to compare.
Legacy sessions (metrics.json from before the newer fields) and NaN/null
values are skipped, never guessed at.

Jobs are duck-typed (``id``, ``status``, ``report_rel``, ``session_dir``,
``created_at``) so this module stays importable without the web stack —
the web app passes real ``swinglab.web.jobs.Job`` rows, tests pass stubs.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .coaching import (
    FLAG_ARM_EXTENSION,
    FLAG_BALANCE,
    FLAG_CONSISTENCY,
    FLAG_HEAD_DIP,
    FLAG_HIP_SLIDE,
    FLAG_SHOULDER_TILT,
    FLAG_SWAY,
    FLAG_TEMPO,
    flag_keys,
)
from .config import Config
from .metrics import NUMERIC_FIELDS

# Mirrors jobs.DONE without importing the web layer (or the pipeline it
# drags in).
DONE = "done"

_DEG = "\N{DEGREE SIGN}"

# metric -> (display label, unit, which side of the benchmark is bad).
# ``worse`` None = a neutral measurement with no benchmark and no notion of
# "best" (durations, downswing-direction lateral moves).
_METRIC_INFO: dict[str, tuple[str, str, str | None]] = {
    "backswing_s": ("Backswing time", "s", None),
    "downswing_s": ("Downswing time", "s", None),
    "tempo_ratio": ("Tempo", ":1", "lower"),
    "head_sway_backswing_sw": ("Head sway (backswing)", "SW", "higher"),
    "head_sway_downswing_sw": ("Head sway (downswing)", "SW", None),
    "hip_slide_backswing_sw": ("Hip slide (backswing)", "SW", "higher"),
    "hip_slide_downswing_sw": ("Hip slide (downswing)", "SW", None),
    "head_dip_sw": ("Head dip", "SW", "higher"),
    "lead_arm_angle_deg": ("Lead-arm extension at impact", _DEG, "lower"),
    "shoulder_tilt_impact_deg": ("Shoulder tilt at impact", _DEG, "lower"),
    "shoulder_tilt_delta_deg": (
        "Shoulder-tilt change (address \N{RIGHTWARDS ARROW} impact)", _DEG, "lower",
    ),
    "finish_balance_sw": ("Finish balance", "SW", "higher"),
}

# Progress-page chip labels for the fired coaching flags.
FLAG_LABELS = {
    FLAG_TEMPO: "Tempo",
    FLAG_SWAY: "Head sway",
    FLAG_HIP_SLIDE: "Hip slide",
    FLAG_HEAD_DIP: "Head dip",
    FLAG_ARM_EXTENSION: "Arm extension",
    FLAG_SHOULDER_TILT: "Shoulder tilt",
    FLAG_BALANCE: "Finish balance",
    FLAG_CONSISTENCY: "Tempo consistency",
}

# The metric the trend sentence leads with — first one with two sessions of
# data wins. Benchmarked metrics only: "Backswing time has moved" is a
# measurement, not a story.
_SENTENCE_PRIORITY = (
    "tempo_ratio",
    "head_sway_backswing_sw",
    "hip_slide_backswing_sw",
    "head_dip_sw",
    "lead_arm_angle_deg",
    "shoulder_tilt_impact_deg",
    "finish_balance_sw",
)


@dataclass(frozen=True)
class SessionSample:
    """One finished session's contribution to the trends."""

    job_id: str
    finished_at: float  # metrics.json mtime (falls back to job created_at)
    means: dict[str, float]  # metric -> session mean, only metrics with data
    flags: tuple[str, ...]  # fired coaching flags (same keys as coaching.py)
    swing_count: int


@dataclass(frozen=True)
class MetricTrend:
    metric: str
    label: str
    unit: str
    points: tuple[tuple[float, float], ...]  # (finished_at, mean), oldest first
    latest: float
    best: float | None  # None for metrics with no better-direction
    delta: float | None  # latest - first session; None below 2 points
    benchmark: float | None  # the coaching threshold, None when there is none
    benchmark_text: str
    worse: str | None  # "higher" | "lower" | None — bad side of the benchmark


@dataclass(frozen=True)
class Trends:
    samples: tuple[SessionSample, ...]  # oldest first
    metrics: dict[str, MetricTrend]  # NUMERIC_FIELDS order, data-bearing only
    flag_counts: dict[str, int]  # flag -> sessions it fired in, most-fired first

    @property
    def session_count(self) -> int:
        return len(self.samples)


def _benchmarks(coach: dict) -> dict[str, tuple[float, str]]:
    """Per-metric benchmark value + honest one-line description, rendered
    from the live coaching thresholds (retuning config.yaml retunes these)."""
    sway = float(coach["sway_warn_sw"])
    return {
        "tempo_ratio": (
            float(coach["tempo_warn_below"]),
            f"target {float(coach['tempo_target']):.1f}:1 · "
            f"flagged below {float(coach['tempo_warn_below']):.1f}:1",
        ),
        "head_sway_backswing_sw": (sway, f"flagged above {sway:.2f} SW"),
        "hip_slide_backswing_sw": (sway, f"flagged above {sway:.2f} SW"),
        "head_dip_sw": (
            float(coach["head_dip_warn_sw"]),
            f"flagged above {float(coach['head_dip_warn_sw']):.2f} SW",
        ),
        "lead_arm_angle_deg": (
            float(coach["lead_arm_warn_deg"]),
            f"180{_DEG} is straight · "
            f"flagged below {float(coach['lead_arm_warn_deg']):.0f}{_DEG}",
        ),
        "shoulder_tilt_impact_deg": (
            float(coach["shoulder_tilt_impact_min_deg"]),
            f"flagged below {float(coach['shoulder_tilt_impact_min_deg']):.0f}{_DEG}",
        ),
        "shoulder_tilt_delta_deg": (
            0.0, "flagged when the tilt decreases from address",
        ),
        "finish_balance_sw": (
            float(coach["finish_balance_warn_sw"]),
            f"flagged above {float(coach['finish_balance_warn_sw']):.2f} SW",
        ),
    }


def format_value(metric: str, value: float) -> str:
    """One value in the same shape the report prints it."""
    unit = _METRIC_INFO[metric][1]
    if unit == ":1":
        return f"{value:.2f}:1"
    if unit == _DEG:
        return f"{value:.0f}{_DEG}"
    return f"{value:.2f} {unit}"  # seconds and shoulder widths


def format_delta(metric: str, delta: float) -> str:
    """Signed change, e.g. "+0.32:1" / "\N{MINUS SIGN}0.08 SW" / "+6\N{DEGREE SIGN}"."""
    unit = _METRIC_INFO[metric][1]
    sign = "+" if delta >= 0 else "\N{MINUS SIGN}"
    mag = abs(delta)
    if unit == ":1":
        return f"{sign}{mag:.2f}:1"
    if unit == _DEG:
        return f"{sign}{mag:.0f}{_DEG}"
    return f"{sign}{mag:.2f} {unit}"


def metrics_json_path(job) -> Path | None:
    """Where a finished job's metrics.json lives (matches app.py's rule),
    or None for jobs that never finished."""
    if getattr(job, "status", None) != DONE or not getattr(job, "report_rel", None):
        return None
    return job.session_dir / Path(job.report_rel).parent / "metrics.json"


def _session_means(payload: dict) -> dict[str, float]:
    """Per-metric mean across the session's swings. A metric only appears
    when at least one swing has a real number for it — missing keys (legacy
    sessions), nulls (NaN sanitized to null on disk), and stray NaN/inf all
    just drop out."""
    swings = payload.get("swings") or []
    if not isinstance(swings, list):
        return {}
    means: dict[str, float] = {}
    for name in NUMERIC_FIELDS:
        values = []
        for swing in swings:
            if not isinstance(swing, dict):
                continue
            value = (swing.get("metrics") or {}).get(name)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            ):
                values.append(float(value))
        if values:
            means[name] = round(sum(values) / len(values), 3)
    return means


def session_sample(job, cfg: Config) -> SessionSample | None:
    """One job's SessionSample, or None when there is nothing measurable
    (job not done, metrics.json missing/unreadable, or no numeric data)."""
    path = metrics_json_path(job)
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    means = _session_means(payload)
    if not means:
        return None
    try:
        finished_at = path.stat().st_mtime
    except OSError:
        finished_at = float(getattr(job, "created_at", 0.0) or 0.0)
    return SessionSample(
        job_id=job.id,
        finished_at=finished_at,
        means=means,
        flags=tuple(flag_keys(payload, cfg)),
        swing_count=len(payload.get("swings") or []),
    )


def build_trends(jobs: Iterable, cfg: Config) -> Trends:
    """Trends across a user's jobs (any order, any status — only finished
    sessions with readable numbers contribute)."""
    ordered = sorted(jobs, key=lambda j: getattr(j, "created_at", 0.0) or 0.0)
    samples = [
        sample
        for sample in (session_sample(job, cfg) for job in ordered)
        if sample is not None
    ]
    benches = _benchmarks(cfg.coaching)
    metrics: dict[str, MetricTrend] = {}
    for name in NUMERIC_FIELDS:
        points = [
            (s.finished_at, s.means[name]) for s in samples if name in s.means
        ]
        if not points:
            continue
        label, unit, worse = _METRIC_INFO[name]
        benchmark, benchmark_text = benches.get(name, (None, ""))
        values = [v for _, v in points]
        better = {"higher": "lower", "lower": "higher"}.get(worse)
        best = (
            max(values) if better == "higher"
            else min(values) if better == "lower"
            else None
        )
        metrics[name] = MetricTrend(
            metric=name,
            label=label,
            unit=unit,
            points=tuple(points),
            latest=values[-1],
            best=best,
            delta=round(values[-1] - values[0], 3) if len(values) >= 2 else None,
            benchmark=benchmark,
            benchmark_text=benchmark_text,
            worse=worse,
        )
    counts: dict[str, int] = {}
    for sample in samples:
        for flag in sample.flags:
            counts[flag] = counts.get(flag, 0) + 1
    counts = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    return Trends(samples=tuple(samples), metrics=metrics, flag_counts=counts)


def trend_sentence(trends: Trends) -> str | None:
    """One honest line, e.g. "Tempo has moved 2.41:1 \N{RIGHTWARDS ARROW}
    2.79:1 across 5 sessions". None until two sessions have measured the
    same benchmarked metric — this never fabricates a number, so callers
    must hide the line entirely when it is None."""
    for name in _SENTENCE_PRIORITY:
        trend = trends.metrics.get(name)
        if trend is None or len(trend.points) < 2:
            continue
        first = format_value(name, trend.points[0][1])
        last = format_value(name, trend.points[-1][1])
        count = len(trend.points)
        if first == last:
            return f"{trend.label} has held at {last} across {count} sessions"
        return (
            f"{trend.label} has moved {first} \N{RIGHTWARDS ARROW} {last} "
            f"across {count} sessions"
        )
    return None
