from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from swinglab.focused_evidence import FocusedEvidenceRenderError
from swinglab.report_artifacts import (
    REPORT_CHECKSUMS_FILENAME,
    REPORT_MANIFEST_FILENAME,
    REPORT_VIEW_FILENAME,
    ReportArtifactValidationError,
)
from swinglab.report_bundle import (
    CoreReportBundleError,
    GuidedReportRendererUnavailable,
    ReportBundleAttempt,
    ReportHtmlWriter,
    begin_report_bundle,
    build_report_bundle,
    publish_report_bundle,
)
from swinglab.report_view import (
    CaptureOnlyReportView,
    CoachingReportView,
    MediaRole,
    ReasonCode,
    TrustState,
    UnavailableEvidence,
)
from tests.report_bundle_fixtures import (
    add_optional_media,
    guided_bundle_inputs,
    write_test_report_html,
)


OWNER = ".report-attempt-owner.json"


def _begin(tmp_path: Path, attempt_id: str = "a" * 32):
    session = tmp_path / "session"
    session.mkdir()
    return begin_report_bundle(session, attempt_id=attempt_id)


def _build(tmp_path: Path, *, attempt_id: str = "a" * 32, **overrides):
    attempt = _begin(tmp_path, attempt_id)
    inputs = guided_bundle_inputs(tmp_path)
    inputs.update(overrides)
    return attempt, build_report_bundle(attempt, **inputs)


def _file_names(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_writer_boundary_is_required_and_fixture_has_exact_escaped_header(tmp_path):
    signature = inspect.signature(build_report_bundle)
    assert signature.parameters["html_writer"].default is inspect.Parameter.empty
    assert "__call__" in ReportHtmlWriter.__dict__

    document = __import__("tests.report_view_fixtures", fromlist=["report_document_fixture"]).report_document_fixture()
    cfg = guided_bundle_inputs(tmp_path)["cfg"]
    cfg.brand["name"] = "Ace <Coach> & Co"
    out = write_test_report_html(tmp_path / "fixture.html", document, cfg=cfg)
    raw = out.read_bytes()
    header = raw[:8192]
    assert header.count(b'name="caddieinsight-report-format" content="caddie-brief-v1"') == 1
    assert header.count(b'name="caddieinsight-report-presentation" content="guided-report-v1"') == 1
    assert header.count(b'name="caddieinsight-report-outcome" content="coaching_ready"') == 1
    assert b"Ace &lt;Coach&gt; &amp; Co" in raw
    assert b"Ace <Coach> & Co" not in raw


def test_begin_creates_one_owned_same_directory_attempt(tmp_path):
    attempt = _begin(tmp_path, "0" * 32)
    assert attempt.staging_dir.name == ".report-attempt-" + "0" * 32
    assert attempt.staging_dir.parent.resolve() == attempt.session_dir.resolve()
    assert attempt.work_dir == attempt.staging_dir / "work"
    assert attempt.media_dir == attempt.staging_dir / "media"
    assert attempt.work_dir.is_dir() and attempt.media_dir.is_dir()
    assert (attempt.staging_dir / OWNER).read_bytes() == (
        b'{"attempt_id":"00000000000000000000000000000000","format":"report-bundle-attempt-v1"}\n'
    )


@pytest.mark.parametrize(
    ("angle", "reasons", "view_type", "trust"),
    [
        ("face-on", (), CoachingReportView, TrustState.CLEAR),
        ("dtl", (), CoachingReportView, TrustState.CLEAR),
        ("face-on", (ReasonCode.SECONDARY_METRIC_UNAVAILABLE,), CoachingReportView, TrustState.LIMITED),
        ("face-on", (ReasonCode.NO_READABLE_SWING,), CaptureOnlyReportView, TrustState.REFILM_REQUIRED),
    ],
)
def test_builds_clear_limited_and_capture_only_complete_bundles(
    tmp_path, angle, reasons, view_type, trust
):
    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path, angle=angle, reason_codes=reasons)
    staged = build_report_bundle(attempt, **inputs)
    assert isinstance(staged.view, view_type)
    assert staged.view.trust.state is trust
    expected = {
        "report.html",
        REPORT_VIEW_FILENAME,
        "metrics.json",
        REPORT_MANIFEST_FILENAME,
        REPORT_CHECKSUMS_FILENAME,
    }
    if isinstance(staged.view, CoachingReportView):
        expected.add("media/priority-evidence.png")
        expected.add("media/drill-illustration.svg")
    assert _file_names(attempt.staging_dir) == expected
    assert OWNER not in _file_names(attempt.staging_dir)
    assert staged.view == staged.document.view
    assert staged.manifest.attempt_id == attempt.attempt_id
    assert staged.manifest.outcome == staged.view.outcome
    assert staged.checksums.manifest_sha256
    assert b"Fixture &lt;note&gt; &amp; safe" not in staged.report_path.read_bytes()
    assert b'name="caddieinsight-report-presentation" content="guided-report-v1"' in staged.report_path.read_bytes()[:8192]
    if angle == "dtl":
        assert all(media.role is not MediaRole.KEY_POSITIONS for media in staged.view.media)
    assert all(media.role is not MediaRole.KEY_POSITIONS for media in staged.view.media)


def test_focused_renderer_failure_degrades_to_limited_unavailable(tmp_path, monkeypatch):
    from swinglab import report_bundle

    def fail(*args, **kwargs):
        raise FocusedEvidenceRenderError("fixture Pillow failure")

    monkeypatch.setattr(report_bundle, "render_focused_evidence", fail)
    attempt = _begin(tmp_path)
    staged = build_report_bundle(attempt, **guided_bundle_inputs(tmp_path))
    assert isinstance(staged.view, CoachingReportView)
    assert staged.view.trust.state is TrustState.LIMITED
    assert isinstance(staged.view.visual_evidence, UnavailableEvidence)
    assert staged.view.visual_evidence.render_reasons == (ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,)
    assert not staged.view.capabilities.focused_evidence
    assert all(media.role is not MediaRole.PRIORITY_EVIDENCE for media in staged.view.media)


def test_focused_renderer_partial_output_is_safely_removed_before_fallback(tmp_path, monkeypatch):
    from swinglab import report_bundle

    def fail(*args, out_path, **kwargs):
        out_path.write_bytes(b"partial-png")
        raise FocusedEvidenceRenderError("fixture Pillow failure after write")

    monkeypatch.setattr(report_bundle, "render_focused_evidence", fail)
    attempt = _begin(tmp_path)
    staged = build_report_bundle(attempt, **guided_bundle_inputs(tmp_path))
    assert isinstance(staged.view, CoachingReportView)
    assert staged.view.trust.state is TrustState.LIMITED
    assert isinstance(staged.view.visual_evidence, UnavailableEvidence)
    assert not (attempt.media_dir / "priority-evidence.png").exists()
    assert "media/priority-evidence.png" not in _file_names(attempt.staging_dir)


def test_missing_annotation_trust_becomes_capture_only_not_renderer_unavailable(tmp_path):
    inputs = guided_bundle_inputs(tmp_path)
    snapshot = inputs["evidence_snapshots"][0]
    from dataclasses import replace
    from types import MappingProxyType

    gates = {key: replace(value, readable=False) for key, value in snapshot.annotation_gates.items()}
    inputs["evidence_snapshots"] = (replace(snapshot, annotation_gates=MappingProxyType(gates)),)
    attempt = _begin(tmp_path)
    staged = build_report_bundle(attempt, **inputs)
    assert isinstance(staged.view, CaptureOnlyReportView)
    assert staged.view.visual_evidence is None
    assert ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE in staged.view.trust.reasons


def test_selected_poor_tracking_snapshot_becomes_fatal_capture_only(tmp_path):
    from dataclasses import replace
    from swinglab import pose

    inputs = guided_bundle_inputs(tmp_path)
    snapshot = inputs["evidence_snapshots"][0]
    inputs["evidence_snapshots"] = (
        replace(snapshot, tracking_quality=pose.TrackingQuality(.8, .2, True)),
    )
    attempt = _begin(tmp_path)
    staged = build_report_bundle(attempt, **inputs)
    assert isinstance(staged.view, CaptureOnlyReportView)
    assert staged.view.visual_evidence is None
    assert ReasonCode.TRACKING_UNSTABLE in staged.view.trust.reasons
    assert all(media.role is not MediaRole.PRIORITY_EVIDENCE for media in staged.view.media)


def test_selector_fatal_reason_becomes_capture_only_before_rendering(tmp_path, monkeypatch):
    from dataclasses import replace
    from swinglab import report_bundle

    inputs = guided_bundle_inputs(tmp_path)
    snapshot = inputs["evidence_snapshots"][0]
    inputs["evidence_snapshots"] = (
        replace(snapshot, metrics=replace(snapshot.metrics, tempo_ratio=float("nan"))),
    )
    monkeypatch.setattr(
        report_bundle,
        "render_focused_evidence",
        lambda *args, **kwargs: pytest.fail("fatal selection reached renderer"),
    )
    attempt = _begin(tmp_path)
    staged = build_report_bundle(attempt, **inputs)
    assert isinstance(staged.view, CaptureOnlyReportView)
    assert ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE in staged.view.trust.reasons


def test_optional_media_is_declared_only_when_actual_inputs_exist(tmp_path):
    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path)
    add_optional_media(attempt, inputs, strip=True, slowmo=True, replay=True)
    staged = build_report_bundle(attempt, **inputs)
    roles = [media.role for media in staged.view.media]
    assert roles == [
        MediaRole.KEY_POSITIONS,
        MediaRole.SLOW_MOTION,
        MediaRole.COACH_REPLAY,
        MediaRole.PRIORITY_EVIDENCE,
        MediaRole.DRILL_ILLUSTRATION,
    ]
    assert staged.view.practice.illustration_media_key == "drill-illustration"
    assert staged.view.capabilities.slow_motion is True
    assert staged.view.capabilities.coach_replay is True
    payload = json.loads((attempt.staging_dir / "metrics.json").read_text(encoding="utf-8"))
    assert tuple(payload["swings"][0]["deliverables"]) == ("strip", "slowmo", "replay")
    assert "overlay" not in payload["swings"][0]["deliverables"]


def test_dtl_strip_is_rejected_before_media_presentation(tmp_path, monkeypatch):
    from swinglab import report_bundle

    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path, angle="dtl")
    add_optional_media(attempt, inputs, strip=True)
    monkeypatch.setattr(
        report_bundle,
        "_coaching_media",
        lambda *args, **kwargs: pytest.fail("DTL strip reached MediaEntry construction"),
    )
    with pytest.raises(CoreReportBundleError, match="DTL|down-the-line|strip"):
        build_report_bundle(attempt, **inputs)
    assert not attempt.staging_dir.exists()


def test_dtl_preserves_timing_focus_with_optional_raw_slowmo_only(tmp_path):
    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path, angle="dtl")
    add_optional_media(attempt, inputs, slowmo=True)
    staged = build_report_bundle(attempt, **inputs)
    assert tuple(media.role for media in staged.view.media) == (
        MediaRole.SLOW_MOTION,
        MediaRole.PRIORITY_EVIDENCE,
        MediaRole.DRILL_ILLUSTRATION,
    )
    assert staged.view.capabilities.slow_motion is True
    assert all(media.role is not MediaRole.KEY_POSITIONS for media in staged.view.media)


def test_fatal_capture_prunes_coaching_media_and_reroles_only_owned_raw_slowmo(tmp_path):
    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(
        tmp_path, reason_codes=(ReasonCode.TRACKING_UNSTABLE,)
    )
    add_optional_media(attempt, inputs, strip=True, slowmo=True, replay=True)
    strip = Path(inputs["swings"][0]["strip"])
    slowmo = Path(inputs["swings"][0]["slowmo"])
    replay = Path(inputs["swings"][0]["replay"])
    staged = build_report_bundle(attempt, **inputs)
    assert isinstance(staged.view, CaptureOnlyReportView)
    assert tuple(media.role for media in staged.view.media) == (MediaRole.CAPTURE_PLAYBACK,)
    assert staged.view.capture_guidance.safe_media_keys == ("capture-playback-s1",)
    assert not strip.exists() and slowmo.exists() and not replay.exists()
    assert staged.view.capabilities.focused_evidence is False
    assert staged.view.capabilities.every_swing is False
    assert staged.view.capabilities.slow_motion is False
    assert staged.view.capabilities.coach_replay is False
    payload = json.loads((attempt.staging_dir / "metrics.json").read_text(encoding="utf-8"))
    assert payload["swings"][0]["deliverables"] == {
        "slowmo": "media/slow-1.mp4"
    }
    assert "media/positions-1.png" not in _file_names(attempt.staging_dir)
    assert "media/replay-1.mp4" not in _file_names(attempt.staging_dir)


def test_capture_prune_refuses_nested_ancestor_swap_before_outside_deletion(tmp_path, monkeypatch):
    from swinglab import report_bundle

    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path, reason_codes=(ReasonCode.TRACKING_UNSTABLE,))
    nested = attempt.media_dir / "nested"
    nested.mkdir()
    strip = nested / "positions.png"
    strip.write_bytes(b"positions")
    sentinel = nested / "outside-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    inputs["swings"][0]["strip"] = strip
    moved = tmp_path / "moved-capture-media"
    attempted = False

    def swap(plans):
        nonlocal attempted
        if attempted:
            raise CoreReportBundleError("stop ambiguous follow-up cleanup")
        attempted = True
        try:
            nested.rename(moved)
        except OSError as exc:
            raise CoreReportBundleError("pinned capture ancestor refused replacement") from exc
        try:
            nested.symlink_to(moved, target_is_directory=True)
        except OSError as exc:
            raise CoreReportBundleError("capture replacement could not be installed") from exc

    monkeypatch.setattr(report_bundle, "_after_owned_tree_plans", swap, raising=False)
    with pytest.raises(CoreReportBundleError):
        build_report_bundle(attempt, **inputs)
    surviving = moved / sentinel.name if moved.exists() else sentinel
    assert surviving.read_text(encoding="utf-8") == "keep"


def test_guided_media_inputs_must_be_owned_paths_not_arbitrary_strings(tmp_path):
    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path)
    inputs["swings"][0]["strip"] = "media/not-an-owned-path.png"
    with pytest.raises(CoreReportBundleError, match="Path|owned"):
        build_report_bundle(attempt, **inputs)
    assert not attempt.staging_dir.exists()


def test_out_of_attempt_media_is_refused_and_never_deleted(tmp_path):
    attempt = _begin(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"keep")
    inputs = guided_bundle_inputs(tmp_path, reason_codes=(ReasonCode.TRACKING_UNSTABLE,))
    inputs["swings"][0]["strip"] = outside
    with pytest.raises(CoreReportBundleError, match="owned|attempt"):
        build_report_bundle(attempt, **inputs)
    assert outside.read_bytes() == b"keep"


def test_symlinked_owned_media_is_refused_without_deleting_target(tmp_path):
    attempt = _begin(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"keep")
    link = attempt.media_dir / "linked.png"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    inputs = guided_bundle_inputs(tmp_path, reason_codes=(ReasonCode.TRACKING_UNSTABLE,))
    inputs["swings"][0]["strip"] = link
    with pytest.raises(CoreReportBundleError, match="link|owned|reparse"):
        build_report_bundle(attempt, **inputs)
    assert outside.read_bytes() == b"keep"


def test_absent_optional_media_stays_absent_and_invokes_no_optional_renderer(tmp_path, monkeypatch):
    from swinglab import report_bundle

    for name in ("render_strip", "render_slow_motion", "render_replay"):
        monkeypatch.setattr(
            report_bundle,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(f"{_name} was invoked"),
            raising=False,
        )
    attempt = _begin(tmp_path)
    staged = build_report_bundle(attempt, **guided_bundle_inputs(tmp_path))
    assert not staged.view.capabilities.slow_motion
    assert not staged.view.capabilities.coach_replay
    assert all(media.role not in {MediaRole.KEY_POSITIONS, MediaRole.SLOW_MOTION, MediaRole.COACH_REPLAY} for media in staged.view.media)
    payload = json.loads((attempt.staging_dir / "metrics.json").read_text(encoding="utf-8"))
    assert payload["swings"][0]["deliverables"] == {}


def test_guided_overlay_is_rejected_and_attempt_is_scoped_away(tmp_path):
    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path)
    overlay = attempt.media_dir / "legacy-overlay.png"
    overlay.write_bytes(b"legacy")
    inputs["swings"][0]["overlay"] = overlay
    with pytest.raises(CoreReportBundleError, match="overlay"):
        build_report_bundle(attempt, **inputs)
    assert not attempt.staging_dir.exists()


def test_prepare_report_input_is_called_once(tmp_path, monkeypatch):
    from swinglab import report_bundle

    calls = []
    real = report_bundle.prepare_report_input

    def recording(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(report_bundle, "prepare_report_input", recording)
    attempt = _begin(tmp_path)
    build_report_bundle(attempt, **guided_bundle_inputs(tmp_path))
    assert len(calls) == 1


def test_staged_result_retains_exact_parsed_validation_objects(tmp_path, monkeypatch):
    from swinglab import report_bundle

    returned = []
    real = report_bundle.validate_staged_bundle

    def recording(*args, **kwargs):
        result = real(*args, **kwargs)
        returned.append(result)
        return result

    monkeypatch.setattr(report_bundle, "validate_staged_bundle", recording)
    attempt = _begin(tmp_path)
    staged = build_report_bundle(attempt, **guided_bundle_inputs(tmp_path))
    manifest, checksums, view = returned[-1]
    assert staged.manifest is manifest
    assert staged.checksums is checksums
    assert staged.view is view


def test_missing_writer_fails_closed_without_a_default_renderer(tmp_path):
    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path)
    inputs["html_writer"] = None
    with pytest.raises(GuidedReportRendererUnavailable):
        build_report_bundle(attempt, **inputs)
    assert not attempt.staging_dir.exists()


@pytest.mark.parametrize("failure", ["view", "html", "manifest", "checksums", "focused", "validation"])
def test_core_build_failures_leave_no_final_or_completed_paths(tmp_path, monkeypatch, failure):
    from swinglab import report_bundle

    attempt = _begin(tmp_path)
    inputs = guided_bundle_inputs(tmp_path)
    if failure == "html":
        inputs["html_writer"] = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("html failed"))
    elif failure == "focused":
        real = report_bundle.render_focused_evidence

        def missing(*args, **kwargs):
            artifact = real(*args, **kwargs)
            artifact.path.unlink()
            return artifact

        monkeypatch.setattr(report_bundle, "render_focused_evidence", missing)
    else:
        target = {
            "view": "write_report_view",
            "manifest": "write_report_manifest",
            "checksums": "write_report_checksums",
            "validation": "validate_staged_bundle",
        }[failure]
        monkeypatch.setattr(
            report_bundle,
            target,
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError(f"{failure} failed")),
        )
    with pytest.raises(CoreReportBundleError):
        build_report_bundle(attempt, **inputs)
    assert not attempt.staging_dir.exists()
    assert not list(attempt.session_dir.glob("report-bundle-*"))


def test_publish_revalidates_then_performs_one_sibling_noclobber_rename(tmp_path, monkeypatch):
    from swinglab import report_bundle

    attempt, staged = _build(tmp_path)
    events = []
    real_validate = report_bundle.validate_staged_bundle
    real_rename = report_bundle._rename_report_bundle_noreplace

    def validate(*args, **kwargs):
        events.append("validate")
        return real_validate(*args, **kwargs)

    def rename(source, destination):
        events.append(("rename", source, destination))
        return real_rename(source, destination)

    monkeypatch.setattr(report_bundle, "validate_staged_bundle", validate)
    monkeypatch.setattr(report_bundle, "_rename_report_bundle_noreplace", rename)
    published = publish_report_bundle(staged)
    assert events[0] == "validate"
    assert len([event for event in events if isinstance(event, tuple) and event[0] == "rename"]) == 1
    assert published.root.parent.resolve() == attempt.session_dir.resolve()
    assert published.root.name == "report-bundle-" + attempt.attempt_id
    assert os.stat(published.root).st_dev == os.stat(published.root.parent).st_dev
    assert not attempt.staging_dir.exists()
    assert published.report_path == published.root / "report.html"
    assert published.report_view_path == published.root / REPORT_VIEW_FILENAME
    assert published.manifest_path == published.root / REPORT_MANIFEST_FILENAME
    assert published.checksums_path == published.root / REPORT_CHECKSUMS_FILENAME


def test_publish_rejects_coherent_destination_substitution_after_atomic_rename(
    tmp_path, monkeypatch
):
    from swinglab import report_bundle

    original_fixture = tmp_path / "original"
    replacement_fixture = tmp_path / "replacement"
    original_fixture.mkdir()
    replacement_fixture.mkdir()
    attempt, staged = _build(original_fixture)
    replacement_attempt, _replacement = _build(
        replacement_fixture,
        swings=[],
    )
    real_rename = report_bundle._rename_report_bundle_noreplace

    def rename_then_substitute(source, destination):
        real_rename(source, destination)
        displaced = destination.with_name("displaced-original")
        destination.rename(displaced)
        replacement_attempt.staging_dir.rename(destination)

    monkeypatch.setattr(
        report_bundle,
        "_rename_report_bundle_noreplace",
        rename_then_substitute,
    )

    with pytest.raises(CoreReportBundleError, match="changed|substitut"):
        publish_report_bundle(staged)


def test_publish_race_preserves_both_roots_without_clobber(tmp_path):
    attempt, staged = _build(tmp_path)
    destination = attempt.session_dir / ("report-bundle-" + attempt.attempt_id)
    destination.mkdir()
    sentinel = destination / "attacker.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(CoreReportBundleError):
        publish_report_bundle(staged)
    assert attempt.staging_dir.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_unsupported_exclusive_rename_fails_closed_without_overwrite_fallback(tmp_path, monkeypatch):
    from swinglab import report_bundle

    attempt, staged = _build(tmp_path)
    calls = []
    monkeypatch.setattr(report_bundle, "_rename_report_bundle_noreplace", lambda *args: (_ for _ in ()).throw(CoreReportBundleError("unsupported")))
    monkeypatch.setattr(report_bundle.os, "replace", lambda *args: calls.append(args))
    with pytest.raises(CoreReportBundleError, match="unsupported"):
        publish_report_bundle(staged)
    assert calls == []
    assert attempt.staging_dir.is_dir()
    assert not (attempt.session_dir / ("report-bundle-" + attempt.attempt_id)).exists()


def test_failed_published_readback_leaves_final_for_scoped_recovery(tmp_path, monkeypatch):
    from swinglab import report_bundle

    attempt, staged = _build(tmp_path)
    monkeypatch.setattr(
        report_bundle,
        "load_published_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(ReportArtifactValidationError("readback failed")),
    )
    with pytest.raises(CoreReportBundleError, match="readback"):
        publish_report_bundle(staged)
    final = attempt.session_dir / ("report-bundle-" + attempt.attempt_id)
    assert final.is_dir()
    assert not attempt.staging_dir.exists()
