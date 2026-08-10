from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from swinglab import report_artifacts as report_artifacts_module
from swinglab.metrics import session_stats
from swinglab.report_artifacts import (
    MAX_REPORT_CHECKSUMS_BYTES,
    MAX_REPORT_MANIFEST_BYTES,
    REPORT_CHECKSUMS_FILENAME,
    REPORT_CHECKSUMS_FORMAT,
    REPORT_MANIFEST_FILENAME,
    REPORT_MANIFEST_FORMAT,
    REPORT_VIEW_FILENAME,
    ChecksumEntry,
    ManifestArtifact,
    ReportArtifactValidationError,
    ReportBundleChecksums,
    ReportBundleManifest,
    ReportEntitlementSnapshot,
    load_published_bundle,
    load_report_checksums,
    load_report_manifest,
    report_entitlements_from_json,
    report_entitlements_to_json,
    resolve_media_path,
    validate_persisted_report_policy,
    validate_staged_bundle,
    write_report_checksums,
    write_report_manifest,
)
from swinglab.report_view import (
    Entitlement,
    GUIDED_REPORT_PRESENTATION_VERSION,
    MAX_REPORT_VIEW_BYTES,
    MediaEntry,
    MediaRole,
    ReportOutcome,
    report_view_from_dict,
    report_view_to_dict,
    write_report_view,
)
from swinglab.report_presenter import build_report_document, prepare_report_input
from tests.report_bundle_fixtures import temporary_directory_redirect
from tests.report_view_fixtures import report_view_payload
from tests.test_report import branded_cfg, fake_swing, fake_video


_REPORT_HTML_LIMIT = 8 * 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.write_bytes(_canonical(payload))


def _artifact_bytes(root: Path, relative_path: str) -> bytes:
    return (root / Path(*relative_path.split("/"))).read_bytes()


def _valid_report_html(*, presentation: str, outcome: str) -> str:
    return (
        "<html><head>"
        '<meta name="caddieinsight-report-format" content="caddie-brief-v1">'
        f'<meta name="caddieinsight-report-presentation" content="{presentation}">'
        f'<meta name="caddieinsight-report-outcome" content="{outcome}">'
        "</head><body>guided report</body></html>\n"
    )


def _valid_metrics_payload(
    *,
    deliverables: dict[str, object] | None = None,
    swing_pattern: dict[str, object] | None = None,
) -> dict[str, object]:
    swings: list[dict[str, object]] = []
    if deliverables is not None:
        swings.append(
            {
                "metrics": {"swing": 1, "tempo_ratio": 3.0},
                "notes": [],
                "deliverables": deliverables,
            }
        )
    if swing_pattern is not None:
        return {
            **_valid_metrics_payload(deliverables=deliverables),
            "swing_pattern": swing_pattern,
        }
    return {
        "generator": {"name": "CaddieInsight", "swinglab_version": "test"},
        "video": {
            "path": "synthetic.mov",
            "duration_s": 1.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "rotation": 0,
            "creation_time": None,
        },
        "swings": swings,
        "session_stats": {},
        "session_notes": [],
        "disclaimer": "Single-camera estimates.",
    }


def _artifact_report_view_payload(name: str = "coaching-improve-clear") -> dict[str, object]:
    payload = report_view_payload(name)
    if payload["outcome"] == "coaching_ready":
        capabilities = payload["capabilities"]
        optional = payload["optional_sections"]
        context = payload["context"]
        assert isinstance(capabilities, dict) and isinstance(optional, list)
        assert isinstance(context, dict)
        detected_swings = context["detected_swings"]
        assert isinstance(detected_swings, int)
        capabilities["every_swing"] = detected_swings > 0
        optional.append(
            {
                "id": "every_swing",
                "label": "Every swing",
                "available": detected_swings > 0,
                "locked": False,
                "item_count": detected_swings,
            }
        )
        practice = payload["practice"]
        assert isinstance(optional, list) and isinstance(practice, dict)
        if capabilities.get("alternative_drills") and not any(
            isinstance(section, dict) and section.get("id") == "alternative_drills"
            for section in optional
        ):
            alternatives = practice["alternatives"]
            assert isinstance(alternatives, list)
            optional.append(
                {
                    "id": "alternative_drills",
                    "label": "Alternative drills",
                    "available": bool(alternatives),
                    "locked": False,
                    "item_count": len(alternatives),
                }
            )
    return payload


def _refresh_checksums(root: Path) -> None:
    checksums_path = root / REPORT_CHECKSUMS_FILENAME
    payload = _load_json(checksums_path)
    rows = payload["files"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        data = _artifact_bytes(root, str(row["relative_path"]))
        row["size_bytes"] = len(data)
        row["sha256"] = _sha(data)
    manifest_data = (root / REPORT_MANIFEST_FILENAME).read_bytes()
    payload["manifest_sha256"] = _sha(manifest_data)
    rows.sort(key=lambda row: str(row["relative_path"]))
    _write_json(checksums_path, payload)


def _build_bundle(
    tmp_path: Path,
    *,
    payload: dict[str, object] | None = None,
    metrics_payload: dict[str, object] | None = None,
    root_name: str = "report-bundle-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
) -> Path:
    root = tmp_path / root_name
    root.mkdir()
    (root / "media").mkdir()

    view_payload = payload if payload is not None else _artifact_report_view_payload()
    (root / "report.html").write_text(
        _valid_report_html(
            presentation=str(view_payload["presentation_version"]),
            outcome=str(view_payload["outcome"]),
        ),
        encoding="utf-8",
    )
    _write_json(
        root / "metrics.json",
        metrics_payload if metrics_payload is not None else _valid_metrics_payload(),
    )
    media = view_payload["media"]
    assert isinstance(media, list)
    media_bytes: dict[str, bytes] = {}
    for entry in media:
        assert isinstance(entry, dict)
        relative_path = str(entry["relative_path"])
        data = f"media:{entry['key']}\n".encode("utf-8")
        path = root / Path(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entry["checksum_sha256"] = _sha(data)
        media_bytes[str(entry["key"])] = data

    view = report_view_from_dict(view_payload)
    write_report_view(root / REPORT_VIEW_FILENAME, view)

    artifacts = [
        ManifestArtifact("metrics.json", "metrics", None, None, True),
        ManifestArtifact("report.html", "report", None, None, True),
        ManifestArtifact(REPORT_VIEW_FILENAME, "report_view", None, None, True),
    ]
    for entry in view.media:
        artifacts.append(
            ManifestArtifact(
                entry.relative_path,
                "media",
                entry.key,
                entry.entitlement,
                entry.entitlement == Entitlement.CORE,
            )
        )
    manifest = ReportBundleManifest(
        REPORT_MANIFEST_FORMAT,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        GUIDED_REPORT_PRESENTATION_VERSION,
        view.outcome,
        tuple(reversed(artifacts)),
    )
    write_report_manifest(root / REPORT_MANIFEST_FILENAME, manifest)

    rows = []
    for relative_path in [
        REPORT_MANIFEST_FILENAME,
        *(artifact.relative_path for artifact in artifacts),
    ]:
        data = _artifact_bytes(root, relative_path)
        rows.append(ChecksumEntry(relative_path, len(data), _sha(data)))
    manifest_data = (root / REPORT_MANIFEST_FILENAME).read_bytes()
    checksums = ReportBundleChecksums(
        REPORT_CHECKSUMS_FORMAT,
        _sha(manifest_data),
        tuple(reversed(rows)),
    )
    write_report_checksums(root / REPORT_CHECKSUMS_FILENAME, checksums)
    return root


def _manifest_payload(root: Path) -> dict[str, object]:
    return _load_json(root / REPORT_MANIFEST_FILENAME)


def _checksums_payload(root: Path) -> dict[str, object]:
    return _load_json(root / REPORT_CHECKSUMS_FILENAME)


def _view_payload(root: Path) -> dict[str, object]:
    return _load_json(root / REPORT_VIEW_FILENAME)


def _persisted_rels(root: Path) -> dict[str, str]:
    prefix = root.name
    return {
        "report_rel": f"{prefix}/report.html",
        "report_view_rel": f"{prefix}/{REPORT_VIEW_FILENAME}",
        "manifest_rel": f"{prefix}/{REPORT_MANIFEST_FILENAME}",
        "checksums_rel": f"{prefix}/{REPORT_CHECKSUMS_FILENAME}",
    }


def _load_bundle(root: Path):
    return load_published_bundle(root.parent, **_persisted_rels(root))


def _job_bundle_layout(
    tmp_path: Path,
) -> tuple[Path, str, Path, Path, dict[str, str]]:
    sessions = tmp_path / "sessions"
    job_id = "job-a"
    analysis = sessions / job_id / "out" / "source"
    analysis.mkdir(parents=True)
    root = _build_bundle(analysis)
    direct = _persisted_rels(root)
    full = {name: f"out/source/{value}" for name, value in direct.items()}
    return sessions, job_id, analysis, root, full


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")


def _junction_or_skip(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junctions are only available on Windows")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        pytest.skip(f"junctions are unavailable: {result.stderr or result.stdout}")


def _payload_with_media_role(role: str) -> tuple[dict[str, object], str]:
    if role == "capture_playback":
        payload = _artifact_report_view_payload("capture-only")
        return payload, "playback-1"

    payload = _artifact_report_view_payload()
    if role == "priority_evidence":
        return payload, "focus-1"
    if role == "drill_illustration":
        return payload, "drill-1"

    capabilities = payload["capabilities"]
    optional_sections = payload["optional_sections"]
    media = payload["media"]
    assert isinstance(capabilities, dict)
    assert isinstance(optional_sections, list)
    assert isinstance(media, list)
    key = f"{role}-1"
    entitlement = "pro" if role == "coach_replay" else "core"
    mime_type = "image/jpeg" if role in {"key_positions", "video_poster"} else "video/mp4"
    suffix = ".jpg" if mime_type == "image/jpeg" else ".mp4"
    media.append(
        {
            "key": key,
            "role": role,
            "mime_type": mime_type,
            "entitlement": entitlement,
            "relative_path": f"media/{key}{suffix}",
            "checksum_sha256": "0" * 64,
        }
    )
    if role == "slow_motion":
        capabilities["slow_motion"] = True
    elif role == "coach_replay":
        capabilities["coach_replay"] = True
        optional_sections.append(
            {
                "id": "replay",
                "label": "Coach replay",
                "available": True,
                "locked": False,
                "item_count": 1,
            }
        )
    return payload, key


def _coaching_payload_with_swing_media() -> dict[str, object]:
    payload, _ = _payload_with_media_role("key_positions")
    capabilities = payload["capabilities"]
    media = payload["media"]
    assert isinstance(capabilities, dict) and isinstance(media, list)
    capabilities["slow_motion"] = True
    media.append(
        {
            "key": "slow-motion-1",
            "role": "slow_motion",
            "mime_type": "video/mp4",
            "entitlement": "core",
            "relative_path": "media/slow-motion-1.mp4",
            "checksum_sha256": "0" * 64,
        }
    )
    return payload


def test_entitlement_snapshot_has_canonical_json_round_trip_and_strict_enum():
    snapshot = ReportEntitlementSnapshot("locked")

    encoded = report_entitlements_to_json(snapshot)

    assert encoded == '{"coach_replay":"locked"}\n'
    assert report_entitlements_from_json(encoded) == snapshot
    with pytest.raises(ReportArtifactValidationError):
        report_entitlements_from_json('{"coach_replay":"future"}\n')
    with pytest.raises(ReportArtifactValidationError):
        report_entitlements_from_json('{"coach_replay":"locked","extra":true}\n')


@pytest.mark.parametrize(
    ("policy", "section_locked", "rendered", "accepted"),
    [
        ("available", False, True, True),
        ("available", False, False, True),
        ("locked", True, False, True),
        ("disabled", False, False, True),
        ("locked", False, True, False),
        ("locked", False, False, False),
        ("disabled", False, True, False),
        ("available", True, False, False),
    ],
)
def test_persisted_policy_validator_reconciles_replay_state_with_validated_bundle(
    tmp_path: Path,
    policy: str,
    section_locked: bool,
    rendered: bool,
    accepted: bool,
):
    if rendered:
        payload, _ = _payload_with_media_role("coach_replay")
    else:
        payload = _artifact_report_view_payload()
        optional = payload["optional_sections"]
        assert isinstance(optional, list)
        optional.append(
            {
                "id": "replay",
                "label": "Coach replay",
                "available": False,
                "locked": section_locked,
                "item_count": 0,
            }
        )
    bundle = _load_bundle(_build_bundle(tmp_path, payload=payload))
    persisted = ReportEntitlementSnapshot(policy).to_json()

    if accepted:
        assert (
            validate_persisted_report_policy(
                bundle,
                report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
                report_entitlements_json=persisted,
            )
            == ReportEntitlementSnapshot(policy)
        )
    else:
        with pytest.raises(ReportArtifactValidationError):
            validate_persisted_report_policy(
                bundle,
                report_presentation_version=GUIDED_REPORT_PRESENTATION_VERSION,
                report_entitlements_json=persisted,
            )


@pytest.mark.parametrize(
    ("presentation", "entitlements"),
    [
        ("premium-coach-v2", '{"coach_replay":"available"}\n'),
        (GUIDED_REPORT_PRESENTATION_VERSION, None),
        (GUIDED_REPORT_PRESENTATION_VERSION, '{"coach_replay":"available"}'),
    ],
)
def test_persisted_policy_validator_requires_guided_and_canonical_policy(
    tmp_path: Path, presentation: str, entitlements: str | None
):
    payload = _artifact_report_view_payload()
    optional = payload["optional_sections"]
    assert isinstance(optional, list)
    optional.append(
        {
            "id": "replay",
            "label": "Coach replay",
            "available": False,
            "locked": False,
            "item_count": 0,
        }
    )
    bundle = _load_bundle(_build_bundle(tmp_path, payload=payload))

    with pytest.raises(ReportArtifactValidationError):
        validate_persisted_report_policy(
            bundle,
            report_presentation_version=presentation,
            report_entitlements_json=entitlements,
        )


def test_manifest_round_trip_is_canonical_sorted_and_newline_terminated(tmp_path: Path):
    manifest = ReportBundleManifest(
        REPORT_MANIFEST_FORMAT,
        "attempt-1",
        GUIDED_REPORT_PRESENTATION_VERSION,
        ReportOutcome.COACHING_READY,
        (
            ManifestArtifact("report.html", "report", None, None, True),
            ManifestArtifact("media/focus.webp", "media", "focus", Entitlement.CORE, True),
            ManifestArtifact("metrics.json", "metrics", None, None, True),
            ManifestArtifact(REPORT_VIEW_FILENAME, "report_view", None, None, True),
        ),
    )
    path = tmp_path / REPORT_MANIFEST_FILENAME

    write_report_manifest(path, manifest)

    assert path.read_text(encoding="utf-8") == (
        '{"artifacts":['
        '{"entitlement":"core","kind":"media","media_key":"focus","relative_path":"media/focus.webp","required":true},'
        '{"entitlement":null,"kind":"metrics","media_key":null,"relative_path":"metrics.json","required":true},'
        '{"entitlement":null,"kind":"report_view","media_key":null,"relative_path":"report-view.json","required":true},'
        '{"entitlement":null,"kind":"report","media_key":null,"relative_path":"report.html","required":true}'
        '],"attempt_id":"attempt-1","format":"report-bundle-v1","outcome":"coaching_ready","presentation_version":"guided-report-v1"}\n'
    )
    loaded = load_report_manifest(path)
    assert tuple(row.relative_path for row in loaded.artifacts) == (
        "media/focus.webp",
        "metrics.json",
        REPORT_VIEW_FILENAME,
        "report.html",
    )
    assert loaded.outcome is ReportOutcome.COACHING_READY


def test_checksums_round_trip_is_canonical_sorted_and_newline_terminated(tmp_path: Path):
    checksums = ReportBundleChecksums(
        REPORT_CHECKSUMS_FORMAT,
        "a" * 64,
        (
            ChecksumEntry("report.html", 2, "c" * 64),
            ChecksumEntry(REPORT_MANIFEST_FILENAME, 1, "b" * 64),
        ),
    )
    path = tmp_path / REPORT_CHECKSUMS_FILENAME

    write_report_checksums(path, checksums)

    assert path.read_text(encoding="utf-8") == (
        '{"files":['
        '{"relative_path":"report-bundle-manifest.json","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","size_bytes":1},'
        '{"relative_path":"report.html","sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","size_bytes":2}'
        '],"format":"report-bundle-checksums-v1","manifest_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n'
    )
    assert load_report_checksums(path) == ReportBundleChecksums(
        REPORT_CHECKSUMS_FORMAT,
        "a" * 64,
        (
            ChecksumEntry(REPORT_MANIFEST_FILENAME, 1, "b" * 64),
            ChecksumEntry("report.html", 2, "c" * 64),
        ),
    )


def test_validation_round_trip_covers_manifest_and_every_artifact_but_not_checksums(tmp_path: Path):
    root = _build_bundle(tmp_path)

    manifest, checksums, view = validate_staged_bundle(
        root,
        manifest_rel=REPORT_MANIFEST_FILENAME,
        checksums_rel=REPORT_CHECKSUMS_FILENAME,
    )

    declared = {artifact.relative_path for artifact in manifest.artifacts}
    covered = {entry.relative_path for entry in checksums.files}
    assert covered == declared | {REPORT_MANIFEST_FILENAME}
    assert REPORT_CHECKSUMS_FILENAME not in covered
    assert tuple(entry.relative_path for entry in checksums.files) == tuple(sorted(covered))
    assert view.outcome is ReportOutcome.COACHING_READY


def test_real_presenter_document_with_swing_media_validates_as_a_complete_bundle(
    tmp_path: Path,
):
    cfg = branded_cfg()
    swing = fake_swing(1, 2.0)
    swing["overlay"] = None
    swing["strip"] = "media/positions-1.jpg"
    swing["slowmo"] = "media/slow-1.mp4"
    evidence = report_view_from_dict(
        report_view_payload("coaching-improve-clear")
    ).visual_evidence
    media = (
        MediaEntry(
            "focus-1",
            MediaRole.PRIORITY_EVIDENCE,
            "image/jpeg",
            Entitlement.CORE,
            "media/focus-1.jpg",
            "a" * 64,
        ),
        MediaEntry(
            "positions-1",
            MediaRole.KEY_POSITIONS,
            "image/jpeg",
            Entitlement.CORE,
            "media/positions-1.jpg",
            "b" * 64,
        ),
        MediaEntry(
            "slow-1",
            MediaRole.SLOW_MOTION,
            "video/mp4",
            Entitlement.CORE,
            "media/slow-1.mp4",
            "c" * 64,
        ),
    )
    source = prepare_report_input(
        fake_video(),
        [swing],
        session_stats([swing["metrics"]]),
        [],
        "right",
        cfg,
        visual_evidence=evidence,
        media=media,
    )
    document = build_report_document(source, cfg)
    assert document.depth.swing_pattern is not None
    root = _build_bundle(
        tmp_path,
        payload=report_view_to_dict(document.view),
        metrics_payload=_valid_metrics_payload(
            deliverables={
                "strip": "media/positions-1.jpg",
                "slowmo": "media/slow-1.mp4",
            },
            swing_pattern=document.depth.swing_pattern.as_dict(),
        ),
    )

    _, _, view = validate_staged_bundle(
        root,
        manifest_rel=REPORT_MANIFEST_FILENAME,
        checksums_rel=REPORT_CHECKSUMS_FILENAME,
    )

    assert view.capabilities.every_swing is True
    assert view.capabilities.slow_motion is True


@pytest.mark.parametrize(
    "html",
    [
        (
            "<html><head>"
            '<meta name="caddieinsight-report-presentation" content="guided-report-v1">'
            '<meta name="caddieinsight-report-outcome" content="coaching_ready">'
            "</head></html>\n"
        ),
        (
            '<meta name="caddieinsight-report-format" content="caddie-brief-v1">'
            '<meta name="caddieinsight-report-format" content="caddie-brief-v1">'
            '<meta name="caddieinsight-report-presentation" content="guided-report-v1">'
            '<meta name="caddieinsight-report-outcome" content="coaching_ready">\n'
        ),
        (
            '<meta name="caddieinsight-report-format" content="caddie-brief-v1">'
            '<meta name="caddieinsight-report-format" content="caddie-brief-v2">'
            '<meta name="caddieinsight-report-presentation" content="guided-report-v1">'
            '<meta name="caddieinsight-report-outcome" content="coaching_ready">\n'
        ),
        _valid_report_html(
            presentation=GUIDED_REPORT_PRESENTATION_VERSION,
            outcome=ReportOutcome.CAPTURE_ONLY.value,
        ),
    ],
)
def test_report_html_requires_one_exact_marker_matching_the_bundle(
    tmp_path: Path, html: str
):
    root = _build_bundle(tmp_path)
    (root / "report.html").write_text(html, encoding="utf-8")
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


@pytest.mark.parametrize(
    "html",
    [
        _valid_report_html(
            presentation=GUIDED_REPORT_PRESENTATION_VERSION,
            outcome=ReportOutcome.COACHING_READY.value,
        ).replace(
            'name="caddieinsight-report-format"',
            'NAME="caddieinsight-report-format"',
            1,
        ),
        _valid_report_html(
            presentation=GUIDED_REPORT_PRESENTATION_VERSION,
            outcome=ReportOutcome.COACHING_READY.value,
        ).replace(
            'name="caddieinsight-report-format" content="caddie-brief-v1"',
            "name='caddieinsight-report-format' content='caddie-brief-v1'",
            1,
        ),
        " " * 8192
        + _valid_report_html(
            presentation=GUIDED_REPORT_PRESENTATION_VERSION,
            outcome=ReportOutcome.COACHING_READY.value,
        ),
    ],
)
def test_report_html_markers_use_exact_compatibility_bytes_in_the_header(
    tmp_path: Path, html: str
):
    root = _build_bundle(tmp_path)
    (root / "report.html").write_text(html, encoding="utf-8")
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_report_html_read_is_bounded(tmp_path: Path):
    root = _build_bundle(tmp_path)
    (root / "report.html").write_bytes(b"<html>" + b" " * _REPORT_HTML_LIMIT + b"</html>")
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_metrics_must_be_json_without_duplicate_keys(tmp_path: Path):
    root = _build_bundle(tmp_path)
    (root / "metrics.json").write_text(
        '{"generator":{},"generator":{},"video":{},"swings":[],'
        '"session_stats":{},"session_notes":[],"disclaimer":"test"}\n',
        encoding="utf-8",
    )
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_metrics_json_read_is_bounded(tmp_path: Path):
    root = _build_bundle(tmp_path)
    (root / "metrics.json").write_bytes(b"{" + b" " * _REPORT_HTML_LIMIT + b"}")
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


@pytest.mark.parametrize("raw", [b"not json\n", _canonical({"swings": []})])
def test_metrics_requires_json_and_the_core_schema(tmp_path: Path, raw: bytes):
    root = _build_bundle(tmp_path)
    (root / "metrics.json").write_bytes(raw)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_metrics_deliverable_must_reference_a_declared_artifact(tmp_path: Path):
    root = _build_bundle(
        tmp_path,
        metrics_payload=_valid_metrics_payload(
            deliverables={"strip": "media/not-declared.png", "slowmo": "media/not-declared.mp4"}
        ),
    )

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_metrics_renderer_deliverables_may_all_be_absent(tmp_path: Path):
    root = _build_bundle(
        tmp_path,
        metrics_payload=_valid_metrics_payload(deliverables={}),
    )

    validate_staged_bundle(
        root,
        manifest_rel=REPORT_MANIFEST_FILENAME,
        checksums_rel=REPORT_CHECKSUMS_FILENAME,
    )


def test_metrics_deliverables_must_match_their_guided_media_roles(tmp_path: Path):
    payload = _coaching_payload_with_swing_media()
    root = _build_bundle(
        tmp_path,
        payload=payload,
        metrics_payload=_valid_metrics_payload(
            deliverables={
                "strip": "media/slow-motion-1.mp4",
                "slowmo": "media/key_positions-1.jpg",
            }
        ),
    )

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_metrics_replay_deliverable_requires_coach_replay_media(tmp_path: Path):
    payload = _coaching_payload_with_swing_media()
    root = _build_bundle(
        tmp_path,
        payload=payload,
        metrics_payload=_valid_metrics_payload(
            deliverables={
                "strip": "media/key_positions-1.jpg",
                "slowmo": "media/slow-motion-1.mp4",
                "replay": "media/slow-motion-1.mp4",
            }
        ),
    )

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_metrics_rejects_guided_overlay_even_when_declared(tmp_path: Path):
    payload = _coaching_payload_with_swing_media()
    root = _build_bundle(
        tmp_path,
        payload=payload,
        metrics_payload=_valid_metrics_payload(
            deliverables={
                "strip": "media/key_positions-1.jpg",
                "overlay": "media/key_positions-1.jpg",
                "slowmo": "media/slow-motion-1.mp4",
            }
        ),
    )

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


@pytest.mark.parametrize(
    "deliverables",
    [{}, {"slowmo": "media/playback-1.mp4"}],
)
def test_capture_metrics_allow_empty_or_safe_playback_deliverables(
    tmp_path: Path, deliverables: dict[str, str]
):
    root = _build_bundle(
        tmp_path,
        payload=_artifact_report_view_payload("capture-only"),
        metrics_payload=_valid_metrics_payload(deliverables=deliverables),
    )

    validate_staged_bundle(
        root,
        manifest_rel=REPORT_MANIFEST_FILENAME,
        checksums_rel=REPORT_CHECKSUMS_FILENAME,
    )


def test_report_view_bytes_must_equal_the_canonical_codec_encoding(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _view_payload(root)
    (root / REPORT_VIEW_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        "/absolute.json",
        "\\absolute.json",
        "C:/drive.json",
        "C:\\drive.json",
        "folder\\file.json",
        "./file.json",
        "folder/./file.json",
        "../file.json",
        "folder/../file.json",
        "folder//file.json",
    ],
)
def test_manifest_rejects_every_noncanonical_or_escaping_path(tmp_path: Path, unsafe: str):
    root = _build_bundle(tmp_path)
    payload = _manifest_payload(root)
    artifacts = payload["artifacts"]
    assert isinstance(artifacts, list)
    assert isinstance(artifacts[0], dict)
    artifacts[0]["relative_path"] = unsafe
    _write_json(root / REPORT_MANIFEST_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        "CON/file.json",
        "aux.txt",
        "media./file.json",
        "media /file.json",
        "media/file.json.",
        "media/file.json ",
    ],
)
def test_windows_ambiguous_relative_path_segments_are_rejected_on_every_platform(
    tmp_path: Path, unsafe: str
):
    manifest = ReportBundleManifest(
        REPORT_MANIFEST_FORMAT,
        "attempt-1",
        GUIDED_REPORT_PRESENTATION_VERSION,
        ReportOutcome.COACHING_READY,
        (
            ManifestArtifact(unsafe, "media", "unsafe", Entitlement.CORE, True),
            ManifestArtifact("metrics.json", "metrics", None, None, True),
            ManifestArtifact(REPORT_VIEW_FILENAME, "report_view", None, None, True),
            ManifestArtifact("report.html", "report", None, None, True),
        ),
    )

    with pytest.raises(ReportArtifactValidationError):
        write_report_manifest(tmp_path / REPORT_MANIFEST_FILENAME, manifest)


@pytest.mark.parametrize("collision", ["report.html", "REPORT.HTML"])
def test_duplicate_and_case_colliding_manifest_paths_are_rejected(tmp_path: Path, collision: str):
    root = _build_bundle(tmp_path)
    payload = _manifest_payload(root)
    artifacts = payload["artifacts"]
    assert isinstance(artifacts, list)
    assert isinstance(artifacts[0], dict)
    duplicate = dict(artifacts[0])
    duplicate["relative_path"] = collision
    artifacts.append(duplicate)
    _write_json(root / REPORT_MANIFEST_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_case_colliding_checksum_paths_are_rejected(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _checksums_payload(root)
    rows = payload["files"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    duplicate = dict(rows[0])
    duplicate["relative_path"] = str(duplicate["relative_path"]).upper()
    rows.append(duplicate)
    _write_json(root / REPORT_CHECKSUMS_FILENAME, payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_symlinked_declared_file_is_rejected_before_hashing(tmp_path: Path):
    root = _build_bundle(tmp_path)
    report = root / "report.html"
    target = tmp_path / "outside.html"
    target.write_bytes(report.read_bytes())
    report.unlink()
    _symlink_or_skip(report, target)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_symlinked_declared_parent_is_rejected_before_hashing(tmp_path: Path):
    root = _build_bundle(tmp_path)
    outside = tmp_path / "outside-media"
    (root / "media").replace(outside)
    _symlink_or_skip(root / "media", outside, directory=True)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_parent_replacement_between_validation_and_open_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _build_bundle(tmp_path)
    outside = tmp_path / "outside-media"
    shutil.copytree(root / "media", outside)
    original_media = tmp_path / "original-media"
    original_join = report_artifacts_module._join_under
    swapped = False

    def swap_after_join(bundle_root: Path, relative: object) -> Path:
        nonlocal swapped
        result = original_join(bundle_root, relative)
        if not swapped and str(relative) == "media/focus-1.jpg":
            (root / "media").replace(original_media)
            _symlink_or_skip(root / "media", outside, directory=True)
            swapped = True
        return result

    monkeypatch.setattr(report_artifacts_module, "_join_under", swap_after_join)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_windows_junction_detection_does_not_depend_on_path_is_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "junction-target"
    target.mkdir()
    link = tmp_path / "junction-link"
    _junction_or_skip(link, target)
    monkeypatch.setattr(Path, "is_junction", lambda self: False, raising=False)

    assert report_artifacts_module._is_link(link)


@pytest.mark.skipif(os.name != "nt", reason="Win32 handle cleanup is Windows-only")
def test_win32_root_handle_closes_once_when_file_info_inspection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _build_bundle(tmp_path)
    opened: list[int] = []
    closed: list[int] = []
    real_open = report_artifacts_module._win_open
    real_close = report_artifacts_module._win_close

    def tracking_open(path: Path, *, directory: bool) -> int:
        handle = real_open(path, directory=directory)
        opened.append(handle)
        return handle

    def tracking_close(handle: int) -> None:
        closed.append(handle)
        real_close(handle)

    def fail_info(handle: int):
        raise OSError("injected GetFileInformationByHandle failure")

    monkeypatch.setattr(report_artifacts_module, "_win_open", tracking_open)
    monkeypatch.setattr(report_artifacts_module, "_win_close", tracking_close)
    monkeypatch.setattr(report_artifacts_module, "_win_info", fail_info)

    with pytest.raises(ReportArtifactValidationError):
        with report_artifacts_module._PinnedBundleRoot(root):
            pass

    assert len(opened) == 1
    close_count = closed.count(opened[0])
    if close_count == 0:
        real_close(opened[0])
    assert close_count == 1


@pytest.mark.skipif(os.name != "nt", reason="Win32 handle cleanup is Windows-only")
def test_win32_child_handle_closes_once_when_final_path_inspection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _build_bundle(tmp_path)
    opened: list[int] = []
    closed: list[int] = []
    final_path_calls = 0
    real_absolute_open = report_artifacts_module._win_open
    real_relative_open = report_artifacts_module._win_open_relative
    real_close = report_artifacts_module._win_close
    real_final_path = report_artifacts_module._win_final_path

    def tracking_absolute_open(path: Path, *, directory: bool) -> int:
        handle = real_absolute_open(path, directory=directory)
        opened.append(handle)
        return handle

    def tracking_relative_open(
        parent_handle: int, name: str, *, directory: bool
    ) -> int:
        handle = real_relative_open(parent_handle, name, directory=directory)
        opened.append(handle)
        return handle

    def tracking_close(handle: int) -> None:
        closed.append(handle)
        real_close(handle)

    def fail_child_final_path(handle: int) -> Path:
        nonlocal final_path_calls
        final_path_calls += 1
        if final_path_calls == 3:
            raise OSError("injected GetFinalPathNameByHandleW failure")
        return real_final_path(handle)

    monkeypatch.setattr(
        report_artifacts_module, "_win_open", tracking_absolute_open
    )
    monkeypatch.setattr(
        report_artifacts_module, "_win_open_relative", tracking_relative_open
    )
    monkeypatch.setattr(report_artifacts_module, "_win_close", tracking_close)
    monkeypatch.setattr(
        report_artifacts_module, "_win_final_path", fail_child_final_path
    )

    with pytest.raises(ReportArtifactValidationError):
        with report_artifacts_module._PinnedBundleRoot(root) as pinned:
            with pinned.open_file(PurePosixPath("media/focus-1.jpg")):
                pass

    assert len(opened) >= 2
    close_count = closed.count(opened[1])
    if close_count == 0:
        real_close(opened[1])
    assert close_count == 1


def test_undeclared_regular_file_is_rejected(tmp_path: Path):
    root = _build_bundle(tmp_path)
    (root / "private-debug.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


@pytest.mark.parametrize("relative", ["work", "unexpected/one/two"])
def test_undeclared_empty_directory_tree_is_rejected(tmp_path: Path, relative: str):
    root = _build_bundle(tmp_path)
    (root / Path(*relative.split("/"))).mkdir(parents=True)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_missing_declared_file_is_rejected(tmp_path: Path):
    root = _build_bundle(tmp_path)
    (root / "metrics.json").unlink()

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_directory_where_declared_file_is_expected_is_rejected(tmp_path: Path):
    root = _build_bundle(tmp_path)
    path = root / "metrics.json"
    path.unlink()
    path.mkdir()

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


@pytest.mark.parametrize("field,value", [("size_bytes", 999), ("sha256", "0" * 64)])
def test_wrong_file_size_or_hash_is_rejected(tmp_path: Path, field: str, value: object):
    root = _build_bundle(tmp_path)
    payload = _checksums_payload(root)
    rows = payload["files"]
    assert isinstance(rows, list)
    report_row = next(row for row in rows if isinstance(row, dict) and row["relative_path"] == "report.html")
    report_row[field] = value
    _write_json(root / REPORT_CHECKSUMS_FILENAME, payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_changed_manifest_hash_is_rejected(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _manifest_payload(root)
    payload["attempt_id"] = "changed"
    _write_json(root / REPORT_MANIFEST_FILENAME, payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


@pytest.mark.parametrize("field", ["manifest_sha256", "file_sha256"])
@pytest.mark.parametrize("invalid", ["f" * 63, "F" * 64, "z" * 64])
def test_invalid_sha256_shapes_are_rejected(tmp_path: Path, field: str, invalid: str):
    root = _build_bundle(tmp_path)
    payload = _checksums_payload(root)
    if field == "manifest_sha256":
        payload["manifest_sha256"] = invalid
    else:
        rows = payload["files"]
        assert isinstance(rows, list) and isinstance(rows[0], dict)
        rows[0]["sha256"] = invalid
    _write_json(root / REPORT_CHECKSUMS_FILENAME, payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("manifest", "format", "report-bundle-v2"),
        ("checksums", "format", "report-bundle-checksums-v2"),
        ("manifest", "outcome", "future_outcome"),
        ("manifest_artifact", "kind", "scratch"),
        ("manifest", "presentation_version", "guided-report-v2"),
        ("view", "version", "report-view-v2"),
        ("view_media", "role", "raw_landmarks"),
        ("view_media", "entitlement", "staff"),
        ("manifest_media", "entitlement", "staff"),
    ],
)
def test_unknown_formats_enums_versions_roles_entitlements_and_kinds_are_rejected(
    tmp_path: Path,
    target: str,
    field: str,
    value: str,
):
    root = _build_bundle(tmp_path)
    if target.startswith("manifest"):
        payload = _manifest_payload(root)
        if target == "manifest_artifact":
            artifacts = payload["artifacts"]
            assert isinstance(artifacts, list) and isinstance(artifacts[0], dict)
            artifacts[0][field] = value
        elif target == "manifest_media":
            artifacts = payload["artifacts"]
            assert isinstance(artifacts, list)
            row = next(item for item in artifacts if isinstance(item, dict) and item["kind"] == "media")
            row[field] = value
        else:
            payload[field] = value
        _write_json(root / REPORT_MANIFEST_FILENAME, payload)
        _refresh_checksums(root)
    elif target == "checksums":
        payload = _checksums_payload(root)
        payload[field] = value
        _write_json(root / REPORT_CHECKSUMS_FILENAME, payload)
    else:
        payload = _view_payload(root)
        if target == "view_media":
            media = payload["media"]
            assert isinstance(media, list) and isinstance(media[0], dict)
            media[0][field] = value
        else:
            payload[field] = value
        _write_json(root / REPORT_VIEW_FILENAME, payload)
        _refresh_checksums(root)

    with pytest.raises((ReportArtifactValidationError, ValueError)):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_unsupported_report_view_version_is_wrapped_at_the_artifact_boundary(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _view_payload(root)
    payload["version"] = "report-view-v2"
    _write_json(root / REPORT_VIEW_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_manifest_and_view_outcomes_must_match(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _manifest_payload(root)
    payload["outcome"] = "capture_only"
    _write_json(root / REPORT_MANIFEST_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_manifest_and_view_presentation_versions_must_match(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _view_payload(root)
    payload["presentation_version"] = "guided-report-v2"
    _write_json(root / REPORT_VIEW_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_rendered_coaching_requires_one_core_required_priority_evidence_file(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _manifest_payload(root)
    artifacts = payload["artifacts"]
    assert isinstance(artifacts, list)
    focus = next(item for item in artifacts if isinstance(item, dict) and item.get("media_key") == "focus-1")
    focus["required"] = False
    _write_json(root / REPORT_MANIFEST_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_rendered_coaching_rejects_noncore_or_wrong_role_focused_media(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _view_payload(root)
    media = payload["media"]
    assert isinstance(media, list)
    focus = next(item for item in media if isinstance(item, dict) and item.get("key") == "focus-1")
    focus["role"] = "drill_illustration"
    _write_json(root / REPORT_VIEW_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


@pytest.mark.parametrize("role", [role.value for role in MediaRole])
def test_each_media_role_accepts_its_coherent_relationships(tmp_path: Path, role: str):
    payload, _ = _payload_with_media_role(role)
    root = _build_bundle(tmp_path, payload=payload)

    validate_staged_bundle(
        root,
        manifest_rel=REPORT_MANIFEST_FILENAME,
        checksums_rel=REPORT_CHECKSUMS_FILENAME,
    )


@pytest.mark.parametrize(
    "role,wrong_entitlement",
    [
        ("priority_evidence", "free"),
        ("drill_illustration", "core"),
        ("key_positions", "free"),
        ("slow_motion", "free"),
        ("coach_replay", "core"),
        ("video_poster", "free"),
        ("capture_playback", "free"),
    ],
)
def test_each_media_role_rejects_the_wrong_entitlement(
    tmp_path: Path, role: str, wrong_entitlement: str
):
    payload, key = _payload_with_media_role(role)
    media = payload["media"]
    assert isinstance(media, list)
    row = next(item for item in media if isinstance(item, dict) and item["key"] == key)
    row["entitlement"] = wrong_entitlement
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_slow_motion_file_requires_the_slow_motion_capability(tmp_path: Path):
    payload, _ = _payload_with_media_role("slow_motion")
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["slow_motion"] = False
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


@pytest.mark.parametrize("role,capability", [("slow_motion", "slow_motion"), ("coach_replay", "coach_replay")])
def test_renderer_capability_requires_corresponding_media(
    tmp_path: Path, role: str, capability: str
):
    payload = _artifact_report_view_payload()
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities[capability] = True
    if role == "coach_replay":
        optional = payload["optional_sections"]
        assert isinstance(optional, list)
        optional.append(
            {
                "id": "replay",
                "label": "Coach replay",
                "available": True,
                "locked": False,
                "item_count": 1,
            }
        )
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_replay_section_count_must_match_rendered_replay_media(tmp_path: Path):
    payload, _ = _payload_with_media_role("coach_replay")
    optional = payload["optional_sections"]
    assert isinstance(optional, list)
    replay = next(item for item in optional if isinstance(item, dict) and item["id"] == "replay")
    replay["item_count"] = 2
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_replay_section_cannot_claim_available_media_that_is_absent(tmp_path: Path):
    payload = _artifact_report_view_payload()
    optional = payload["optional_sections"]
    assert isinstance(optional, list)
    optional.append(
        {
            "id": "replay",
            "label": "Coach replay",
            "available": True,
            "locked": False,
            "item_count": 1,
        }
    )
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_locked_unrendered_replay_is_a_valid_absent_renderer_state(tmp_path: Path):
    payload = _artifact_report_view_payload()
    optional = payload["optional_sections"]
    assert isinstance(optional, list)
    optional.append(
        {
            "id": "replay",
            "label": "Coach replay",
            "available": False,
            "locked": True,
            "item_count": 0,
        }
    )
    root = _build_bundle(tmp_path, payload=payload)

    validate_staged_bundle(
        root,
        manifest_rel=REPORT_MANIFEST_FILENAME,
        checksums_rel=REPORT_CHECKSUMS_FILENAME,
    )


def test_every_swing_capability_must_follow_its_available_section(tmp_path: Path):
    payload = _artifact_report_view_payload()
    capabilities = payload["capabilities"]
    optional = payload["optional_sections"]
    assert isinstance(capabilities, dict) and isinstance(optional, list)
    section = next(
        item
        for item in optional
        if isinstance(item, dict) and item["id"] == "every_swing"
    )
    capabilities["every_swing"] = False
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_every_swing_count_cannot_be_less_than_key_position_media_count(tmp_path: Path):
    payload, _ = _payload_with_media_role("key_positions")
    media = payload["media"]
    optional = payload["optional_sections"]
    assert isinstance(media, list) and isinstance(optional, list)
    media.append(
        {
            "key": "key-positions-2",
            "role": "key_positions",
            "mime_type": "image/jpeg",
            "entitlement": "core",
            "relative_path": "media/key-positions-2.jpg",
            "checksum_sha256": "0" * 64,
        }
    )
    section = next(item for item in optional if isinstance(item, dict) and item["id"] == "every_swing")
    section["item_count"] = 1
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_every_swing_count_must_equal_detected_swing_count(tmp_path: Path):
    payload = _artifact_report_view_payload()
    capabilities = payload["capabilities"]
    optional = payload["optional_sections"]
    context = payload["context"]
    assert isinstance(capabilities, dict)
    assert isinstance(optional, list)
    assert isinstance(context, dict) and context["detected_swings"] == 3
    section = next(
        item
        for item in optional
        if isinstance(item, dict) and item["id"] == "every_swing"
    )
    assert capabilities["every_swing"] is True
    section["item_count"] = 999
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_nonempty_alternative_drills_require_matching_section_and_capability(
    tmp_path: Path,
):
    payload = _artifact_report_view_payload()
    capabilities = payload["capabilities"]
    optional = payload["optional_sections"]
    practice = payload["practice"]
    assert isinstance(capabilities, dict)
    assert isinstance(optional, list)
    assert isinstance(practice, dict)
    assert isinstance(practice["alternatives"], list) and practice["alternatives"]
    capabilities["alternative_drills"] = False
    payload["optional_sections"] = [
        section
        for section in optional
        if not isinstance(section, dict) or section.get("id") != "alternative_drills"
    ]
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_phase_measurements_require_matching_section_and_capability(tmp_path: Path):
    payload = _artifact_report_view_payload()
    capabilities = payload["capabilities"]
    optional = payload["optional_sections"]
    phases = payload["phases"]
    assert isinstance(capabilities, dict)
    assert isinstance(optional, list)
    assert isinstance(phases, list)
    assert any(
        isinstance(phase, dict) and phase.get("measurements")
        for phase in phases
    )
    capabilities["measurements"] = False
    payload["optional_sections"] = [
        section
        for section in optional
        if not isinstance(section, dict) or section.get("id") != "measurements"
    ]
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_practice_illustration_reference_requires_drill_illustration_role(tmp_path: Path):
    payload = _artifact_report_view_payload()
    media = payload["media"]
    assert isinstance(media, list)
    row = next(item for item in media if isinstance(item, dict) and item["key"] == "drill-1")
    row["role"] = "video_poster"
    row["entitlement"] = "core"
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_capture_guidance_safe_media_key_requires_capture_playback_role(tmp_path: Path):
    payload = _artifact_report_view_payload("capture-only")
    media = payload["media"]
    assert isinstance(media, list) and isinstance(media[0], dict)
    media[0]["role"] = "video_poster"
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_capture_guidance_rejects_duplicate_safe_media_keys(tmp_path: Path):
    payload = _artifact_report_view_payload("capture-only")
    capture_guidance = payload["capture_guidance"]
    assert isinstance(capture_guidance, dict)
    safe_media_keys = capture_guidance["safe_media_keys"]
    assert safe_media_keys == ["playback-1"]
    safe_media_keys.append("playback-1")
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


@pytest.mark.parametrize("mutation", ["capability", "section"])
def test_capture_only_rejects_coaching_depth_capabilities_and_sections(
    tmp_path: Path, mutation: str
):
    payload = _artifact_report_view_payload("capture-only")
    if mutation == "capability":
        capabilities = payload["capabilities"]
        assert isinstance(capabilities, dict)
        capabilities["slow_motion"] = True
    else:
        optional = payload["optional_sections"]
        assert isinstance(optional, list)
        optional.append(
            {
                "id": "measurements",
                "label": "Measurements",
                "available": False,
                "locked": False,
                "item_count": 0,
            }
        )
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_key_positions_file_requires_unlocked_available_every_swing_section(tmp_path: Path):
    payload, _ = _payload_with_media_role("key_positions")
    optional = payload["optional_sections"]
    assert isinstance(optional, list)
    section = next(item for item in optional if isinstance(item, dict) and item["id"] == "every_swing")
    section["locked"] = True
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(
            root,
            manifest_rel=REPORT_MANIFEST_FILENAME,
            checksums_rel=REPORT_CHECKSUMS_FILENAME,
        )


def test_locked_unrendered_replay_cannot_be_declared_as_a_file(tmp_path: Path):
    payload = _artifact_report_view_payload()
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["coach_replay"] = False
    optional = payload["optional_sections"]
    assert isinstance(optional, list)
    optional.append({"id": "replay", "label": "Coach replay", "available": True, "locked": True, "item_count": 1})
    media = payload["media"]
    assert isinstance(media, list)
    media.append(
        {
            "key": "replay-1",
            "role": "coach_replay",
            "mime_type": "video/mp4",
            "entitlement": "pro",
            "relative_path": "media/replay-1.mp4",
            "checksum_sha256": "0" * 64,
        }
    )
    root = _build_bundle(tmp_path, payload=payload)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_view_media_checksum_must_equal_checksum_artifact(tmp_path: Path):
    root = _build_bundle(tmp_path)
    payload = _view_payload(root)
    media = payload["media"]
    assert isinstance(media, list) and isinstance(media[0], dict)
    media[0]["checksum_sha256"] = "f" * 64
    _write_json(root / REPORT_VIEW_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


@pytest.mark.parametrize("mismatch", ["relative_path", "media_key", "entitlement"])
def test_view_and_manifest_media_identity_fields_must_match(tmp_path: Path, mismatch: str):
    root = _build_bundle(tmp_path)
    payload = _view_payload(root)
    media = payload["media"]
    assert isinstance(media, list)
    focus = next(item for item in media if isinstance(item, dict) and item.get("key") == "focus-1")
    if mismatch == "relative_path":
        focus["relative_path"] = "media/renamed-focus.jpg"
    elif mismatch == "media_key":
        focus["key"] = "renamed-focus"
        evidence = payload["visual_evidence"]
        assert isinstance(evidence, dict)
        evidence["media_key"] = "renamed-focus"
    else:
        focus["entitlement"] = "free"
    _write_json(root / REPORT_VIEW_FILENAME, payload)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


@pytest.mark.parametrize("missing_kind", ["report", "report_view", "metrics"])
def test_exactly_one_core_artifact_of_each_kind_is_required(tmp_path: Path, missing_kind: str):
    root = _build_bundle(tmp_path)
    manifest = _manifest_payload(root)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    manifest["artifacts"] = [row for row in artifacts if not (isinstance(row, dict) and row["kind"] == missing_kind)]
    _write_json(root / REPORT_MANIFEST_FILENAME, manifest)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_nonmedia_artifacts_cannot_carry_media_fields(tmp_path: Path):
    root = _build_bundle(tmp_path)
    manifest = _manifest_payload(root)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    report = next(row for row in artifacts if isinstance(row, dict) and row["kind"] == "report")
    report["media_key"] = "focus-1"
    report["entitlement"] = "core"
    _write_json(root / REPORT_MANIFEST_FILENAME, manifest)
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


@pytest.mark.parametrize("target", ["manifest", "checksums"])
def test_manifest_and_checksum_json_reads_are_bounded(tmp_path: Path, target: str):
    root = _build_bundle(tmp_path)
    path = root / (REPORT_MANIFEST_FILENAME if target == "manifest" else REPORT_CHECKSUMS_FILENAME)
    limit = MAX_REPORT_MANIFEST_BYTES if target == "manifest" else MAX_REPORT_CHECKSUMS_BYTES
    path.write_bytes(b"{" + b" " * limit + b"}")

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_report_view_json_read_is_bounded(tmp_path: Path):
    root = _build_bundle(tmp_path)
    (root / REPORT_VIEW_FILENAME).write_bytes(b"{" + b" " * MAX_REPORT_VIEW_BYTES + b"}")
    _refresh_checksums(root)

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


def test_job_bundle_loader_returns_canonical_rels_and_matches_direct_loader(
    tmp_path: Path,
):
    sessions, job_id, analysis, _root, full = _job_bundle_layout(tmp_path)
    direct = load_published_bundle(
        analysis,
        **{
            name: value.removeprefix("out/source/")
            for name, value in full.items()
        },
    )
    expected_rels = tuple(full.values())

    with report_artifacts_module.open_job_published_bundle(
        sessions, job_id=job_id, **full
    ) as pinned:
        assert pinned.report_rels == expected_rels
        assert pinned.bundle.view == direct.view
        assert pinned.bundle.manifest == direct.manifest
        assert pinned.bundle.checksums == direct.checksums
        pinned.verify_lexical_identity()


def test_job_bundle_loader_rejects_redirected_job_root(tmp_path: Path):
    sessions, job_id, _analysis, _root, full = _job_bundle_layout(tmp_path)
    original = sessions / job_id
    target = tmp_path / "donor-job"
    shutil.copytree(original, target)

    with temporary_directory_redirect(tmp_path, original, target):
        with pytest.raises(ReportArtifactValidationError):
            with report_artifacts_module.open_job_published_bundle(
                sessions, job_id=job_id, **full
            ):
                pass


def test_job_bundle_loader_rejects_redirected_out_root(tmp_path: Path):
    sessions, job_id, _analysis, _root, full = _job_bundle_layout(tmp_path)
    original = sessions / job_id / "out"
    target = tmp_path / "donor-out"
    shutil.copytree(original, target)

    with temporary_directory_redirect(tmp_path, original, target):
        with pytest.raises(ReportArtifactValidationError):
            with report_artifacts_module.open_job_published_bundle(
                sessions, job_id=job_id, **full
            ):
                pass


def test_job_bundle_loader_rejects_redirected_analysis_child(tmp_path: Path):
    sessions, job_id, analysis, _root, full = _job_bundle_layout(tmp_path)
    target = tmp_path / "donor-analysis"
    shutil.copytree(analysis, target)

    with temporary_directory_redirect(tmp_path, analysis, target):
        with pytest.raises(ReportArtifactValidationError):
            with report_artifacts_module.open_job_published_bundle(
                sessions, job_id=job_id, **full
            ):
                pass


def test_job_bundle_loader_rejects_redirected_bundle_root(tmp_path: Path):
    sessions, job_id, _analysis, root, full = _job_bundle_layout(tmp_path)
    target = tmp_path / "donor-bundle"
    shutil.copytree(root, target)

    with temporary_directory_redirect(
        tmp_path,
        root,
        target,
        saved_sentinel_rel="report.html",
        target_sentinel_rel=REPORT_VIEW_FILENAME,
    ):
        with pytest.raises(ReportArtifactValidationError):
            with report_artifacts_module.open_job_published_bundle(
                sessions, job_id=job_id, **full
            ):
                pass


@pytest.mark.parametrize(
    "damage",
    [
        "nontext",
        "absolute",
        "empty",
        "dot",
        "parent",
        "backslash",
        "colon",
        "reserved",
        "trailing-dot",
        "trailing-space",
        "nested-analysis",
        "wrong-out-case",
        "wrong-bundle-case",
        "wrong-filename-case",
        "duplicate",
        "cross-child",
        "cross-bundle",
        "noncanonical-filename",
    ],
)
def test_job_bundle_loader_rejects_noncanonical_job_relative_rels(
    tmp_path: Path, damage: str
):
    sessions, job_id, _analysis, root, full = _job_bundle_layout(tmp_path)
    values: list[object] = list(full.values())
    prefix = f"out/source/{root.name}/"
    if damage == "nontext":
        values[0] = Path(str(values[0]))
    elif damage == "absolute":
        values[0] = "/" + str(values[0])
    elif damage == "empty":
        values[0] = ""
    elif damage == "dot":
        values[0] = str(values[0]).replace("out/source/", "out/./source/", 1)
    elif damage == "parent":
        values[0] = str(values[0]).replace("out/source/", "out/source/../", 1)
    elif damage == "backslash":
        values[0] = str(values[0]).replace("out/source/", "out\\source\\", 1)
    elif damage == "colon":
        values[0] = str(values[0]).replace("out/source/", "out/source:/", 1)
    elif damage == "reserved":
        values = [str(value).replace("out/source/", "out/CON/", 1) for value in values]
    elif damage == "trailing-dot":
        values = [str(value).replace("out/source/", "out/source./", 1) for value in values]
    elif damage == "trailing-space":
        values = [str(value).replace("out/source/", "out/source /", 1) for value in values]
    elif damage == "nested-analysis":
        values = [str(value).replace("out/source/", "out/outer/source/", 1) for value in values]
    elif damage == "wrong-out-case":
        values = [str(value).replace("out/", "Out/", 1) for value in values]
    elif damage == "wrong-bundle-case":
        values = [str(value).replace(root.name, root.name.replace("a", "A", 1), 1) for value in values]
    elif damage == "wrong-filename-case":
        values[0] = str(values[0]).replace("report.html", "REPORT.HTML")
    elif damage == "duplicate":
        values[1] = values[0]
    elif damage == "cross-child":
        values[1] = str(values[1]).replace("out/source/", "out/source-2/", 1)
    elif damage == "cross-bundle":
        values[2] = str(values[2]).replace(root.name, "report-bundle-" + "b" * 32, 1)
    else:
        values[0] = prefix + "index.html"

    with pytest.raises(ReportArtifactValidationError):
        with report_artifacts_module.open_job_published_bundle(
            sessions,
            job_id=job_id,
            report_rel=values[0],
            report_view_rel=values[1],
            manifest_rel=values[2],
            checksums_rel=values[3],
        ):
            pass


def test_job_bundle_loader_detects_lexical_replacement_while_pinned(
    tmp_path: Path,
):
    sessions, job_id, analysis, _root, full = _job_bundle_layout(tmp_path)
    original_sentinel = analysis / ".original-analysis-sentinel"
    original_sentinel.write_bytes(b"original analysis\n")
    replacement = tmp_path / "replacement-analysis"
    shutil.copytree(analysis, replacement)
    replacement_sentinel = replacement / ".replacement-analysis-sentinel"
    replacement_sentinel.write_bytes(b"replacement analysis\n")
    saved = analysis.with_name("source.saved-for-replacement")
    moved_original = False
    installed_replacement = False

    with report_artifacts_module.open_job_published_bundle(
        sessions, job_id=job_id, **full
    ) as pinned:
        try:
            try:
                analysis.replace(saved)
                moved_original = True
                replacement.replace(analysis)
                installed_replacement = True
            except PermissionError:
                assert os.name == "nt"
            else:
                with pytest.raises(ReportArtifactValidationError):
                    pinned.verify_lexical_identity()
        finally:
            if installed_replacement:
                analysis.replace(replacement)
            if moved_original:
                saved.replace(analysis)

    assert original_sentinel.read_bytes() == b"original analysis\n"
    assert replacement_sentinel.read_bytes() == b"replacement analysis\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle traversal")
def test_windows_job_bundle_opens_every_component_relative_to_its_pinned_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sessions, job_id, _analysis, root, full = _job_bundle_layout(tmp_path)
    real_absolute_open = report_artifacts_module._win_open
    real_relative_open = report_artifacts_module._win_open_relative
    absolute_calls: list[tuple[Path, bool, int]] = []
    relative_calls: list[tuple[int, str, bool, int]] = []

    def tracking_absolute_open(path: Path, *, directory: bool) -> int:
        handle = real_absolute_open(path, directory=directory)
        absolute_calls.append((path, directory, handle))
        return handle

    def tracking_relative_open(
        parent_handle: int, name: str, *, directory: bool
    ) -> int:
        handle = real_relative_open(parent_handle, name, directory=directory)
        relative_calls.append((parent_handle, name, directory, handle))
        return handle

    monkeypatch.setattr(
        report_artifacts_module, "_win_open", tracking_absolute_open
    )
    monkeypatch.setattr(
        report_artifacts_module, "_win_open_relative", tracking_relative_open
    )

    with report_artifacts_module.open_job_published_bundle(
        sessions, job_id=job_id, **full
    ) as pinned:
        pinned.verify_lexical_identity()

    assert absolute_calls
    assert all(
        path == sessions and directory
        for path, directory, _handle in absolute_calls
    )
    first_chain = relative_calls[:4]
    assert [(name, directory) for _parent, name, directory, _child in first_chain] == [
        (job_id, True),
        ("out", True),
        ("source", True),
        (root.name, True),
    ]
    assert first_chain[0][0] == absolute_calls[0][2]
    assert all(
        first_chain[index][0] == first_chain[index - 1][3]
        for index in range(1, len(first_chain))
    )
    direct_file_calls = {
        name
        for parent, name, directory, _handle in relative_calls
        if parent == first_chain[-1][3] and not directory
    }
    assert {
        "report.html",
        REPORT_VIEW_FILENAME,
        REPORT_MANIFEST_FILENAME,
        REPORT_CHECKSUMS_FILENAME,
    }.issubset(direct_file_calls)


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle traversal")
def test_windows_relative_children_ignore_injected_lexical_ancestor_redirection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sessions, job_id, _analysis, _root, full = _job_bundle_layout(tmp_path)
    donor_sessions = tmp_path / "redirected-sessions"
    shutil.copytree(sessions, donor_sessions)
    donor_report = next(donor_sessions.rglob("report.html"))
    donor_report.write_bytes(b"foreign report bytes\n")
    real_absolute_open = report_artifacts_module._win_open
    real_relative_open = report_artifacts_module._win_open_relative
    absolute_calls: list[Path] = []
    relative_calls: list[tuple[int, str, bool]] = []

    def redirected_absolute_open(path: Path, *, directory: bool) -> int:
        absolute_calls.append(path)
        if path == sessions:
            return real_absolute_open(path, directory=directory)
        relative = path.relative_to(sessions)
        return real_absolute_open(donor_sessions / relative, directory=directory)

    def tracking_relative_open(
        parent_handle: int, name: str, *, directory: bool
    ) -> int:
        relative_calls.append((parent_handle, name, directory))
        return real_relative_open(parent_handle, name, directory=directory)

    monkeypatch.setattr(
        report_artifacts_module, "_win_open", redirected_absolute_open
    )
    monkeypatch.setattr(
        report_artifacts_module, "_win_open_relative", tracking_relative_open
    )

    with report_artifacts_module.open_job_published_bundle(
        sessions, job_id=job_id, **full
    ) as pinned:
        assert pinned.bundle.report_path.read_bytes() != donor_report.read_bytes()

    assert absolute_calls
    assert all(path == sessions for path in absolute_calls)
    assert relative_calls


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle traversal")
def test_windows_handle_enumeration_is_case_exact_and_not_path_redirectable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    original = tmp_path / "original"
    donor = tmp_path / "donor"
    original.mkdir()
    donor.mkdir()
    (original / "Exact.txt").write_bytes(b"original\n")
    (donor / "foreign.txt").write_bytes(b"foreign\n")
    real_scandir = report_artifacts_module.os.scandir
    path_scans: list[object] = []

    def redirected_path_scan(path):
        path_scans.append(path)
        return real_scandir(donor)

    handle = report_artifacts_module._win_open(original, directory=True)
    monkeypatch.setattr(report_artifacts_module.os, "scandir", redirected_path_scan)
    try:
        rows = report_artifacts_module._win_scan_directory(handle, limit=16)
        assert [row.name for row in rows] == ["Exact.txt"]

        def reject_relative_open_enumeration(_handle: int):
            raise AssertionError("relative child open enumerated its parent")

        monkeypatch.setattr(
            report_artifacts_module,
            "_win_iter_directory",
            reject_relative_open_enumeration,
        )
        child = report_artifacts_module._win_open_relative(
            handle,
            "Exact.txt",
            directory=False,
        )
        try:
            assert os.path.samefile(
                report_artifacts_module._win_final_path(child),
                original / "Exact.txt",
            )
        finally:
            report_artifacts_module._win_close(child)
        with pytest.raises(OSError):
            child = report_artifacts_module._win_open_relative(
                handle,
                "exact.txt",
                directory=False,
            )
            report_artifacts_module._win_close(child)
    finally:
        report_artifacts_module._win_close(handle)

    assert path_scans == []


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle traversal")
def test_windows_relative_open_finds_exact_child_beyond_topology_scan_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    parent = tmp_path / "large-parent"
    parent.mkdir()
    for index in range(report_artifacts_module._MAX_DIRECTORY_ENTRIES + 1):
        (parent / f"a-{index:04d}.tmp").touch()
    target = parent / "z-exact-target"
    target.mkdir()

    parent_handle = report_artifacts_module._win_open(parent, directory=True)
    child_handle: int | None = None
    try:
        with pytest.raises(
            ReportArtifactValidationError,
            match="too many filesystem entries",
        ):
            report_artifacts_module._win_scan_directory(
                parent_handle,
                limit=report_artifacts_module._MAX_DIRECTORY_ENTRIES,
            )

        def reject_relative_open_enumeration(_handle: int):
            raise AssertionError("relative child open enumerated its parent")

        monkeypatch.setattr(
            report_artifacts_module,
            "_win_iter_directory",
            reject_relative_open_enumeration,
        )
        child_handle = report_artifacts_module._win_open_relative(
            parent_handle,
            target.name,
            directory=True,
        )
        assert os.path.samefile(
            report_artifacts_module._win_final_path(child_handle),
            target,
        )
        with pytest.raises(FileNotFoundError):
            report_artifacts_module._win_open_relative(
                parent_handle,
                "z-missing-target",
                directory=True,
            )
    finally:
        if child_handle is not None:
            report_artifacts_module._win_close(child_handle)
        report_artifacts_module._win_close(parent_handle)


def test_job_bundle_context_exit_is_cleanup_only_after_explicit_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sessions, job_id, _analysis, _root, full = _job_bundle_layout(tmp_path)
    real_verify = report_artifacts_module._PinnedJobDirectoryChain.verify_lexical_identity
    calls = 0

    def reject_exit_time_verify(chain) -> None:
        nonlocal calls
        calls += 1
        if calls > 2:
            raise ReportArtifactValidationError("unexpected exit-time verification")
        real_verify(chain)

    monkeypatch.setattr(
        report_artifacts_module._PinnedJobDirectoryChain,
        "verify_lexical_identity",
        reject_exit_time_verify,
    )

    with report_artifacts_module.open_job_published_bundle(
        sessions, job_id=job_id, **full
    ) as pinned:
        pinned.verify_lexical_identity()

    assert calls == 2


def test_job_bundle_context_exit_does_not_raise_after_cleanup_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sessions, job_id, _analysis, _root, full = _job_bundle_layout(tmp_path)
    real_close = report_artifacts_module._close_directory_handle
    cleanup_armed = False
    closed: list[int] = []

    def close_then_fail(handle: int) -> None:
        real_close(handle)
        if cleanup_armed:
            closed.append(handle)
            raise OSError("injected cleanup close failure")

    monkeypatch.setattr(
        report_artifacts_module,
        "_close_directory_handle",
        close_then_fail,
    )

    with report_artifacts_module.open_job_published_bundle(
        sessions, job_id=job_id, **full
    ) as pinned:
        pinned.verify_lexical_identity()
        cleanup_armed = True

    assert len(closed) == 5


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor ownership")
def test_posix_fdopen_failure_closes_owned_descriptor_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _build_bundle(tmp_path)
    descriptor: int | None = None
    real_close = os.close
    closed: list[int] = []

    def tracking_close(candidate: int) -> None:
        if descriptor is not None and candidate == descriptor:
            closed.append(candidate)
        real_close(candidate)

    def fail_fdopen(candidate: int, mode: str):
        nonlocal descriptor
        descriptor = candidate
        assert mode == "rb"
        raise OSError("injected fdopen failure")

    with report_artifacts_module._PinnedBundleRoot(root) as pinned:
        monkeypatch.setattr(report_artifacts_module.os, "close", tracking_close)
        monkeypatch.setattr(report_artifacts_module.os, "fdopen", fail_fdopen)
        try:
            with pytest.raises(
                ReportArtifactValidationError,
                match="cannot be opened safely",
            ):
                with pinned.open_file(PurePosixPath("report.html")):
                    pass
        finally:
            if descriptor is not None:
                try:
                    os.fstat(descriptor)
                except OSError:
                    pass
                else:
                    real_close(descriptor)

    assert descriptor is not None
    assert closed == [descriptor]


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT handle ownership")
def test_windows_fdopen_failure_closes_transferred_handle_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _build_bundle(tmp_path)
    real_transfer = report_artifacts_module.msvcrt.open_osfhandle
    real_close = report_artifacts_module.os.close
    transferred: list[tuple[int, int]] = []
    closed: list[int] = []

    def tracking_transfer(handle: int, flags: int) -> int:
        descriptor = real_transfer(handle, flags)
        transferred.append((handle, descriptor))
        return descriptor

    def fail_fdopen(descriptor: int, mode: str):
        assert transferred and descriptor == transferred[-1][1]
        assert mode == "rb"
        raise OSError("injected Windows fdopen failure")

    def tracking_close(descriptor: int) -> None:
        if transferred and descriptor == transferred[-1][1]:
            closed.append(descriptor)
        real_close(descriptor)

    with report_artifacts_module._PinnedBundleRoot(root) as pinned:
        monkeypatch.setattr(
            report_artifacts_module.msvcrt,
            "open_osfhandle",
            tracking_transfer,
        )
        monkeypatch.setattr(report_artifacts_module.os, "fdopen", fail_fdopen)
        monkeypatch.setattr(report_artifacts_module.os, "close", tracking_close)
        try:
            with pytest.raises(
                ReportArtifactValidationError,
                match="cannot be opened safely",
            ):
                with pinned.open_file(PurePosixPath("report.html")):
                    pass
        finally:
            if transferred:
                descriptor = transferred[-1][1]
                try:
                    os.fstat(descriptor)
                except OSError:
                    pass
                else:
                    real_close(descriptor)

    assert len(transferred) == 1
    assert closed == [transferred[0][1]]


def test_job_bundle_loader_closes_partial_handle_chain_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sessions, job_id, _analysis, _root, full = _job_bundle_layout(tmp_path)
    opened: list[int] = []
    closed: list[int] = []

    if os.name == "nt":
        real_absolute_open = report_artifacts_module._win_open
        real_relative_open = report_artifacts_module._win_open_relative
        real_close = report_artifacts_module._win_close
        calls = 0

        def track_open(open_call, *args, **kwargs) -> int:
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("injected partial job-chain open failure")
            handle = open_call(*args, **kwargs)
            opened.append(handle)
            return handle

        def tracking_absolute_open(path: Path, *, directory: bool) -> int:
            return track_open(real_absolute_open, path, directory=directory)

        def tracking_relative_open(
            parent_handle: int, name: str, *, directory: bool
        ) -> int:
            return track_open(
                real_relative_open,
                parent_handle,
                name,
                directory=directory,
            )

        def tracking_close(handle: int) -> None:
            closed.append(handle)
            real_close(handle)

        monkeypatch.setattr(
            report_artifacts_module, "_win_open", tracking_absolute_open
        )
        monkeypatch.setattr(
            report_artifacts_module, "_win_open_relative", tracking_relative_open
        )
        monkeypatch.setattr(report_artifacts_module, "_win_close", tracking_close)
    else:
        real_open = os.open
        real_close = os.close
        calls = 0

        def tracking_open(
            path: str | bytes | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("injected partial job-chain open failure")
            handle = real_open(path, flags, mode, dir_fd=dir_fd)
            opened.append(handle)
            return handle

        def tracking_close(handle: int) -> None:
            closed.append(handle)
            real_close(handle)

        monkeypatch.setattr(report_artifacts_module.os, "open", tracking_open)
        monkeypatch.setattr(report_artifacts_module.os, "close", tracking_close)
        monkeypatch.setattr(
            report_artifacts_module.os,
            "supports_dir_fd",
            set(os.supports_dir_fd) | {tracking_open},
        )

    try:
        with pytest.raises(ReportArtifactValidationError):
            with report_artifacts_module.open_job_published_bundle(
                sessions, job_id=job_id, **full
            ):
                pass
    finally:
        leaked = [handle for handle in opened if closed.count(handle) == 0]
        for handle in leaked:
            real_close(handle)

    assert opened
    assert all(closed.count(handle) == 1 for handle in opened)


@pytest.mark.skipif(os.name == "nt", reason="POSIX capability gate")
@pytest.mark.parametrize("missing", ["O_DIRECTORY", "O_NOFOLLOW", "dir_fd"])
def test_job_bundle_loader_fails_closed_without_posix_directory_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
):
    sessions, job_id, _analysis, _root, full = _job_bundle_layout(tmp_path)
    if missing in ("O_DIRECTORY", "O_NOFOLLOW"):
        monkeypatch.setattr(report_artifacts_module.os, missing, 0, raising=False)
    else:
        monkeypatch.setattr(report_artifacts_module.os, "supports_dir_fd", set())

    with pytest.raises(ReportArtifactValidationError):
        with report_artifacts_module.open_job_published_bundle(
            sessions, job_id=job_id, **full
        ):
            pass


@pytest.mark.parametrize("field", ["report_rel", "report_view_rel", "manifest_rel", "checksums_rel"])
@pytest.mark.parametrize(
    "unsafe",
    ["/outside", "../outside", "C:/outside", "folder\\outside", "./outside"],
)
def test_all_four_published_relative_paths_use_the_single_safe_parser(
    tmp_path: Path,
    field: str,
    unsafe: str,
):
    root = _build_bundle(tmp_path)
    rels = _persisted_rels(root)
    rels[field] = unsafe

    with pytest.raises(ReportArtifactValidationError):
        load_published_bundle(root.parent, **rels)


@pytest.mark.skipif(os.name != "nt", reason="Windows path aliases are Windows-only")
@pytest.mark.parametrize("field", ["report_rel", "report_view_rel", "manifest_rel", "checksums_rel"])
def test_published_paths_reject_real_case_aliases(tmp_path: Path, field: str):
    root = _build_bundle(tmp_path, root_name="report-bundle-AbCdEf0123456789abcdef0123456789")
    rels = _persisted_rels(root)
    rels[field] = rels[field].replace(root.name, root.name.swapcase(), 1)

    with pytest.raises(ReportArtifactValidationError):
        load_published_bundle(root.parent, **rels)


@pytest.mark.parametrize("field", ["report_rel", "report_view_rel", "manifest_rel", "checksums_rel"])
def test_all_four_published_paths_must_refer_to_one_bundle_root(tmp_path: Path, field: str):
    root = _build_bundle(tmp_path)
    other = tmp_path / "report-bundle-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    other.mkdir()
    source_name = {
        "report_rel": "report.html",
        "report_view_rel": REPORT_VIEW_FILENAME,
        "manifest_rel": REPORT_MANIFEST_FILENAME,
        "checksums_rel": REPORT_CHECKSUMS_FILENAME,
    }[field]
    (other / source_name).write_bytes((root / source_name).read_bytes())
    rels = _persisted_rels(root)
    rels[field] = f"{other.name}/{source_name}"

    with pytest.raises(ReportArtifactValidationError):
        load_published_bundle(root.parent, **rels)


@pytest.mark.parametrize(
    "root_name",
    [
        ".report-attempt-" + "a" * 32,
        "published-report",
        "report-bundle-" + "b" * 32,
    ],
)
def test_published_lookup_requires_direct_canonical_root_bound_to_manifest_attempt(
    tmp_path: Path, root_name: str
):
    root = _build_bundle(tmp_path, root_name=root_name)

    with pytest.raises(ReportArtifactValidationError):
        load_published_bundle(tmp_path, **_persisted_rels(root))


def test_published_lookup_rejects_nested_canonical_final_root(tmp_path: Path):
    nested_parent = tmp_path / "arbitrary-parent"
    nested_parent.mkdir()
    root = _build_bundle(nested_parent)
    rels = {
        key: f"{nested_parent.name}/{value}"
        for key, value in _persisted_rels(root).items()
    }

    with pytest.raises(ReportArtifactValidationError):
        load_published_bundle(tmp_path, **rels)


def test_published_paths_must_match_manifest_declared_canonical_identities(tmp_path: Path):
    root = _build_bundle(tmp_path)
    rels = _persisted_rels(root)
    rels["report_rel"], rels["report_view_rel"] = rels["report_view_rel"], rels["report_rel"]

    with pytest.raises(ReportArtifactValidationError):
        load_published_bundle(root.parent, **rels)


def test_published_lookup_performs_full_bundle_validation(tmp_path: Path):
    root = _build_bundle(tmp_path)
    (root / "metrics.json").write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(ReportArtifactValidationError):
        _load_bundle(root)


def test_published_load_rejects_bundle_root_replacement_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _build_bundle(tmp_path)
    replacement = tmp_path / "replacement-bundle"
    shutil.copytree(root, replacement)
    validated_original = tmp_path / "validated-original"
    original_validate = report_artifacts_module.validate_staged_bundle
    swapped = False

    def validate_then_swap(*args: object, **kwargs: object):
        nonlocal swapped
        result = original_validate(*args, **kwargs)
        try:
            root.replace(validated_original)
            replacement.replace(root)
        except PermissionError as exc:
            raise ReportArtifactValidationError(
                "the pinned root blocked lexical replacement"
            ) from exc
        swapped = True
        return result

    monkeypatch.setattr(
        report_artifacts_module, "validate_staged_bundle", validate_then_swap
    )

    with pytest.raises(ReportArtifactValidationError):
        _load_bundle(root)
    if os.name != "nt":
        assert swapped


def test_published_lookup_returns_validated_paths_and_parsed_contracts(tmp_path: Path):
    root = _build_bundle(tmp_path)

    bundle = _load_bundle(root)

    assert bundle.root == root.resolve()
    assert bundle.report_path == (root / "report.html").resolve()
    assert bundle.report_view_path == (root / REPORT_VIEW_FILENAME).resolve()
    assert bundle.manifest_path == (root / REPORT_MANIFEST_FILENAME).resolve()
    assert bundle.checksums_path == (root / REPORT_CHECKSUMS_FILENAME).resolve()
    assert bundle.view.outcome == bundle.manifest.outcome


def test_resolve_media_path_accepts_only_an_exact_known_media_key(tmp_path: Path):
    root = _build_bundle(tmp_path)
    bundle = _load_bundle(root)

    path = resolve_media_path(bundle, "focus-1")

    assert path == (root / "media" / "focus-1.jpg").resolve()
    with pytest.raises(ReportArtifactValidationError):
        resolve_media_path(bundle, "unknown")


def test_resolve_media_path_rejects_duplicate_view_keys_even_for_forged_bundle(tmp_path: Path):
    root = _build_bundle(tmp_path)
    bundle = _load_bundle(root)
    entry = bundle.view.media[0]
    forged_view = replace(bundle.view, media=(entry, entry, *bundle.view.media[1:]))

    with pytest.raises(ReportArtifactValidationError):
        resolve_media_path(replace(bundle, view=forged_view), entry.key)


def test_resolve_media_path_rejects_a_key_attached_to_a_nonmedia_manifest_entry(tmp_path: Path):
    root = _build_bundle(tmp_path)
    bundle = _load_bundle(root)
    artifacts = tuple(
        replace(row, media_key="focus-1", entitlement=Entitlement.CORE)
        if row.kind == "report"
        else row
        for row in bundle.manifest.artifacts
        if not (row.kind == "media" and row.media_key == "focus-1")
    )
    forged = replace(bundle, manifest=replace(bundle.manifest, artifacts=artifacts))

    with pytest.raises(ReportArtifactValidationError):
        resolve_media_path(forged, "focus-1")


def test_resolve_media_path_rejects_post_load_replacement(tmp_path: Path):
    root = _build_bundle(tmp_path)
    bundle = _load_bundle(root)
    media = root / "media" / "focus-1.jpg"
    replacement = tmp_path / "replacement.jpg"
    replacement.write_bytes(media.read_bytes())
    os.replace(replacement, media)

    with pytest.raises(ReportArtifactValidationError):
        resolve_media_path(bundle, "focus-1")


def test_resolve_media_path_rejects_post_load_symlink_replacement(tmp_path: Path):
    root = _build_bundle(tmp_path)
    bundle = _load_bundle(root)
    media = root / "media" / "focus-1.jpg"
    outside = tmp_path / "same-media.jpg"
    outside.write_bytes(media.read_bytes())
    media.unlink()
    _symlink_or_skip(media, outside)

    with pytest.raises(ReportArtifactValidationError):
        resolve_media_path(bundle, "focus-1")
