"""Local-only guided report release-gate harness.

The production pipeline, bundle builder, publisher, loaders, and media resolver
remain real.  Only codec/probe/frame extraction and synthetic media emission
are replaced so this gate is deterministic on hosts without FFmpeg.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Iterator
from unittest.mock import patch

from PIL import Image

from swinglab import pipeline, pose, report_bundle
from swinglab.config import Config
from swinglab.ffmpeg import VideoInfo
from swinglab.focused_evidence import FocusedEvidenceRenderError
from swinglab.frames import FrameSet
from swinglab.pipeline import SessionResult, analyze_video
from swinglab.report_artifacts import (
    PublishedReportBundle,
    ReportEntitlementSnapshot,
    load_published_bundle,
    resolve_media_path,
)
from swinglab.report_view import (
    EvidenceKind,
    GUIDED_REPORT_PRESENTATION_VERSION,
    MediaRole,
    PhaseId,
    ReasonCode,
)
from swinglab.web import jobs as jobs_module
from swinglab.web.jobs import Job, JobManager
from tests.report_bundle_fixtures import write_test_report_html
from tests.test_pipeline_e2e import FakeTracker


def _fast_cfg() -> Config:
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    cfg.slowmo["annotated"] = False
    return cfg


@contextmanager
def _deterministic_media_io() -> Iterator[None]:
    def probe_fixture(path: str | Path) -> VideoInfo:
        return VideoInfo(Path(path), 4.0, 1000, 1000, 30.0, 0, None, False)

    def extract_window(
        video: Path,
        strike: float,
        work: Path,
        swing: int,
        cfg: Config,
        fps: float | None = None,
    ) -> FrameSet:
        del video, strike, cfg
        paths: list[Path] = []
        for index in range(75):
            path = Path(work) / f"s{swing}_{index + 1:03d}.png"
            with Image.new("RGB", (20, 20), "white") as frame:
                frame.save(path)
            paths.append(path)
        return FrameSet(paths, 0.0, float(fps or 30.0))

    def extract_fullres(
        video: Path,
        timestamp: float,
        out: Path,
        cfg: Config,
    ) -> Path:
        del video, timestamp, cfg
        path = Path(out)
        with Image.new("RGB", (1000, 1000), "white") as frame:
            frame.save(path)
        return path

    def make_slowmo(
        video: Path,
        strike: float,
        out: Path,
        cfg: Config,
        fast: bool = False,
    ) -> Path:
        del video, strike, cfg, fast
        path = Path(out)
        path.write_bytes(b"deterministic slow motion fixture\n")
        return path

    def strip_unavailable(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("deterministic key-position renderer unavailable")

    def unexpected_media_call(*args, **kwargs):
        del args, kwargs
        raise AssertionError("guided gate called a forbidden legacy media renderer")

    with ExitStack() as stack:
        stack.enter_context(patch.object(pose, "PoseTracker", FakeTracker))
        stack.enter_context(patch.object(pipeline.pose, "PoseTracker", FakeTracker))
        stack.enter_context(patch.object(pipeline, "require_binaries", lambda: None))
        stack.enter_context(patch.object(pipeline, "probe", probe_fixture))
        stack.enter_context(
            patch.object(pipeline.audio, "extract_audio", unexpected_media_call)
        )
        stack.enter_context(
            patch.object(pipeline.audio, "detect_strikes", unexpected_media_call)
        )
        stack.enter_context(
            patch.object(pipeline.frames, "extract_window", extract_window)
        )
        stack.enter_context(
            patch.object(pipeline.frames, "extract_fullres_frame", extract_fullres)
        )
        stack.enter_context(patch.object(pipeline.slowmo, "make_slowmo", make_slowmo))
        stack.enter_context(patch.object(pipeline.strip, "make_strip", strip_unavailable))
        stack.enter_context(
            patch.object(pipeline.overlay, "make_overlay", unexpected_media_call)
        )
        yield


def run_guided_synthetic(out: Path, *, angle: str) -> SessionResult:
    if angle not in ("face-on", "dtl"):
        raise ValueError("gate angle must be face-on or dtl")
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    video = root / "gate-media.bin"
    video.write_bytes(b"deterministic media fixture\n")
    with _deterministic_media_io():
        return analyze_video(
            video,
            out_dir=root / "sessions",
            cfg=_fast_cfg(),
            manual_strikes=[2.0],
            fast=True,
            angle=angle,
            log=lambda message: None,
            report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
            report_entitlements=ReportEntitlementSnapshot("disabled"),
            guided_html_writer=write_test_report_html,
        )


def assert_bundle(result: SessionResult) -> PublishedReportBundle:
    if not result.structured_report:
        raise AssertionError("guided result is not structured")
    if result.report_view_path is None:
        raise AssertionError("guided result has no report view")
    if result.manifest_path is None:
        raise AssertionError("guided result has no manifest")
    if result.checksums_path is None:
        raise AssertionError("guided result has no checksums")

    rels = {
        "report_rel": result.report_path.relative_to(result.session_dir).as_posix(),
        "report_view_rel": result.report_view_path.relative_to(
            result.session_dir
        ).as_posix(),
        "manifest_rel": result.manifest_path.relative_to(
            result.session_dir
        ).as_posix(),
        "checksums_rel": result.checksums_path.relative_to(
            result.session_dir
        ).as_posix(),
    }
    bundle = load_published_bundle(result.session_dir, **rels)
    _assert_loaded_bundle(bundle)
    return bundle


def _assert_loaded_bundle(bundle: PublishedReportBundle) -> None:
    for row in bundle.checksums.files:
        path = bundle.root.joinpath(*PurePosixPath(row.relative_path).parts)
        payload = path.read_bytes()
        if len(payload) != row.size_bytes:
            raise AssertionError(f"checksum size mismatch: {row.relative_path}")
        if sha256(payload).hexdigest() != row.sha256:
            raise AssertionError(f"checksum digest mismatch: {row.relative_path}")

    for media in bundle.view.media:
        resolved = resolve_media_path(bundle, media.key)
        if not resolved.is_file():
            raise AssertionError(f"media key did not resolve: {media.key}")

    expected_files = {row.relative_path for row in bundle.checksums.files}
    expected_files.add(bundle.checksums_path.relative_to(bundle.root).as_posix())
    actual_files = {
        path.relative_to(bundle.root).as_posix()
        for path in bundle.root.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise AssertionError("published bundle topology differs from checksums graph")


def assert_job_bundle(job: Job) -> PublishedReportBundle:
    if not job.structured_report:
        raise AssertionError("guided job is not structured")
    rels = (
        job.report_rel,
        job.report_view_rel,
        job.report_manifest_rel,
        job.report_checksums_rel,
    )
    if not all(isinstance(relative, str) and relative for relative in rels):
        raise AssertionError("guided job is missing canonical report rels")
    bundle = load_published_bundle(
        job.session_dir,
        report_rel=job.report_rel,
        report_view_rel=job.report_view_rel,
        manifest_rel=job.report_manifest_rel,
        checksums_rel=job.report_checksums_rel,
    )
    _assert_loaded_bundle(bundle)
    return bundle


def run_guided_job(
    out: Path,
    *,
    angle: str,
    renderer_failure: bool = False,
    core_writer_failure: bool = False,
) -> tuple[Job, JobManager]:
    if renderer_failure == core_writer_failure:
        raise ValueError("select exactly one guided job failure scenario")
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)

    def fail_core_writer(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("deterministic core writer failure")

    manager = JobManager(
        root / "sessions",
        _fast_cfg(),
        guided_html_writer=(
            fail_core_writer if core_writer_failure else write_test_report_html
        ),
    )
    job = manager.create_session(
        user_id="guided-gate-user",
        angle=angle,
        strikes=[2.0],
        fast=True,
        report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
    )
    video = job.session_dir / "gate-media.bin"
    video.write_bytes(b"deterministic media fixture\n")

    def fail_focused_renderer(*args, **kwargs):
        del args
        out_path = Path(kwargs["out_path"])
        out_path.write_bytes(b"deterministic partial focused fixture\n")
        raise FocusedEvidenceRenderError("deterministic focused renderer failure")

    with _deterministic_media_io(), ExitStack() as stack:
        if renderer_failure:
            stack.enter_context(
                patch.object(
                    report_bundle,
                    "render_focused_evidence",
                    fail_focused_renderer,
                )
            )
        if core_writer_failure:
            stack.enter_context(
                patch.object(jobs_module.logger, "exception", lambda *a, **k: None)
            )
        manager._run(job, video)
    stored = manager.get(job.id)
    if stored is None:
        close_job_manager(manager)
        raise AssertionError("guided job row disappeared")
    return stored, manager


def close_job_manager(manager: JobManager) -> None:
    manager._pool.shutdown(wait=True)
    manager._conn.close()


_SCENARIOS = ("face-on", "dtl", "renderer-only", "core-writer")
_BODY_METRICS = (
    "head_sway_backswing_sw",
    "hip_slide_backswing_sw",
    "head_dip_sw",
    "lead_arm_angle_deg",
    "shoulder_tilt_impact_deg",
    "finish_balance_sw",
)


def _canonical_rels(bundle: PublishedReportBundle) -> dict[str, str]:
    parent = bundle.root.parent
    return {
        "report": bundle.report_path.relative_to(parent).as_posix(),
        "view": bundle.report_view_path.relative_to(parent).as_posix(),
        "manifest": bundle.manifest_path.relative_to(parent).as_posix(),
        "checksums": bundle.checksums_path.relative_to(parent).as_posix(),
    }


def _bundle_summary(
    scenario: str,
    bundle: PublishedReportBundle,
) -> dict[str, object]:
    declared = {row.relative_path for row in bundle.manifest.artifacts}
    lowered = "\n".join(sorted(declared)).lower()
    for forbidden in (
        "work/",
        "raw_landmark",
        "raw-pose",
        "raw_pose",
        "overlay",
        "strip",
        "replay",
    ):
        if forbidden in lowered:
            raise AssertionError(f"forbidden declared artifact category: {forbidden}")
    if any(path.name == "work" for path in bundle.root.parent.rglob("*")):
        raise AssertionError("published analysis retained a work directory")

    priority_count = sum(
        media.role is MediaRole.PRIORITY_EVIDENCE for media in bundle.view.media
    )
    return {
        "scenario": scenario,
        "outcome": bundle.view.outcome.value,
        "presentation_version": bundle.view.presentation_version,
        "view_version": bundle.view.version,
        "canonical_rels": _canonical_rels(bundle),
        "media": [
            {"key": media.key, "role": media.role.value}
            for media in bundle.view.media
        ],
        "checksum_verification": {
            "rows": len(bundle.checksums.files),
            "verified": len(bundle.checksums.files),
            "media_resolved": len(bundle.view.media),
        },
        "reason_codes": [reason.value for reason in bundle.view.trust.reasons],
        "priority_evidence_count": priority_count,
        "topology_verified": True,
    }


def _run_face_on(root: Path) -> dict[str, object]:
    result = run_guided_synthetic(root / "face-on", angle="face-on")
    bundle = assert_bundle(result)
    summary = _bundle_summary("face-on", bundle)
    if summary["priority_evidence_count"] != 1:
        raise AssertionError("face-on gate requires one priority evidence artifact")
    if bundle.view.visual_evidence is None:
        raise AssertionError("face-on gate has no visual evidence")
    summary["visual_evidence_state"] = bundle.view.visual_evidence.state
    summary["durable_job"] = None
    return summary


def _run_dtl(root: Path) -> dict[str, object]:
    result = run_guided_synthetic(root / "dtl", angle="dtl")
    bundle = assert_bundle(result)
    summary = _bundle_summary("dtl", bundle)
    visual = bundle.view.visual_evidence
    if summary["priority_evidence_count"] != 1:
        raise AssertionError("DTL gate requires one priority evidence artifact")
    if visual is None or visual.kind is not EvidenceKind.TEMPO_TIMELINE:
        raise AssertionError("DTL gate requires timing evidence")
    if bundle.view.next_move is None or bundle.view.next_move.category is not PhaseId.TIMING_RHYTHM:
        raise AssertionError("DTL next move is not timing scoped")
    if [phase.id for phase in bundle.view.phases] != [PhaseId.TIMING_RHYTHM]:
        raise AssertionError("DTL phases include body-language semantics")
    if not visual.events or any(not event.method.value for event in visual.events):
        raise AssertionError("DTL timing event methods are missing")

    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    measured = metrics["swings"][0]["metrics"]
    timing_present = all(
        measured[key] is not None
        for key in ("backswing_s", "downswing_s", "tempo_ratio")
    )
    body_null = all(measured[key] is None for key in _BODY_METRICS)
    body_measurements = {
        measurement.id
        for phase in bundle.view.phases
        for measurement in phase.measurements
    }.intersection(_BODY_METRICS)
    if not timing_present or not body_null or body_measurements:
        raise AssertionError("DTL metrics are not angle honest")

    summary["visual_evidence_state"] = visual.state
    summary["event_methods"] = [event.method.value for event in visual.events]
    summary["timing_metrics_present"] = True
    summary["body_metrics_null"] = True
    summary["body_language_semantics"] = False
    summary["durable_job"] = None
    return summary


def _run_renderer_only(root: Path) -> dict[str, object]:
    row, manager = run_guided_job(
        root / "renderer-only",
        angle="face-on",
        renderer_failure=True,
    )
    try:
        bundle = assert_job_bundle(row)
        summary = _bundle_summary("renderer-only", bundle)
        visual = bundle.view.visual_evidence
        usage = manager.usage_this_month("guided-gate-user")
        if row.status != jobs_module.DONE or not row.structured_report:
            raise AssertionError("renderer-only failure did not commit a done row")
        if visual is None or visual.state != "unavailable":
            raise AssertionError("renderer-only failure is not unavailable")
        if visual.render_reasons != (ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,):
            raise AssertionError("renderer-only reason code is incorrect")
        if summary["priority_evidence_count"] != 0:
            raise AssertionError("renderer-only failure retained priority media")
        if any(
            path.name == "priority-evidence.png"
            for path in row.session_dir.rglob("*")
        ):
            raise AssertionError("renderer-only failure retained a partial file")
        summary["visual_evidence_state"] = visual.state
        summary["durable_job"] = {
            "status": row.status,
            "structured_report": row.structured_report,
            "canonical_rels_present": all(
                (
                    row.report_rel,
                    row.report_view_rel,
                    row.report_manifest_rel,
                    row.report_checksums_rel,
                )
            ),
            "usage_this_month": usage,
        }
        summary["final_bundle_roots"] = 1
        return summary
    finally:
        close_job_manager(manager)


def _run_core_writer(root: Path) -> dict[str, object]:
    row, manager = run_guided_job(
        root / "core-writer",
        angle="face-on",
        core_writer_failure=True,
    )
    try:
        rels = (
            row.report_rel,
            row.report_view_rel,
            row.report_manifest_rel,
            row.report_checksums_rel,
        )
        usage = manager.usage_this_month("guided-gate-user")
        final_roots = sum(
            path.is_dir() and path.name.startswith("report-bundle-")
            for path in row.session_dir.rglob("*")
        )
        partial_roots = sum(
            path.is_dir() and path.name.startswith(".report-attempt-")
            for path in row.session_dir.rglob("*")
        )
        if (
            row.status != jobs_module.FAILED
            or row.structured_report
            or rels != (None, None, None, None)
            or usage != 0
            or final_roots
            or partial_roots
        ):
            raise AssertionError("core writer failure was not transactional")
        return {
            "scenario": "core-writer",
            "outcome": "failed",
            "presentation_version": GUIDED_REPORT_PRESENTATION_VERSION,
            "view_version": None,
            "canonical_rels": {
                "report": None,
                "view": None,
                "manifest": None,
                "checksums": None,
            },
            "media": [],
            "checksum_verification": {
                "rows": 0,
                "verified": 0,
                "media_resolved": 0,
            },
            "reason_codes": [],
            "failure_code": "core_html_writer_failed",
            "priority_evidence_count": 0,
            "topology_verified": True,
            "durable_job": {
                "status": row.status,
                "structured_report": row.structured_report,
                "canonical_rels_present": False,
                "usage_this_month": usage,
            },
            "final_bundle_roots": final_roots,
        }
    finally:
        close_job_manager(manager)


def _current_commit() -> str:
    repo = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    commit = completed.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise AssertionError("git returned an invalid commit identifier")
    return commit


def _prepare_output_root(root: Path) -> None:
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise ValueError("gate output directory must be an empty directory")
        return
    root.mkdir(parents=True)


def _run_scenarios(root: Path, scenarios: list[str]) -> dict[str, object]:
    runners = {
        "face-on": _run_face_on,
        "dtl": _run_dtl,
        "renderer-only": _run_renderer_only,
        "core-writer": _run_core_writer,
    }
    return {
        "result_version": "guided-report-local-gate-v1",
        "commit_sha": _current_commit(),
        "media_io": "deterministic-no-ffmpeg-harness",
        "scenarios": [runners[name](root) for name in scenarios],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local guided report gate")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=_SCENARIOS,
        dest="scenarios",
    )
    args = parser.parse_args(argv)
    scenarios = list(dict.fromkeys(args.scenarios or _SCENARIOS))
    try:
        _prepare_output_root(args.out)
        payload = _run_scenarios(args.out, scenarios)
        failures_only = set(scenarios).issubset({"renderer-only", "core-writer"})
        filename = (
            "renderer-core-failure-result.json"
            if failures_only
            else "guided-gate-result.json"
        )
        (args.out / filename).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"guided gate failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
