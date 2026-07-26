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
        "name": "CaddieInsight",
        "logo_path": None,
        "primary_color": "#1a5c38",
        "accent_color": "#e8720c",
        "footer_text": "CaddieInsight — swing analysis from a single phone video.",
        "watermark": False,
        "disclaimer": (
            "Automated estimates from a single camera. Not a substitute for "
            "instruction from a teaching professional."
        ),
        # Shown wherever a user would need the operator's help (e.g. the
        # login page when password reset is unavailable because SMTP isn't
        # configured). None = the generic phrasing alone.
        "support_text": None,
    },
    "detection": {
        "audio_height": 0.30,
        "audio_prominence": 0.25,
        "min_gap_s": 4.0,
        # Analyze at most this many strikes per clip (the first N, in clip
        # order); 0 = no limit. Every strike costs real CPU (pose tracking +
        # renders), so an unbounded range session can occupy a worker for
        # hours. When the cap trims a clip, the report says so honestly.
        "max_strikes": 8,
    },
    "coaching": {
        "sway_warn_sw": 0.35,
        "tempo_target": 3.0,
        "tempo_warn_below": 2.4,
        "tempo_std_praise": 0.3,
        # Head drop address->impact beyond this flags "head-dip" (in shoulder
        # widths; ~9-10 cm on an adult — a genuine dip, not noise).
        "head_dip_warn_sw": 0.25,
        # Lead arm bent below this at impact flags "arm-extension"
        # (shoulder-elbow-wrist angle as seen from the camera; 180 = straight).
        "lead_arm_warn_deg": 150,
        # Impact shoulder tilt below this (positive = trail shoulder lower,
        # measured face-on) — or tilt decreasing from address — flags
        # "shoulder-tilt".
        "shoulder_tilt_impact_min_deg": 5.0,
        # Mean ankle-midpoint drift over the finish hold beyond this flags
        # "balance" (in shoulder widths; a step, well above pose jitter).
        "finish_balance_warn_sw": 0.15,
    },
    "analysis": {
        "window_pre_s": 1.8,
        "window_post_s": 0.8,
        "fps": 30,
        # Refuse clips longer than this many seconds (checked right after
        # probe, before any work is done); 0 = no limit. A one-hour clip
        # means hours of CPU and a multi-hundred-MB audio track — trimming
        # to the swings is better for everyone.
        "max_video_s": 300,
        # When the source video was filmed at >= 50 fps, extract the
        # analysis windows at min(source_fps, 60) instead of analysis.fps.
        # The downswing is only 7-8 frames at 30 fps, so tempo carries a
        # ~13% quantization error there; 60 fps halves it. analysis.fps
        # stays the floor (and the value used for low-fps sources).
        "auto_fps": True,
        "analysis_width": 480,
        "fullres_height": 1000,
        "takeaway_threshold_sw": 0.25,
        "finish_offset_s": 0.55,
        "impact_behind_sw": 0.10,
        # Frames after the finish event used for the balance metric; must fit
        # inside window_post_s or the metric reads NaN (never crashes).
        "finish_hold_frames": 6,
    },
    "slowmo": {
        "factor": 4,
        "pre_s": 1.4,
        "duration_s": 2.4,
        "height": 720,
        "crf": 20,
        # Also render replay_sN.mp4 (skeleton + fading hand-path trace +
        # metric chips burned in); never motion-interpolated.
        "annotated": True,
        # The hand-path trace fades out over this many source-time seconds.
        "trail_fade_s": 0.9,
    },
    "web": {
        "workers": 2,
        "max_upload_mb": 500,
        "max_active_jobs_per_ip": 3,
        # Bare-code default keeps everything forever (safe for a white-label
        # operator who hasn't thought about retention yet); the SHIPPED
        # config.yaml sets 180 days — see the inline doc there for the
        # GDPR/storage-minimization angle.
        "retention_days": 0,
        # Delete the original upload once a job is DONE and its report
        # exists (deliverables — report, media, metrics — are kept). Saves
        # most of the disk per session and stops holding raw footage of
        # people longer than needed; the trade-off is that re-analyzing an
        # old session needs a re-upload. Bare-code default keeps the
        # source; the shipped config.yaml turns this on.
        "delete_source_after_done": False,
        # Proxy/CDN hops whose X-Forwarded-For header is trusted for the
        # real client IP: "*" (any — right for PaaS like Railway where the
        # app only ever hears from the platform proxy), a list of proxy
        # IPs, or ""/null to disable and use the socket peer address.
        # Without this, every visitor behind the proxy shares one IP and
        # max_active_jobs_per_ip silently caps the whole site. Honest
        # caveat: with "*" a client that can reach the app DIRECTLY
        # (bypassing the proxy) can spoof its IP via the header — on a
        # PaaS the app port isn't publicly reachable so that's moot, but
        # on a bare VM exposed to the internet list your proxy IPs instead.
        "trusted_proxies": "*",
        # Auth throttling (sliding windows, backed by the same SQLite
        # file). 0 = off. Login is limited per client IP AND per email;
        # signups per client IP. Uploads are governed separately (quota +
        # max_active_jobs_per_ip).
        "login_attempts_per_15min": 10,
        "signups_per_hour_per_ip": 5,
        # Bare-code default is an open, no-login instance; the shipped
        # config.yaml turns accounts on.
        "require_account": False,
        # Weekly practice-plan email scheduler. Even when true, nothing
        # sends unless SMTP is configured (SWINGLAB_SMTP_URL +
        # SWINGLAB_MAIL_FROM) AND the user opted in.
        "digest_enabled": True,
    },
    "billing": {
        "free_per_month": 3,
        "pro_per_month": 0,
        # Coach-replay Pro gate. When true AND accounts are on, the annotated
        # replay (replay_sN.mp4) is rendered only for jobs whose owner has
        # Pro at analysis time; free users' reports show an honest locked
        # note (with a /pricing link) in the replay slot instead, and the
        # render is skipped entirely — the CPU is saved too. Open instances
        # (require_account false) and CLI runs are never gated. Bare-code
        # default is false — a white-label install stays ungated until the
        # operator opts in; the SHIPPED config.yaml turns it on.
        "replay_pro_only": False,
        "shopify_pro_handle": "swinglab-pro",
        "shopify_skus": {"SL-PRO-1MO": 31, "SL-PRO-12MO": 365},
        # DISPLAY strings for the pricing page only — what is actually
        # charged always lives in Shopify/Stripe. Keep these matching the
        # store or don't set them.
        "pro_price_monthly_text": "$9.99/month",
        "pro_price_annual_text": "$79.99/year — $6.67/month",
    },
    "shop": {
        "enabled": True,
        "cache_minutes": 10,
        "tag_prefix": "swinglab:",
        "max_recommendations": 3,
        # Public storefront URL for the report's "Matched training aids"
        # link; empty = the report renders no link. The shipped config.yaml
        # points at the CaddieInsight store.
        "store_url": "",
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
