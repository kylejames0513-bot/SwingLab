"""Premium upload journey keeps the analysis contract clear and intact."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE


@pytest.fixture
def upload_html(tmp_path, monkeypatch) -> str:
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    app = create_app(Config(), sessions_dir=tmp_path / "sessions")
    response = TestClient(app).get("/")
    assert response.status_code == 200
    return response.text


def test_primary_decisions_follow_club_hand_angle_video_then_analyze(upload_html):
    markers = (
        '<select id="club"',
        '<select id="hand"',
        'name="angle"',
        '<input id="video"',
        '<details class="advanced"',
        '<button id="submit"',
    )
    positions = [upload_html.index(marker) for marker in markers]

    assert positions == sorted(positions)
    assert "First · Swing context" in upload_html
    assert "Next · Video" in upload_html


def test_club_is_unmistakably_required_and_sets_coaching_context(upload_html):
    compact = " ".join(upload_html.split())

    assert '<select id="club" name="club" required>' in upload_html
    assert '<option value="" disabled selected>Choose a club</option>' in upload_html
    assert "Golf club is required" in compact
    assert "coaching recommendations depend on the club you choose" in compact
    for club in ("driver", "fairway-wood", "hybrid", "iron", "wedge"):
        assert f'value="{club}"' in upload_html


def test_upload_contract_hooks_and_fields_are_preserved(upload_html):
    assert (
        '<form id="upload-form" action="/upload" method="post" '
        'enctype="multipart/form-data">'
    ) in upload_html
    for hook in (
        "upload-form",
        "club",
        "hand",
        "video",
        "drop",
        "drop-text",
        "fast",
        "strikes",
        "submit",
        "upload-progress",
        "upload-bar",
        "upload-label",
        "upload-error",
    ):
        assert f'id="{hook}"' in upload_html

    assert 'accept="video/*,.mov,.mp4,.m4v,.avi,.mkv" required' in upload_html
    assert '<option value="right" selected>Right-handed</option>' in upload_html
    assert '<option value="left" >Left-handed</option>' in upload_html
    assert re.search(r'name="angle" value="face-on"[^>]* required>', upload_html)
    assert re.search(r'name="angle" value="dtl"[^>]* required>', upload_html)
    assert 'name="level" value=""' in upload_html
    assert 'name="level" value="new"' in upload_html
    assert 'name="level" value="improving"' in upload_html
    assert 'name="level" value="experienced"' in upload_html
    assert 'id="fast" name="fast" type="checkbox"' in upload_html
    assert 'id="strikes" name="strikes" type="text"' in upload_html


def test_transfer_hook_responsive_layout_and_reduced_motion_remain_in_template():
    source = Path("swinglab/templates/web_upload.html.j2").read_text(
        encoding="utf-8"
    )

    assert (
        'id="transfer-check" name="transfer_check" type="checkbox" value="on"'
        in source
    )
    assert "grid-template-columns: minmax(0, 1fr) minmax(280px, 340px)" in source
    assert "@media (max-width: 900px)" in source
    assert ".upload-layout { grid-template-columns: 1fr; }" in source
    assert "@media (prefers-reduced-motion: reduce)" in source
    assert "usually under a minute" not in source
    assert "results in a fraction of the time" not in source


@pytest.mark.parametrize("pro", (False, True), ids=("free", "finite-pro"))
def test_exhausted_accounts_never_claim_to_be_ready_to_analyze(
    tmp_path, monkeypatch, pro
):
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.billing["free_per_month"] = 1
    cfg.billing["pro_per_month"] = 1
    app = create_app(cfg, sessions_dir=tmp_path / ("pro" if pro else "free"))
    users = app.state.users
    user = users.create(
        f"{'pro' if pro else 'free'}-exhausted@example.com", "longenough"
    )
    if pro:
        users.set_plan(user.id, "pro", "active")

    job = app.state.jobs.create_session(
        source_name="used.mov",
        club="iron",
        user_id=user.id,
    )
    job.status = DONE
    result_dir = job.session_dir / "out"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "report.html").write_text(
        "<html><body>legacy result</body></html>", encoding="utf-8"
    )
    job.report_rel = "out/report.html"
    app.state.jobs._save(job)

    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    html = client.get("/").text

    assert "Allowance used" in html
    assert "No analyses left this month" in html
    assert "Your allowance resets on the 1st" in html
    assert "Ready to analyze" not in html
    assert 'id="submit" type="submit" disabled aria-disabled="true"' in html
    assert "var uploadAllowed = false;" in html


def test_unlimited_free_account_has_truthful_ready_status(tmp_path, monkeypatch):
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.billing["free_per_month"] = 0
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    user = app.state.users.create("unlimited-free@example.com", "longenough")
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    html = client.get("/").text

    assert "Ready to analyze" in html
    assert "Unlimited full analyses" in html
    assert "0 full analys" not in html
    assert 'id="submit" type="submit">Upload &amp; analyze</button>' in html
    assert "var uploadAllowed = true;" in html
