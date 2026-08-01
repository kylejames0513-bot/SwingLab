"""Crash-safe, account-scoped history deletion at the JobManager boundary."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.jobs import (
    DONE,
    FAILED,
    PROCESSING,
    HistoryResetConflict,
    HistoryResetError,
    HistoryResetSafetyError,
    Job,
    JobManager,
)


def terminal_job(
    manager: JobManager,
    user_id: str | None,
    *,
    status: str = DONE,
    capture_only: bool = False,
) -> Job:
    job = manager.create_session(source_name="swing.mov", user_id=user_id)
    (job.session_dir / "source.mov").write_bytes(b"video")
    if status == DONE:
        output = job.session_dir / "out"
        output.mkdir()
        if capture_only:
            report = (
                '<meta name="caddieinsight-report-format" '
                'content="caddie-brief-v1">'
                '<meta name="caddieinsight-report-outcome" '
                'content="capture_only">'
            )
        else:
            # A pre-metrics report remains coaching-eligible for compatibility.
            report = "<html>legacy coaching report</html>"
        (output / "report.html").write_text(report, encoding="utf-8")
        job.report_rel = "out/report.html"
    job.status = status
    manager._save(job)
    return job


def test_reset_is_exactly_user_scoped_and_callback_shares_transaction(tmp_path):
    manager = JobManager(tmp_path / "sessions", Config())
    alice_done = terminal_job(manager, "alice")
    alice_failed = terminal_job(manager, "alice", status=FAILED)
    bob = terminal_job(manager, "bob")
    ownerless = terminal_job(manager, None)
    with manager._lock:
        manager._conn.execute(
            "CREATE TABLE related_history"
            " (user_id TEXT NOT NULL, session_id TEXT NOT NULL)"
        )
        manager._conn.executemany(
            "INSERT INTO related_history VALUES (?, ?)",
            [
                ("alice", alice_done.id),
                ("alice", alice_failed.id),
                ("bob", bob.id),
            ],
        )
        manager._conn.commit()

    callback_sessions: list[tuple[str, ...]] = []

    def delete_related(connection, user_id):
        owned = connection.execute(
            "SELECT id FROM jobs WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        callback_sessions.append(tuple(row[0] for row in owned))
        connection.execute(
            "DELETE FROM related_history WHERE user_id = ?", (user_id,)
        )

    summary = manager.reset_user_history(
        "alice", delete_related=delete_related
    )

    assert summary.deleted_jobs == 2
    assert summary.jobs_deleted == 2
    assert not summary.cleanup_pending
    assert callback_sessions == [tuple(sorted((alice_done.id, alice_failed.id)))]
    assert manager.list_recent(user_id="alice") == []
    assert manager.get(bob.id) is not None
    assert manager.get(ownerless.id) is not None
    assert not alice_done.session_dir.exists()
    assert not alice_failed.session_dir.exists()
    assert bob.session_dir.is_dir()
    assert ownerless.session_dir.is_dir()
    with manager._lock:
        related = manager._conn.execute(
            "SELECT user_id, session_id FROM related_history"
        ).fetchall()
    assert [(row[0], row[1]) for row in related] == [("bob", bob.id)]


@pytest.mark.parametrize("status", ["queued", PROCESSING])
def test_reset_conflicts_without_mutation_for_active_jobs(tmp_path, status):
    manager = JobManager(tmp_path / "sessions", Config())
    job = manager.create_session(source_name="swing.mov", user_id="alice")
    job.status = status
    manager._save(job)
    callback_called = False

    def delete_related(_connection, _user_id):
        nonlocal callback_called
        callback_called = True

    with pytest.raises(HistoryResetConflict) as error:
        manager.reset_user_history("alice", delete_related=delete_related)

    assert error.value.active_job_ids == (job.id,)
    assert not callback_called
    assert manager.get(job.id) is not None
    assert job.session_dir.is_dir()
    assert manager.history_cleanup_pending_count() == 0


def test_stale_upload_epoch_conflicts_before_directory_or_row_creation(tmp_path):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    with manager._lock:
        manager._conn.execute(
            "CREATE TABLE users"
            " (id TEXT PRIMARY KEY, history_epoch INTEGER NOT NULL)"
        )
        manager._conn.execute("INSERT INTO users VALUES ('alice', 2)")
        manager._conn.commit()
    before = {path.name for path in sessions.iterdir()}

    with pytest.raises(HistoryResetConflict):
        manager.create_session(
            source_name="stale.mov",
            user_id="alice",
            expected_history_epoch=1,
        )

    assert {path.name for path in sessions.iterdir()} == before
    assert manager.list_recent(user_id="alice") == []
    current = manager.create_session(
        source_name="current.mov",
        user_id="alice",
        expected_history_epoch=2,
    )
    assert current.session_dir.is_dir()


def test_quota_and_refilm_courtesy_survive_repeated_resets(tmp_path):
    manager = JobManager(tmp_path / "sessions", Config())
    terminal_job(manager, "alice", capture_only=True)
    terminal_job(manager, "alice", capture_only=True)
    terminal_job(manager, "alice")
    assert manager.refilm_rejections_this_month("alice") == 2
    assert manager.usage_this_month("alice") == 2

    first = manager.reset_user_history("alice")
    second = manager.reset_user_history("alice")

    assert first.deleted_jobs == 3
    assert second.deleted_jobs == 0
    assert manager.refilm_rejections_this_month("alice") == 2
    assert manager.usage_this_month("alice") == 2
    terminal_job(manager, "alice", capture_only=True)
    assert manager.refilm_rejections_this_month("alice") == 3
    assert manager.usage_this_month("alice") == 3
    with manager._lock:
        receipts = manager._conn.execute(
            "SELECT * FROM analysis_usage_monthly"
        ).fetchall()
    assert len(receipts) == 1
    assert "alice" not in tuple(receipts[0])


def test_rename_failure_restores_every_directory_and_aborts_journal(
    tmp_path, monkeypatch
):
    manager = JobManager(tmp_path / "sessions", Config())
    jobs = sorted(
        [terminal_job(manager, "alice"), terminal_job(manager, "alice")],
        key=lambda item: item.id,
    )
    original_replace = Path.replace
    injected = False

    def fail_second_source(path, target):
        nonlocal injected
        if path == jobs[1].session_dir and not injected:
            injected = True
            raise OSError("injected rename failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_second_source)
    with pytest.raises(HistoryResetError) as error:
        manager.reset_user_history("alice")
    assert isinstance(error.value.__cause__, OSError)
    assert "injected rename failure" in str(error.value.__cause__)

    assert {job.id for job in manager.list_recent(user_id="alice")} == {
        jobs[0].id,
        jobs[1].id,
    }
    assert all(job.session_dir.is_dir() for job in jobs)
    assert manager.history_cleanup_pending_count() == 0


def test_callback_failure_rolls_back_database_and_restores_artifacts(tmp_path):
    manager = JobManager(tmp_path / "sessions", Config())
    job = terminal_job(manager, "alice")
    with manager._lock:
        manager._conn.execute("CREATE TABLE related_history (user_id TEXT)")
        manager._conn.execute("INSERT INTO related_history VALUES ('alice')")
        manager._conn.commit()

    def fail_after_delete(connection, user_id):
        connection.execute(
            "DELETE FROM related_history WHERE user_id = ?", (user_id,)
        )
        raise RuntimeError("injected callback failure")

    with pytest.raises(HistoryResetError) as error:
        manager.reset_user_history("alice", delete_related=fail_after_delete)
    assert isinstance(error.value.__cause__, RuntimeError)
    assert "injected callback failure" in str(error.value.__cause__)

    assert manager.get(job.id) is not None
    assert job.session_dir.is_dir()
    with manager._lock:
        assert manager._conn.execute(
            "SELECT COUNT(*) FROM related_history WHERE user_id = 'alice'"
        ).fetchone()[0] == 1
    assert manager.history_cleanup_pending_count() == 0


def test_post_commit_cleanup_is_retried_on_restart(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = terminal_job(manager, "alice")
    original_rmtree = jobs_module.shutil.rmtree

    def fail_history_trash(path, *args, **kwargs):
        candidate = Path(path)
        if candidate.parent.name == ".history-trash":
            raise PermissionError("injected cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(jobs_module.shutil, "rmtree", fail_history_trash)
    summary = manager.reset_user_history("alice")

    assert summary.cleanup_pending
    assert manager.get(job.id) is None
    assert manager.usage_this_month("alice") == 1
    assert manager.history_cleanup_pending_count() == 1

    monkeypatch.setattr(jobs_module.shutil, "rmtree", original_rmtree)
    restarted = JobManager(sessions, Config())
    assert restarted.history_cleanup_pending_count() == 0
    assert restarted.get(job.id) is None
    assert restarted.usage_this_month("alice") == 1
    assert not (sessions / ".history-trash").exists()


def test_prepare_journal_failure_leaves_connection_and_history_untouched(tmp_path):
    manager = JobManager(tmp_path / "sessions", Config())
    job = terminal_job(manager, "alice")
    with manager._lock:
        manager._conn.execute(
            "CREATE TRIGGER fail_history_prepare"
            " BEFORE INSERT ON history_reset_operations"
            " BEGIN SELECT RAISE(FAIL, 'journal unavailable'); END"
        )
        manager._conn.commit()

    with pytest.raises(HistoryResetError) as error:
        manager.reset_user_history("alice")

    assert "journal unavailable" in str(error.value.__cause__)
    assert not manager._conn.in_transaction
    assert manager.get(job.id) is not None
    assert job.session_dir.is_dir()
    assert manager.history_cleanup_pending_count() == 0


def test_cleaned_committed_journal_is_retried_after_database_failure(tmp_path):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = terminal_job(manager, "alice")
    with manager._lock:
        manager._conn.execute(
            "CREATE TRIGGER fail_committed_journal_delete"
            " BEFORE DELETE ON history_reset_operations"
            " WHEN OLD.state = 'committed'"
            " BEGIN SELECT RAISE(FAIL, 'journal delete unavailable'); END"
        )
        manager._conn.commit()

    summary = manager.reset_user_history("alice")

    assert summary.cleanup_pending
    assert manager.get(job.id) is None
    assert not manager._conn.in_transaction
    assert manager.history_cleanup_pending_count() == 1
    with manager._lock:
        manager._conn.execute("DROP TRIGGER fail_committed_journal_delete")
        manager._conn.commit()

    restarted = JobManager(sessions, Config())
    assert restarted.history_cleanup_pending_count() == 0
    assert restarted.get(job.id) is None


def test_committed_recovery_purges_artifact_if_rename_was_lost(tmp_path):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = terminal_job(manager, "alice")
    with manager._lock:
        row = manager._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        operation_id = manager._prepare_history_operation_locked(
            (row,), kind="user_reset", subject_hash=manager._user_hash("alice")
        )
        manager._conn.execute("BEGIN IMMEDIATE")
        manager._conn.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
        manager._conn.execute(
            "UPDATE history_reset_operations SET state = 'committed'"
            " WHERE operation_id = ?",
            (operation_id,),
        )
        manager._conn.commit()
    staged = sessions / ".history-trash" / operation_id / job.id
    assert staged.is_dir()
    staged.replace(job.session_dir)
    assert job.session_dir.is_dir()

    restarted = JobManager(sessions, Config())

    assert restarted.get(job.id) is None
    assert restarted.history_cleanup_pending_count() == 0
    assert not job.session_dir.exists()
    assert not (sessions / ".history-trash").exists()


def test_prepared_operation_restores_artifacts_on_restart(tmp_path):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = terminal_job(manager, "alice")
    with manager._lock:
        row = manager._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        manager._prepare_history_operation_locked(
            (row,), kind="user_reset", subject_hash=manager._user_hash("alice")
        )
    assert not job.session_dir.exists()
    assert manager.history_cleanup_pending_count() == 1

    restarted = JobManager(sessions, Config())
    assert restarted.history_cleanup_pending_count() == 0
    assert restarted.get(job.id) is not None
    assert (job.session_dir / "source.mov").is_file()


def test_unresolved_prepared_operation_blocks_retry_without_orphaning_history(
    tmp_path, monkeypatch
):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = terminal_job(manager, "alice")
    with manager._lock:
        row = manager._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        operation_id = manager._prepare_history_operation_locked(
            (row,), kind="user_reset", subject_hash=manager._user_hash("alice")
        )
    operation_dir = sessions / ".history-trash" / operation_id
    staged = operation_dir / job.id
    assert staged.is_dir()
    assert not job.session_dir.exists()
    original_replace = Path.replace

    def fail_restore(path, target):
        if path == staged and target == job.session_dir:
            raise PermissionError("injected prepared restore failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_restore)
    with pytest.raises(HistoryResetError, match="Earlier history cleanup"):
        manager.reset_user_history("alice")

    # The retry must not create an artifact-empty second operation and commit
    # deletion of the still-journaled job row.
    assert manager.get(job.id) is not None
    assert manager.history_cleanup_pending_count() == 1
    with manager._lock:
        operations = manager._conn.execute(
            "SELECT operation_id, state FROM history_reset_operations"
        ).fetchall()
    assert [(row[0], row[1]) for row in operations] == [
        (operation_id, "prepared")
    ]
    assert [path.name for path in (sessions / ".history-trash").iterdir()] == [
        operation_id
    ]
    assert staged.is_dir()
    assert not job.session_dir.exists()

    monkeypatch.setattr(Path, "replace", original_replace)
    manager._recover_history_operations()

    assert manager.history_cleanup_pending_count() == 0
    assert manager.get(job.id) is not None
    assert (job.session_dir / "out" / "report.html").is_file()
    assert not (sessions / ".history-trash").exists()


def test_symlinked_session_cannot_escape_history_reset(tmp_path):
    manager = JobManager(tmp_path / "sessions", Config())
    job = terminal_job(manager, "alice")
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "keep.txt"
    protected.write_text("keep", encoding="utf-8")
    shutil.rmtree(job.session_dir)
    try:
        job.session_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(HistoryResetSafetyError):
        manager.reset_user_history("alice")

    assert protected.read_text(encoding="utf-8") == "keep"
    assert job.session_dir.is_symlink()
    assert manager.get(job.id) is not None


def test_unsafe_persisted_job_id_is_never_interpreted_as_a_path(tmp_path):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "keep.txt"
    protected.write_text("keep", encoding="utf-8")
    now = time.time()
    with manager._lock:
        manager._conn.execute(
            "INSERT INTO jobs (id, status, created_at, updated_at, user_id)"
            " VALUES (?, ?, ?, ?, ?)",
            ("../outside", DONE, now, now, "alice"),
        )
        manager._conn.commit()

    with pytest.raises(HistoryResetSafetyError):
        manager.reset_user_history("alice")

    assert protected.read_text(encoding="utf-8") == "keep"
    assert manager.get("../outside") is not None
