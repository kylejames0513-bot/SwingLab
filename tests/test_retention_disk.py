"""Retention and disk safety: web.delete_source_after_done (drop the raw
upload once the report exists; deliverables stay) and the /healthz disk
gauges. Durable quota receipts cover retention_days cleanup below."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import DEFAULTS, Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import JobManager

from tests.test_history_reset_core import structured_tree_paths, terminal_job
from tests.test_web import fake_analyze_no_strikes, fake_analyze_ok, upload, wait_for


def source_files(session_dir):
    return list(session_dir.glob("source.*"))


def test_shipped_config_and_code_defaults_differ_deliberately():
    """DEFAULTS keep everything forever (white-label operators opt in);
    the SHIPPED config.yaml turns retention + source deletion on."""
    assert DEFAULTS["web"]["retention_days"] == 0
    assert DEFAULTS["web"]["delete_source_after_done"] is False

    shipped = Config.load(Path(__file__).parent.parent / "config.yaml")
    assert shipped.web["retention_days"] == 180
    assert shipped.web["delete_source_after_done"] is True


def test_source_deleted_after_done_deliverables_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["delete_source_after_done"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    data = wait_for(client, job_id)
    assert data["status"] == "done"

    job = client.app.state.jobs.get(job_id)
    assert source_files(job.session_dir) == []  # raw upload gone
    # deliverables still served
    report = client.get(f"/session/{job_id}/report", follow_redirects=True)
    assert report.status_code == 200 and "fake report" in report.text
    assert client.get(
        f"/session/{job_id}/files/out/source/media/strip_s1.png"
    ).status_code == 200
    # the trade-off is stated in the session log, not hidden
    assert any("fresh upload" in line for line in data["log"])


def test_source_kept_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    wait_for(client, job_id)
    job = client.app.state.jobs.get(job_id)
    assert len(source_files(job.session_dir)) == 1


def test_failed_job_source_also_deleted_when_configured(tmp_path, monkeypatch):
    """FAILED is terminal (restart only re-queues queued/processing work) and
    failed uploads don't count against quota — so with the deletion switch on,
    a failed job's source is dropped too. Otherwise refused clips (e.g.
    over-length videos) would accumulate on disk for free."""
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_no_strikes)
    cfg = Config()
    cfg.web["delete_source_after_done"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    data = wait_for(client, job_id)
    assert data["status"] == "failed"
    job = client.app.state.jobs.get(job_id)
    assert source_files(job.session_dir) == []
    assert any("upload again to retry" in line for line in data["log"])


def test_failed_status_waits_for_terminal_source_cleanup(tmp_path, monkeypatch):
    """A client that stops polling at FAILED must see final cleanup notes."""

    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_no_strikes)
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    original_cleanup = JobManager._delete_failed_source_if_configured

    def blocked_cleanup(manager, job):
        cleanup_started.set()
        assert release_cleanup.wait(timeout=5), "test never released cleanup"
        return original_cleanup(manager, job)

    monkeypatch.setattr(
        JobManager, "_delete_failed_source_if_configured", blocked_cleanup
    )
    cfg = Config()
    cfg.web["delete_source_after_done"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    assert cleanup_started.wait(timeout=5), "failed cleanup never started"

    executor = ThreadPoolExecutor(max_workers=1)
    lookup_started = threading.Event()

    def lookup_job():
        lookup_started.set()
        return client.app.state.jobs.get(job_id)

    lookup = executor.submit(lookup_job)
    try:
        assert lookup_started.wait(timeout=1), "job lookup never started"
        with pytest.raises(FutureTimeout):
            lookup.result(timeout=0.25)
    finally:
        release_cleanup.set()

    persisted = lookup.result(timeout=5)
    executor.shutdown()
    assert persisted is not None and persisted.status == "failed"
    assert source_files(persisted.session_dir) == []
    assert any("upload again to retry" in line for line in persisted.log)


def test_failed_job_keeps_source_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_no_strikes)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    assert wait_for(client, job_id)["status"] == "failed"
    job = client.app.state.jobs.get(job_id)
    assert len(source_files(job.session_dir)) == 1


def test_healthz_reports_disk_and_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    before = client.get("/healthz").json()
    assert before["sessions_count"] == 0
    assert isinstance(before["disk_free_mb"], int) and before["disk_free_mb"] > 0

    job_id = upload(client)
    wait_for(client, job_id)
    assert client.get("/healthz").json()["sessions_count"] == 1


def test_done_job_with_deleted_source_survives_restart(tmp_path, monkeypatch):
    """The restart pass only re-queues QUEUED/PROCESSING jobs, so a DONE job
    whose source was deleted keeps serving results after a restart."""
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["delete_source_after_done"] = True
    sessions = tmp_path / "s"
    client = TestClient(create_app(cfg, sessions_dir=sessions))
    job_id = upload(client)
    wait_for(client, job_id)

    fresh = TestClient(create_app(cfg, sessions_dir=sessions))  # "restart"
    data = fresh.get(f"/api/session/{job_id}").json()
    assert data["status"] == "done"
    assert "fake report" in fresh.get(
        f"/session/{job_id}/report", follow_redirects=True
    ).text


def test_retention_archives_current_month_quota_before_deleting(tmp_path):
    cfg = Config()
    cfg.web["retention_days"] = 1
    manager = JobManager(tmp_path / "sessions", cfg)
    eligible = terminal_job(manager, "alice", structured=True)
    rejected = terminal_job(
        manager, "alice", capture_only=True, structured=True
    )
    ownerless = terminal_job(manager, None)
    eligible_tree = structured_tree_paths(eligible)
    rejected_tree = structured_tree_paths(rejected)
    old = time.time() - 2 * 86400
    with manager._lock:
        manager._conn.executemany(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            [(old, eligible.id), (old, rejected.id), (old, ownerless.id)],
        )
        manager._conn.commit()

    assert manager.usage_this_month("alice") == 1
    assert manager.refilm_rejections_this_month("alice") == 1
    manager._cleanup_expired()
    manager._cleanup_expired()

    assert manager.get(eligible.id) is None
    assert manager.get(rejected.id) is None
    assert manager.get(ownerless.id) is None
    assert not eligible.session_dir.exists()
    assert not rejected.session_dir.exists()
    assert not ownerless.session_dir.exists()
    assert all(not path.exists() for path in (*eligible_tree, *rejected_tree))
    assert manager.usage_this_month("alice") == 1
    assert manager.refilm_rejections_this_month("alice") == 1
    with manager._lock:
        receipts = manager._conn.execute(
            "SELECT user_hash, coaching_eligible, refilm_rejections, expires_at"
            " FROM analysis_usage_monthly"
        ).fetchall()
    assert len(receipts) == 1
    assert receipts[0][0] != "alice"
    assert (receipts[0][1], receipts[0][2]) == (1, 1)
    assert receipts[0][3] > time.time()


def test_expired_pseudonymous_quota_receipts_are_purged(tmp_path):
    manager = JobManager(tmp_path / "sessions", Config())
    terminal_job(manager, "alice")
    manager.reset_user_history("alice")
    with manager._lock:
        manager._conn.execute(
            "UPDATE analysis_usage_monthly SET expires_at = ?",
            (time.time() - 1,),
        )
        manager._conn.commit()

    manager._cleanup_expired()

    with manager._lock:
        assert manager._conn.execute(
            "SELECT COUNT(*) FROM analysis_usage_monthly"
        ).fetchone()[0] == 0
    assert manager.usage_this_month("alice") == 0


def test_old_retained_jobs_do_not_create_already_expired_receipts(tmp_path):
    cfg = Config()
    cfg.web["retention_days"] = 1
    manager = JobManager(tmp_path / "sessions", cfg)
    job = terminal_job(manager, "alice")
    old = time.time() - 120 * 86400
    with manager._lock:
        manager._conn.execute(
            "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (old, old, job.id),
        )
        manager._conn.commit()

    manager._cleanup_expired()

    assert manager.get(job.id) is None
    with manager._lock:
        assert manager._conn.execute(
            "SELECT COUNT(*) FROM analysis_usage_monthly"
        ).fetchone()[0] == 0


def test_retention_stops_when_prior_history_recovery_remains_unresolved(
    tmp_path, monkeypatch
):
    cfg = Config()
    cfg.web["retention_days"] = 1
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, cfg)
    blocked = terminal_job(manager, "alice", structured=True)
    blocked_tree = structured_tree_paths(blocked)
    untouched = terminal_job(manager, "bob")
    old = time.time() - 2 * 86400
    with manager._lock:
        manager._conn.executemany(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            [(old, blocked.id), (old, untouched.id)],
        )
        manager._conn.commit()
        row = manager._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (blocked.id,)
        ).fetchone()
        operation_id = manager._prepare_history_operation_locked(
            (row,), kind="retention", subject_hash=None
        )
    staged = sessions / ".history-trash" / operation_id / blocked.id
    assert all(
        (staged / path.relative_to(blocked.session_dir)).exists()
        for path in blocked_tree
    )
    original_replace = Path.replace

    def fail_restore(path, target):
        if path == staged and target == blocked.session_dir:
            raise PermissionError("injected retention restore failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_restore)
    manager._cleanup_expired()

    assert manager.history_cleanup_pending_count() == 1
    assert manager.get(blocked.id) is not None
    assert manager.get(untouched.id) is not None
    assert untouched.session_dir.is_dir()
    with manager._lock:
        assert manager._conn.execute(
            "SELECT COUNT(*) FROM history_reset_operations"
        ).fetchone()[0] == 1

    monkeypatch.setattr(Path, "replace", original_replace)
    manager._recover_history_operations()
    assert manager.history_cleanup_pending_count() == 0
    assert blocked.session_dir.is_dir()
    assert manager.get(blocked.id) is not None
    assert all(path.exists() for path in blocked_tree)
