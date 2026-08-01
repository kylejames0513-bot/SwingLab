"""Manifest-driven, sequential analysis batches.

The original ``swinglab analyze folder --batch`` command deliberately stays
small and discovery-oriented.  This module is the operator-facing path for a
repeatable set of named clips: every JSONL row declares the context that is
stored in that clip's report, and a small local state file makes an interrupted
run restartable without turning the analysis worker into a queue.

There is intentionally no parallelism here.  A single analysis can be CPU and
disk intensive, and serial execution keeps output ordering, failure handling,
and the state transition easy to inspect.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .clubs import CLUB_LABELS
from .config import Config
from .events import EventError
from .ffmpeg import FFmpegError
from .levels import LEVEL_LABELS
from .metrics import ANGLES
from .pipeline import SessionResult, VideoTooLongError, ZeroStrikesError


VIDEO_SUFFIXES = frozenset({".mov", ".mp4", ".m4v", ".avi", ".mkv"})
STATE_FORMAT = "caddieinsight.batch-v2-state.v1"
SUMMARY_FORMAT = "caddieinsight.batch-v2-summary.v1"


class BatchManifestError(ValueError):
    """The JSONL manifest is malformed or describes an unsafe batch."""


class BatchStateError(BatchManifestError):
    """The local resume state is malformed or incompatible with the manifest."""


@dataclass(frozen=True)
class BatchItem:
    """One fully validated analysis instruction from a manifest row."""

    id: str
    line: int
    path: Path
    hand: str
    angle: str
    club: str | None
    level: str | None
    strikes: tuple[float, ...] | None
    source_size: int
    source_mtime_ns: int
    fingerprint: str

    def as_plan(self) -> dict[str, Any]:
        """A stable, JSON-safe view suitable for dry runs and summaries."""

        return {
            "id": self.id,
            "line": self.line,
            "path": str(self.path),
            "hand": self.hand,
            "angle": self.angle,
            "club": self.club,
            "level": self.level,
            "strikes": list(self.strikes) if self.strikes is not None else None,
        }


@dataclass(frozen=True)
class BatchManifest:
    path: Path
    items: tuple[BatchItem, ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _line_error(line: int, message: str) -> BatchManifestError:
    return BatchManifestError(f"manifest line {line}: {message}")


def _require_string(value: Any, *, line: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _line_error(line, f'"{field}" must be a non-empty string')
    if value != value.strip():
        raise _line_error(line, f'"{field}" cannot start or end with whitespace')
    return value


def _optional_choice(
    value: Any,
    *,
    line: int,
    field: str,
    allowed: dict[str, str],
) -> str | None:
    if value is None:
        return None
    text = _require_string(value, line=line, field=field)
    if text not in allowed:
        choices = ", ".join(sorted(allowed))
        raise _line_error(line, f'"{field}" must be one of: {choices}')
    return text


def _parse_strikes(value: Any, *, line: int) -> tuple[float, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise _line_error(line, '"strikes" must be a non-empty array of seconds')

    strikes: list[float] = []
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _line_error(line, f'"strikes" item {index} must be a number')
        strike = float(raw)
        if not math.isfinite(strike) or strike < 0:
            raise _line_error(
                line,
                f'"strikes" item {index} must be a finite, non-negative second',
            )
        if strikes and strike <= strikes[-1]:
            raise _line_error(
                line,
                '"strikes" must be strictly increasing (one time per swing)',
            )
        strikes.append(strike)
    return tuple(strikes)


def _item_fingerprint(
    *,
    item_id: str,
    path: Path,
    hand: str,
    angle: str,
    club: str | None,
    level: str | None,
    strikes: tuple[float, ...] | None,
    source_size: int,
    source_mtime_ns: int,
) -> str:
    """Fingerprint only the normalized, execution-relevant instruction."""

    payload = {
        "id": item_id,
        "path": str(path),
        "hand": hand,
        "angle": angle,
        "club": club,
        "level": level,
        "strikes": list(strikes) if strikes is not None else None,
        # Full video hashing would make a dry run surprisingly expensive.
        # The file signature catches the normal edit/re-export case and fails
        # closed on resume; a changed source is never silently treated as the
        # clip that produced an old report.
        "source_size": source_size,
        "source_mtime_ns": source_mtime_ns,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_item(raw: Any, *, line: int, manifest_dir: Path) -> BatchItem:
    if not isinstance(raw, dict):
        raise _line_error(line, "each JSONL value must be an object")

    allowed = {"id", "path", "hand", "angle", "club", "level", "strikes"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _line_error(line, "unknown field(s): " + ", ".join(unknown))
    missing = sorted({"id", "path"} - set(raw))
    if missing:
        raise _line_error(line, "missing required field(s): " + ", ".join(missing))

    item_id = _require_string(raw["id"], line=line, field="id")
    raw_path = _require_string(raw["path"], line=line, field="path")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = manifest_dir / candidate
    path = candidate.resolve()
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        suffixes = ", ".join(sorted(VIDEO_SUFFIXES))
        raise _line_error(line, f'"path" must name a supported video ({suffixes})')
    if not path.is_file():
        raise _line_error(line, f'"path" does not name a file: {path}')
    try:
        source_stat = path.stat()
    except OSError as exc:
        raise _line_error(line, f'could not inspect "path": {path}') from exc

    hand = raw.get("hand", "right")
    if not isinstance(hand, str) or hand not in {"right", "left"}:
        raise _line_error(line, '"hand" must be "right" or "left"')
    angle = raw.get("angle", "face-on")
    if not isinstance(angle, str) or angle not in ANGLES:
        raise _line_error(line, '"angle" must be "face-on" or "dtl"')
    club = _optional_choice(
        raw.get("club"), line=line, field="club", allowed=CLUB_LABELS
    )
    level = _optional_choice(
        raw.get("level"), line=line, field="level", allowed=LEVEL_LABELS
    )
    strikes = _parse_strikes(raw.get("strikes"), line=line)

    return BatchItem(
        id=item_id,
        line=line,
        path=path,
        hand=hand,
        angle=angle,
        club=club,
        level=level,
        strikes=strikes,
        source_size=source_stat.st_size,
        source_mtime_ns=source_stat.st_mtime_ns,
        fingerprint=_item_fingerprint(
            item_id=item_id,
            path=path,
            hand=hand,
            angle=angle,
            club=club,
            level=level,
            strikes=strikes,
            source_size=source_stat.st_size,
            source_mtime_ns=source_stat.st_mtime_ns,
        ),
    )


def load_manifest(path: str | Path) -> BatchManifest:
    """Read and validate a whole JSONL manifest before any analysis starts."""

    manifest_path = Path(path).resolve()
    if not manifest_path.is_file():
        raise BatchManifestError(f"manifest does not name a file: {manifest_path}")
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise BatchManifestError("manifest must be UTF-8 JSONL") from exc
    except OSError as exc:
        raise BatchManifestError(f"could not read manifest: {manifest_path}") from exc

    items: list[BatchItem] = []
    seen_ids: dict[str, int] = {}
    for line_number, text in enumerate(lines, start=1):
        if not text.strip():
            continue
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _line_error(line_number, f"invalid JSON: {exc.msg}") from exc
        item = _parse_item(raw, line=line_number, manifest_dir=manifest_path.parent)
        if item.id in seen_ids:
            raise _line_error(
                line_number,
                f'"id" {item.id!r} already appeared on line {seen_ids[item.id]}',
            )
        seen_ids[item.id] = line_number
        items.append(item)

    if not items:
        raise BatchManifestError("manifest contains no clip rows")
    return BatchManifest(path=manifest_path, items=tuple(items))


def default_state_path(manifest_path: str | Path) -> Path:
    manifest = Path(manifest_path).resolve()
    return manifest.with_name(manifest.name + ".state.json")


def _new_state() -> dict[str, Any]:
    return {"format": STATE_FORMAT, "completed": {}}


def load_state(path: str | Path) -> dict[str, Any]:
    """Load a state file strictly; a corrupt resume record must not be guessed."""

    state_path = Path(path).resolve()
    if not state_path.exists():
        return _new_state()
    if not state_path.is_file():
        raise BatchStateError(f"state path is not a file: {state_path}")
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise BatchStateError(f"state file is not valid JSON: {state_path}") from exc
    if not isinstance(raw, dict) or raw.get("format") != STATE_FORMAT:
        raise BatchStateError(f"state file has an unsupported format: {state_path}")
    completed = raw.get("completed")
    if not isinstance(completed, dict):
        raise BatchStateError(f"state file has no valid completed map: {state_path}")
    for item_id, record in completed.items():
        if not isinstance(item_id, str) or not isinstance(record, dict):
            raise BatchStateError(f"state file has an invalid completed record: {state_path}")
        if not isinstance(record.get("fingerprint"), str):
            raise BatchStateError(f"state file has an invalid fingerprint: {state_path}")
        if not isinstance(record.get("report_path"), str):
            raise BatchStateError(f"state file has an invalid report path: {state_path}")
    return raw


def write_state_atomic(path: str | Path, state: dict[str, Any]) -> None:
    """Durably replace state only after one clip has completed successfully."""

    state_path = Path(path).resolve()
    parent = state_path.parent
    if not parent.is_dir():
        raise BatchStateError(f"state directory does not exist: {parent}")
    encoded = (_canonical_json(state) + "\n").encode("utf-8")
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{state_path.name}.", suffix=".tmp", dir=parent,
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, state_path)
        temp_name = None
    except OSError as exc:
        raise BatchStateError(f"could not atomically write state: {state_path}") from exc
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _state_record(item: BatchItem, result: SessionResult) -> dict[str, Any]:
    return {
        "fingerprint": item.fingerprint,
        "line": item.line,
        "report_path": str(result.report_path.resolve()),
    }


def _state_allows_resume(item: BatchItem, state: dict[str, Any]) -> bool:
    record = state["completed"].get(item.id)
    if record is None:
        return False
    if record["fingerprint"] != item.fingerprint:
        raise BatchStateError(
            f'completed item {item.id!r} no longer matches this manifest; '
            "use a new --state path after reviewing the change"
        )
    # A stale state record must never hide a missing report.  Re-running is
    # safer than declaring an artifact delivered when it no longer exists.
    return Path(record["report_path"]).is_file()


def _source_is_unchanged(item: BatchItem) -> bool:
    """Confirm the exact source inspected during the all-row preflight remains."""

    try:
        current = item.path.stat()
    except OSError:
        return False
    return (
        item.path.is_file()
        and current.st_size == item.source_size
        and current.st_mtime_ns == item.source_mtime_ns
    )


def _empty_summary(
    manifest: BatchManifest,
    state_path: Path,
    *,
    dry_run: bool,
    resume: bool,
) -> dict[str, Any]:
    return {
        "format": SUMMARY_FORMAT,
        "manifest": str(manifest.path),
        "state": str(state_path),
        "dry_run": dry_run,
        "resume": resume,
        "total": len(manifest.items),
        "planned": 0,
        "completed": 0,
        "resumed": 0,
        "failed": 0,
        "items": [],
    }


def run_manifest_batch(
    manifest_path: str | Path,
    *,
    cfg: Config,
    out_dir: Path | None,
    keep_work: bool,
    fast: bool,
    dry_run: bool,
    resume: bool,
    state_path: Path | None,
    analyze: Callable[..., SessionResult],
    on_result: Callable[[SessionResult], None] | None,
    log: Callable[[str], None],
    error: Callable[[str], None],
) -> tuple[int, dict[str, Any]]:
    """Run a validated manifest sequentially and return ``(exit_code, summary)``.

    ``analyze`` and the output callbacks are injected by the CLI so JSON mode
    can keep stdout machine-readable while pipeline progress goes to stderr.
    """

    manifest = load_manifest(manifest_path)
    resolved_state_path = (
        Path(state_path).resolve() if state_path is not None
        else default_state_path(manifest.path)
    )
    state = load_state(resolved_state_path)
    summary = _empty_summary(
        manifest, resolved_state_path, dry_run=dry_run, resume=resume
    )
    # Prove the local state target is writable before a potentially expensive
    # analysis begins.  This also creates an explicit empty checkpoint for a
    # first run; dry-run remains genuinely read-only.
    if not dry_run:
        write_state_atomic(resolved_state_path, state)

    for item in manifest.items:
        plan = item.as_plan()
        can_resume = resume and _state_allows_resume(item, state)
        if can_resume:
            plan["status"] = "resumed"
            summary["resumed"] += 1
            summary["items"].append(plan)
            if not dry_run:
                log(f"RESUMED {item.id}: {item.path.name}")
            continue

        if dry_run:
            plan["status"] = "planned"
            summary["planned"] += 1
            summary["items"].append(plan)
            continue

        if not _source_is_unchanged(item):
            plan["status"] = "failed"
            plan["error"] = "source changed after manifest validation; re-run the batch"
            summary["failed"] += 1
            summary["items"].append(plan)
            error(f"FAILED {item.id}: {plan['error']}")
            continue

        log(f"\n=== {item.id}: {item.path.name} ===")
        try:
            result = analyze(
                item.path,
                out_dir=out_dir,
                hand=item.hand,
                manual_strikes=list(item.strikes) if item.strikes is not None else None,
                cfg=cfg,
                keep_work=keep_work,
                fast=fast,
                log=log,
                angle=item.angle,
                club=item.club,
                level=item.level,
            )
        except (ZeroStrikesError, VideoTooLongError, EventError, FFmpegError) as exc:
            plan["status"] = "failed"
            plan["error"] = str(exc)
            summary["failed"] += 1
            summary["items"].append(plan)
            error(f"FAILED {item.id}: {exc}")
            continue

        state["completed"][item.id] = _state_record(item, result)
        write_state_atomic(resolved_state_path, state)
        plan["status"] = "completed"
        plan["report_path"] = str(result.report_path.resolve())
        summary["completed"] += 1
        summary["items"].append(plan)
        if on_result is not None:
            on_result(result)

    return (1 if summary["failed"] else 0), summary
