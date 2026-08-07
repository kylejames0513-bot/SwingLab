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


def test_discard_refuses_nested_ancestor_swap_before_outside_deletion(tmp_path, monkeypatch):
    from swinglab import report_bundle

    session = _session(tmp_path)
    attempt = begin_report_bundle(session, attempt_id="9" * 32)
    nested = attempt.work_dir / "nested"
    nested.mkdir()
    sentinel = nested / "outside-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    moved = tmp_path / "moved-owned-tree"

    def swap(plans):
        try:
            nested.rename(moved)
        except OSError as exc:
            raise CoreReportBundleError("pinned owned ancestor refused replacement") from exc
        try:
            nested.symlink_to(moved, target_is_directory=True)
        except OSError as exc:
            raise CoreReportBundleError("owned replacement could not be installed") from exc

    monkeypatch.setattr(report_bundle, "_after_owned_tree_plans", swap, raising=False)
    with pytest.raises(CoreReportBundleError):
        discard_report_bundle_attempt(attempt)
    surviving = moved / sentinel.name if moved.exists() else sentinel
    assert surviving.read_text(encoding="utf-8") == "keep"


def test_recovery_refuses_nested_ancestor_swap_before_any_candidate_deletion(tmp_path, monkeypatch):
    from swinglab import report_bundle

    session = _session(tmp_path)
    first = begin_report_bundle(session, attempt_id="0" * 32)
    second = begin_report_bundle(session, attempt_id="f" * 32)
    nested = first.work_dir / "nested"
    nested.mkdir()
    sentinel = nested / "outside-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    moved = tmp_path / "moved-recovery-tree"

    def swap(plans):
        try:
            nested.rename(moved)
        except OSError as exc:
            raise CoreReportBundleError("pinned recovery ancestor refused replacement") from exc
        try:
            nested.symlink_to(moved, target_is_directory=True)
        except OSError as exc:
            raise CoreReportBundleError("recovery replacement could not be installed") from exc

    monkeypatch.setattr(report_bundle, "_after_owned_tree_plans", swap)
    with pytest.raises(CoreReportBundleError):
        cleanup_abandoned_report_bundles(session)
    surviving = moved / sentinel.name if moved.exists() else sentinel
    assert surviving.read_text(encoding="utf-8") == "keep"
    assert second.staging_dir.is_dir()


def test_recovery_refuses_nested_relocation_after_validation_before_first_delete(
    tmp_path, monkeypatch
):
    from swinglab import report_bundle

    session = _session(tmp_path)
    attempt = begin_report_bundle(session, attempt_id="e" * 32)
    nested = attempt.work_dir / "nested"
    nested.mkdir()
    (nested / "owned.txt").write_text("owned", encoding="utf-8")
    moved = tmp_path / "moved-after-validation"
    outside = tmp_path / "outside-replacement"
    outside.mkdir()
    sentinel = outside / "outside-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    def relocate(plans):
        try:
            nested.rename(moved)
        except OSError as exc:
            raise CoreReportBundleError("pinned platform refused post-validation relocation") from exc
        try:
            nested.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            raise CoreReportBundleError("post-validation replacement could not be installed") from exc

    monkeypatch.setattr(
        report_bundle,
        "_after_owned_tree_validation",
        relocate,
        raising=False,
    )
    with pytest.raises(CoreReportBundleError):
        cleanup_abandoned_report_bundles(session)
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("O_DIRECTORY", "O_DIRECTORY"),
        ("O_NOFOLLOW", "O_NOFOLLOW"),
        ("dir_fd", "dir_fd"),
    ],
)
def test_posix_delete_capability_check_fails_closed_when_required_support_is_missing(
    monkeypatch, missing, message
):
    from swinglab import report_bundle

    monkeypatch.setattr(report_bundle.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(report_bundle.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(
        report_bundle.os,
        "supports_dir_fd",
        {
            report_bundle.os.open,
            report_bundle.os.stat,
            report_bundle.os.unlink,
            report_bundle.os.rmdir,
        },
    )
    monkeypatch.setattr(
        report_bundle.os,
        "supports_follow_symlinks",
        {report_bundle.os.stat},
    )
    if missing == "dir_fd":
        monkeypatch.setattr(report_bundle.os, "supports_dir_fd", set())
    else:
        monkeypatch.delattr(report_bundle.os, missing, raising=False)
    with pytest.raises(CoreReportBundleError, match=message):
        report_bundle._require_posix_delete_capabilities()


def test_posix_owned_tree_validation_uses_anchor_pins_not_windows_parent_pins(
    tmp_path, monkeypatch
):
    from swinglab import report_bundle

    root = tmp_path / "owned"
    plan = report_bundle._PinnedOwnedTree(root, session_anchor=tmp_path)
    plan.entries.append(
        report_bundle._OwnedEntry(root, root.name, 0, 0, 1, 2)
    )
    plan._posix_anchor_handle = 0
    plan._posix_anchor_identity = (1, 2)
    validated = []

    monkeypatch.setattr(plan, "_validate_posix", lambda: validated.append("posix"))
    monkeypatch.setattr(
        report_bundle,
        "os",
        type("PosixOS", (), {"name": "posix"})(),
    )

    plan.validate()

    assert validated == ["posix"]


@pytest.mark.parametrize(
    ("platform", "symbol"),
    [("linux", "renameat2"), ("darwin", "renameatx_np")],
)
def test_posix_quarantine_rename_fails_closed_without_exclusive_libc_symbol(
    monkeypatch, platform, symbol
):
    from swinglab import report_bundle

    monkeypatch.setattr(report_bundle.sys, "platform", platform)
    monkeypatch.setattr(report_bundle.ctypes, "CDLL", lambda *args, **kwargs: object())
    with pytest.raises(CoreReportBundleError, match=symbol):
        report_bundle._posix_rename_to_quarantine_noreplace(11, "owned", 22, "quarantine")


@pytest.mark.parametrize(
    ("platform", "symbol", "flag"),
    [("linux", "renameat2", 1), ("darwin", "renameatx_np", 0x00000004)],
)
def test_posix_quarantine_rename_is_descriptor_relative_and_exclusive(
    monkeypatch, platform, symbol, flag
):
    from swinglab import report_bundle

    calls = []

    class Rename:
        argtypes = None
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return 0

    library = type("Library", (), {symbol: Rename()})()
    monkeypatch.setattr(report_bundle.sys, "platform", platform)
    monkeypatch.setattr(report_bundle.ctypes, "CDLL", lambda *args, **kwargs: library)
    report_bundle._posix_rename_to_quarantine_noreplace(11, "owned", 22, "quarantine")
    assert calls == [(11, b"owned", 22, b"quarantine", flag)]


def test_recovery_direct_entry_limit_counts_unrelated_children_before_planning(tmp_path, monkeypatch):
    from swinglab import report_bundle

    session = _session(tmp_path)
    attempt = begin_report_bundle(session, attempt_id="a" * 32)
    (session / "unrelated-one").mkdir()
    (session / "unrelated-two").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(report_bundle, "_MAX_RECOVERY_DIRECT_ENTRIES", 2, raising=False)

    with pytest.raises(CoreReportBundleError, match="direct|bound"):
        cleanup_abandoned_report_bundles(session)
    assert attempt.staging_dir.is_dir()
    assert (session / "unrelated-two").read_text(encoding="utf-8") == "keep"


def test_recovery_cumulative_plan_budget_fails_before_any_candidate_deletion(tmp_path, monkeypatch):
    from swinglab import report_bundle

    session = _session(tmp_path)
    first = begin_report_bundle(session, attempt_id="c" * 32)
    second = begin_report_bundle(session, attempt_id="d" * 32)
    monkeypatch.setattr(report_bundle, "_MAX_RECOVERY_PLANNED_ENTRIES", 7, raising=False)

    with pytest.raises(CoreReportBundleError, match="cumulative|budget|bound"):
        cleanup_abandoned_report_bundles(session)
    assert first.staging_dir.is_dir()
    assert second.staging_dir.is_dir()
