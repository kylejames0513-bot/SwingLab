"""Input caps: analysis.max_video_s (refuse marathon clips before any work)
and detection.max_strikes (analyze the first N, say so honestly)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from swinglab import pipeline, pose
from swinglab.config import Config
from swinglab.ffmpeg import VideoInfo
from swinglab.frames import FrameSet
from swinglab.pipeline import SessionResult, VideoTooLongError, analyze_video
from swinglab.report import REPORT_PRESENTATION_VERSION
from swinglab.report_artifacts import ReportEntitlementSnapshot
from swinglab.report_bundle import (
    GuidedReportRendererUnavailable,
    begin_report_bundle,
    build_report_bundle,
    publish_report_bundle,
)
from swinglab.report_view import (
    EvidenceKind,
    GUIDED_REPORT_PRESENTATION_VERSION,
    MediaRole,
    PhaseMethod,
    ReasonCode,
    UnsupportedReportPresentationVersion,
    load_report_view,
)
from swinglab.web.humanize import friendly_error
from swinglab.web import jobs as jobs_module
from swinglab.web.jobs import Job, JobManager
from tests.conftest import generate_test_video, make_landmarks, needs_ffmpeg
from tests.report_bundle_fixtures import (
    guided_bundle_inputs,
    temporary_directory_redirect,
    write_test_report_html,
)
from tests.test_pipeline_e2e import FakeTracker

CLICKS = [3.0, 9.5, 16.25]


def _guided_cap_result(
    job: Job,
    tmp_path: Path,
    *,
    capture_only: bool = False,
) -> SessionResult:
    analysis = job.session_dir / "out" / "source"
    analysis.mkdir(parents=True, exist_ok=True)
    attempt = begin_report_bundle(analysis, attempt_id="a" * 32)
    inputs = guided_bundle_inputs(tmp_path, swings=[] if capture_only else None)
    published = publish_report_bundle(build_report_bundle(attempt, **inputs))
    return SessionResult(
        session_dir=analysis,
        report_path=published.report_path,
        metrics_path=published.root / "metrics.json",
        video=inputs["video"],
        report_view_path=published.report_view_path,
        manifest_path=published.manifest_path,
        checksums_path=published.checksums_path,
        structured_report=True,
    )


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


@pytest.mark.parametrize("depth", ["job", "out", "analysis"])
def test_structured_classification_rejects_redirected_job_chain(
    tmp_path: Path, depth: str,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    user_id = "redirected-golfer"
    job = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(job) is True
    result = _guided_cap_result(job, tmp_path)
    manager._complete_job(job, result)

    if depth == "job":
        original = job.session_dir
        target = manager.sessions_dir / "classification-donor-job"
    elif depth == "out":
        original = job.session_dir / "out"
        target = job.session_dir / "classification-donor-out"
    else:
        original = result.session_dir
        target = result.session_dir.with_name("classification-donor-analysis")
    shutil.copytree(original, target)

    with temporary_directory_redirect(tmp_path, original, target):
        row = manager._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        stored = manager.get(job.id)
        assert row is not None and stored is not None
        assert (
            manager._completed_report_classification(row)
            == jobs_module._COMPLETION_CORRUPT
        )
        assert manager.coaching_eligible(stored) is False
        assert manager.refilm_rejections_this_month(user_id) == 0
        assert manager.usage_this_month(user_id) == 1


@pytest.mark.parametrize(
    ("capture_only", "classification", "eligible", "rejections", "usage"),
    [
        (False, jobs_module._COMPLETION_COACHING, True, 0, 1),
        (True, jobs_module._COMPLETION_CAPTURE, False, 1, 0),
    ],
)
def test_structured_classification_valid_controls(
    tmp_path: Path,
    capture_only: bool,
    classification: str,
    eligible: bool,
    rejections: int,
    usage: int,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    user_id = "valid-control-golfer"
    job = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(job) is True
    manager._complete_job(
        job,
        _guided_cap_result(job, tmp_path, capture_only=capture_only),
    )
    row = manager._conn.execute(
        "SELECT * FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    stored = manager.get(job.id)
    assert row is not None and stored is not None

    assert manager._completed_report_classification(row) == classification
    assert manager.coaching_eligible(stored) is eligible
    assert manager.refilm_rejections_this_month(user_id) == rejections
    assert manager.usage_this_month(user_id) == usage


def test_genuine_legacy_classification_control(tmp_path: Path):
    manager = JobManager(tmp_path / "sessions", Config())
    user_id = "legacy-control-golfer"
    job = manager.create_session(user_id=user_id)
    assert manager._mark_processing(job) is True
    analysis = job.session_dir / "out" / "source"
    analysis.mkdir(parents=True)
    report = analysis / "report.html"
    report.write_text("<html>legacy report</html>\n", encoding="utf-8")
    manager._complete_job(
        job,
        SessionResult(
            session_dir=analysis,
            report_path=report,
            metrics_path=analysis / "metrics.json",
            video=guided_bundle_inputs(tmp_path)["video"],
        ),
    )
    row = manager._conn.execute(
        "SELECT * FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    stored = manager.get(job.id)
    assert row is not None and stored is not None

    assert (
        manager._completed_report_classification(row)
        == jobs_module._COMPLETION_COACHING
    )
    assert manager.coaching_eligible(stored) is True
    assert manager.refilm_rejections_this_month(user_id) == 0
    assert manager.usage_this_month(user_id) == 1


# -- max_video_s -------------------------------------------------------------


def test_unknown_presentation_fails_before_video_or_artifact_work(
    tmp_path, monkeypatch,
):
    out = tmp_path / "results"
    monkeypatch.setattr(
        pipeline,
        "require_binaries",
        lambda: (_ for _ in ()).throw(AssertionError("video work started")),
    )

    with pytest.raises(
        UnsupportedReportPresentationVersion, match="unknown report presentation"
    ):
        analyze_video(
            tmp_path / "private-source.mov",
            out_dir=out,
            report_presentation_version="future-report-v9",
        )

    assert not out.exists()


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


def test_guided_visibility_gates_and_render_landmarks_share_analysis_observation(
    tmp_path, fast_cfg, guided_without_ffmpeg, monkeypatch,
):
    class DivergentFullresTracker(FakeTracker):
        fullres_calls = 0

        def detect(self, frame_path):
            if "full_s" in Path(frame_path).name:
                type(self).fullres_calls += 1
                landmarks = make_landmarks()
                landmarks[pose.LEFT_SHOULDER] = np.array([7.0, 11.0])
                return landmarks
            return super().detect(frame_path)

        def detect_observation(self, frame_path):
            observation = super().detect_observation(frame_path)
            assert observation is not None
            visibility = dict(observation.visibility)
            visibility[pose.LEFT_WRIST] = 0.1
            return pose.PoseObservation(observation.landmarks, visibility)

    monkeypatch.setattr(pipeline.pose, "PoseTracker", DivergentFullresTracker)

    result = analyze_video(
        guided_without_ffmpeg,
        out_dir=tmp_path / "results",
        cfg=fast_cfg,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("disabled"),
        guided_html_writer=write_test_report_html,
    )

    snapshot = result.evidence_snapshots[0]
    assert snapshot.annotation_gates["lead_arm_angle_deg"].readable is False
    assert DivergentFullresTracker.fullres_calls == 0
    # Analysis frames are 20x20 and event deliverables are 1000x1000. The
    # stable analysis shoulder is projected to the render frame; a second
    # full-resolution detection would have substituted the poisoned [7, 11].
    impact = snapshot.event_landmarks[pipeline.EventId.IMPACT]
    assert impact is not None
    assert impact[pose.LEFT_SHOULDER].tolist() == [27500.0, 12500.0]


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
