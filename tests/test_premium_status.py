"""Premium status surface contracts across the customer-visible states."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape


TEMPLATES = Path(__file__).resolve().parents[1] / "swinglab" / "templates"
ENV = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(["html", "j2"]),
)


def job(**overrides):
    values = {
        "id": "session-123",
        "swings_total": 0,
        "swings_done": 0,
        "fast": False,
        "log": [],
        "error": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def brief(*, refilm=False):
    return SimpleNamespace(
        refilm_required=refilm,
        warning=("The full body was not readable." if refilm else None),
        strength=None if refilm else "Tempo stayed inside its coaching line.",
        focus_name=("Capture a clear face-on view" if refilm else "Head sway"),
        focus_value=None if refilm else "0.44 SW",
        benchmark_text=None if refilm else "flagged above 0.35 SW",
        why="This measurement crossed its coaching line.",
        fix="Keep the turn centered and re-film with the same setup.",
        recurring_sessions=2,
        remaining_issues=1,
        drill=SimpleNamespace(
            name="Wall-turn rehearsal",
            aim="Rehearse a centered turn.",
            dosage="3 x 8 swings",
            success_metric="Re-film below 0.35 SW.",
        ),
        trend="The last comparable session was steadier",
        clean=False,
    )


def proof():
    return SimpleNamespace(
        tone="positive",
        target_name="Head sway",
        heading="Matched improvement confirmed",
        summary="Two matched follow-ups moved in the right direction.",
        detail="This confirms a measurement pattern, not its cause.",
        next_step="Keep the same setup for the next check-in.",
        accepted_refilm_count=2,
    )


def practice_proof():
    return SimpleNamespace(
        tone="neutral",
        heading="Practice context recorded",
        summary="Your practice receipt stays attached to this target.",
        detail="It does not change the measurement verdict.",
        next_step="Re-film with the same context.",
        practice_session_count=1,
        practice_minutes=20,
    )


def render(**overrides):
    context = {
        "brand": {
            "name": "CaddieInsight",
            "primary_color": "#1a5c38",
            "accent_color": "#e8720c",
            "footer_text": "CaddieInsight swing analysis.",
            "logo_url": None,
        },
        "storefront_url": "",
        "shop_enabled": False,
        "require_account": False,
        "progress_pro_only": False,
        "user": None,
        "header_profile": None,
        "current_path": "/session/session-123",
        "job": job(),
        "done": False,
        "failed": False,
        "error_help": None,
        "queue_position": None,
        "caddie_brief": None,
        "proof_cycle": None,
        "proof_cycle_practice": None,
        "refilm_needed": False,
        "legacy_report": False,
        "current_report_only": False,
        "capture_report_available": False,
        "gear": [],
    }
    context.update(overrides)
    return ENV.get_template("web_status.html.j2").render(**context)


def test_queued_state_has_plain_language_indeterminate_progress_and_polling():
    html = render(queue_position=3)

    assert '<h1 id="live-title">' in html and "Waiting in line…" in html
    assert 'id="live-bar-wrap" role="progressbar"' in html
    assert "status-progress is-indeterminate" in html
    progress_start = html.index('id="live-bar-wrap"')
    progress_tag = html[progress_start : html.index(">", progress_start)]
    assert "aria-valuenow" not in progress_tag
    assert "You are #3 in line" in html
    assert 'fetch("/api/session/session-123"' in html
    assert "setInterval(poll, 1500)" in html


def test_processing_state_exposes_real_counts_without_fake_percentage():
    html = render(
        job=job(
            swings_total=5,
            swings_done=2,
            log=["Detecting strikes", "Rendering swing 3"],
        )
    )

    assert "Analyzing your swing…" in html
    assert 'aria-valuemax="5"' in html
    assert 'aria-valuenow="2"' in html
    assert 'style="width: 40%"' in html
    assert "Swing 3 of 5" in html
    assert '<details class="technical-details">' in html
    assert "Behind-the-scenes processing details" in html
    assert "Detecting strikes\nRendering swing 3" in html


def test_failed_state_keeps_actionable_guidance_visible():
    help_copy = SimpleNamespace(
        message="No ball strikes were detected.",
        tips=("Keep sound on.", "Do not trim the clip."),
        checklist=True,
    )
    html = render(failed=True, error_help=help_copy, job=job(error="raw error"))

    assert "Analysis failed" in html
    assert "We couldn’t finish this swing." in html
    assert "No ball strikes were detected." in html
    assert "Keep sound on." in html and "Do not trim the clip." in html
    assert 'href="/#filming-checklist"' in html
    assert '<a class="button" href="/">Try again</a>' in html
    assert "Behind-the-scenes processing details" in html


def test_coaching_result_orders_priority_practice_proof_and_actions():
    html = render(
        done=True,
        job=job(swings_total=3, swings_done=3),
        caddie_brief=brief(),
        proof_cycle=proof(),
        proof_cycle_practice=practice_proof(),
    )

    assert html.count("<h1") == 1
    assert "Your caddie's read" in html
    priority = html.index("<h2>Fix first</h2>")
    practice = html.index("<h2>Practice this</h2>")
    proof_card = html.index("data-proof-cycle")
    primary_action = html.index("Practice, then re-film")
    report_action = html.index("See your full coaching plan")
    assert priority < practice < proof_card < primary_action < report_action
    assert "<h2>Matched improvement confirmed</h2>" in html
    assert "<h2>Practice context recorded</h2>" in html
    assert "data-proof-cycle-practice" in html
    assert 'href="/session/session-123/report"' in html
    assert "2 matched follow-ups counted" in html


def test_refilm_state_leads_with_capture_fix_and_keeps_details_link():
    html = render(
        done=True,
        refilm_needed=True,
        caddie_brief=brief(refilm=True),
        capture_report_available=True,
    )

    assert "<h1>Re-film before coaching</h1>" in html
    assert "Why this session stopped" in html
    assert "The full body was not readable." in html
    assert '<a class="button" href="/">Re-film with the checklist</a>' in html
    assert "Review capture details" in html


@pytest.mark.parametrize(
    ("current_only", "expected"),
    [
        (True, "structured metrics could not be read"),
        (False, "created by an earlier CaddieInsight version"),
    ],
)
def test_report_only_and_legacy_sessions_keep_the_original_report(
    current_only, expected
):
    html = render(
        done=True,
        legacy_report=True,
        current_report_only=current_only,
    )

    assert "<h1>Results ready</h1>" in html
    assert expected in " ".join(html.split())
    assert 'href="/session/session-123/report">View original report</a>' in html


def test_capture_only_without_structured_brief_never_invents_coaching():
    html = render(
        done=True,
        refilm_needed=True,
        capture_report_available=True,
    )

    assert "<h1>Re-film before coaching</h1>" in html
    assert "did not produce enough readable motion data" in html
    assert "Review capture details" in html
    assert "Fix first" not in html
