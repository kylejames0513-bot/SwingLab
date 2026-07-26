"""The coach-replay Pro gate (billing.replay_pro_only).

The annotated replay is the report's most shareable artifact and the one
clean quality line between free and Pro. These tests pin the whole matrix —
gate on/off x plan (free/Pro) x mode (accounts/open) — plus the deliberate
DEFAULTS-vs-shipped config difference (like retention), the report's honest
locked note, and (with ffmpeg) the pipeline actually skipping the render:
no file on disk, no replay key in metrics.json, CPU never spent.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import DEFAULTS, Config
from swinglab.metrics import session_stats
from swinglab.pipeline import analyze_video
from swinglab.report import write_report_html
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import JobManager
from swinglab.web.users import PRO, UserStore

from tests.test_report import fake_swing, fake_video
from tests.test_web import fake_analyze_ok, wait_for

LOCKED_NOTE_TAIL = "Upgrade and re-film to get it."


# -- config pin (same pattern as the retention pin) ---------------------------

def test_shipped_gate_and_code_default_differ_deliberately():
    """DEFAULTS ship the gate OFF (white-label installs stay ungated until
    they opt in); the SHIPPED config.yaml turns it on — the same deliberate
    difference as retention_days, pinned here so it can't drift silently."""
    assert DEFAULTS["billing"]["replay_pro_only"] is False
    shipped = Config.load(Path(__file__).parent.parent / "config.yaml")
    assert shipped.billing["replay_pro_only"] is True


def test_cli_pipeline_is_never_gated_by_default():
    """analyze_video only gates when its CALLER says so — the CLI never
    passes replay_locked, so CLI runs render the replay unconditionally."""
    param = inspect.signature(analyze_video).parameters["replay_locked"]
    assert param.default is False


# -- the gate decision, at analysis time --------------------------------------

def make_manager(tmp_path, gate=True, accounts=True, user_store=None,
                 annotated=True, name="s"):
    cfg = Config()
    cfg.billing["replay_pro_only"] = gate
    cfg.web["require_account"] = accounts
    cfg.slowmo["annotated"] = annotated
    return JobManager(tmp_path / name, cfg, user_store=user_store)


def test_gate_decision_matrix(tmp_path):
    users = UserStore(tmp_path / "users.db")
    free = users.create("free@example.com", "longenough")
    pro = users.create("pro@example.com", "longenough")
    users.set_plan(pro.id, PRO, "active")

    manager = make_manager(tmp_path, gate=True, accounts=True,
                           user_store=users, name="a")
    free_job = manager.create_session(user_id=free.id)
    pro_job = manager.create_session(user_id=pro.id)
    ownerless = manager.create_session()  # pre-accounts era job
    ghost = manager.create_session(user_id="vanished")

    assert manager.replay_locked(free_job) is True
    assert manager.replay_locked(pro_job) is False
    # Ownerless (pre-account) jobs stay ungated — reachable by link only.
    assert manager.replay_locked(ownerless) is False
    # An owner whose row vanished has no Pro either.
    assert manager.replay_locked(ghost) is True

    # Gate off: nothing is ever locked, plan is irrelevant.
    off = make_manager(tmp_path, gate=False, accounts=True,
                       user_store=users, name="b")
    assert off.replay_locked(off.create_session(user_id=free.id)) is False

    # Open mode (require_account false): never gated, even with the gate on.
    open_mode = make_manager(tmp_path, gate=True, accounts=False,
                             user_store=users, name="c")
    assert open_mode.replay_locked(
        open_mode.create_session(user_id=free.id)
    ) is False

    # No user store (CLI-adjacent embedders): never gated.
    no_store = make_manager(tmp_path, gate=True, accounts=True,
                            user_store=None, name="d")
    assert no_store.replay_locked(
        no_store.create_session(user_id=free.id)
    ) is False

    # Replay feature disabled outright: nothing to gate, nothing to sell.
    no_replay = make_manager(tmp_path, gate=True, accounts=True,
                             user_store=users, annotated=False, name="e")
    assert no_replay.replay_locked(
        no_replay.create_session(user_id=free.id)
    ) is False


def test_pro_at_analysis_time_is_what_counts(tmp_path):
    """The gate reads the plan when the analysis RUNS — a lapsed pro_until
    is free, a live one is Pro."""
    users = UserStore(tmp_path / "users.db")
    user = users.create("kyle@example.com", "longenough")
    manager = make_manager(tmp_path, user_store=users)
    job = manager.create_session(user_id=user.id)
    assert manager.replay_locked(job) is True
    users.grant_pro_days(user.id, 31)
    assert manager.replay_locked(job) is False
    users.revoke_pro_days(user.id, 31)
    assert manager.replay_locked(job) is True


# -- web wiring: the decision reaches the pipeline, honestly logged -----------

def signup(client, email="kyle@example.com", password="longenough"):
    resp = client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def upload(client):
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def make_client(tmp_path, monkeypatch, seen, gate=True, accounts=True):
    def spy(video_path, replay_locked=False, **kwargs):
        seen.append(replay_locked)
        return fake_analyze_ok(video_path, **kwargs)

    monkeypatch.setattr(jobs_module, "analyze_video", spy)
    cfg = Config()
    cfg.billing["replay_pro_only"] = gate
    cfg.web["require_account"] = accounts
    return TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))


def test_free_upload_is_gated_pro_upload_is_not(tmp_path, monkeypatch):
    seen: list[bool] = []
    client = make_client(tmp_path, monkeypatch, seen)
    signup(client)
    data = wait_for(client, upload(client))
    assert data["status"] == "done"
    assert seen == [True]
    # The skip is stated honestly in the session log, with the re-film rule.
    assert any("Coach replay is a Pro feature" in line for line in data["log"])
    assert any("re-film" in line for line in data["log"])

    users: UserStore = client.app.state.users
    users.set_plan(users.get_by_email("kyle@example.com").id, PRO, "active")
    data = wait_for(client, upload(client))
    assert seen == [True, False]
    assert not any("Pro feature" in line for line in data["log"])


def test_open_mode_and_gate_off_never_lock(tmp_path, monkeypatch):
    # Open mode with the gate on: anonymous uploads are never gated.
    seen: list[bool] = []
    client = make_client(tmp_path, monkeypatch, seen, gate=True, accounts=False)
    wait_for(client, upload(client))
    assert seen == [False]

    # Accounts on but gate off: free users keep the replay.
    seen2: list[bool] = []
    client2 = make_client(tmp_path, monkeypatch, seen2, gate=False, accounts=True)
    signup(client2)
    wait_for(client2, upload(client2))
    assert seen2 == [False]


def test_sample_report_keeps_whatever_it_has_today(tmp_path, monkeypatch):
    """The public sample report is untouched by the gate — no locked note."""
    seen: list[bool] = []
    client = make_client(tmp_path, monkeypatch, seen, gate=True, accounts=True)
    html = client.get("/sample-report/", follow_redirects=True).text
    assert LOCKED_NOTE_TAIL not in html


# -- the report's locked note -------------------------------------------------

def test_report_locked_note_in_each_replay_slot(tmp_path):
    swings = [fake_swing(1), fake_swing(2)]
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        tmp_path / "report.html", fake_video(), swings, stats, [], "right",
        Config(), replay_locked=True,
    )
    html = out.read_text()
    # One locked slot per swing, beside each slow-mo, honest wording + link.
    assert html.count("your swing annotated frame-by-frame") == 2
    assert html.count(LOCKED_NOTE_TAIL) == 2
    assert 'href="/pricing"' in html
    assert "media/replay_s" not in html  # no replay video anywhere
    assert html.count("media/slowmo_s") == 2  # slow motion untouched


def test_report_without_lock_shows_no_note(tmp_path):
    swings = [fake_swing(1)]
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        tmp_path / "report.html", fake_video(), swings, stats, [], "right",
        Config(),
    )
    html = out.read_text()
    assert LOCKED_NOTE_TAIL not in html
    assert 'href="/pricing"' not in html


# -- the pipeline really skips the render (ffmpeg) ----------------------------

from tests.conftest import generate_test_video, needs_ffmpeg  # noqa: E402
from tests.test_pipeline_e2e import FakeTracker  # noqa: E402


@needs_ffmpeg
def test_pipeline_skips_render_and_metrics_json_omits_replay(tmp_path, monkeypatch):
    from swinglab import pipeline, pose

    monkeypatch.setattr(pose, "PoseTracker", FakeTracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", FakeTracker)
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240

    video = generate_test_video(tmp_path / "oneswing.mov", [9.5])
    result = analyze_video(
        video, out_dir=tmp_path / "results", cfg=cfg, replay_locked=True
    )

    # No replay file — the render never ran, the CPU was never spent.
    media = sorted(p.name for p in (result.session_dir / "media").iterdir())
    assert media == ["overlay_s1.png", "slowmo_s1.mp4", "strip_s1.png"]
    # metrics.json: replay key absent when gated, exactly as when disabled —
    # so /progress and the weekly digest can never reference a replay the
    # session doesn't have.
    data = json.loads(result.metrics_path.read_text())
    assert all("replay" not in s["deliverables"] for s in data["swings"])
    # The report shows the honest locked note in the replay slot.
    html = result.report_path.read_text()
    assert "media/replay_s" not in html
    assert LOCKED_NOTE_TAIL in html and 'href="/pricing"' in html


@needs_ffmpeg
def test_locked_note_not_shown_when_replay_feature_is_off(tmp_path, monkeypatch):
    """slowmo.annotated false = no replay feature at all: locking it must
    not advertise something the operator disabled."""
    from swinglab import pipeline, pose

    monkeypatch.setattr(pose, "PoseTracker", FakeTracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", FakeTracker)
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    cfg.slowmo["annotated"] = False

    video = generate_test_video(tmp_path / "oneswing.mov", [9.5])
    result = analyze_video(
        video, out_dir=tmp_path / "results", cfg=cfg, replay_locked=True
    )
    html = result.report_path.read_text()
    assert "Coach replay" not in html
    assert LOCKED_NOTE_TAIL not in html
