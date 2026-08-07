from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess

import pytest

from swinglab.config import Config
from swinglab.pipeline import SessionResult, ZeroStrikesError
from swinglab.report import REPORT_PRESENTATION_VERSION
from swinglab.report_artifacts import ReportEntitlementSnapshot
from swinglab.report_bundle import (
    CoreReportBundleError,
    GuidedReportRendererUnavailable,
    begin_report_bundle,
    build_report_bundle,
    publish_report_bundle,
)
from swinglab.report_view import GUIDED_REPORT_PRESENTATION_VERSION
from swinglab.web import jobs as jobs_module
from swinglab.web.jobs import DONE, PROCESSING, Job, JobManager
from tests.report_bundle_fixtures import guided_bundle_inputs, write_test_report_html


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


def test_guided_report_presentation_gate_is_strict_and_ships_disabled(tmp_path: Path):
    assert Config().report["guided_presentation_enabled"] is False
    shipped = Config.load(Path(__file__).parent.parent / "config.yaml")
    assert shipped.report["guided_presentation_enabled"] is False

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

    with pytest.raises(ValueError, match="unknown report presentation"):
        manager.create_session(report_presentation_version="future-report-v9")

    after_dirs = {path.name for path in manager.sessions_dir.iterdir() if path.is_dir()}
    assert after_dirs == before_dirs
    assert manager._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


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
