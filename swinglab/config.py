"""Configuration loading.

All tunables live in config.yaml so the product can be re-branded and re-tuned
without code edits. Missing keys fall back to the defaults below, so a partial
config file (or none at all) is fine.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "brand": {
        "name": "SwingLab",
        "logo_path": None,
        "primary_color": "#1a5c38",
        "accent_color": "#e8720c",
        "footer_text": "SwingLab — swing analysis from a single phone video.",
        "watermark": False,
        "disclaimer": (
            "Automated estimates from a single camera. Not a substitute for "
            "instruction from a teaching professional."
        ),
    },
    "detection": {
        "audio_height": 0.30,
        "audio_prominence": 0.25,
        "min_gap_s": 4.0,
    },
    "coaching": {
        "sway_warn_sw": 0.35,
        "tempo_target": 3.0,
        "tempo_warn_below": 2.4,
        "tempo_std_praise": 0.3,
    },
    "analysis": {
        "window_pre_s": 1.8,
        "window_post_s": 0.8,
        "fps": 30,
        "analysis_width": 480,
        "fullres_height": 1000,
        "takeaway_threshold_sw": 0.25,
        "finish_offset_s": 0.55,
        "impact_behind_sw": 0.10,
    },
    "slowmo": {
        "factor": 4,
        "pre_s": 1.4,
        "duration_s": 2.4,
        "height": 720,
        "crf": 20,
    },
    "web": {
        "workers": 2,
        "max_upload_mb": 500,
        "max_active_jobs_per_ip": 3,
        "retention_days": 0,
        "require_account": False,
    },
    "billing": {
        "free_per_month": 3,
        "pro_per_month": 0,
        "shopify_pro_handle": "swinglab-pro",
        "shopify_skus": {"SL-PRO-1MO": 31, "SL-PRO-12MO": 365},
    },
    "shop": {
        "enabled": True,
        "cache_minutes": 10,
        "tag_prefix": "swinglab:",
        "max_recommendations": 3,
    },
    "overlay": {
        "captured_color": "#ff8c1a",
        "corrected_color": "#2ecc40",
        "arrow_min_px": 12,
    },
    "output_dir": "results",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    source_path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """Load config.yaml, merged over defaults.

        If ``path`` is None, looks for config.yaml in the current directory,
        then falls back to pure defaults.
        """
        if path is None:
            candidate = Path("config.yaml")
            path = candidate if candidate.is_file() else None
        if path is None:
            return cls()
        path = Path(path)
        with open(path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path}: top level of config must be a mapping")
        return cls(data=_deep_merge(DEFAULTS, loaded), source_path=path)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @property
    def brand(self) -> dict[str, Any]:
        return self.data["brand"]

    @property
    def detection(self) -> dict[str, Any]:
        return self.data["detection"]

    @property
    def coaching(self) -> dict[str, Any]:
        return self.data["coaching"]

    @property
    def analysis(self) -> dict[str, Any]:
        return self.data["analysis"]

    @property
    def slowmo(self) -> dict[str, Any]:
        return self.data["slowmo"]

    @property
    def overlay(self) -> dict[str, Any]:
        return self.data["overlay"]

    @property
    def web(self) -> dict[str, Any]:
        return self.data["web"]

    @property
    def billing(self) -> dict[str, Any]:
        return self.data["billing"]

    @property
    def shop(self) -> dict[str, Any]:
        return self.data["shop"]

    @property
    def output_dir(self) -> str:
        return self.data["output_dir"]
