from __future__ import annotations

from dataclasses import fields, replace
import html as html_lib
from pathlib import Path
import re

import pytest

from swinglab.config import Config
from swinglab.report import REPORT_FORMAT_VERSION, write_report_html
from swinglab.report_bundle import begin_report_bundle, build_report_bundle
from swinglab.report_html import write_report_document_html
from swinglab.report_presenter import (
    ReportDepthContent,
    ReportDocument,
    SwingDetail,
)
from swinglab.report_view import (
    GUIDED_REPORT_PRESENTATION_VERSION,
    report_view_from_dict,
    report_view_to_dict,
)
from tests.report_bundle_fixtures import guided_bundle_inputs
from tests.report_view_fixtures import (
    LOCKED_REPLAY_SENTINEL,
    report_document_fixture,
)
from tests.test_report import fake_swing, fake_video


def field_names(model: type) -> set[str]:
    return {field.name for field in fields(model)}


def render_fixture_path(
    tmp_path: Path, name: str, *, sample_banner: dict | None = None
) -> Path:
    from swinglab.report_html import write_report_document_html

    out_path = tmp_path / f"{name}.html"
    return write_report_document_html(
        out_path,
        report_document_fixture(name),
        cfg=Config(),
        sample_banner=sample_banner,
    )


def render_fixture(
    tmp_path: Path, name: str, *, sample_banner: dict | None = None
) -> str:
    return render_fixture_path(
        tmp_path, name, sample_banner=sample_banner
    ).read_text(encoding="utf-8")


def render_guided(tmp_path: Path, document: ReportDocument) -> str:
    out_path = tmp_path / "guided.html"
    write_report_document_html(out_path, document, cfg=Config())
    return out_path.read_text(encoding="utf-8")


def positions(html: str, *needles: str) -> tuple[int, ...]:
    return tuple(html.index(needle) for needle in needles)


def report_block(html: str, name: str, next_name: str | None = None) -> str:
    start = html.index(f'data-report-block="{name}"')
    end = (
        html.index(f'data-report-block="{next_name}"', start)
        if next_name is not None
        else len(html)
    )
    return html[start:end]


def phase_card(html: str, phase_id: str) -> str:
    match = re.search(
        rf'<details class="phase-card[^>]*data-phase-id="{phase_id}"[^>]*>.*?</details>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None, f"missing phase card: {phase_id}"
    return match.group(0)


def normalized_html_text(fragment: str) -> str:
    return " ".join(
        html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split()
    )


def optional_section(html: str, section_id: str) -> str:
    match = re.search(
        rf'<details[^>]*data-optional-section="{re.escape(section_id)}"[^>]*>.*?</details>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None, f"missing optional section {section_id}"
    return match.group(0)


def test_report_document_exposes_all_server_owned_html_depth():
    assert field_names(ReportDocument) == {"view", "depth", "media_by_key"}
    assert {
        "swings", "secondary_findings", "strengths", "measurements",
        "session_details", "glossary", "limitations", "gear", "navigation",
    } <= field_names(ReportDepthContent)
    assert {
        "key_positions_media_key", "key_positions_alt_text",
        "slow_motion_media_key", "slow_motion_caption",
        "coach_replay_media_key", "coach_replay_caption",
        "video_poster_media_key", "video_poster_alt_text",
        "print_playback_reference", "replay_locked",
        "locked_replay_explanation",
    } <= field_names(SwingDetail)


def test_guided_writer_emits_contract_markers_outcome_and_focused_media(tmp_path: Path):
    document = report_document_fixture()

    html = render_fixture(tmp_path, "coaching-improve-clear")

    header = html.encode("utf-8")[:8192]
    assert header.count(
        f'name="caddieinsight-report-format" content="{REPORT_FORMAT_VERSION}"'.encode()
    ) == 1
    assert header.count(
        b'name="caddieinsight-report-presentation" content="guided-report-v1"'
    ) == 1
    assert header.count(
        b'name="caddieinsight-report-outcome" content="coaching_ready"'
    ) == 1
    assert document.view.presentation_version == GUIDED_REPORT_PRESENTATION_VERSION
    assert document.view.visual_evidence is not None
    assert document.view.visual_evidence.media_key is not None
    assert document.media_by_key[document.view.visual_evidence.media_key].relative_path in html

    legacy_path = write_report_html(
        tmp_path / "legacy.html",
        fake_video(),
        [fake_swing(1, tempo=2.0)],
        {},
        [],
        "right",
        Config(),
    )
    assert 'name="caddieinsight-report-presentation" content="premium-coach-v2"' in legacy_path.read_text(encoding="utf-8")


def test_production_writer_builds_a_strictly_validated_bundle(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    attempt = begin_report_bundle(session_dir)
    inputs = guided_bundle_inputs(tmp_path)
    inputs["html_writer"] = write_report_document_html

    staged = build_report_bundle(attempt, **inputs)

    assert staged.report_path.is_file()
    assert staged.manifest.presentation_version == GUIDED_REPORT_PRESENTATION_VERSION
    evidence = staged.view.visual_evidence
    assert staged.view.next_move is not None
    assert evidence is not None and evidence.supporting_measurement is not None
    assert (
        staged.view.next_move.measurement_detail_id
        == evidence.supporting_measurement.id
    )
    selected_phase = next(
        phase
        for phase in staged.view.phases
        if phase.id is staged.view.next_move.category
    )
    assert evidence.supporting_measurement in selected_phase.measurements
    rendered = staged.report_path.read_text(encoding="utf-8")
    assert f'href="#{evidence.supporting_measurement.id}"' in rendered
    assert "See measurement" in rendered


def test_guided_writer_rejects_unknown_media_before_writing(tmp_path: Path):
    document = report_document_fixture()
    assert document.view.visual_evidence is not None
    broken_view = replace(
        document.view,
        visual_evidence=replace(
            document.view.visual_evidence,
            media_key="missing-priority",
        ),
    )
    broken_document = replace(document, view=broken_view)
    out_path = tmp_path / "broken.html"

    with pytest.raises(
        ValueError,
        match="^guided report references unknown media key: missing-priority$",
    ):
        write_report_document_html(out_path, broken_document, cfg=Config())

    assert not out_path.exists()


@pytest.mark.parametrize(
    "name",
    (
        "coaching-improve-clear-long-copy",
        "coaching-protect-clear",
        "coaching-improve-limited",
        "coaching-improve-visual-unavailable",
        "coaching-dtl-clear",
    ),
)
def test_named_coaching_documents_round_trip_through_persisted_view(name: str):
    view = report_document_fixture(name).view

    assert report_view_from_dict(report_view_to_dict(view)) == view


def test_coaching_report_has_one_action_first_journey(tmp_path: Path):
    document = report_document_fixture(
        name="coaching-improve-clear-long-copy"
    )
    html = render_guided(tmp_path, document)

    order = positions(
        html,
        'data-report-block="next-move"',
        'data-report-block="understand"',
        'data-report-block="practice"',
        'data-report-block="refilm"',
    )
    assert order == tuple(sorted(order))
    assert html.count('data-canonical="priority"') == 1
    assert html.count('data-canonical="practice"') == 1
    assert html.count('data-canonical="refilm"') == 1
    assert html.count(document.view.practice.name) == 1
    assert html.count(document.view.refilm.target.text) == 1

    preview = report_block(html, "next-move", "understand")
    assert document.view.practice.name not in preview
    assert document.view.refilm.target.text not in preview
    assert positions(preview, "Understand", "Practice", "Re-film") == tuple(
        sorted(positions(preview, "Understand", "Practice", "Re-film"))
    )


def test_improve_and_protect_use_server_authored_journey_copy(tmp_path: Path):
    improve = report_document_fixture("coaching-improve-clear")
    improve_html = render_guided(tmp_path, improve)
    assert improve.view.next_move.eyebrow in report_block(
        improve_html, "next-move", "understand"
    )

    protect = report_document_fixture("coaching-protect-clear")
    protect_html = render_guided(tmp_path, protect)
    next_move = report_block(protect_html, "next-move", "understand")
    assert "Protect this" in next_move
    assert "Strength to protect" in next_move

    going_back = phase_card(protect_html, "going_back")
    assert "Steady" in going_back
    assert "Keep this ordinary steady pattern." in going_back


def test_focused_evidence_preserves_provenance_counts_and_measurement_link(
    tmp_path: Path,
):
    document = report_document_fixture("coaching-improve-clear")
    evidence = document.view.visual_evidence
    assert evidence is not None and evidence.supporting_measurement is not None
    html = render_guided(tmp_path, document)
    understand = report_block(html, "understand", "practice")

    assert "Swing 1" in understand
    assert "Going Back" in understand
    assert "Top from highest hand position" in understand
    assert 'data-evidence-tracking="clear"' in understand
    assert 'data-evidence-count="detected">3<' in understand
    assert 'data-evidence-count="readable">2<' in understand
    assert 'data-evidence-count="triggered">1<' in understand
    assert "Head rose" in understand
    assert "Address" in understand
    assert "Keep within boundary" in understand
    assert "The rise is visible." in understand
    assert f'alt="{evidence.alt_text}"' in understand
    assert f'href="#{evidence.supporting_measurement.id}"' in html
    assert f'id="{evidence.supporting_measurement.id}"' in understand
    assert "See measurement" in understand
    assert evidence.supporting_measurement.plain_value in understand
    assert evidence.supporting_measurement.plain_value not in report_block(
        html, "next-move", "understand"
    )

    limited = report_document_fixture("coaching-improve-limited")
    limited_html = render_guided(tmp_path, limited)
    limited_understand = report_block(limited_html, "understand", "practice")
    assert 'data-evidence-count="detected">2<' in limited_understand
    assert 'data-evidence-count="readable">1<' in limited_understand
    assert 'data-evidence-count="triggered"' not in limited_understand


def test_dtl_evidence_renders_persisted_event_provenance_in_order(tmp_path: Path):
    document = report_document_fixture("coaching-dtl-clear")
    html = render_guided(tmp_path, document)
    understand = report_block(html, "understand", "practice")
    evidence = document.view.visual_evidence
    assert evidence is not None and evidence.supporting_measurement is not None
    expected = (
        ("address", "Address", 120, "Address from opening setup"),
        ("top", "Top", 760, "Top from highest hand position"),
        ("impact", "Impact", 1090, "Impact estimated from sound"),
        ("finish", "Finish", 1680, "Finish after impact"),
    )

    event_positions = positions(
        understand,
        *(f'data-event="{event_id}"' for event_id, *_rest in expected),
    )
    assert event_positions == tuple(sorted(event_positions))
    for index, (event_id, label, timestamp, method) in enumerate(expected):
        start = understand.index(f'data-event="{event_id}"')
        end = (
            understand.index(
                f'data-event="{expected[index + 1][0]}"', start
            )
            if index + 1 < len(expected)
            else understand.index("</ol>", start)
        )
        event_html = understand[start:end]
        assert label in event_html
        assert f'data-event-timestamp="{timestamp}"' in event_html
        assert f"{timestamp} ms" in event_html
        assert method in event_html

    assert understand.count("data-event-timestamp=") == 4
    assert "640 ms" not in understand
    assert "330 ms" not in understand
    measurement = evidence.supporting_measurement
    disclosure = re.search(
        rf'<details class="measurement-detail" id="{re.escape(measurement.id)}">.*?</details>',
        understand,
        flags=re.DOTALL,
    )
    assert disclosure is not None
    assert measurement.plain_value in disclosure.group(0)
    assert understand.count(measurement.plain_value) >= 1
    assert measurement.plain_value not in evidence.observed_label
    assert f'id="{measurement.id}"' in understand
    assert measurement.plain_value in re.search(
        rf'<details class="measurement-detail" id="{re.escape(measurement.id)}">.*?</details>',
        understand,
        flags=re.DOTALL,
    ).group(0)


def test_visual_unavailable_keeps_explanation_without_fallback_image(tmp_path: Path):
    document = report_document_fixture("coaching-improve-visual-unavailable")
    evidence = document.view.visual_evidence
    assert evidence is not None
    html = render_guided(tmp_path, document)
    understand = report_block(html, "understand", "practice")

    assert "Visual unavailable" in understand
    assert evidence.observation in understand
    assert "Top from highest hand position" in understand
    assert 'data-evidence-tracking="clear"' in understand
    assert "Focused replay is unavailable" in understand
    assert document.view.trust.explanation in understand
    assert 'class="focused-evidence"' not in understand
    assert 'src="media/focus-1.jpg"' not in html
    assert "See measurement" in understand


def test_limited_trust_appears_on_next_move(tmp_path: Path):
    document = report_document_fixture("coaching-improve-limited")
    html = render_guided(tmp_path, document)
    next_move = report_block(html, "next-move", "understand")
    assert 'data-trust-state="limited"' in next_move
    assert document.view.trust.explanation in next_move
    assert "Drill ready" in next_move
    assert "Target set" in next_move


def test_understand_shows_evidence_legend_and_phase_measurements(tmp_path: Path):
    document = report_document_fixture("coaching-improve-clear")
    html = render_guided(tmp_path, document)
    understand = report_block(html, "understand", "practice")
    assert "Evidence color key" in understand
    assert "Orange marks what was observed" in understand
    assert "Green marks the starting reference" in understand
    assert "Dashed line marks the coaching boundary" in understand
    going_back = phase_card(html, "going_back")
    measurement = document.view.phases[1].measurements[0]
    assert measurement.label in going_back
    assert measurement.plain_value in going_back
    assert measurement.explanation in going_back


def test_priority_playback_links_timestamp_when_depth_has_matching_swing(
    tmp_path: Path,
):
    document = report_document_fixture("pro-unlocked")
    evidence = document.view.visual_evidence
    assert evidence is not None and evidence.timestamp_ms is not None
    assert document.depth.swings
    assert document.depth.swings[0].slow_motion_media_key == "slow-motion-1"

    html = render_guided(tmp_path, document)
    understand = report_block(html, "understand", "practice")
    assert 'data-priority-playback="1"' in understand
    assert "Watch this swing" in understand
    assert " muted" in understand
    assert " playsinline" in understand
    assert "Open at this moment" in understand
    stamp = f"#t={(evidence.timestamp_ms / 1000):g}"
    assert stamp in understand
    assert "slow-motion-1" in understand


def test_phase_cards_follow_server_order_status_and_expansion(tmp_path: Path):
    face_on = report_document_fixture("coaching-improve-clear")
    face_html = render_guided(tmp_path, face_on)
    phase_tags = re.findall(
        r'<details class="phase-card[^>]*>', face_html, flags=re.DOTALL
    )
    assert len(phase_tags) == 5
    assert sum(bool(re.search(r"\sopen(?:\s|>)", tag)) for tag in phase_tags) == 1
    phase_order = positions(
        face_html,
        *(f'data-phase-id="{phase.id}"' for phase in face_on.view.phases),
    )
    assert phase_order == tuple(sorted(phase_order))
    for phase in face_on.view.phases:
        card = phase_card(face_html, phase.id)
        assert phase.status_label in card
        assert 'aria-hidden="true"' in card

    protect_html = render_guided(
        tmp_path, report_document_fixture("coaching-protect-clear")
    )
    protect_tags = re.findall(
        r'<details class="phase-card[^>]*>', protect_html, flags=re.DOTALL
    )
    assert len(protect_tags) == 5
    assert sum(bool(re.search(r"\sopen(?:\s|>)", tag)) for tag in protect_tags) == 1

    dtl_html = render_guided(tmp_path, report_document_fixture("coaching-dtl-clear"))
    dtl_tags = re.findall(r'<details class="phase-card[^>]*>', dtl_html, flags=re.DOTALL)
    assert len(dtl_tags) == 1
    timing = phase_card(dtl_html, "timing_rhythm")
    assert "Timing and rhythm" in timing
    for face_label in (
        "Setup",
        "Going back",
        "Transition and downswing",
        "Impact",
        "Finish",
    ):
        assert f">{face_label}<" not in timing

    limited_html = render_guided(
        tmp_path, report_document_fixture("coaching-improve-limited")
    )
    impact = phase_card(limited_html, "impact")
    assert "Not measured" in impact
    assert "Impact timing could not be measured from this clip." in impact
    assert "Swing timing is estimated" in impact


def test_measurement_limitations_practice_and_refilm_keep_authored_content(
    tmp_path: Path,
):
    document = report_document_fixture("coaching-improve-clear-long-copy")
    html = render_guided(tmp_path, document)
    understand = report_block(html, "understand", "practice")
    practice = report_block(html, "practice", "refilm")
    refilm = report_block(html, "refilm")

    assert "What this video can measure" in understand
    for limitation in document.depth.limitations:
        assert limitation in understand
    assert (
        "2D phone-video evidence, not a personalized ideal pose" in understand
    )

    assert document.view.practice.name in practice
    assert document.view.practice.aim in practice
    summary_order = positions(practice, *document.view.practice.summary_steps)
    assert summary_order == tuple(sorted(summary_order))
    assert document.view.practice.setup in practice
    assert document.view.practice.feel_cue in practice
    assert document.view.practice.dosage in practice
    assert document.view.practice.equipment in practice
    assert "Instructional illustration — not your measured pose" in practice
    assert 'src="media/drill-1.jpg"' in practice
    assert "Full drill steps" in practice
    assert "Try a different drill" in practice
    assert document.view.practice.alternatives[0].name in practice
    assert 'href="#refilm"' in practice
    assert document.view.refilm.target.text not in practice

    checklist_order = positions(refilm, *document.view.refilm.checklist)
    assert checklist_order == tuple(sorted(checklist_order))
    assert refilm.count(document.view.refilm.target.text) == 1
    assert 'data-field="pass-mark"' in refilm
    for confirmation in (
        "Same club",
        "Same hand",
        "Same camera angle",
        "Same camera height",
        "Same framing",
        "Same effort",
    ):
        assert confirmation in refilm
    assert f'href="{document.depth.navigation.app_url}"' in refilm
    assert document.view.refilm.primary_action_label in refilm

    offline = report_document_fixture("coaching-improve-limited")
    offline_html = render_guided(tmp_path, offline)
    offline_refilm = report_block(offline_html, "refilm")
    assert 'data-offline-action="true"' in offline_refilm
    offline_action = re.search(
        r'<(?P<tag>[a-z]+)[^>]*data-offline-action="true"[^>]*>.*?</(?P=tag)>',
        offline_refilm,
        flags=re.DOTALL,
    )
    assert offline_action is not None
    assert 'role="note"' in offline_action.group(0).split(">", 1)[0]
    assert normalized_html_text(offline_action.group(0)) == (
        offline.view.refilm.primary_action_label
    )
    assert re.search(
        rf'<a[^>]*>{re.escape(offline.view.refilm.primary_action_label)}</a>',
        offline_refilm,
    ) is None
    assert "Full drill steps" not in report_block(
        offline_html, "practice", "refilm"
    )


@pytest.mark.parametrize(
    ("name", "reason_label", "correction", "primary_action"),
    (
        (
            "capture-only-angle",
            "Camera angle mismatch",
            "Move the camera to belt height directly face-on.",
            "Re-film from face-on",
        ),
        (
            "capture-only-tracking",
            "Tracking was unstable",
            "Use brighter, even light and keep your full body clear of obstructions.",
            "Re-film with clearer tracking",
        ),
        (
            "capture-only-no-readable-swing",
            "No readable swing",
            "Re-film with your full body visible.",
            "Re-film your swing",
        ),
    ),
)
def test_capture_only_is_an_ordered_recovery_path_without_partial_coaching(
    tmp_path: Path,
    name: str,
    reason_label: str,
    correction: str,
    primary_action: str,
):
    document = report_document_fixture(name)
    html = render_guided(tmp_path, document)
    guidance = document.view.capture_guidance
    assert guidance is not None

    order = positions(
        html,
        reason_label,
        guidance.explanation,
        correction,
        "Quick filming checklist",
        primary_action,
    )
    assert order == tuple(sorted(order))
    for item in guidance.checklist:
        assert item in html
    if guidance.secondary_action_label:
        assert guidance.secondary_action_label in html
    assert 'data-capture-playback="playback-1"' in html
    assert 'src="media/playback-1.mp4"' in html
    assert 'poster="media/capture-poster-1.jpg"' in html
    assert document.depth.swings[0].video_poster_alt_text in html

    forbidden = (
        'data-report-block="understand"',
        'data-report-block="practice"',
        'data-report-block="refilm"',
        'data-phase-id=',
        'data-canonical="priority"',
        "Coach cue:",
        "Pass mark",
        "Coach replay",
    )
    for marker in forbidden:
        assert marker not in html
    for gear in document.depth.gear:
        assert gear.url not in html


def test_capture_retry_failure_keeps_reason_and_both_recovery_actions(tmp_path: Path):
    document = report_document_fixture("capture-only-retry-failure")
    html = render_guided(tmp_path, document)
    guidance = document.view.capture_guidance
    assert guidance is not None

    assert guidance.reason_label in html
    assert guidance.explanation in html
    assert guidance.primary_action_label in html
    assert guidance.secondary_action_label in html
    assert re.search(
        rf'<a[^>]*data-capture-action="primary"[^>]*href="/"[^>]*>{re.escape(guidance.primary_action_label)}</a>',
        html,
    )
    assert re.search(
        rf'<a[^>]*data-capture-action="secondary"[^>]*href="/"[^>]*>{re.escape(guidance.secondary_action_label)}</a>',
        html,
    )
    assert 'data-report-block="practice"' not in html


def test_capture_playback_requires_the_server_safe_media_allowlist(tmp_path: Path):
    document = report_document_fixture("capture-only-angle")
    guidance = document.view.capture_guidance
    assert guidance is not None
    blocked = replace(
        document,
        view=replace(
            document.view,
            capture_guidance=replace(guidance, safe_media_keys=()),
        ),
    )

    html = render_guided(tmp_path, blocked)

    assert 'data-capture-playback=' not in html
    assert 'src="media/playback-1.mp4"' not in html


def test_optional_depth_uses_each_typed_payload_and_accessible_tables(tmp_path: Path):
    document = report_document_fixture("pro-unlocked")
    html = render_guided(tmp_path, document)

    for section in document.view.optional_sections:
        fragment = optional_section(html, section.id)
        assert section.label in fragment
        assert str(section.item_count) in fragment

    every_swing = optional_section(html, "every_swing")
    swing = document.depth.swings[0]
    assert swing.summary in every_swing
    assert swing.notes[0] in every_swing
    assert 'src="media/key-positions-1.jpg"' in every_swing

    replay = optional_section(html, "replay")
    assert 'src="media/slow-motion-1.mp4"' in replay
    assert 'src="media/coach-replay-1.mp4"' in replay
    assert swing.coach_replay_caption in replay

    secondary = optional_section(html, "secondary_findings")
    assert document.depth.secondary_findings[0].title in secondary
    assert document.depth.secondary_findings[0].cue in secondary
    assert 'data-report-block="practice"' not in secondary

    alternatives = optional_section(html, "alternative_drills")
    assert document.view.practice.alternatives[0].name in alternatives
    strengths = optional_section(html, "more_strengths")
    assert document.depth.strengths[0].title in strengths

    measurements = optional_section(html, "measurements")
    assert '<caption>' in measurements
    assert 'role="region"' in measurements
    assert 'tabindex="0"' in measurements
    assert 'scope="col"' in measurements
    assert document.depth.session_details[0].value in measurements
    assert document.depth.measurements[0].value in measurements
    assert document.depth.limitations[0] in measurements

    glossary = optional_section(html, "glossary")
    assert document.depth.glossary[0].term in glossary
    assert document.depth.glossary[0].definition in glossary
    gear = optional_section(html, "gear")
    assert document.depth.gear[0].label in gear
    assert document.depth.gear[0].url in gear
    practice_start = html.index('data-report-block="practice"')
    practice_end = html.index("</section>", practice_start)
    assert practice_end < html.index('data-optional-section="gear"')


def test_unavailable_optional_sections_do_not_appear(tmp_path: Path):
    document = report_document_fixture("coaching-improve-clear")
    html = render_guided(tmp_path, document)

    assert 'data-optional-section="measurements"' in html
    assert document.view.practice.alternatives[0].name not in html
    for section_id in (
        "every_swing",
        "replay",
        "secondary_findings",
        "more_strengths",
        "glossary",
        "gear",
    ):
        assert f'data-optional-section="{section_id}"' not in html


def test_locked_alternatives_explain_the_lock_and_locked_gear_stays_absent(
    tmp_path: Path,
):
    document = report_document_fixture("pro-unlocked")
    locked_sections = tuple(
        replace(section, available=False, locked=True, item_count=0)
        if section.id in {"alternative_drills", "gear"}
        else section
        for section in document.view.optional_sections
    )
    locked = replace(document, view=replace(document.view, optional_sections=locked_sections))

    html = render_guided(tmp_path, locked)

    alternatives = optional_section(html, "alternative_drills")
    assert "This section is locked." in alternatives
    assert document.view.practice.alternatives[0].name not in alternatives
    assert 'data-optional-section="gear"' not in html
    assert document.depth.gear[0].url not in html


def test_locked_replay_never_resolves_hidden_media_but_pro_replay_does(tmp_path: Path):
    locked = report_document_fixture("free-locked")
    locked_html = render_guided(tmp_path, locked)
    locked_replay = optional_section(locked_html, "replay")
    locked_replay_contract = next(
        section for section in locked.view.optional_sections if section.id == "replay"
    )
    locked_measurements = next(
        section
        for section in locked.view.optional_sections
        if section.id == "measurements"
    )
    assert not locked_replay_contract.available
    assert locked_replay_contract.locked
    assert locked_replay_contract.item_count == 0
    assert locked_measurements.item_count == len(locked.depth.measurements)
    assert locked.depth.swings[0].locked_replay_explanation in locked_replay
    assert "coach-replay-1" not in locked.media_by_key
    assert "coach-replay-1" not in locked_html
    assert LOCKED_REPLAY_SENTINEL not in repr(locked)
    assert LOCKED_REPLAY_SENTINEL not in locked_html

    unlocked = report_document_fixture("pro-unlocked")
    unlocked_html = render_guided(tmp_path, unlocked)
    unlocked_replay = optional_section(unlocked_html, "replay")
    unlocked_replay_contract = next(
        section for section in unlocked.view.optional_sections if section.id == "replay"
    )
    assert unlocked_replay_contract.available
    assert not unlocked_replay_contract.locked
    assert unlocked_replay_contract.item_count == 1
    assert 'src="media/coach-replay-1.mp4"' in unlocked_replay
    assert unlocked.depth.swings[0].coach_replay_caption in unlocked_replay


def test_capture_only_suppresses_malicious_depth_gear(tmp_path: Path):
    document = report_document_fixture("capture-only-angle")
    malicious = replace(
        document,
        depth=replace(
            document.depth,
            gear=(
                replace(
                    document.depth.gear[0],
                    label="SHOULD NOT RENDER",
                    url="/private-capture-gear",
                ),
            ),
        ),
    )
    html = render_guided(tmp_path, malicious)

    assert "SHOULD NOT RENDER" not in html
    assert "/private-capture-gear" not in html
