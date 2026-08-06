from __future__ import annotations

from pathlib import PurePosixPath

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
    source = prepare_report_input(
        fake_video(), swings, session_stats([swings[0]["metrics"]]), [], "right", cfg,
        replay_locked=True, navigation=ReportNavigation("/app", None, None),
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
    source = prepare_report_input(
        fake_video(), [swing], session_stats([swing["metrics"]]), [], "right", cfg,
        media=media,
    )
    document = build_report_document(source, cfg)
    detail = document.depth.swings[0]
    assert detail.video_poster_media_key == "poster-one"
    assert detail.slow_motion_media_key == "slow-one"
    assert set(document.media_by_key) == {"poster-one", "slow-one"}
