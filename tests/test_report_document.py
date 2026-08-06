from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

import pytest

from swinglab.caddie_brief import build_caddie_brief, scope_metrics_for_angle
from swinglab.coaching import issue_cards, priority_rule_version, strength_cards
from swinglab.drills import practice_plan
from swinglab.metrics import session_stats
from swinglab.report_view import Entitlement, MediaEntry, MediaRole
from swinglab.report_presenter import (
    ReportDocument,
    ReportNavigation,
    build_report_document,
    prepare_report_input,
)
from tests.report_view_fixtures import report_document_fixture
from tests.test_report import branded_cfg, fake_swing, fake_video


def _typed_strings(value):
    if isinstance(value, str):
        return (value,)
    if dataclasses.is_dataclass(value):
        return tuple(
            item
            for field in dataclasses.fields(value)
            for item in _typed_strings(getattr(value, field.name))
        )
    if isinstance(value, Mapping):
        return tuple(item for child in value.values() for item in _typed_strings(child))
    if isinstance(value, Sequence):
        return tuple(item for child in value for item in _typed_strings(child))
    return ()


def _depth_count(document: ReportDocument, section_id: str) -> int:
    alternatives = document.view.practice.alternatives if document.view.practice else ()
    return {
        "every_swing": len(document.depth.swings),
        "secondary_findings": len(document.depth.secondary_findings),
        "more_strengths": len(document.depth.strengths),
        "measurements": len(document.depth.measurements),
        "glossary": len(document.depth.glossary),
        "gear": len(document.depth.gear),
        "alternative_drills": len(alternatives),
        "replay": sum(item.coach_replay_media_key is not None for item in document.depth.swings),
    }[section_id]


def test_document_fixture_keeps_optional_counts_and_media_references_integral():
    document = report_document_fixture()
    for section in document.view.optional_sections:
        assert section.item_count == _depth_count(document, section.id.value)
    referenced = {
        key
        for swing in document.depth.swings
        for key in (
            swing.key_positions_media_key,
            swing.slow_motion_media_key,
            swing.coach_replay_media_key,
            swing.video_poster_media_key,
        )
        if key is not None
    }
    assert referenced <= document.media_by_key.keys()
    poster_keys = [swing.video_poster_media_key for swing in document.depth.swings]
    assert len([key for key in poster_keys if key is not None]) == len(set(key for key in poster_keys if key is not None))
    assert all(swing.print_playback_reference for swing in document.depth.swings)
    assert all(swing.slow_motion_caption for swing in document.depth.swings)


def test_prepare_report_input_matches_the_existing_direct_coaching_builders():
    cfg = branded_cfg()
    swings = [fake_swing(1, 2.0), fake_swing(2, 2.2)]
    metrics = [swing["metrics"] for swing in swings]
    stats = session_stats(metrics)
    source = prepare_report_input(fake_video(), swings, stats, ["Session note"], "right", cfg)
    scoped = scope_metrics_for_angle(metrics, "face_on")
    direct_brief = build_caddie_brief(scoped, stats, cfg, angle="face_on", rule_version=priority_rule_version(cfg))
    direct_issues = issue_cards(scoped, stats, cfg, rule_version=priority_rule_version(cfg))
    direct_strengths = strength_cards(scoped, cfg, stats)
    assert source.brief == direct_brief
    assert source.issues == tuple(direct_issues)
    assert source.primary_drill == direct_brief.drill
    direct_plan = practice_plan([card.flag for card in direct_issues], cfg)
    expected_alternatives = tuple(
        drill for block in direct_plan for drill in block["drills"]
        if drill.id != direct_brief.drill.id
    )
    assert tuple(source.alternative_drills) == expected_alternatives
    assert source.strengths == tuple(direct_strengths)
    assert source.primary_drill.success_metric == direct_brief.drill.success_metric


def test_document_boundary_owns_complete_prescription_navigation_and_paths():
    document = report_document_fixture()
    assert document.view.next_move.title == "Keep your head steadier"
    assert document.view.practice.full_steps == ("Set up by a wall.", "Make three slow turns.")
    assert document.view.refilm.target.text == "Keep head rise at or below 0.5 shoulder widths."
    assert document.depth.navigation == ReportNavigation("/", "/shop", "/collections/swinglab-gear")
    assert document.view.next_move.title != document.depth.secondary_findings[0].title
    assert len(document.view.practice.full_steps) == 2
    assert document.view.refilm.target.text not in document.depth.limitations
    for media in document.media_by_key.values():
        assert not PurePosixPath(media.relative_path).is_absolute()


def test_locked_replay_and_missing_posters_are_explicit_server_owned_states():
    cfg = branded_cfg()
    swings = [fake_swing(1, 2.0)]
    evidence = report_document_fixture().view.visual_evidence
    source = prepare_report_input(
        fake_video(), swings, session_stats([swings[0]["metrics"]]), [], "right", cfg,
        replay_locked=True, navigation=ReportNavigation("/app", None, None),
        visual_evidence=evidence,
    )
    document = build_report_document(source, cfg)
    detail = document.depth.swings[0]
    assert detail.coach_replay_media_key is None
    assert detail.locked_replay_explanation
    assert detail.video_poster_media_key is None
    assert detail.print_playback_reference
    for section in document.view.optional_sections:
        assert section.item_count == _depth_count(document, section.id.value)


def test_explicit_media_keys_are_preserved_without_filename_inference():
    cfg = branded_cfg()
    swing = fake_swing(1, 2.0)
    swing["poster"] = "media/poster-one.jpg"
    swing["slowmo"] = "media/slow-one.mp4"
    media = (
        MediaEntry("poster-one", MediaRole.VIDEO_POSTER, "image/jpeg", Entitlement.CORE, "media/poster-one.jpg", "a" * 64),
        MediaEntry("slow-one", MediaRole.SLOW_MOTION, "video/mp4", Entitlement.CORE, "media/slow-one.mp4", "b" * 64),
    )
    evidence = report_document_fixture().view.visual_evidence
    source = prepare_report_input(
        fake_video(), [swing], session_stats([swing["metrics"]]), [], "right", cfg,
        media=media, visual_evidence=evidence,
    )
    document = build_report_document(source, cfg)
    detail = document.depth.swings[0]
    assert detail.video_poster_media_key == "poster-one"
    assert detail.slow_motion_media_key == "slow-one"
    assert set(document.media_by_key) == {"poster-one", "slow-one"}


def test_capture_only_document_exposes_only_explicit_safe_playback_media():
    cfg = branded_cfg()
    swing = fake_swing(1, 2.0)
    swing["slowmo"] = "media/safe.mp4"
    swing["overlay"] = "media/unsafe.jpg"
    swing["poster"] = "media/unsafe-poster.jpg"
    media = (
        MediaEntry("safe", MediaRole.CAPTURE_PLAYBACK, "video/mp4", Entitlement.CORE, "media/safe.mp4", "a" * 64),
        MediaEntry("unsafe", MediaRole.KEY_POSITIONS, "image/jpeg", Entitlement.CORE, "media/unsafe.jpg", "b" * 64),
        MediaEntry("unsafe-poster", MediaRole.VIDEO_POSTER, "image/jpeg", Entitlement.CORE, "media/unsafe-poster.jpg", "c" * 64),
    )
    source = prepare_report_input(
        fake_video(), [swing], session_stats([swing["metrics"]]),
        ["Tracking was unstable; numbers may be off."], "right", cfg,
        media=media, safe_media_keys=("safe",),
    )
    document = build_report_document(source, cfg)
    assert document.view.outcome.value == "capture_only"
    assert set(document.media_by_key) == {"safe"}
    detail = document.depth.swings[0]
    assert detail.slow_motion_media_key == "safe"
    assert detail.key_positions_media_key is None
    assert detail.video_poster_media_key is None
    assert detail.coach_replay_media_key is None
    assert document.depth.secondary_findings == ()
    assert document.depth.strengths == ()
    assert document.depth.measurements == ()
    assert document.depth.glossary == ()
    assert document.depth.limitations == ()
    assert document.depth.gear == ()


@pytest.mark.parametrize("relative_path", [
    "/private/x.jpg",
    r"C:\private\x.jpg",
    r"\\server\private\x.jpg",
])
def test_document_rejects_absolute_media_paths_on_all_platforms(relative_path):
    cfg = branded_cfg()
    swing = fake_swing(1, 2.0)
    media = (MediaEntry("bad", MediaRole.SLOW_MOTION, "video/mp4", Entitlement.CORE, relative_path, "a" * 64),)
    source = prepare_report_input(
        fake_video(), [swing], session_stats([swing["metrics"]]), [], "right", cfg,
        media=media,
    )
    with pytest.raises(ValueError, match="relative"):
        build_report_document(source, cfg)


def test_document_never_exposes_the_source_video_filename():
    cfg = branded_cfg()
    swing = fake_swing(1, 2.0)
    video = dataclasses.replace(fake_video(), path=Path("customer-secret-2026.mov"))
    source = prepare_report_input(
        video, [swing], session_stats([swing["metrics"]]), [], "right", cfg,
    )
    document = build_report_document(source, cfg)
    assert "customer-secret-2026" not in repr(document)


def test_production_document_owns_priority_prescription_target_and_navigation():
    cfg = branded_cfg()
    swing = fake_swing(1, 2.0)
    navigation = ReportNavigation("/app", "/shop", "/shop/gear")
    fixture = report_document_fixture()
    source = prepare_report_input(
        fake_video(), [swing], session_stats([swing["metrics"]]), [], "right", cfg,
        navigation=navigation, visual_evidence=fixture.view.visual_evidence,
        media=tuple(fixture.media_by_key.values()),
    )
    document = build_report_document(source, cfg)
    assert document.view.next_move.title == source.brief.focus_name
    assert document.view.practice.full_steps == source.primary_drill.protocol
    assert document.view.refilm.target.text
    assert document.depth.navigation == navigation
    strings = _typed_strings(document)
    assert strings.count(document.view.next_move.title) == 1
    assert strings.count(document.view.refilm.target.text) == 1
    assert all(strings.count(step) == 1 for step in document.view.practice.full_steps)
