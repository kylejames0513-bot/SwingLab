from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from swinglab.report_artifacts import (
    REPORT_CHECKSUMS_FILENAME,
    REPORT_MANIFEST_FILENAME,
    REPORT_MANIFEST_FORMAT,
    REPORT_VIEW_FILENAME,
    ReportBundleManifest,
    ReportOutcome,
    write_report_manifest,
)
from swinglab.report_bundle import (
    CoreReportBundleError,
    ReportBundleAttempt,
    begin_report_bundle,
    build_report_bundle,
    cleanup_abandoned_report_bundles,
    discard_report_bundle_attempt,
    publish_report_bundle,
)
from tests.report_bundle_fixtures import guided_bundle_inputs


OWNER = ".report-attempt-owner.json"


def _session(tmp_path: Path) -> Path:
    path = tmp_path / "session"
    path.mkdir()
    return path


def _staged(tmp_path: Path, attempt_id: str = "b" * 32):
    session = _session(tmp_path)
    attempt = begin_report_bundle(session, attempt_id=attempt_id)
    staged = build_report_bundle(attempt, **guided_bundle_inputs(tmp_path))
    return attempt, staged


def _published(tmp_path: Path, attempt_id: str = "b" * 32):
    attempt, staged = _staged(tmp_path, attempt_id)
    return attempt, publish_report_bundle(staged)


def _rels(bundle) -> tuple[str, str, str, str]:
    return tuple(path.relative_to(bundle.root.parent).as_posix() for path in (
        bundle.report_path,
        bundle.report_view_path,
        bundle.manifest_path,
        bundle.checksums_path,
    ))


def test_recovery_removes_exact_owner_marked_premanifest_attempt(tmp_path):
    session = _session(tmp_path)
    attempt = begin_report_bundle(session, attempt_id="1" * 32)
    assert cleanup_abandoned_report_bundles(session) == 1
    assert not attempt.staging_dir.exists()


def test_matching_manifest_replaces_owner_marker_authority(tmp_path):
    session = _session(tmp_path)
    attempt = begin_report_bundle(session, attempt_id="2" * 32)
    manifest = ReportBundleManifest(
        REPORT_MANIFEST_FORMAT,
        attempt.attempt_id,
        "guided-report-v1",
        ReportOutcome.CAPTURE_ONLY,
        (),
    )
    write_report_manifest(attempt.staging_dir / REPORT_MANIFEST_FILENAME, manifest)
    (attempt.staging_dir / OWNER).unlink()
    assert cleanup_abandoned_report_bundles(session) == 1
    assert not attempt.staging_dir.exists()


def test_manifest_and_remaining_marker_must_agree(tmp_path):
    session = _session(tmp_path)
    attempt = begin_report_bundle(session, attempt_id="3" * 32)
    manifest = ReportBundleManifest(
        REPORT_MANIFEST_FORMAT,
        attempt.attempt_id,
        "guided-report-v1",
        ReportOutcome.CAPTURE_ONLY,
        (),
    )
    write_report_manifest(attempt.staging_dir / REPORT_MANIFEST_FILENAME, manifest)
    (attempt.staging_dir / OWNER).write_bytes(
        b'{"attempt_id":"44444444444444444444444444444444","format":"report-bundle-attempt-v1"}\n'
    )
    with pytest.raises(CoreReportBundleError, match="ownership"):
        cleanup_abandoned_report_bundles(session)
    assert attempt.staging_dir.is_dir()


@pytest.mark.parametrize(
    "owner",
    [
        b"{}\n",
        b'{"attempt_id":"ABC","format":"report-bundle-attempt-v1"}\n',
        b'{"attempt_id":"55555555555555555555555555555555","format":"wrong"}\n',
        b'{"format":"report-bundle-attempt-v1","attempt_id":"55555555555555555555555555555555"}\n',
        b"x" * 4097,
    ],
)
def test_malformed_or_noncanonical_owner_fails_closed(tmp_path, owner):
    session = _session(tmp_path)
    target = session / (".report-attempt-" + "5" * 32)
    target.mkdir()
    (target / OWNER).write_bytes(owner)
    with pytest.raises(CoreReportBundleError):
        cleanup_abandoned_report_bundles(session)
    assert target.is_dir()


def test_recovery_removes_validated_attempt_but_preserves_unrelated_neighbor(tmp_path):
    attempt, _ = _staged(tmp_path)
    attacker = attempt.session_dir / ".report-attempt-not-owned"
    attacker.mkdir()
    (attacker / OWNER).write_text("attacker", encoding="utf-8")
    assert cleanup_abandoned_report_bundles(attempt.session_dir) == 1
    assert not attempt.staging_dir.exists()
    assert attacker.is_dir()


def test_protected_rels_none_preserves_every_valid_final(tmp_path):
    attempt, published = _published(tmp_path)
    assert cleanup_abandoned_report_bundles(attempt.session_dir, protected_rels=None) == 0
    assert published.root.is_dir()


def test_complete_four_rel_group_protects_current_final(tmp_path):
    attempt, published = _published(tmp_path)
    assert cleanup_abandoned_report_bundles(attempt.session_dir, protected_rels=_rels(published)) == 0
    assert published.root.is_dir()


def test_complete_empty_snapshot_removes_valid_renamed_unpublished_final(tmp_path):
    attempt, published = _published(tmp_path)
    assert cleanup_abandoned_report_bundles(attempt.session_dir, protected_rels=()) == 1
    assert not published.root.exists()


@pytest.mark.parametrize(
    "protected",
    [
        ("report-bundle-" + "b" * 32 + "/report.html",),
        (
            "report-bundle-" + "b" * 32 + "/report.html",
            "report-bundle-" + "b" * 32 + "/" + REPORT_VIEW_FILENAME,
            "report-bundle-" + "b" * 32 + "/" + REPORT_MANIFEST_FILENAME,
            "other/report-bundle-checksums.json",
        ),
        (
            "../report.html",
            "../report-view.json",
            "../report-bundle-manifest.json",
            "../report-bundle-checksums.json",
        ),
        (
            "report-bundle-" + "b" * 32 + "/report.html",
            "report-bundle-" + "b" * 32 + "/report.html",
            "report-bundle-" + "b" * 32 + "/" + REPORT_MANIFEST_FILENAME,
            "report-bundle-" + "b" * 32 + "/" + REPORT_CHECKSUMS_FILENAME,
        ),
    ],
)
def test_malformed_protection_snapshot_deletes_nothing(tmp_path, protected):
    attempt, published = _published(tmp_path)
    with pytest.raises(CoreReportBundleError, match="protected"):
        cleanup_abandoned_report_bundles(attempt.session_dir, protected_rels=protected)
    assert published.root.is_dir()


def test_final_manifest_id_must_match_its_exact_directory(tmp_path):
    attempt, published = _published(tmp_path)
    mismatched = attempt.session_dir / ("report-bundle-" + "c" * 32)
    published.root.rename(mismatched)
    with pytest.raises(CoreReportBundleError, match="manifest"):
        cleanup_abandoned_report_bundles(attempt.session_dir, protected_rels=())
    assert mismatched.is_dir()


def test_discard_refuses_session_root_media_root_and_forged_nonchild_targets(tmp_path):
    session = _session(tmp_path)
    attempt = begin_report_bundle(session, attempt_id="6" * 32)
    forged = (
        replace(attempt, staging_dir=session),
        replace(attempt, staging_dir=attempt.media_dir),
        replace(attempt, staging_dir=tmp_path / attempt.staging_dir.name),
    )
    for target in forged:
        with pytest.raises(CoreReportBundleError):
            discard_report_bundle_attempt(target)
    assert session.is_dir() and attempt.media_dir.is_dir()


def test_nested_attempt_names_are_never_enumerated_or_removed(tmp_path):
    session = _session(tmp_path)
    unrelated = session / "unrelated"
    nested = unrelated / (".report-attempt-" + "7" * 32)
    nested.mkdir(parents=True)
    (nested / OWNER).write_bytes(
        b'{"attempt_id":"77777777777777777777777777777777","format":"report-bundle-attempt-v1"}\n'
    )
    assert cleanup_abandoned_report_bundles(session) == 0
    assert nested.is_dir()


def test_symlinked_attempt_root_is_refused_without_touching_target(tmp_path):
    session = _session(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    link = session / (".report-attempt-" + "8" * 32)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(CoreReportBundleError, match="reparse|link|ownership"):
        cleanup_abandoned_report_bundles(session)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_failed_cleanup_of_one_ambiguous_target_does_not_delete_later_targets(tmp_path):
    session = _session(tmp_path)
    bad = session / (".report-attempt-" + "0" * 32)
    bad.mkdir()
    (bad / OWNER).write_bytes(b"{}\n")
    good = begin_report_bundle(session, attempt_id="f" * 32)
    with pytest.raises(CoreReportBundleError):
        cleanup_abandoned_report_bundles(session)
    assert bad.is_dir()
    assert good.staging_dir.is_dir()
