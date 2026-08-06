from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

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
    validate_staged_bundle,
    write_report_checksums,
    write_report_manifest,
)
from swinglab.report_view import (
    Entitlement,
    GUIDED_REPORT_PRESENTATION_VERSION,
    MAX_REPORT_VIEW_BYTES,
    MediaRole,
    ReportOutcome,
    report_view_from_dict,
    write_report_view,
)
from tests.report_view_fixtures import report_view_payload


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
    root_name: str = "report-bundle-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
) -> Path:
    root = tmp_path / root_name
    root.mkdir()
    (root / "media").mkdir()
    (root / "report.html").write_text("<html>guided report</html>\n", encoding="utf-8")
    (root / "metrics.json").write_text('{"swings":[]}\n', encoding="utf-8")

    view_payload = payload if payload is not None else report_view_payload()
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


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")


def test_entitlement_snapshot_has_canonical_json_round_trip_and_strict_enum():
    snapshot = ReportEntitlementSnapshot("locked")

    encoded = report_entitlements_to_json(snapshot)

    assert encoded == '{"coach_replay":"locked"}\n'
    assert report_entitlements_from_json(encoded) == snapshot
    with pytest.raises(ReportArtifactValidationError):
        report_entitlements_from_json('{"coach_replay":"future"}\n')
    with pytest.raises(ReportArtifactValidationError):
        report_entitlements_from_json('{"coach_replay":"locked","extra":true}\n')


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


def test_undeclared_regular_file_is_rejected(tmp_path: Path):
    root = _build_bundle(tmp_path)
    (root / "private-debug.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ReportArtifactValidationError):
        validate_staged_bundle(root, manifest_rel=REPORT_MANIFEST_FILENAME, checksums_rel=REPORT_CHECKSUMS_FILENAME)


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


def test_locked_unrendered_replay_cannot_be_declared_as_a_file(tmp_path: Path):
    payload = report_view_payload()
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
