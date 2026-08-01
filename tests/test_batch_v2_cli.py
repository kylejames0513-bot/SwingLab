"""Focused contract tests for the sequential JSONL batch command."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import swinglab.cli as cli_module
from swinglab.cli import main


def _video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not-a-real-video")
    return path


def _manifest(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def _fake_analyzer(calls: list[tuple[Path, dict]]):
    def fake(video_path, **kwargs):
        video = Path(video_path)
        calls.append((video, kwargs))
        report = Path(kwargs["out_dir"]) / f"{video.stem}-{len(calls)}" / "report.html"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("<html>synthetic report</html>", encoding="utf-8")
        return SimpleNamespace(report_path=report, swings=[], stats={}, skipped=[])

    return fake


def test_manifest_dry_run_is_json_and_does_not_analyze_or_write_state(
    tmp_path, monkeypatch, capsys
):
    clips = _video(tmp_path / "clips" / "driver.mov")
    manifest = _manifest(
        tmp_path / "clips.jsonl",
        [
            {
                "id": "driver-baseline",
                "path": "clips/driver.mov",
                "hand": "left",
                "angle": "dtl",
                "club": "driver",
                "level": "experienced",
                "strikes": [12.5, 31.0],
            }
        ],
    )
    calls: list[tuple[Path, dict]] = []
    monkeypatch.setattr(cli_module, "analyze_video", _fake_analyzer(calls))

    assert main(["batch", str(manifest), "--dry-run", "--json"]) == 0

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert captured.err == ""
    assert calls == []
    assert summary["dry_run"] is True
    assert summary["planned"] == 1
    assert summary["completed"] == 0
    assert summary["items"] == [
        {
            "id": "driver-baseline",
            "line": 1,
            "path": str(clips.resolve()),
            "hand": "left",
            "angle": "dtl",
            "club": "driver",
            "level": "experienced",
            "strikes": [12.5, 31.0],
            "status": "planned",
        }
    ]
    assert not (tmp_path / "clips.jsonl.state.json").exists()


def test_manifest_passes_context_writes_state_and_resumes(
    tmp_path, monkeypatch, capsys
):
    clip = _video(tmp_path / "clips" / "wedge.mp4")
    manifest = _manifest(
        tmp_path / "clips.jsonl",
        [
            {
                "id": "wedge-check",
                "path": "clips/wedge.mp4",
                "hand": "left",
                "angle": "dtl",
                "club": "wedge",
                "level": "new",
                "strikes": [4, 9.5],
            }
        ],
    )
    output = tmp_path / "results"
    calls: list[tuple[Path, dict]] = []
    monkeypatch.setattr(cli_module, "analyze_video", _fake_analyzer(calls))

    assert main(["batch", str(manifest), "--out", str(output), "--fast", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["completed"] == 1
    assert first["failed"] == 0
    assert len(calls) == 1
    video_path, kwargs = calls[0]
    assert video_path == clip.resolve()
    assert kwargs["hand"] == "left"
    assert kwargs["angle"] == "dtl"
    assert kwargs["club"] == "wedge"
    assert kwargs["level"] == "new"
    assert kwargs["manual_strikes"] == [4.0, 9.5]
    assert kwargs["fast"] is True
    assert kwargs["keep_work"] is False

    state_path = tmp_path / "clips.jsonl.state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["format"] == "caddieinsight.batch-v2-state.v1"
    assert state["completed"]["wedge-check"]["report_path"].endswith("report.html")
    assert not list(tmp_path.glob(f".{state_path.name}.*.tmp"))

    assert main(["batch", str(manifest), "--out", str(output), "--resume", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert len(calls) == 1
    assert second["completed"] == 0
    assert second["resumed"] == 1
    assert second["items"][0]["status"] == "resumed"


def test_resume_fails_closed_when_a_completed_instruction_changes(
    tmp_path, monkeypatch, capsys
):
    _video(tmp_path / "clips" / "baseline.mov")
    manifest = tmp_path / "clips.jsonl"
    original = {"id": "baseline", "path": "clips/baseline.mov", "hand": "right"}
    _manifest(manifest, [original])
    calls: list[tuple[Path, dict]] = []
    monkeypatch.setattr(cli_module, "analyze_video", _fake_analyzer(calls))

    assert main(["batch", str(manifest), "--out", str(tmp_path / "results"), "--json"]) == 0
    capsys.readouterr()
    _manifest(manifest, [{**original, "hand": "left"}])

    assert main(["batch", str(manifest), "--resume", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no longer matches this manifest" in captured.err
    assert len(calls) == 1


def test_resume_preflights_every_saved_instruction_before_any_rerun(
    tmp_path, monkeypatch, capsys
):
    first = _video(tmp_path / "clips" / "first.mov")
    second = _video(tmp_path / "clips" / "second.mov")
    manifest = _manifest(
        tmp_path / "clips.jsonl",
        [
            {"id": "first", "path": str(first)},
            {"id": "second", "path": str(second), "hand": "right"},
        ],
    )
    calls: list[tuple[Path, dict]] = []
    monkeypatch.setattr(cli_module, "analyze_video", _fake_analyzer(calls))

    assert main(["batch", str(manifest), "--out", str(tmp_path / "results")]) == 0
    capsys.readouterr()
    assert len(calls) == 2
    first_report = tmp_path / "results" / "first-1" / "report.html"
    first_report.unlink()
    _manifest(
        manifest,
        [
            {"id": "first", "path": str(first)},
            {"id": "second", "path": str(second), "hand": "left"},
        ],
    )

    assert main(["batch", str(manifest), "--resume", "--json"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no longer matches this manifest" in captured.err
    assert len(calls) == 2


def test_manifest_is_fully_validated_before_the_first_clip_runs(
    tmp_path, monkeypatch, capsys
):
    _video(tmp_path / "clips" / "first.mov")
    _video(tmp_path / "clips" / "second.mov")
    manifest = _manifest(
        tmp_path / "clips.jsonl",
        [
            {"id": "first", "path": "clips/first.mov"},
            {"id": "second", "path": "clips/second.mov", "surprise": True},
        ],
    )
    calls: list[tuple[Path, dict]] = []
    monkeypatch.setattr(cli_module, "analyze_video", _fake_analyzer(calls))

    assert main(["batch", str(manifest), "--json"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "manifest line 2: unknown field(s): surprise" in captured.err
    assert calls == []


def test_state_target_is_checked_before_the_first_clip_runs(
    tmp_path, monkeypatch, capsys
):
    _video(tmp_path / "clips" / "first.mov")
    manifest = _manifest(
        tmp_path / "clips.jsonl", [{"id": "first", "path": "clips/first.mov"}]
    )
    calls: list[tuple[Path, dict]] = []
    monkeypatch.setattr(cli_module, "analyze_video", _fake_analyzer(calls))

    assert main([
        "batch", str(manifest), "--state", str(tmp_path / "missing" / "state.json"),
        "--json",
    ]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "state directory does not exist" in captured.err
    assert calls == []


def test_existing_folder_batch_command_still_uses_its_original_path(
    tmp_path, monkeypatch
):
    folder = tmp_path / "quick-batch"
    _video(folder / "one.mov")
    calls: list[tuple[Path, dict]] = []
    monkeypatch.setattr(cli_module, "analyze_video", _fake_analyzer(calls))

    assert main(["analyze", str(folder), "--batch", "--out", str(tmp_path / "out")]) == 0

    assert len(calls) == 1
    assert calls[0][0] == (folder / "one.mov")
