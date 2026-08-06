from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest
from PIL import Image

from swinglab import pose
from swinglab.config import Config
from swinglab.evidence import AnnotationGate, EvidenceSnapshot, EventSnapshot
from swinglab.metrics import SwingMetrics
from swinglab.report_presenter import PriorityEvidenceRule
from swinglab.report_view import EvidenceKind, EventId, PhaseId, PhaseMethod, ReasonCode
from tests.conftest import make_landmarks


def _snapshot(tmp_path: Path, swing: int = 1, *, value: float = .4, eligible: bool = True, confident: bool = True, hand: str = "right") -> EvidenceSnapshot:
    frame = tmp_path / f"swing-{swing}.png"
    Image.new("RGB", (800, 1000), (180, 180, 180)).save(frame)
    metrics = SwingMetrics(swing, 1, 1, .3, 3.0, value, .1, value, .1, 1,
                           head_dip_sw=value, lead_arm_angle_deg=145,
                           shoulder_tilt_address_deg=2, shoulder_tilt_impact_deg=8,
                           shoulder_tilt_delta_deg=6, finish_balance_sw=value,
                           target_confident=confident)
    events = tuple(EventSnapshot(event, i, i * 100, PhaseMethod.OPENING_BASELINE, event.value.title()) for i, event in enumerate(EventId))
    landmarks = {event: make_landmarks(nose_x=500 + (20 if event is EventId.TOP else 0)) for event in EventId}
    gate = AnnotationGate("head_sway_backswing_sw", eligible, ())
    return EvidenceSnapshot(swing, metrics, events, MappingProxyType({event: frame for event in EventId}), MappingProxyType(landmarks), ((500., 900.), (510., 900.)), MappingProxyType({"head_sway_backswing_sw": gate, "tempo_ratio": gate, "lead_arm_angle_deg": gate, "shoulder_tilt_impact_deg": gate, "shoulder_tilt_delta_deg": gate, "finish_balance_sw": gate, "head_dip_sw": gate, "hip_slide_backswing_sw": gate}), pose.TrackingQuality(0, 0, False), 1, confident, 100., hand)


def _rule(kind=EvidenceKind.HEAD_BOUNDARY, metric="head_sway_backswing_sw", event=EventId.TOP):
    return PriorityEvidenceRule("sway", metric, kind, PhaseId.GOING_BACK, event, "threshold", .35, "higher")


def test_selector_keeps_metric_and_image_on_same_selected_swing(tmp_path):
    from swinglab.focused_evidence import select_focused_evidence
    one, two = _snapshot(tmp_path, 1, value=.2), _snapshot(tmp_path, 2, value=.6)
    selection = select_focused_evidence(rule=_rule(), snapshots=(one, two), stats={})
    assert selection.snapshot is two
    assert selection.snapshot.metrics.head_sway_backswing_sw == .6


def test_selector_uses_canonical_session_mean_and_counts_threshold_crossings(tmp_path):
    from swinglab.focused_evidence import select_focused_evidence
    one, two = _snapshot(tmp_path, 1, value=.2), _snapshot(tmp_path, 2, value=.8)
    rule = replace(_rule(), selection_basis="session_mean")
    selection = select_focused_evidence(rule=rule, snapshots=(one, two), stats={rule.metric_id: {"mean": .75}})
    assert selection.snapshot is two and selection.session_value == .75
    threshold = select_focused_evidence(rule=_rule(), snapshots=(one, two), stats={})
    assert threshold.triggered_swings == 1


def test_selector_distinguishes_no_visual_from_fatal_metric_and_event(tmp_path):
    from swinglab.focused_evidence import select_focused_evidence
    unreadable = _snapshot(tmp_path, eligible=False)
    selection = select_focused_evidence(rule=_rule(), snapshots=(unreadable,), stats={})
    assert selection.snapshot is None and selection.fatal_reason is None
    bad_metric = replace(unreadable, metrics=replace(unreadable.metrics, head_sway_backswing_sw=math.nan))
    assert select_focused_evidence(rule=_rule(), snapshots=(bad_metric,), stats={}).fatal_reason is ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE
    missing_impact = replace(unreadable, event_frames=MappingProxyType({EventId.ADDRESS: unreadable.event_frames[EventId.ADDRESS]}))
    impact_rule = _rule(EvidenceKind.LEAD_ARM_ANGLE, "lead_arm_angle_deg", EventId.IMPACT)
    assert select_focused_evidence(rule=impact_rule, snapshots=(missing_impact,), stats={}).fatal_reason is ReasonCode.NO_RELIABLE_STRIKE_EVENT


@pytest.mark.parametrize("kind", list(EvidenceKind))
def test_each_evidence_kind_renders_observed_evidence(tmp_path, kind):
    from swinglab.focused_evidence import FocusedEvidenceSelection, render_focused_evidence
    snapshot = _snapshot(tmp_path)
    rule = _rule(kind, "tempo_ratio" if kind is EvidenceKind.TEMPO_TIMELINE else "head_sway_backswing_sw", None if kind is EvidenceKind.TEMPO_TIMELINE else EventId.TOP)
    artifact = render_focused_evidence(FocusedEvidenceSelection(rule, snapshot, 1, 1, None, None), out_path=tmp_path / f"{kind}.png", relative_path=f"media/{kind}.png", cfg=Config())
    assert artifact.path.is_file() and artifact.evidence.swing == snapshot.swing
    assert "Swing 1" in artifact.evidence.alt_text and "observed" in artifact.evidence.alt_text.lower()


def test_dtl_allows_only_timing_and_never_body_language(tmp_path):
    from swinglab.focused_evidence import FocusedEvidenceSelection, UnsupportedFocusedEvidence, render_focused_evidence
    snapshot = _snapshot(tmp_path)
    tempo = _rule(EvidenceKind.TEMPO_TIMELINE, "tempo_ratio", None)
    artifact = render_focused_evidence(FocusedEvidenceSelection(tempo, snapshot, 1, 1, None, None), out_path=tmp_path / "tempo.png", relative_path="media/tempo.png", cfg=Config())
    text = artifact.evidence.alt_text.lower()
    assert "timeline" in text and "toward" not in text and "away" not in text
    with pytest.raises(UnsupportedFocusedEvidence):
        render_focused_evidence(FocusedEvidenceSelection(_rule(), snapshot, 1, 1, None, None), out_path=tmp_path / "body.png", relative_path="media/body.png", cfg=Config(), angle="dtl")


def test_tempo_exposes_methods_durations_ratio_and_consistency(tmp_path):
    from swinglab.focused_evidence import select_focused_evidence, render_focused_evidence
    snapshot = _snapshot(tmp_path)
    rule = _rule(EvidenceKind.TEMPO_TIMELINE, "tempo_ratio", None)
    selection = select_focused_evidence(rule=rule, snapshots=(snapshot,), stats={"tempo_ratio": {"mean": 3.0, "std": .18}})
    artifact = render_focused_evidence(selection, out_path=tmp_path / "tempo.png", relative_path="media/tempo.png", cfg=Config(), angle="dtl")
    assert "opening_baseline" in artifact.evidence.alt_text and "backswing" in artifact.evidence.alt_text and "0.18" in artifact.evidence.alt_text


def test_renderer_dispatch_covers_every_evidence_kind():
    from swinglab import focused_evidence as focused
    assert focused.RENDERERS == {
        EvidenceKind.HEAD_BOUNDARY: focused._render_head_boundary,
        EvidenceKind.HIP_BOUNDARY: focused._render_hip_boundary,
        EvidenceKind.HEAD_HEIGHT: focused._render_head_height,
        EvidenceKind.LEAD_ARM_ANGLE: focused._render_lead_arm_angle,
        EvidenceKind.SHOULDER_TILT: focused._render_shoulder_tilt,
        EvidenceKind.FINISH_STABILITY: focused._render_finish_stability,
        EvidenceKind.STEADY_REFERENCE: focused._render_steady_reference,
        EvidenceKind.TEMPO_TIMELINE: focused._render_tempo,
    }


def test_named_renderers_do_not_depend_on_a_central_geometry_router(tmp_path, monkeypatch):
    from swinglab import focused_evidence as focused
    monkeypatch.setattr(focused, "_render_body", lambda *args: (_ for _ in ()).throw(AssertionError("central geometry router used")), raising=False)
    snapshot = _snapshot(tmp_path)
    for kind, metric, event in (
        (EvidenceKind.HEAD_BOUNDARY, "head_sway_backswing_sw", EventId.TOP),
        (EvidenceKind.HIP_BOUNDARY, "hip_slide_backswing_sw", EventId.TOP),
        (EvidenceKind.HEAD_HEIGHT, "head_dip_sw", EventId.TOP),
        (EvidenceKind.LEAD_ARM_ANGLE, "lead_arm_angle_deg", EventId.TOP),
        (EvidenceKind.SHOULDER_TILT, "shoulder_tilt_delta_deg", EventId.TOP),
        (EvidenceKind.FINISH_STABILITY, "finish_balance_sw", EventId.FINISH),
        (EvidenceKind.STEADY_REFERENCE, "head_sway_backswing_sw", EventId.TOP),
    ):
        focused.render_focused_evidence(focused.FocusedEvidenceSelection(_rule(kind, metric, event), snapshot, 1, 1, 1, None), out_path=tmp_path / f"independent-{kind.value}.png", relative_path=f"media/{kind.value}.png", cfg=Config())


def test_head_boundary_confidence_controls_dashed_line_and_label(tmp_path, monkeypatch):
    from swinglab import focused_evidence as focused
    calls = []
    monkeypatch.setattr(focused, "draw_dashed_line", lambda *args, **kwargs: calls.append(args))
    for confident, expected in ((True, 1), (False, 0)):
        calls.clear()
        snapshot = _snapshot(tmp_path, swing=10 + expected, confident=confident)
        artifact = focused.render_focused_evidence(
            focused.FocusedEvidenceSelection(_rule(), snapshot, 1, 1, 1, None),
            out_path=tmp_path / f"head-{confident}.png", relative_path=f"media/head-{confident}.png", cfg=Config())
        assert len(calls) == expected
        assert (artifact.evidence.boundary_label is not None) is confident
        assert ("configured coaching boundary" in artifact.evidence.alt_text.lower()) is confident


@pytest.mark.parametrize("hand,expected_elbow", [("right", pose.LEFT_ELBOW), ("left", pose.RIGHT_ELBOW)])
def test_lead_arm_uses_handed_elbow_for_line_and_arc(tmp_path, monkeypatch, hand, expected_elbow):
    from swinglab import focused_evidence as focused
    snapshot = _snapshot(tmp_path, hand=hand)
    impact = dict(snapshot.event_landmarks[EventId.TOP])
    impact[pose.LEFT_ELBOW] = np.array([240., 420.])
    impact[pose.RIGHT_ELBOW] = np.array([610., 470.])
    snapshot = replace(snapshot, event_landmarks=MappingProxyType({**snapshot.event_landmarks, EventId.TOP: impact}))
    arcs, offsets = [], []
    real_crop = focused._crop
    def recording_crop(image, points):
        cropped, offset = real_crop(image, points); offsets.append(offset); return cropped, offset
    monkeypatch.setattr(focused, "_crop", recording_crop)
    monkeypatch.setattr(focused, "draw_angle_arc", lambda draw, center, *args: arcs.append(center))
    focused.render_focused_evidence(focused.FocusedEvidenceSelection(_rule(EvidenceKind.LEAD_ARM_ANGLE, "lead_arm_angle_deg"), snapshot, 1, 1, 1, None), out_path=tmp_path / f"arm-{hand}.png", relative_path=f"media/arm-{hand}.png", cfg=Config())
    elbow = impact[expected_elbow]
    assert len(arcs) == 1
    assert arcs[0][0] == pytest.approx(elbow[0] - offsets[0][0])
    assert arcs[0][1] == pytest.approx(elbow[1] - offsets[0][1])


def test_shoulder_tilt_draws_distinct_address_green_and_impact_orange(tmp_path, monkeypatch):
    from swinglab import focused_evidence as focused
    snapshot = _snapshot(tmp_path)
    address = dict(snapshot.event_landmarks[EventId.ADDRESS]); impact = dict(snapshot.event_landmarks[EventId.IMPACT])
    address[pose.LEFT_SHOULDER], address[pose.RIGHT_SHOULDER] = np.array([300., 300.]), np.array([500., 300.])
    impact[pose.LEFT_SHOULDER], impact[pose.RIGHT_SHOULDER] = np.array([310., 340.]), np.array([510., 280.])
    snapshot = replace(snapshot, event_landmarks=MappingProxyType({**snapshot.event_landmarks, EventId.ADDRESS: address, EventId.IMPACT: impact}))
    lines, offsets = [], []
    real_crop = focused._crop
    def recording_crop(image, points):
        cropped, offset = real_crop(image, points); offsets.append(offset); return cropped, offset
    monkeypatch.setattr(focused, "_crop", recording_crop)
    class Recorder:
        def __init__(self, wrapped): self.wrapped = wrapped
        def line(self, xy, **kwargs): lines.append((xy, kwargs.get("fill"))); return self.wrapped.line(xy, **kwargs)
        def __getattr__(self, name): return getattr(self.wrapped, name)
    real_draw = focused.ImageDraw.Draw
    monkeypatch.setattr(focused.ImageDraw, "Draw", lambda image: Recorder(real_draw(image)))
    cfg = Config()
    rule = _rule(EvidenceKind.SHOULDER_TILT, "shoulder_tilt_delta_deg", EventId.IMPACT)
    focused.render_focused_evidence(focused.FocusedEvidenceSelection(rule, snapshot, 1, 1, 1, None), out_path=tmp_path / "shoulders.png", relative_path="media/shoulders.png", cfg=cfg)
    offset = offsets[0]
    address_line = tuple((float(address[index][0] - offset[0]), float(address[index][1] - offset[1])) for index in (pose.LEFT_SHOULDER, pose.RIGHT_SHOULDER))
    impact_line = tuple((float(impact[index][0] - offset[0]), float(impact[index][1] - offset[1])) for index in (pose.LEFT_SHOULDER, pose.RIGHT_SHOULDER))
    assert (address_line, cfg.overlay["corrected_color"]) in lines
    assert (impact_line, cfg.overlay["captured_color"]) in lines


def test_finish_path_is_ordered_and_crop_contains_every_endpoint(tmp_path, monkeypatch):
    from swinglab import focused_evidence as focused
    snapshot = replace(_snapshot(tmp_path), finish_ankle_midpoints=((80., 850.), (400., 700.), (720., 860.)))
    paths, offsets = [], []
    real_crop = focused._crop
    def recording_crop(image, points):
        cropped, offset = real_crop(image, points); offsets.append(offset); return cropped, offset
    monkeypatch.setattr(focused, "_crop", recording_crop)
    real_draw = focused.ImageDraw.Draw
    class Recorder:
        def __init__(self, wrapped): self.wrapped = wrapped
        def line(self, xy, **kwargs): paths.append(tuple(xy)); return self.wrapped.line(xy, **kwargs)
        def __getattr__(self, name): return getattr(self.wrapped, name)
    monkeypatch.setattr(focused.ImageDraw, "Draw", lambda image: Recorder(real_draw(image)))
    artifact = focused.render_focused_evidence(focused.FocusedEvidenceSelection(_rule(EvidenceKind.FINISH_STABILITY, "finish_balance_sw", EventId.FINISH), snapshot, 1, 1, 1, None), out_path=tmp_path / "finish.png", relative_path="media/finish.png", cfg=Config())
    expected = tuple((x - offsets[0][0], y - offsets[0][1]) for x, y in snapshot.finish_ankle_midpoints)
    assert expected in paths
    with Image.open(artifact.path) as image:
        assert all(0 <= x < image.width and 0 <= y < image.height for x, y in expected)


def test_missing_required_landmark_fails_before_output_exists(tmp_path):
    from swinglab import focused_evidence as focused
    snapshot = _snapshot(tmp_path)
    top = dict(snapshot.event_landmarks[EventId.TOP]); del top[pose.NOSE]
    snapshot = replace(snapshot, event_landmarks=MappingProxyType({**snapshot.event_landmarks, EventId.TOP: top}))
    out = tmp_path / "missing.png"
    with pytest.raises(focused.FocusedEvidenceRenderError, match="required landmarks"):
        focused.render_focused_evidence(focused.FocusedEvidenceSelection(_rule(), snapshot, 1, 1, 1, None), out_path=out, relative_path="media/missing.png", cfg=Config())
    assert not out.exists()


def test_crop_preserves_asymmetric_source_orientation(tmp_path):
    from swinglab import focused_evidence as focused
    snapshot = _snapshot(tmp_path)
    frame = snapshot.event_frames[EventId.TOP]
    source = Image.new("RGB", (800, 1000), "red"); source.paste("blue", (510, 0, 800, 1000)); source.save(frame)
    artifact = focused.render_focused_evidence(focused.FocusedEvidenceSelection(_rule(), snapshot, 1, 1, 1, None), out_path=tmp_path / "orientation.png", relative_path="media/orientation.png", cfg=Config())
    with Image.open(artifact.path).convert("RGB") as image:
        assert image.getpixel((0, image.height - 1)) == (255, 0, 0)
        assert image.getpixel((image.width - 1, image.height - 1)) == (0, 0, 255)


def test_dtl_timing_pixels_and_visible_text_exclude_body_semantics(tmp_path, monkeypatch):
    from swinglab import focused_evidence as focused
    snapshot = _snapshot(tmp_path)
    cfg = Config(); cfg.overlay["captured_color"] = "#f06a00"; cfg.overlay["corrected_color"] = "#00ff00"
    texts = []
    real_draw = focused.ImageDraw.Draw
    class Recorder:
        def __init__(self, wrapped): self.wrapped = wrapped
        def text(self, xy, value, **kwargs): texts.append(str(value)); return self.wrapped.text(xy, value, **kwargs)
        def __getattr__(self, name): return getattr(self.wrapped, name)
    monkeypatch.setattr(focused.ImageDraw, "Draw", lambda image: Recorder(real_draw(image)))
    rule = _rule(EvidenceKind.TEMPO_TIMELINE, "tempo_ratio", None)
    artifact = focused.render_focused_evidence(focused.FocusedEvidenceSelection(rule, snapshot, 1, 1, None, None, tempo_ratio_std=.18), out_path=tmp_path / "dtl.png", relative_path="media/dtl.png", cfg=cfg, angle="dtl")
    pixels = np.asarray(Image.open(artifact.path).convert("RGB"))
    assert not np.any(np.all(pixels == (0, 255, 0), axis=-1))
    visible = " ".join(texts).lower()
    assert all(event.method.value in visible for event in snapshot.events)
    assert all(term not in visible for term in ("body", "centerline", "boundary", "toward", "away"))
