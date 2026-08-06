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
    from swinglab.focused_evidence import RENDERERS
    assert set(RENDERERS) == set(EvidenceKind)
