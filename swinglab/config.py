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
        "logo_url": None,
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
        # Optional post-peak noise gate.  0 keeps the established detector
        # behavior; an operator may later require candidates to be at least
        # this fraction of the loudest eligible transient (0.0..1.0).
        "relative_height": 0.0,
        # Analyze at most this many strikes per clip (the first N, in clip
        # order); 0 = no limit. Every strike costs real CPU (pose tracking +
        # renders), so an unbounded range session can occupy a worker for
        # hours. When the cap trims a clip, the report says so honestly.
        "max_strikes": 8,
    },
    "coaching": {
        # Compatibility floor for the versioned club-aware priority policy.
        # Only the literal boolean True selects rule 2; missing, false, and
        # malformed values keep the legacy rule-1 order.  Thresholds and
        # measured values are identical under both rules.
        "club_aware_enabled": False,
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
    "proof_cycle": {
        # A deliberately separate product policy from the coaching thresholds:
        # coaching decides what to work on; Proof Cycle decides when a matched
        # re-film has moved enough to say so.  Bare-code installs keep the
        # feature off until an operator has reviewed the copy and rollout.
        "enabled": False,
        # A separate second-stage gate for self-reported practice receipts
        # and normal-swing transfer declarations.  This lets operators first
        # observe the read-only result surface before collecting new practice
        # context; practice never changes a measurement verdict either way.
        "practice_evidence_enabled": False,
        # Keep one active target's retained evidence bounded.  The worker
        # scans a slightly wider set of candidate jobs because it must reject
        # other camera angles/handedness before this cap is applied.
        "history_limit": 6,
        "minimum_readable_swings": 3,
        "minimum_refilms_for_improved": 2,
        # None means the domain engine uses the minimum detectable effect as
        # the maximum permitted spread between confirming re-films.
        "maximum_refilm_spread": None,
        # These are measurement-noise floors, not coaching thresholds.  They
        # deliberately remain conservative even if the coaching lines move.
        "metric_noise_floors": {
            "tempo_ratio": 0.10,
            "head_sway_backswing_sw": 0.03,
            "head_sway_downswing_sw": 0.03,
            "hip_slide_backswing_sw": 0.03,
            "hip_slide_downswing_sw": 0.03,
            "head_dip_sw": 0.03,
            "lead_arm_angle_deg": 3.0,
            "shoulder_tilt_impact_deg": 3.0,
            "shoulder_tilt_delta_deg": 3.0,
            "finish_balance_sw": 0.03,
        },
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
        # Native email auth is an explicit recovery-gated rollout.  These
        # positive bounds are validated strictly at app composition time.
        "mobile_native_auth_enabled": False,
        "mobile_auth_starts_per_15_minutes_per_ip": 20,
        "mobile_auth_starts_per_15_minutes_per_email": 5,
        "mobile_auth_failed_exchanges_per_15_minutes_per_ip": 20,
        "mobile_auth_failed_exchanges_per_15_minutes_per_email": 10,
        "mobile_auth_live_challenges_per_ip": 20,
        "mobile_auth_live_challenges_per_email": 3,
        # Native product surfaces are independently default-off. Read-only
        # resources may be rolled out before any mutation capability.
        "mobile_resources_enabled": False,
        "mobile_profile_writes_enabled": False,
        "mobile_practice_writes_enabled": False,
        "mobile_device_management_enabled": False,
        "mobile_resumable_upload_enabled": False,
        "mobile_privacy_enabled": False,
        "mobile_events_enabled": False,
        "mobile_push_enabled": False,
        # Public EAS project UUID. Blank is allowed while push is off; flag-on
        # startup requires one canonical UUID (overridable by
        # CADDIEINSIGHT_EXPO_PROJECT_ID when set).
        "mobile_push_expo_project_id": "",
        "mobile_push_send_envelope_seconds": 30,
        "mobile_push_cutover_clock_skew_seconds": 60,
        # Enforced nonterminal outbox caps (global / per selector).
        "mobile_push_outbox_global_cap": 10000,
        "mobile_push_outbox_per_selector_cap": 50,
        "mobile_native_billing_enabled": False,
        # Resumable-upload policy is server owned even while its route is off.
        "mobile_upload_chunk_mb": 5,
        "mobile_active_uploads_per_user": 2,
        "mobile_upload_ttl_seconds": 86400,
        # Durable analysis-retry policy. The window/attempt defaults ship
        # positive; the two capacity guards ship as 0 (disabled) and must be
        # given measured positive values before resumable uploads are enabled.
        "mobile_analysis_retry_window_seconds": 86400,
        "mobile_analysis_retry_max_attempts": 2,
        "mobile_upload_global_max_reserved_bytes": 0,
        "mobile_upload_min_filesystem_free_bytes": 0,
        "review_auth_starts_per_15_minutes_per_ip": 20,
        "review_auth_starts_per_15_minutes_per_account": 5,
        "review_auth_failed_exchanges_per_15_minutes_per_ip": 20,
        "review_auth_failed_exchanges_per_15_minutes_per_account": 10,
        "review_auth_live_challenges_per_ip": 20,
        "review_auth_live_challenges_per_account": 3,
        # Bare-code default is an open, no-login instance; the shipped
        # config.yaml turns accounts on.
        "require_account": False,
        # Customer-triggered swing-history deletion. Keep this off in the
        # compatibility-floor release; activate only after that release is
        # live so rollback never targets a binary that ignores reset quota
        # receipts or history epochs.
        "history_reset_enabled": False,
        # Email-code sign-in ("one account": the store email is the app
        # identity, nobody needs a password). Safe to default on — it only
        # activates when SMTP is configured (SWINGLAB_SMTP_URL +
        # SWINGLAB_MAIL_FROM); without SMTP the login/signup pages keep
        # the classic password flows exactly, so white-label installs with
        # no email infrastructure are unaffected.
        "passwordless_login": True,
        # Weekly practice-plan email scheduler. Even when true, nothing
        # sends unless SMTP is configured (SWINGLAB_SMTP_URL +
        # SWINGLAB_MAIL_FROM) AND the user opted in.
        "digest_enabled": True,
    },
    "billing": {
        "free_per_month": 1,
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
        # Progress-dashboard Pro gate, same shape as replay_pro_only: when
        # true AND accounts are on, /progress shows free users a locked
        # teaser (what the dashboard tracks, plus a /pricing link) instead
        # of their trend charts. Open instances are never gated. Bare-code
        # default is false; the SHIPPED config.yaml turns it on.
        "progress_pro_only": False,
        "shopify_pro_handle": "swinglab-pro",
        # Variant SKU -> days of Pro one unit grants. SL-PRO-LIFE is the
        # lifetime tier: 36500 days (100 years); the account page displays
        # anything more than 50 years out as "Lifetime" rather than a date.
        "shopify_skus": {"SL-PRO-1MO": 31, "SL-PRO-12MO": 365, "SL-PRO-LIFE": 36500},
        # DISPLAY strings for the pricing page only — what is actually
        # charged always lives in Shopify/Stripe. Keep these matching the
        # store or don't set them. The badge is a display string for the
        # same reason: the "save 42%" arithmetic ($69.99/year vs the
        # $119.88 twelve months at $9.99 would cost) is only true of the
        # real store prices, so it lives here where the operator retunes
        # it (or empties it) alongside them. The Founders Pass is the
        # capped successor to the open lifetime tier: $149 once, first
        # 100 members only — a lifetime product is only honest if the
        # business can afford to keep running it, so we cap how many
        # exist. It still rides the SL-PRO-LIFE SKU; existing lifetime
        # buyers are grandfathered unchanged.
        "pro_price_monthly_text": "$9.99/month",
        "pro_price_annual_text": "$69.99/year — $5.83/month",
        "pro_price_lifetime_text": "$149 once — the Founders Pass",
        "pro_annual_badge_text": "Best value — save 42%",
        # True only once the store actually sells auto-renewing
        # subscriptions (Shopify's Subscriptions app installed, selling
        # plans attached to the monthly/yearly variants). The pricing page
        # describes renewal from this flag — false keeps the copy honest
        # on a passes-only store, where nothing ever auto-renews.
        "store_subscriptions": False,
    },
    "allowances": {
        # Free matched re-film credit. The product's own method is film ->
        # practice -> re-film, so a free account that produced a
        # coaching-ready baseline this calendar month keeps ONE more upload
        # free within 14 days of that baseline — PROVIDED the declared
        # context matches it (same club, same handedness, same camera
        # angle; the same comparison boundary the Proof Cycle uses). One
        # credit per calendar month, the first-rejected-clip courtesy is
        # unchanged, and Pro accounts are unaffected. Bare-code default is
        # false — a white-label install stays on the plain quota until the
        # operator opts in; the SHIPPED config.yaml turns it on, the same
        # deliberate difference as replay_pro_only.
        "free_matched_refilm": False,
    },
    "shopify_customer_sync": {
        # App-created accounts remain local-first. This bridge is deliberately
        # off until an operator provisions the Admin API credentials, reviews
        # the protected-customer-data requirements, and enables a staged
        # rollout. auto_sync_new_users only has an effect while enabled.
        "enabled": False,
        "auto_sync_new_users": True,
        # Outbound Admin API calls must be bounded. Retry delays are
        # exponential from retry_base_seconds and never exceed the cap.
        "request_timeout_seconds": 10,
        "max_attempts": 5,
        "retry_base_seconds": 30,
        "retry_max_seconds": 3600,
        "retry_jitter_ratio": 0.2,
    },
    "shop": {
        "enabled": True,
        "cache_minutes": 10,
        "tag_prefix": "swinglab:",
        "max_recommendations": 3,
        # Bare-code/white-label behavior remains compatible.  The shipped
        # CaddieInsight config enables the supplier-evidence gate below.
        "first_sale_catalog_only": False,
        "first_sale_verified_tag": "caddieinsight:fulfillment-verified",
        "first_sale_candidate_tags": [],
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
    def proof_cycle(self) -> dict[str, Any]:
        return self.data["proof_cycle"]

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
    def allowances(self) -> dict[str, Any]:
        return self.data["allowances"]

    @property
    def shopify_customer_sync(self) -> dict[str, Any]:
        return self.data["shopify_customer_sync"]

    @property
    def shop(self) -> dict[str, Any]:
        return self.data["shop"]

    @property
    def output_dir(self) -> str:
        return self.data["output_dir"]
