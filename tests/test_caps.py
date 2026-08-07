"""Input caps: analysis.max_video_s (refuse marathon clips before any work)
and detection.max_strikes (analyze the first N, say so honestly)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from swinglab import pipeline, pose
from swinglab.config import Config
from swinglab.ffmpeg import VideoInfo
from swinglab.frames import FrameSet
from swinglab.pipeline import VideoTooLongError, analyze_video
from swinglab.report import REPORT_PRESENTATION_VERSION
from swinglab.report_artifacts import ReportEntitlementSnapshot
from swinglab.report_bundle import GuidedReportRendererUnavailable
from swinglab.report_view import (
    EvidenceKind,
    GUIDED_REPORT_PRESENTATION_VERSION,
    MediaRole,
    PhaseMethod,
    ReasonCode,
    load_report_view,
)
from swinglab.web.humanize import friendly_error
from tests.conftest import generate_test_video, make_landmarks, needs_ffmpeg
from tests.report_bundle_fixtures import write_test_report_html
from tests.test_pipeline_e2e import FakeTracker

CLICKS = [3.0, 9.5, 16.25]


@pytest.fixture
def fast_cfg() -> Config:
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    cfg.slowmo["annotated"] = False
    return cfg


@pytest.fixture(autouse=True)
def fake_pose(monkeypatch):
    monkeypatch.setattr(pose, "PoseTracker", FakeTracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", FakeTracker)


@pytest.fixture
def guided_without_ffmpeg(monkeypatch, tmp_path):
    video = tmp_path / "private-source-name.mov"
    video.write_bytes(b"source")
    monkeypatch.setattr(pipeline, "require_binaries", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "probe",
        lambda path: VideoInfo(Path(path), 4.0, 1000, 1000, 30.0, 0, None, True),
    )
    monkeypatch.setattr(pipeline.audio, "extract_audio", lambda video, out: out)
    monkeypatch.setattr(pipeline.audio, "detect_strikes", lambda wav, cfg: [2.0])

    def extract_window(video, strike, work, swing, cfg, fps=None):
        paths = []
        for index in range(75):
            path = Path(work) / f"s{swing}_{index + 1:03d}.png"
            Image.new("RGB", (20, 20), "white").save(path)
            paths.append(path)
        return FrameSet(paths, 0.0, float(fps or 30.0))

    def extract_fullres(video, timestamp, out, cfg):
        path = Path(out)
        Image.new("RGB", (1000, 1000), "white").save(path)
        return path

    def make_slowmo(video, strike, out, cfg, fast=False):
        path = Path(out)
        path.write_bytes(b"slow motion")
        return path

    monkeypatch.setattr(pipeline.frames, "extract_window", extract_window)
    monkeypatch.setattr(pipeline.frames, "extract_fullres_frame", extract_fullres)
    monkeypatch.setattr(pipeline.slowmo, "make_slowmo", make_slowmo)
    return video


# -- max_video_s -------------------------------------------------------------


def test_guided_null_writer_fails_before_creating_a_bundle(tmp_path):
    out = tmp_path / "results"
    with pytest.raises(GuidedReportRendererUnavailable):
        analyze_video(
            tmp_path / "private-source.mov",
            out_dir=out,
            report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
            report_entitlements=ReportEntitlementSnapshot("available"),
            guided_html_writer=None,
        )
    assert not out.exists()


def test_guided_null_entitlement_snapshot_is_rejected_before_video_work(tmp_path):
    out = tmp_path / "results"
    with pytest.raises(TypeError, match="entitlement snapshot"):
        analyze_video(
            tmp_path / "private-source.mov",
            out_dir=out,
            report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
            report_entitlements=None,
            guided_html_writer=write_test_report_html,
        )
    assert not out.exists()


def test_guided_pipeline_publishes_without_ffmpeg_and_redacts_source_name(
    tmp_path, fast_cfg, guided_without_ffmpeg,
):
    messages = []
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        log=messages.append,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("disabled"),
        guided_html_writer=write_test_report_html,
    )

    view = load_report_view(result.report_view_path)
    assert result.structured_report is True
    assert view.mode == "structured"
    assert result.evidence_snapshots[0].events[2].method is PhaseMethod.DETECTED_AUDIO
    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    persisted = result.report_view_path.read_text(encoding="utf-8") + json.dumps(metrics)
    assert "private-source-name" not in persisted
    assert "private-source-name" not in "\n".join(messages)


def test_guided_metrics_preserve_literal_session_context_without_source_path(
    tmp_path, fast_cfg, guided_without_ffmpeg,
):
    fast_cfg.analysis["fps"] = 24.0
    fast_cfg.analysis["auto_fps"] = False
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        hand="left",
        angle="face-on",
        club="7-iron",
        level="advanced",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("disabled"),
        guided_html_writer=write_test_report_html,
    )

    payload = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    assert payload["meta"] == {
        "camera_angle": "face-on",
        "club": "7-iron",
        "level": "advanced",
        "hand": "left",
        "analysis_fps": 24.0,
    }
    assert payload["video"]["path"] == "uploaded-video"
    assert "private-source-name" not in json.dumps(payload)


def test_legacy_version_ignores_guided_inputs_and_keeps_overlay_call_shape(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    calls = []
    real_overlay = pipeline.overlay.make_overlay

    def overlay_spy(frame_paths, landmarks, target_direction, out_path, cfg):
        calls.append((frame_paths, landmarks, target_direction, out_path, cfg))
        return real_overlay(frame_paths, landmarks, target_direction, out_path, cfg)

    monkeypatch.setattr(pipeline.overlay, "make_overlay", overlay_spy)
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        report_presentation_version=REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("locked"),
        guided_html_writer=None,
    )
    assert result.structured_report is False
    assert result.report_view_path is None
    assert len(calls) == 1 and len(calls[0]) == 5
    assert (result.session_dir / "media" / "overlay_s1.png").is_file()


def test_guided_manual_event_method_is_persisted_exactly(
    tmp_path, fast_cfg, guided_without_ffmpeg,
):
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        manual_strikes=[2.0],
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("disabled"),
        guided_html_writer=write_test_report_html,
    )
    impact = result.evidence_snapshots[0].events[2]
    assert impact.method is PhaseMethod.MANUAL_STRIKE
    assert load_report_view(result.report_view_path).visual_evidence.events[2].method is PhaseMethod.MANUAL_STRIKE


def test_guided_silent_manual_strike_skips_audio_and_persists_provenance(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    monkeypatch.setattr(
        pipeline,
        "probe",
        lambda path: VideoInfo(Path(path), 4.0, 1000, 1000, 30.0, 0, None, False),
    )
    monkeypatch.setattr(
        pipeline.audio,
        "extract_audio",
        lambda *args, **kwargs: pytest.fail("manual silent input extracted audio"),
    )
    monkeypatch.setattr(
        pipeline.audio,
        "detect_strikes",
        lambda *args, **kwargs: pytest.fail("manual silent input detected audio strikes"),
    )

    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        manual_strikes=[2.0],
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("disabled"),
        guided_html_writer=write_test_report_html,
    )

    payload = json.loads(result.report_view_path.read_text(encoding="utf-8"))
    assert payload["visual_evidence"]["events"][2] == {
        "event": "impact",
        "method": "manual_strike",
        "timestamp_ms": 2000,
        "label": "Impact",
    }


def test_guided_missing_event_publishes_capture_only_without_coaching(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    monkeypatch.setattr(pipeline.audio, "detect_strikes", lambda wav, cfg: [])
    monkeypatch.setattr(
        pipeline.pose,
        "PoseTracker",
        lambda: pytest.fail("zero-strike capture-only initialized pose tracking"),
    )
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("available"),
        guided_html_writer=write_test_report_html,
    )
    view = load_report_view(result.report_view_path)
    assert view.outcome.value == "capture_only"
    assert view.trust.reasons == (ReasonCode.NO_RELIABLE_STRIKE_EVENT,)
    assert view.next_move is None and view.practice is None and view.visual_evidence is None
    assert view.media == ()
    assert result.swings == [] and result.evidence_snapshots == []


def test_optional_partial_cleanup_uses_task4_pinned_owned_tree(
    tmp_path, monkeypatch,
):
    from swinglab import report_bundle

    session = tmp_path / "session"
    media = session / ".report-attempt-" / "media"
    media.mkdir(parents=True)
    partial = media / "partial.png"
    partial.write_bytes(b"partial")
    planned = []
    monkeypatch.setattr(
        report_bundle,
        "_after_owned_tree_plans",
        lambda plans: planned.append(tuple(plans)),
    )

    pipeline._remove_optional_partial(
        media,
        partial,
        session_anchor=session,
    )

    assert len(planned) == 1
    assert not partial.exists()


def test_guided_dtl_publishes_timing_only_and_never_calls_body_renderers(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    monkeypatch.setattr(
        pipeline.overlay,
        "make_overlay",
        lambda *args, **kwargs: pytest.fail("guided DTL called legacy overlay"),
    )
    monkeypatch.setattr(
        pipeline.strip,
        "make_strip",
        lambda *args, **kwargs: pytest.fail("guided DTL called key-position strip"),
    )
    monkeypatch.setattr(
        pipeline.annotate,
        "make_replay",
        lambda *args, **kwargs: pytest.fail("guided DTL called coach replay"),
    )
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        angle="dtl",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("available"),
        guided_html_writer=write_test_report_html,
    )
    view = load_report_view(result.report_view_path)
    assert view.visual_evidence.kind is EvidenceKind.TEMPO_TIMELINE
    assert [event.method for event in view.visual_evidence.events] == [
        PhaseMethod.OPENING_BASELINE,
        PhaseMethod.HIGHEST_TRACKED_HANDS,
        PhaseMethod.DETECTED_AUDIO,
        PhaseMethod.CONFIGURED_FINISH_OFFSET,
    ]
    assert {media.role for media in view.media} == {
        MediaRole.SLOW_MOTION,
        MediaRole.PRIORITY_EVIDENCE,
    }
    persisted = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    measured = result.evidence_snapshots[0].metrics
    assert persisted["swings"][0]["metrics"]["tempo_ratio"] == measured.tempo_ratio
    assert "overlay" not in persisted["swings"][0]["deliverables"]
    assert "strip" not in persisted["swings"][0]["deliverables"]
    assert "replay" not in persisted["swings"][0]["deliverables"]


def _assert_capture_only(view, reason):
    assert view.outcome.value == "capture_only"
    assert reason in view.trust.reasons
    assert view.next_move is None
    assert view.practice is None
    assert view.visual_evidence is None
    assert view.optional_sections == ()
    assert view.capabilities.coach_replay is False
    assert view.capabilities.gear is False


def test_guided_angle_mismatch_prunes_all_coaching_media(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    class WideTracker(FakeTracker):
        def detect(self, frame_path):
            landmarks = super().detect(frame_path)
            if landmarks is not None:
                landmarks[pose.LEFT_SHOULDER] += np.array([60.0, 0.0])
                landmarks[pose.RIGHT_SHOULDER] -= np.array([60.0, 0.0])
            return landmarks

    monkeypatch.setattr(pipeline.pose, "PoseTracker", WideTracker)
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        angle="dtl",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("available"),
        guided_html_writer=write_test_report_html,
    )
    view = load_report_view(result.report_view_path)
    _assert_capture_only(view, ReasonCode.CAMERA_ANGLE_MISMATCH)
    assert {media.role for media in view.media} == {MediaRole.CAPTURE_PLAYBACK}
    assert "strip" not in result.swings[0]
    assert "replay" not in result.swings[0]


def test_guided_unstable_tracking_is_capture_only(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    class UnstableTracker(FakeTracker):
        def detect_observation(self, frame_path):
            match = re.search(r"_(\d+)\.png$", str(frame_path))
            if match is not None and (int(match.group(1)) - 1) % 2:
                return None
            return super().detect_observation(frame_path)

    monkeypatch.setattr(pipeline.pose, "PoseTracker", UnstableTracker)
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("available"),
        guided_html_writer=write_test_report_html,
    )
    view = load_report_view(result.report_view_path)
    assert set(view.trust.reasons) & {
        ReasonCode.TRACKING_UNSTABLE,
        ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE,
    }
    _assert_capture_only(view, view.trust.reasons[0])
    assert {media.role for media in view.media} == {MediaRole.CAPTURE_PLAYBACK}


@pytest.mark.parametrize(
    ("tracker", "reason"),
    [
        (
            type(
                "SparseTracker",
                (FakeTracker,),
                {"detect_observation": lambda self, frame_path: None},
            ),
            ReasonCode.INSUFFICIENT_POSE_FRAMES,
        ),
        (
            type(
                "StaticTracker",
                (FakeTracker,),
                {"detect": lambda self, frame_path: make_landmarks()},
            ),
            ReasonCode.NO_READABLE_SWING,
        ),
    ],
)
def test_guided_unreadable_capture_failures_publish_without_coaching(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch, tracker, reason,
):
    monkeypatch.setattr(pipeline.pose, "PoseTracker", tracker)
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("disabled"),
        guided_html_writer=write_test_report_html,
    )
    view = load_report_view(result.report_view_path)
    _assert_capture_only(view, reason)
    assert view.media == ()
    assert result.swings == []


def test_guided_partial_metrics_failure_passes_fatal_reason_to_capture_pruning(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    class PartialTracker(FakeTracker):
        def detect(self, frame_path):
            if "s2_" in str(frame_path):
                return make_landmarks()
            return super().detect(frame_path)

    monkeypatch.setattr(pipeline.pose, "PoseTracker", PartialTracker)
    monkeypatch.setattr(pipeline.audio, "detect_strikes", lambda wav, cfg: [2.0, 3.0])
    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("available"),
        guided_html_writer=write_test_report_html,
    )
    view = load_report_view(result.report_view_path)
    _assert_capture_only(view, ReasonCode.NO_READABLE_SWING)
    assert len(result.swings) == 1
    assert len(result.evidence_snapshots) == 1
    assert set(result.swings[0]) == {"metrics", "notes", "apparent_angle", "slowmo"}
    assert {media.role for media in view.media} == {MediaRole.CAPTURE_PLAYBACK}
    persisted = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    assert len(persisted["swings"]) == 1
    assert set(persisted["swings"][0]["deliverables"]) == {"slowmo"}


@pytest.mark.parametrize(
    ("renderer", "missing_role", "expected_roles"),
    [
        (
            "strip",
            MediaRole.KEY_POSITIONS,
            {MediaRole.SLOW_MOTION, MediaRole.PRIORITY_EVIDENCE},
        ),
        (
            "slowmo",
            MediaRole.SLOW_MOTION,
            {MediaRole.KEY_POSITIONS, MediaRole.PRIORITY_EVIDENCE},
        ),
        (
            "replay",
            MediaRole.COACH_REPLAY,
            {
                MediaRole.KEY_POSITIONS,
                MediaRole.SLOW_MOTION,
                MediaRole.PRIORITY_EVIDENCE,
            },
        ),
    ],
)
def test_guided_optional_renderer_failure_removes_only_its_owned_partial(
    tmp_path,
    fast_cfg,
    guided_without_ffmpeg,
    monkeypatch,
    renderer,
    missing_role,
    expected_roles,
):
    def fail_after_partial(out):
        path = Path(out)
        path.write_bytes(b"partial")
        raise RuntimeError("renderer failed after a partial write")

    if renderer == "strip":
        monkeypatch.setattr(
            pipeline.strip,
            "make_strip",
            lambda frames, swing, out, cfg: fail_after_partial(out),
        )
    elif renderer == "slowmo":
        monkeypatch.setattr(
            pipeline.slowmo,
            "make_slowmo",
            lambda video, strike, out, cfg, fast=False: fail_after_partial(out),
        )
    else:
        fast_cfg.slowmo["annotated"] = True

        def replay_frames(video, strike, work, cfg):
            path = Path(work) / "r0001.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (20, 20), "white").save(path)
            return FrameSet([path], 0.0, 30.0)

        monkeypatch.setattr(pipeline.slowmo, "extract_replay_frames", replay_frames)
        monkeypatch.setattr(
            pipeline.annotate,
            "make_replay",
            lambda replay, analysis, tracked, events, metrics, out, cfg: fail_after_partial(out),
        )

    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("available"),
        guided_html_writer=write_test_report_html,
    )
    view = load_report_view(result.report_view_path)
    roles = {media.role for media in view.media}
    assert missing_role not in roles
    assert roles == expected_roles
    assert result.manifest_path.is_file() and result.checksums_path.is_file()
    assert not any(path.read_bytes() == b"partial" for path in result.report_path.parent.rglob("*.*"))
    persisted = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    deliverables = persisted["swings"][0]["deliverables"]
    assert {
        "strip": "strip" in deliverables,
        "slowmo": "slowmo" in deliverables,
        "replay": "replay" in deliverables,
    }[renderer] is False


def test_guided_ambiguous_optional_partial_is_a_core_failure(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    def fail_with_directory(frames, swing, out, cfg):
        Path(out).mkdir()
        raise RuntimeError("ambiguous partial")

    monkeypatch.setattr(pipeline.strip, "make_strip", fail_with_directory)
    out = tmp_path / "results"
    with pytest.raises(pipeline.CoreReportBundleError):
        analyze_video(
            guided_without_ffmpeg,
            out_dir=out,
            cfg=fast_cfg,
            report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
            report_entitlements=ReportEntitlementSnapshot("disabled"),
            guided_html_writer=write_test_report_html,
        )
    session = out / guided_without_ffmpeg.stem
    assert not any(path.name.startswith("report-bundle-") for path in session.iterdir())

@needs_ffmpeg
def test_over_length_clip_refused_before_any_work(tmp_path, fast_cfg):
    fast_cfg.analysis["max_video_s"] = 10
    video = generate_test_video(tmp_path / "long.mov", [3.0], duration_s=20.0)
    out = tmp_path / "results"
    with pytest.raises(VideoTooLongError, match="analysis limit"):
        analyze_video(video, out_dir=out, cfg=fast_cfg)
    assert not out.exists()  # refused before creating the session folder


@needs_ffmpeg
def test_zero_disables_length_cap(tmp_path, fast_cfg):
    fast_cfg.analysis["max_video_s"] = 0
    video = generate_test_video(tmp_path / "long.mov", [9.5], duration_s=20.0)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)
    assert len(result.swings) == 1


def test_too_long_error_translates_without_jargon():
    raw = (
        "clip.mov is 3600 seconds long — over the 300-second analysis "
        "limit. Trim the clip to the swings you want analyzed and try "
        "again. (Operators: the limit is analysis.max_video_s in config; "
        "0 disables it.)"
    )
    help_ = friendly_error(raw)
    assert "3600 seconds" in help_.message  # keeps the honest numbers
    assert "300-second" in help_.message
    text = help_.message + " ".join(help_.tips)
    assert "max_video_s" not in text and "config" not in text.lower()
    assert any("Trim" in tip for tip in help_.tips)


# -- max_strikes -------------------------------------------------------------

@needs_ffmpeg
def test_strike_cap_analyzes_first_n_with_honest_note(tmp_path, fast_cfg):
    fast_cfg.detection["max_strikes"] = 2
    video = generate_test_video(tmp_path / "three.mov", CLICKS)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)

    assert len(result.swings) == 2  # the FIRST two, in clip order
    strikes = [s["metrics"].strike_s for s in result.swings]
    assert strikes == pytest.approx(CLICKS[:2], abs=0.05)

    data = json.loads(result.metrics_path.read_text())
    note = next(
        (n for n in data["session_notes"] if "analyzed the first 2" in n), None
    )
    assert note is not None and "3 strikes" in note
    assert note in result.report_path.read_text()  # the report says so too


@needs_ffmpeg
def test_strike_cap_zero_and_under_limit_are_untouched(tmp_path, fast_cfg):
    fast_cfg.detection["max_strikes"] = 0
    video = generate_test_video(tmp_path / "three.mov", CLICKS)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)
    assert len(result.swings) == 3
    data = json.loads(result.metrics_path.read_text())
    assert not any("analyzed the first" in n for n in data["session_notes"])


@needs_ffmpeg
def test_strike_cap_applies_to_manual_strikes_too(tmp_path, fast_cfg):
    fast_cfg.detection["max_strikes"] = 1
    video = generate_test_video(tmp_path / "silent.mov", [], silent=True)
    result = analyze_video(
        video, out_dir=tmp_path / "results", cfg=fast_cfg,
        manual_strikes=[9.5, 16.25],
    )
    assert len(result.swings) == 1
    assert result.swings[0]["metrics"].strike_s == pytest.approx(9.5)
