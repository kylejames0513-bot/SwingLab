from __future__ import annotations

import shutil
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess
import threading

import pytest

from swinglab.config import Config
from swinglab.pipeline import SessionResult, ZeroStrikesError
from swinglab.report import REPORT_PRESENTATION_VERSION
from swinglab.report_artifacts import (
    ReportArtifactValidationError,
    ReportEntitlementSnapshot,
)
from swinglab.report_bundle import (
    CoreReportBundleError,
    GuidedReportRendererUnavailable,
    begin_report_bundle,
    build_report_bundle,
    publish_report_bundle,
)
from swinglab.report_view import (
    GUIDED_REPORT_PRESENTATION_VERSION,
    MediaRole,
    ReasonCode,
    TrackingState,
    UnsupportedReportPresentationVersion,
)
from swinglab.web import jobs as jobs_module
from swinglab.web.jobs import DONE, PROCESSING, Job, JobManager
from tests import guided_report_gate_helper as gate_helper
from tests.report_bundle_fixtures import (
    guided_bundle_inputs,
    temporary_directory_redirect,
    write_test_report_html,
)


def _guided_result(
    job: Job,
    tmp_path: Path,
    *,
    attempt_id: str = "a" * 32,
    analysis_name: str = "source",
    capture_only: bool = False,
):
    analysis_dir = job.session_dir / "out" / analysis_name
    analysis_dir.mkdir(parents=True, exist_ok=True)
    attempt = begin_report_bundle(analysis_dir, attempt_id=attempt_id)
    inputs = guided_bundle_inputs(tmp_path, swings=[] if capture_only else None)
    published = publish_report_bundle(build_report_bundle(attempt, **inputs))
    return SessionResult(
        session_dir=analysis_dir,
        report_path=published.report_path,
        metrics_path=published.root / "metrics.json",
        video=inputs["video"],
        report_view_path=published.report_view_path,
        manifest_path=published.manifest_path,
        checksums_path=published.checksums_path,
        structured_report=True,
    )


def _assert_processing_without_publication(manager: JobManager, job: Job) -> None:
    row = manager._conn.execute(
        "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
        " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == (PROCESSING, None, None, None, None, 0)
    assert job.status == PROCESSING
    assert (
        job.report_rel,
        job.report_view_rel,
        job.report_manifest_rel,
        job.report_checksums_rel,
        job.structured_report,
    ) == (None, None, None, None, False)


def test_guided_report_presentation_gate_is_strict_and_ships_enabled(tmp_path: Path):
    assert Config().report["guided_presentation_enabled"] is False
    shipped = Config.load(Path(__file__).parent.parent / "config.yaml")
    assert shipped.report["guided_presentation_enabled"] is True
    assert (
        jobs_module.configured_report_presentation(shipped)
        == GUIDED_REPORT_PRESENTATION_VERSION
    )

    assert jobs_module.configured_report_presentation(Config()) == REPORT_PRESENTATION_VERSION
    for malformed in ("true", 1, "1", None):
        cfg = Config()
        cfg.report["guided_presentation_enabled"] = malformed
        assert jobs_module.configured_report_presentation(cfg) == REPORT_PRESENTATION_VERSION

    enabled = Config()
    enabled.report["guided_presentation_enabled"] = True
    assert (
        jobs_module.configured_report_presentation(enabled)
        == GUIDED_REPORT_PRESENTATION_VERSION
    )


def test_job_round_trips_all_structured_publication_fields_and_canonical_entitlement(
    tmp_path: Path,
):
    manager = JobManager(tmp_path / "sessions", Config())
    job = Job(
        id="roundtrip",
        session_dir=manager.sessions_dir / "roundtrip",
        status=DONE,
        created_at=1.0,
        report_rel="out/source/report-bundle-" + "a" * 32 + "/report.html",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
        report_entitlements=ReportEntitlementSnapshot("locked"),
        report_view_rel=(
            "out/source/report-bundle-" + "a" * 32 + "/report-view.json"
        ),
        report_manifest_rel=(
            "out/source/report-bundle-"
            + "a" * 32
            + "/report-bundle-manifest.json"
        ),
        report_checksums_rel=(
            "out/source/report-bundle-"
            + "a" * 32
            + "/report-bundle-checksums.json"
        ),
        structured_report=True,
    )
    manager._save(job)

    stored = manager.get(job.id)
    assert stored is not None
    assert stored.report_presentation_version == GUIDED_REPORT_PRESENTATION_VERSION
    assert stored.report_entitlements == ReportEntitlementSnapshot("locked")
    assert stored.report_rel == job.report_rel
    assert stored.report_view_rel == job.report_view_rel
    assert stored.report_manifest_rel == job.report_manifest_rel
    assert stored.report_checksums_rel == job.report_checksums_rel
    assert stored.structured_report is True
    raw = manager._conn.execute(
        "SELECT report_entitlements_json FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()[0]
    assert raw == '{"coach_replay":"locked"}\n'


def test_unknown_report_presentation_is_rejected_before_directory_or_row(tmp_path: Path):
    manager = JobManager(tmp_path / "sessions", Config())
    before_dirs = {path.name for path in manager.sessions_dir.iterdir() if path.is_dir()}

    with pytest.raises(
        UnsupportedReportPresentationVersion, match="unknown report presentation"
    ):
        manager.create_session(report_presentation_version="future-report-v9")

    after_dirs = {path.name for path in manager.sessions_dir.iterdir() if path.is_dir()}
    assert after_dirs == before_dirs
    assert manager._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_restart_rejects_unknown_persisted_presentation_without_submitting(
    tmp_path: Path, monkeypatch,
):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = manager.create_session(source_name="source.mov")
    (job.session_dir / "source.mov").write_bytes(b"source")
    manager._conn.execute(
        "UPDATE jobs SET report_presentation_version = ? WHERE id = ?",
        ("future-report-v9", job.id),
    )
    manager._conn.commit()
    manager._pool.shutdown(wait=True)
    manager._conn.close()
    submitted: list[str] = []
    monkeypatch.setattr(
        JobManager,
        "submit",
        lambda self, current, video: submitted.append(current.id),
    )

    restarted = JobManager(sessions, Config())

    assert submitted == []
    row = restarted._conn.execute(
        "SELECT status, error, report_rel FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert "unknown report presentation" in row["error"]
    assert row["report_rel"] is None


def test_explicit_empty_strikes_survive_persistence_restart_and_requeue(
    tmp_path: Path, monkeypatch,
):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = manager.create_session(source_name="source.mov", strikes=[])
    (job.session_dir / "source.mov").write_bytes(b"source")
    raw = manager._conn.execute(
        "SELECT strikes FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()[0]
    manager._pool.shutdown(wait=True)
    manager._conn.close()
    submitted: list[tuple[str, list[float] | None]] = []
    monkeypatch.setattr(
        JobManager,
        "submit",
        lambda self, current, video: submitted.append(
            (current.id, current.strikes)
        ),
    )

    JobManager(sessions, Config())

    assert raw == "[]"
    assert submitted == [(job.id, [])]


@pytest.mark.parametrize("explicit", [False, True])
def test_guided_session_requires_injected_writer_before_directory_or_row(
    tmp_path: Path, explicit: bool,
):
    cfg = Config()
    cfg.report["guided_presentation_enabled"] = not explicit
    manager = JobManager(tmp_path / ("explicit" if explicit else "gate"), cfg)
    kwargs = (
        {"report_presentation_version": GUIDED_REPORT_PRESENTATION_VERSION}
        if explicit
        else {}
    )

    with pytest.raises(GuidedReportRendererUnavailable, match="writer"):
        manager.create_session(**kwargs)

    assert not any(path.is_dir() for path in manager.sessions_dir.iterdir())
    assert manager._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("explicit", [False, True])
def test_guided_session_with_writer_persists_assignment_and_disabled_replay(
    tmp_path: Path, explicit: bool,
):
    cfg = Config()
    cfg.report["guided_presentation_enabled"] = not explicit
    cfg.slowmo["annotated"] = False
    manager = JobManager(
        tmp_path / ("explicit" if explicit else "gate"),
        cfg,
        guided_html_writer=write_test_report_html,
    )
    kwargs = (
        {"report_presentation_version": GUIDED_REPORT_PRESENTATION_VERSION}
        if explicit
        else {}
    )

    job = manager.create_session(**kwargs)
    stored = manager.get(job.id)

    assert stored is not None
    assert stored.report_presentation_version == GUIDED_REPORT_PRESENTATION_VERSION
    assert stored.report_entitlements == ReportEntitlementSnapshot("disabled")
    assert stored.session_dir.is_dir()
    assert manager._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_progress_save_cannot_rewrite_creation_time_presentation_or_entitlement(
    tmp_path: Path,
):
    cfg = Config()
    cfg.slowmo["annotated"] = False
    manager = JobManager(
        tmp_path / "sessions",
        cfg,
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    job.report_presentation_version = REPORT_PRESENTATION_VERSION
    job.report_entitlements = ReportEntitlementSnapshot("available")
    job.log.append("progress")

    manager._save(job)

    stored = manager.get(job.id)
    assert stored is not None
    assert stored.report_presentation_version == GUIDED_REPORT_PRESENTATION_VERSION
    assert stored.report_entitlements == ReportEntitlementSnapshot("disabled")
    assert stored.log == ["progress"]


def test_guided_completion_commits_all_terminal_publication_fields_once(tmp_path: Path):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    job.status = PROCESSING
    manager._save(job)
    result = _guided_result(job, tmp_path)

    before = manager._conn.execute(
        "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
        " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(before) == (PROCESSING, None, None, None, None, 0)

    manager._complete_job(job, result)

    stored = manager.get(job.id)
    assert stored is not None and stored.status == DONE
    assert stored.report_rel == result.report_path.relative_to(job.session_dir).as_posix()
    assert stored.report_view_rel == result.report_view_path.relative_to(
        job.session_dir
    ).as_posix()
    assert stored.report_manifest_rel == result.manifest_path.relative_to(
        job.session_dir
    ).as_posix()
    assert stored.report_checksums_rel == result.checksums_path.relative_to(
        job.session_dir
    ).as_posix()
    assert stored.structured_report is True
    assert job.status == DONE
    assert job.report_rel == stored.report_rel


def test_guided_core_publication_commit_acknowledgement_loss_reads_back_done_row(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    user_id = "golfer"
    job = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)

    class CommitThenRaise:
        def __init__(self, connection):
            self.connection = connection
            self.raised = False

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def commit(self):
            self.connection.commit()
            if not self.raised:
                self.raised = True
                raise sqlite3.OperationalError("commit acknowledgement lost")

    manager._conn = CommitThenRaise(manager._conn)

    manager._complete_job(job, result)

    stored = manager.get(job.id)
    assert stored is not None and stored.status == DONE
    assert stored.structured_report is True
    assert (
        stored.report_rel,
        stored.report_view_rel,
        stored.report_manifest_rel,
        stored.report_checksums_rel,
    ) == (
        result.report_path.relative_to(job.session_dir).as_posix(),
        result.report_view_path.relative_to(job.session_dir).as_posix(),
        result.manifest_path.relative_to(job.session_dir).as_posix(),
        result.checksums_path.relative_to(job.session_dir).as_posix(),
    )
    assert job.status == DONE and job.structured_report is True
    assert manager.usage_this_month(user_id) == 1


def test_guided_gate_renderer_only_job_is_done_limited_and_has_no_partial(
    tmp_path: Path,
):
    row, manager = gate_helper.run_guided_job(
        tmp_path / "renderer-only",
        angle="face-on",
        renderer_failure=True,
    )
    try:
        bundle = gate_helper.assert_job_bundle(row)
        assert row.status == DONE
        assert row.structured_report is True
        assert bundle.view.trust.state.value == "limited"
        assert bundle.view.visual_evidence.state == "unavailable"
        assert bundle.view.visual_evidence.tracking_state is not TrackingState.UNAVAILABLE
        assert bundle.view.visual_evidence.render_reasons == (
            ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,
        )
        assert ReasonCode.FOCUSED_MEDIA_RENDER_FAILED in bundle.view.trust.reasons
        assert not any(
            media.role is MediaRole.PRIORITY_EVIDENCE
            for media in bundle.view.media
        )
        assert not any(
            path.name == "priority-evidence.png"
            for path in row.session_dir.rglob("*")
        )
        assert manager.usage_this_month("guided-gate-user") == 1
    finally:
        gate_helper.close_job_manager(manager)


def test_guided_gate_core_writer_job_is_failed_unpublished_and_zero_usage(
    tmp_path: Path,
):
    row, manager = gate_helper.run_guided_job(
        tmp_path / "core-writer",
        angle="face-on",
        core_writer_failure=True,
    )
    try:
        assert row.status == jobs_module.FAILED
        assert row.structured_report is False
        assert (
            row.report_rel,
            row.report_view_rel,
            row.report_manifest_rel,
            row.report_checksums_rel,
        ) == (None, None, None, None)
        assert manager.usage_this_month("guided-gate-user") == 0
        assert not any(
            path.is_dir() and path.name.startswith("report-bundle-")
            for path in row.session_dir.rglob("*")
        )
        assert not any(
            path.is_dir() and path.name.startswith(".report-attempt-")
            for path in row.session_dir.rglob("*")
        )
    finally:
        gate_helper.close_job_manager(manager)


def test_terminal_log_append_is_status_and_policy_guarded(tmp_path: Path):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    manager._complete_job(job, _guided_result(job, tmp_path))

    def core_publication_row():
        return tuple(
            manager._conn.execute(
                "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
                " report_checksums_rel, structured_report,"
                " report_presentation_version, report_entitlements_json"
                " FROM jobs WHERE id = ?",
                (job.id,),
            ).fetchone()
        )

    published = core_publication_row()
    assert manager._append_terminal_log(job, "post-commit note") is True
    assert manager.get(job.id).log == ["post-commit note"]
    assert core_publication_row() == published

    job.status = "failed"
    assert manager._append_terminal_log(job, "stale status") is False
    job.status = DONE
    job.report_presentation_version = REPORT_PRESENTATION_VERSION
    assert manager._append_terminal_log(job, "stale presentation") is False
    job.report_presentation_version = GUIDED_REPORT_PRESENTATION_VERSION
    job.report_entitlements = ReportEntitlementSnapshot("locked")
    assert manager._append_terminal_log(job, "stale entitlement") is False

    stored = manager.get(job.id)
    assert stored is not None and stored.log == ["post-commit note"]
    assert core_publication_row() == published


def test_terminal_log_append_commit_ambiguity_reads_back_without_touching_core(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    manager._complete_job(job, _guided_result(job, tmp_path))
    before = tuple(
        manager._conn.execute(
            "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
            " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
            (job.id,),
        ).fetchone()
    )

    class CommitThenRaise:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def commit(self):
            self.connection.commit()
            raise sqlite3.OperationalError("commit acknowledgement lost")

    manager._conn = CommitThenRaise(manager._conn)

    assert manager._append_terminal_log(job, "durably appended") is True

    stored = manager.get(job.id)
    assert stored is not None and stored.log == ["durably appended"]
    after = tuple(
        manager._conn.execute(
            "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
            " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
            (job.id,),
        ).fetchone()
    )
    assert after == before


def test_terminal_log_append_database_failure_cannot_touch_done_publication(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    manager._complete_job(job, _guided_result(job, tmp_path))
    before = tuple(
        manager._conn.execute(
            "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
            " report_checksums_rel, structured_report, log"
            " FROM jobs WHERE id = ?",
            (job.id,),
        ).fetchone()
    )
    manager._conn.execute(
        "CREATE TRIGGER reject_terminal_log BEFORE UPDATE OF log ON jobs"
        " BEGIN SELECT RAISE(ABORT, 'terminal log unavailable'); END"
    )
    manager._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="terminal log unavailable"):
        manager._append_terminal_log(job, "not persisted")

    after = tuple(
        manager._conn.execute(
            "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
            " report_checksums_rel, structured_report, log"
            " FROM jobs WHERE id = ?",
            (job.id,),
        ).fetchone()
    )
    assert after == before


def test_guided_runner_passes_persisted_policy_and_injected_writer(
    tmp_path: Path, monkeypatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")
    seen = {}

    def guided_analyze(
        video_path,
        out_dir=None,
        hand="right",
        manual_strikes=None,
        cfg=None,
        keep_work=False,
        fast=False,
        log=print,
        progress=None,
        angle="face-on",
        club=None,
        level=None,
        replay_locked=False,
        report_presentation_version=None,
        report_entitlements=None,
        guided_html_writer=None,
    ):
        seen.update(
            presentation=report_presentation_version,
            entitlements=report_entitlements,
            writer=guided_html_writer,
            replay_locked=replay_locked,
        )
        return _guided_result(job, tmp_path)

    monkeypatch.setattr(jobs_module, "analyze_video", guided_analyze)
    manager._run(job, source)

    stored = manager.get(job.id)
    assert stored is not None and stored.status == DONE
    assert stored.structured_report is True
    assert seen == {
        "presentation": GUIDED_REPORT_PRESENTATION_VERSION,
        "entitlements": ReportEntitlementSnapshot("available"),
        "writer": write_test_report_html,
        "replay_locked": False,
    }


def test_stale_worker_cannot_overwrite_an_already_committed_done_row(
    tmp_path: Path, monkeypatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")

    def winning_worker(*args, **kwargs):
        result = _guided_result(job, tmp_path)
        manager._complete_job(job, result)
        return result

    monkeypatch.setattr(jobs_module, "analyze_video", winning_worker)
    manager._run(job, source)

    stored = manager.get(job.id)
    assert stored is not None and stored.status == DONE
    assert stored.structured_report is True
    assert stored.report_view_rel is not None


@pytest.mark.parametrize(
    "damage",
    ["view-mismatch", "manifest-mismatch", "checksums-mismatch", "missing-view"],
)
def test_invalid_guided_bundle_never_publishes_a_done_row(
    tmp_path: Path, damage: str,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    job.status = PROCESSING
    manager._save(job)
    result = _guided_result(job, tmp_path)
    if damage == "view-mismatch":
        result = replace(result, report_view_path=result.manifest_path)
    elif damage == "manifest-mismatch":
        result = replace(result, manifest_path=result.checksums_path)
    elif damage == "checksums-mismatch":
        result = replace(result, checksums_path=result.report_view_path)
    else:
        result.report_view_path.unlink()

    with pytest.raises((OSError, RuntimeError, ValueError)):
        manager._complete_job(job, result)

    stored = manager.get(job.id)
    assert stored is not None and stored.status == PROCESSING
    assert (
        stored.report_rel,
        stored.report_view_rel,
        stored.report_manifest_rel,
        stored.report_checksums_rel,
        stored.structured_report,
    ) == (None, None, None, None, False)


def test_guided_completion_rejects_redirected_job_root_without_committing_artifact_rels(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)
    target = tmp_path / "donor-job"
    shutil.copytree(job.session_dir, target)

    with temporary_directory_redirect(tmp_path, job.session_dir, target):
        with pytest.raises((ReportArtifactValidationError, ValueError)):
            manager._complete_job(job, result)

    _assert_processing_without_publication(manager, job)


def test_guided_completion_rejects_redirected_out_root_without_committing_artifact_rels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)
    original = job.session_dir / "out"
    target = job.session_dir / "donor-out"
    shutil.copytree(original, target)

    # A static out redirect is already rejected by the old layout check. Swap
    # at the loader boundary to reproduce the confirmed resolve-before-validate
    # window and keep this regression genuinely RED against the old flow.
    if hasattr(jobs_module, "open_job_published_bundle"):
        real_open = jobs_module.open_job_published_bundle

        @contextmanager
        def open_after_redirect(*args, **kwargs):
            with temporary_directory_redirect(tmp_path, original, target):
                with real_open(*args, **kwargs) as pinned:
                    yield pinned

        monkeypatch.setattr(
            jobs_module, "open_job_published_bundle", open_after_redirect
        )
    else:
        real_load = jobs_module.load_published_bundle

        def load_after_redirect(*args, **kwargs):
            with temporary_directory_redirect(tmp_path, original, target):
                return real_load(*args, **kwargs)

        monkeypatch.setattr(jobs_module, "load_published_bundle", load_after_redirect)

    with pytest.raises((ReportArtifactValidationError, ValueError)):
        manager._complete_job(job, result)

    _assert_processing_without_publication(manager, job)


def test_guided_completion_rejects_redirected_analysis_child_without_committing_artifact_rels(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)
    target = result.session_dir.with_name("donor-analysis")
    shutil.copytree(result.session_dir, target)

    with temporary_directory_redirect(tmp_path, result.session_dir, target):
        with pytest.raises((ReportArtifactValidationError, ValueError)):
            manager._complete_job(job, result)

    _assert_processing_without_publication(manager, job)


def test_guided_completion_rejects_artifacts_from_a_different_analysis_child(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    source = _guided_result(job, tmp_path, analysis_name="source")
    donor = _guided_result(
        job,
        tmp_path,
        attempt_id="b" * 32,
        analysis_name="donor",
    )
    mismatched = replace(donor, session_dir=source.session_dir)

    with pytest.raises(ValueError, match="direct analysis session"):
        manager._complete_job(job, mismatched)

    _assert_processing_without_publication(manager, job)


def test_guided_completion_rolls_back_when_final_precommit_identity_verify_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)
    real_open = jobs_module.open_job_published_bundle
    verify_calls = 0
    context_exited = False

    @contextmanager
    def fail_final_precommit_verify(*args, **kwargs):
        nonlocal context_exited, verify_calls
        with real_open(*args, **kwargs) as pinned:

            class ControlledPinnedBundle:
                bundle = pinned.bundle
                report_rels = pinned.report_rels

                @staticmethod
                def verify_lexical_identity() -> None:
                    nonlocal verify_calls
                    verify_calls += 1
                    if verify_calls == 2:
                        raise ReportArtifactValidationError(
                            "injected final precommit identity failure"
                        )

            try:
                yield ControlledPinnedBundle()
            finally:
                context_exited = True

    monkeypatch.setattr(
        jobs_module,
        "open_job_published_bundle",
        fail_final_precommit_verify,
    )

    with pytest.raises(
        ReportArtifactValidationError,
        match="final precommit identity failure",
    ):
        manager._complete_job(job, result)

    assert verify_calls == 2
    assert context_exited is True
    _assert_processing_without_publication(manager, job)


def test_guided_completion_keeps_manager_lock_through_pinned_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    manager.cfg.web["retention_days"] = 0
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)

    cleanup_started = threading.Event()
    allow_cleanup = threading.Event()
    retention_attempted = threading.Event()
    retention_began = threading.Event()
    completion_errors: list[BaseException] = []
    retention_errors: list[BaseException] = []
    real_open = jobs_module.open_job_published_bundle
    real_recover = manager._recover_history_operations_locked

    @contextmanager
    def block_pinned_cleanup(*args, **kwargs):
        with real_open(*args, **kwargs) as pinned:
            try:
                yield pinned
            finally:
                cleanup_started.set()
                if not allow_cleanup.wait(timeout=5):
                    raise AssertionError("publication cleanup was not released")

    def observe_retention_start() -> None:
        retention_began.set()
        real_recover()

    def complete() -> None:
        try:
            manager._complete_job(job, result)
        except BaseException as exc:  # pragma: no cover - asserted below
            completion_errors.append(exc)

    def retain() -> None:
        retention_attempted.set()
        try:
            manager._cleanup_expired()
        except BaseException as exc:  # pragma: no cover - asserted below
            retention_errors.append(exc)

    monkeypatch.setattr(
        jobs_module,
        "open_job_published_bundle",
        block_pinned_cleanup,
    )
    monkeypatch.setattr(
        manager,
        "_recover_history_operations_locked",
        observe_retention_start,
    )
    completion_thread = threading.Thread(target=complete)
    retention_thread = threading.Thread(target=retain)
    retention_started = False
    retention_began_before_cleanup_finished = False
    completion_thread.start()
    try:
        assert cleanup_started.wait(timeout=5)
        retention_thread.start()
        retention_started = True
        assert retention_attempted.wait(timeout=5)
        retention_began_before_cleanup_finished = retention_began.wait(timeout=1)
    finally:
        allow_cleanup.set()
        completion_thread.join(timeout=5)
        if retention_started:
            retention_thread.join(timeout=5)

    assert not completion_thread.is_alive()
    assert not retention_thread.is_alive()
    assert completion_errors == []
    assert retention_errors == []
    assert retention_began_before_cleanup_finished is False
    assert retention_began.is_set()


def test_stale_nonprocessing_status_cannot_publish_bundle(tmp_path: Path):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    result = _guided_result(job, tmp_path)

    with pytest.raises(RuntimeError, match="no longer processing"):
        manager._complete_job(job, result)

    row = manager._conn.execute(
        "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
        " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == (jobs_module.QUEUED, None, None, None, None, 0)


def test_completion_rejects_noncanonical_final_root_without_committing_artifact_rels(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)
    original_root = result.report_path.parent
    invalid_root = original_root.with_name("published-report")
    original_root.rename(invalid_root)
    result = replace(
        result,
        report_path=invalid_root / result.report_path.name,
        metrics_path=invalid_root / result.metrics_path.name,
        report_view_path=invalid_root / result.report_view_path.name,
        manifest_path=invalid_root / result.manifest_path.name,
        checksums_path=invalid_root / result.checksums_path.name,
    )

    with pytest.raises(ValueError):
        manager._complete_job(job, result)

    row = manager._conn.execute(
        "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
        " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == (PROCESSING, None, None, None, None, 0)


@pytest.mark.parametrize("damage", ["presentation", "malformed-entitlement"])
def test_persisted_policy_mismatch_or_malformed_json_fails_closed_before_publish(
    tmp_path: Path, damage: str,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    job.status = PROCESSING
    manager._save(job)
    result = _guided_result(job, tmp_path)
    if damage == "presentation":
        manager._conn.execute(
            "UPDATE jobs SET report_presentation_version = ? WHERE id = ?",
            (REPORT_PRESENTATION_VERSION, job.id),
        )
    else:
        manager._conn.execute(
            "UPDATE jobs SET report_entitlements_json = ? WHERE id = ?",
            ('{"coach_replay":"available", "extra":true}', job.id),
        )
    manager._conn.commit()

    with pytest.raises((RuntimeError, ValueError)):
        manager._complete_job(job, result)

    row = manager._conn.execute(
        "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
        " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == (PROCESSING, None, None, None, None, 0)


def test_completion_rejects_bundle_replay_state_that_disagrees_with_persisted_policy(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    job.report_entitlements = ReportEntitlementSnapshot("locked")
    manager._conn.execute(
        "UPDATE jobs SET report_entitlements_json = ? WHERE id = ?",
        (job.report_entitlements.to_json(), job.id),
    )
    manager._conn.commit()
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)

    with pytest.raises(ValueError):
        manager._complete_job(job, result)

    row = manager._conn.execute(
        "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
        " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == (PROCESSING, None, None, None, None, 0)


def test_guided_publication_database_abort_leaves_processing_row_and_memory_untouched(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    job.status = PROCESSING
    manager._save(job)
    result = _guided_result(job, tmp_path)
    manager._conn.execute(
        "CREATE TRIGGER reject_done BEFORE UPDATE OF status ON jobs"
        " WHEN NEW.status = 'done' BEGIN"
        " SELECT RAISE(ABORT, 'deterministic publication failure'); END"
    )
    manager._conn.commit()

    with pytest.raises(sqlite3.DatabaseError, match="deterministic publication failure"):
        manager._complete_job(job, result)

    row = manager._conn.execute(
        "SELECT status, report_rel, report_view_rel, report_manifest_rel,"
        " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert tuple(row) == (PROCESSING, None, None, None, None, 0)
    assert job.status == PROCESSING
    assert job.report_rel is None
    assert job.structured_report is False


def test_legacy_completion_sets_only_report_rel_and_unstructured_capability(
    tmp_path: Path,
):
    manager = JobManager(tmp_path / "sessions", Config())
    job = manager.create_session()
    job.status = PROCESSING
    manager._save(job)
    result_dir = job.session_dir / "out" / "source"
    result_dir.mkdir(parents=True)
    report = result_dir / "report.html"
    metrics = result_dir / "metrics.json"
    report.write_text("<html>legacy</html>", encoding="utf-8")
    metrics.write_text("{}", encoding="utf-8")
    inputs = guided_bundle_inputs(tmp_path)
    result = SessionResult(
        session_dir=result_dir,
        report_path=report,
        metrics_path=metrics,
        video=inputs["video"],
    )

    manager._complete_job(job, result)

    stored = manager.get(job.id)
    assert stored is not None and stored.status == DONE
    assert stored.report_rel == "out/source/report.html"
    assert stored.report_view_rel is None
    assert stored.report_manifest_rel is None
    assert stored.report_checksums_rel is None
    assert stored.structured_report is False


def test_retry_treats_all_null_structured_rels_as_authoritative_empty_protection(
    tmp_path: Path, monkeypatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")
    abandoned = _guided_result(job, tmp_path)
    abandoned_root = abandoned.report_path.parent

    def observe_cleanup(*args, **kwargs):
        assert not abandoned_root.exists()
        raise ZeroStrikesError("stop after recovery observation")

    monkeypatch.setattr(jobs_module, "analyze_video", observe_cleanup)
    manager._run(job, source)

    assert not abandoned_root.exists()


def test_analysis_failure_cleans_worker_created_attempt_and_final_before_failed(
    tmp_path: Path, monkeypatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        source_name="source.mov",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")
    owned: list[Path] = []

    def fail_after_writing_owned_graph(*args, **kwargs):
        published = _guided_result(job, tmp_path, attempt_id="a" * 32)
        attempt = begin_report_bundle(
            published.session_dir, attempt_id="b" * 32
        )
        owned.extend((published.report_path.parent, attempt.staging_dir))
        raise ZeroStrikesError("no usable strikes")

    monkeypatch.setattr(
        jobs_module, "analyze_video", fail_after_writing_owned_graph
    )

    manager._run(job, source)

    stored = manager.get(job.id)
    assert stored is not None and stored.status == "failed"
    assert stored.report_rel is None
    assert owned and all(not path.exists() for path in owned)


def test_cleanup_refusal_keeps_failure_actionable_and_durable(
    tmp_path: Path, monkeypatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        source_name="source.mov",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")
    calls = 0
    owned: list[Path] = []

    def cleanup(current):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 0
        raise CoreReportBundleError("ambiguous ownership marker")

    def fail_after_writing_owned_graph(*args, **kwargs):
        published = _guided_result(job, tmp_path, attempt_id="c" * 32)
        owned.append(published.report_path.parent)
        raise ZeroStrikesError("no usable strikes")

    monkeypatch.setattr(manager, "_cleanup_retry_report_bundles", cleanup)
    monkeypatch.setattr(
        jobs_module, "analyze_video", fail_after_writing_owned_graph
    )

    manager._run(job, source)

    row = manager._conn.execute(
        "SELECT status, error, log, report_rel FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert calls == 2
    assert row["status"] == PROCESSING
    assert "cleanup is pending" in row["error"].lower()
    assert any("cleanup is pending" in line.lower() for line in jobs_module.json.loads(row["log"]))
    assert row["report_rel"] is None
    assert owned[0].is_dir()


def test_worker_completion_validation_failure_stays_processing_without_exposure(
    tmp_path: Path, monkeypatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        source_name="source.mov",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")

    def invalid_result(*args, **kwargs):
        result = _guided_result(job, tmp_path, attempt_id="d" * 32)
        original = result.report_path.parent
        invalid = original.with_name("published-report")
        original.rename(invalid)
        return replace(
            result,
            report_path=invalid / result.report_path.name,
            metrics_path=invalid / result.metrics_path.name,
            report_view_path=invalid / result.report_view_path.name,
            manifest_path=invalid / result.manifest_path.name,
            checksums_path=invalid / result.checksums_path.name,
        )

    monkeypatch.setattr(jobs_module, "analyze_video", invalid_result)

    manager._run(job, source)

    row = manager._conn.execute(
        "SELECT status, error, report_rel, report_view_rel, report_manifest_rel,"
        " report_checksums_rel, structured_report FROM jobs WHERE id = ?",
        (job.id,),
    ).fetchone()
    assert row["status"] == PROCESSING
    assert "publication could not be validated" in row["error"].lower()
    assert tuple(row[name] for name in (
        "report_rel", "report_view_rel", "report_manifest_rel", "report_checksums_rel"
    )) == (None, None, None, None)
    assert row["structured_report"] == 0


def test_restart_missing_source_cleans_owned_graph_before_failed(
    tmp_path: Path, monkeypatch,
):
    sessions = tmp_path / "sessions"
    manager = JobManager(
        sessions,
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        source_name="missing.mov",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(job) is True
    published = _guided_result(job, tmp_path, attempt_id="e" * 32)
    attempt = begin_report_bundle(published.session_dir, attempt_id="f" * 32)
    owned = (published.report_path.parent, attempt.staging_dir)
    manager._pool.shutdown(wait=True)
    manager._conn.close()
    monkeypatch.setattr(JobManager, "submit", lambda self, current, video: None)

    restarted = JobManager(
        sessions,
        Config(),
        guided_html_writer=write_test_report_html,
    )

    stored = restarted.get(job.id)
    assert stored is not None and stored.status == "failed"
    assert all(not path.exists() for path in owned)


def test_retry_protects_complete_persisted_bundle_and_reclaims_other_final(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    protected = _guided_result(job, tmp_path, attempt_id="a" * 32)
    abandoned = _guided_result(
        job,
        tmp_path,
        attempt_id="b" * 32,
        analysis_name="source-2",
    )
    rels = tuple(
        path.relative_to(job.session_dir).as_posix()
        for path in (
            protected.report_path,
            protected.report_view_path,
            protected.manifest_path,
            protected.checksums_path,
        )
    )
    manager._conn.execute(
        "UPDATE jobs SET report_rel = ?, report_view_rel = ?,"
        " report_manifest_rel = ?, report_checksums_rel = ?,"
        " structured_report = 1 WHERE id = ?",
        (*rels, job.id),
    )
    manager._conn.commit()

    assert manager._cleanup_retry_report_bundles(job) == 1
    assert protected.report_path.parent.is_dir()
    assert not abandoned.report_path.parent.exists()


def test_retry_prepares_every_analysis_child_before_deleting_an_earlier_bundle(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    removable = _guided_result(
        job,
        tmp_path,
        attempt_id="d" * 32,
        analysis_name="a-valid",
    )
    malformed_session = job.session_dir / "out" / "z-malformed"
    malformed = malformed_session / (".report-attempt-" + "e" * 32)
    malformed.mkdir(parents=True)
    (malformed / ".report-attempt-owner.json").write_text(
        "{}\n", encoding="utf-8"
    )

    with pytest.raises(CoreReportBundleError, match="ownership marker"):
        manager._cleanup_retry_report_bundles(job)

    assert removable.report_path.parent.is_dir()
    assert malformed.is_dir()


@pytest.mark.parametrize(
    "damage",
    ["partial", "unsafe", "duplicate", "cross-child", "recursive"],
)
def test_retry_fails_closed_before_cleanup_for_invalid_persisted_rel_snapshot(
    tmp_path: Path, damage: str,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    published = _guided_result(job, tmp_path)
    rels = [
        path.relative_to(job.session_dir).as_posix()
        for path in (
            published.report_path,
            published.report_view_path,
            published.manifest_path,
            published.checksums_path,
        )
    ]
    if damage == "partial":
        rels[1:] = [None, None, None]
    elif damage == "unsafe":
        rels[0] = rels[0].replace("out/source/", "out/source/../")
    elif damage == "duplicate":
        rels[1] = rels[0]
    elif damage == "cross-child":
        rels[3] = rels[3].replace("out/source/", "out/source-2/")
    else:
        rels = [value.replace("out/source/", "out/outer/nested/") for value in rels]
    manager._conn.execute(
        "UPDATE jobs SET report_rel = ?, report_view_rel = ?,"
        " report_manifest_rel = ?, report_checksums_rel = ? WHERE id = ?",
        (*rels, job.id),
    )
    manager._conn.commit()

    with pytest.raises(CoreReportBundleError):
        manager._cleanup_retry_report_bundles(job)

    assert published.report_path.parent.is_dir()


def test_retry_counts_every_direct_out_entry_and_fails_at_entry_257(tmp_path: Path):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    published = _guided_result(job, tmp_path)
    out_dir = job.session_dir / "out"
    for index in range(256):
        (out_dir / f"plain-{index:03d}.txt").write_text("decoy", encoding="utf-8")

    with pytest.raises(CoreReportBundleError, match="direct-entry bound"):
        manager._cleanup_retry_report_bundles(job)

    assert published.report_path.parent.is_dir()


def test_retry_preserves_direct_files_and_recursive_only_bundle_decoys(tmp_path: Path):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    nested = _guided_result(
        job,
        tmp_path,
        attempt_id="c" * 32,
        analysis_name="decoy/nested",
    )
    plain = job.session_dir / "out" / "plain-file.txt"
    plain.write_text("preserve", encoding="utf-8")

    assert manager._cleanup_retry_report_bundles(job) == 0
    assert plain.read_text(encoding="utf-8") == "preserve"
    assert nested.report_path.parent.is_dir()


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_retry_does_not_follow_direct_reparse_analysis_children(
    tmp_path: Path, kind: str,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    outside = tmp_path / f"outside-{kind}"
    outside.mkdir()
    attempt = begin_report_bundle(outside, attempt_id="d" * 32)
    published = publish_report_bundle(
        build_report_bundle(attempt, **guided_bundle_inputs(tmp_path))
    )
    out_dir = job.session_dir / "out"
    out_dir.mkdir()
    linked = out_dir / f"linked-{kind}"
    try:
        if kind == "symlink":
            linked.symlink_to(outside, target_is_directory=True)
        else:
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                pytest.skip("junction creation is unavailable")
    except OSError:
        pytest.skip(f"{kind} creation is unavailable")

    assert manager._cleanup_retry_report_bundles(job) == 0
    assert published.report_path.parent.is_dir()


def test_proof_build_excludes_just_committed_job_from_real_done_query(
    tmp_path: Path, monkeypatch,
):
    cfg = Config()
    cfg.proof_cycle["enabled"] = True
    manager = JobManager(tmp_path / "sessions", cfg)
    job = manager.create_session(user_id="golfer", club="iron")
    job.status = PROCESSING
    manager._save(job)
    result_dir = job.session_dir / "out" / "source"
    result_dir.mkdir(parents=True)
    report = result_dir / "report.html"
    report.write_text("<html>legacy</html>", encoding="utf-8")
    result = SessionResult(
        session_dir=result_dir,
        report_path=report,
        metrics_path=result_dir / "metrics.json",
        video=guided_bundle_inputs(tmp_path)["video"],
    )
    manager._complete_job(job, result)
    seen_ids = []
    real_build = jobs_module.build_proof_cycle_artifact

    def recording_build(current, prior_jobs, configured, **kwargs):
        seen_ids.extend(candidate.id for candidate in prior_jobs)
        return real_build(current, prior_jobs, configured, **kwargs)

    monkeypatch.setattr(jobs_module, "build_proof_cycle_artifact", recording_build)
    manager._write_proof_cycle_artifact(job)

    assert job.id not in seen_ids


def test_proof_failure_after_core_commit_is_nonblocking_and_never_ordinary_saves(
    tmp_path: Path, monkeypatch,
):
    cfg = Config()
    cfg.proof_cycle["enabled"] = True
    manager = JobManager(
        tmp_path / "sessions",
        cfg,
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        user_id="golfer",
        club="iron",
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")
    result_box = {}

    def analyze(*args, **kwargs):
        result = _guided_result(job, tmp_path)
        result_box["result"] = result
        result_box["checksums"] = result.checksums_path.read_bytes()
        return result

    def fail_after_observing_done(current, artifact):
        row = manager._conn.execute(
            "SELECT status, structured_report, report_checksums_rel"
            " FROM jobs WHERE id = ?",
            (current.id,),
        ).fetchone()
        assert tuple(row[:2]) == (DONE, 1)
        assert row["report_checksums_rel"] is not None
        raise OSError("proof disk unavailable")

    monkeypatch.setattr(jobs_module, "analyze_video", analyze)
    monkeypatch.setattr(jobs_module, "write_proof_cycle_artifact", fail_after_observing_done)
    monkeypatch.setattr(
        manager,
        "_save",
        lambda job: pytest.fail("ordinary _save ran after guarded publication"),
    )

    manager._run(job, source)

    stored = manager.get(job.id)
    result = result_box["result"]
    assert stored is not None and stored.status == DONE
    assert stored.structured_report is True
    assert result.checksums_path.read_bytes() == result_box["checksums"]


def test_source_cleanup_failure_cannot_rewrite_committed_done_row(
    tmp_path: Path, monkeypatch,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    job = manager.create_session(
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        jobs_module,
        "analyze_video",
        lambda *args, **kwargs: _guided_result(job, tmp_path),
    )
    monkeypatch.setattr(
        manager,
        "_delete_source_if_configured",
        lambda current: (_ for _ in ()).throw(OSError("source cleanup failed")),
    )
    monkeypatch.setattr(
        manager,
        "_save",
        lambda current: pytest.fail("ordinary _save ran after guarded publication"),
    )

    manager._run(job, source)

    stored = manager.get(job.id)
    assert stored is not None and stored.status == DONE
    assert stored.structured_report is True


def test_guided_capture_only_completion_keeps_existing_courtesy_calculation(
    tmp_path: Path,
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    user_id = "golfer"
    first = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(first) is True
    manager._complete_job(first, _guided_result(first, tmp_path, capture_only=True))
    assert manager.usage_this_month(user_id) == 0

    second = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(second) is True
    manager._complete_job(
        second,
        _guided_result(
            second,
            tmp_path,
            attempt_id="b" * 32,
            capture_only=True,
        ),
    )
    assert manager.usage_this_month(user_id) == 1


def test_guided_coaching_completion_consumes_allowance_exactly_once(tmp_path: Path):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    user_id = "golfer"
    job = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)

    manager._complete_job(job, result)
    assert manager.usage_this_month(user_id) == 1
    with pytest.raises(RuntimeError, match="no longer processing"):
        manager._complete_job(job, result)
    assert manager.usage_this_month(user_id) == 1


@pytest.mark.parametrize("artifact", ["report", "metrics", "structured-flag"])
def test_structured_quota_never_reclassifies_mutated_raw_artifacts_as_courtesy(
    tmp_path: Path, artifact: str
):
    manager = JobManager(
        tmp_path / "sessions",
        Config(),
        guided_html_writer=write_test_report_html,
    )
    user_id = "golfer"
    job = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)
    manager._complete_job(job, result)
    if artifact == "report":
        result.report_path.write_text(
            result.report_path.read_text(encoding="utf-8").replace(
                "coaching_ready", "capture_only"
            ),
            encoding="utf-8",
        )
    elif artifact == "metrics":
        payload = jobs_module.json.loads(
            result.metrics_path.read_text(encoding="utf-8")
        )
        payload["session_notes"] = ["Tracking was unstable — numbers may be off."]
        result.metrics_path.write_text(
            jobs_module.json.dumps(payload), encoding="utf-8"
        )
    else:
        manager._conn.execute(
            "UPDATE jobs SET structured_report = 0 WHERE id = ?", (job.id,)
        )
        manager._conn.commit()

    stored = manager.get(job.id)
    assert stored is not None
    assert manager.coaching_eligible(stored) is False
    assert manager.refilm_rejections_this_month(user_id) == 0
    assert manager.usage_this_month(user_id) == 1


def test_corrupt_structured_bundle_remains_consumed_across_restart_and_reset_receipt(
    tmp_path: Path, monkeypatch
):
    sessions = tmp_path / "sessions"
    manager = JobManager(
        sessions,
        Config(),
        guided_html_writer=write_test_report_html,
    )
    user_id = "golfer"
    job = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    assert manager._mark_processing(job) is True
    result = _guided_result(job, tmp_path)
    manager._complete_job(job, result)
    result.report_path.write_text("corrupt", encoding="utf-8")
    manager._pool.shutdown(wait=True)
    manager._conn.close()
    monkeypatch.setattr(JobManager, "submit", lambda self, current, video: None)

    restarted = JobManager(
        sessions,
        Config(),
        guided_html_writer=write_test_report_html,
    )
    assert restarted.usage_this_month(user_id) == 1
    summary = restarted.reset_user_history(user_id)
    assert summary.deleted_jobs == 1
    assert restarted.usage_this_month(user_id) == 1
    assert restarted.refilm_rejections_this_month(user_id) == 0


def test_crash_after_bundle_rename_requeues_one_reservation_and_completes_once(
    tmp_path: Path, monkeypatch,
):
    sessions = tmp_path / "sessions"
    cfg = Config()
    manager = JobManager(
        sessions,
        cfg,
        guided_html_writer=write_test_report_html,
    )
    user_id = "golfer"
    job = manager.create_session(
        user_id=user_id,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    source = job.session_dir / "source.mov"
    source.write_bytes(b"source")
    assert manager._mark_processing(job) is True
    renamed_not_committed = _guided_result(job, tmp_path, attempt_id="a" * 32)
    abandoned_root = renamed_not_committed.report_path.parent
    assert manager.usage_this_month(user_id) == 1

    monkeypatch.setattr(JobManager, "submit", lambda self, current, video: None)
    restarted = JobManager(
        sessions,
        cfg,
        guided_html_writer=write_test_report_html,
    )
    retry = restarted.get(job.id)
    assert retry is not None and retry.status == jobs_module.QUEUED
    assert retry.report_rel is None and retry.structured_report is False
    assert restarted.usage_this_month(user_id) == 1

    monkeypatch.setattr(
        jobs_module,
        "analyze_video",
        lambda *args, **kwargs: _guided_result(
            retry, tmp_path, attempt_id="e" * 32
        ),
    )
    restarted._run(retry, source)

    assert not abandoned_root.exists()
    assert restarted.get(job.id).status == DONE
    assert restarted.usage_this_month(user_id) == 1
