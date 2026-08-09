"""Compatibility and dependency contracts for the foundation migration."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import swinglab
from swinglab import pipeline
from swinglab.analysis import (
    SessionResult,
    VideoTooLongError,
    ZeroStrikesError,
    analyze_video,
)
from swinglab.integrations import shopify
from swinglab.web import shop, shopify_billing


ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = Path(swinglab.__file__).parent
BOUNDARY_NAMES = {"api", "integrations", "web"}


def test_analysis_facade_preserves_engine_objects():
    assert analyze_video is pipeline.analyze_video
    assert SessionResult is pipeline.SessionResult
    assert VideoTooLongError is pipeline.VideoTooLongError
    assert ZeroStrikesError is pipeline.ZeroStrikesError


def test_shopify_facade_preserves_legacy_modules():
    assert shopify.storefront is shop
    assert shopify.webhooks is shopify_billing


def test_flat_engine_modules_do_not_import_boundary_layers():
    offenders = []
    for path in PACKAGE_ROOT.glob("*.py"):
        if path.name in {"__init__.py", "cli.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[:1] == ["swinglab"] and len(parts) > 1:
                        if parts[1] in BOUNDARY_NAMES:
                            offenders.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                module_root = (node.module or "").split(".")[0]
                if (
                    node.level == 1
                    and module_root in BOUNDARY_NAMES
                    or node.level == 0
                    and (node.module or "").split(".")[:1] == ["swinglab"]
                    and len((node.module or "").split(".")) > 1
                    and (node.module or "").split(".")[1] in BOUNDARY_NAMES
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"analysis-to-boundary imports found: {offenders}"


def test_runtime_environment_variables_are_documented():
    names = {"PORT"}
    for path in PACKAGE_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        names.update(
            re.findall(
                r"""os\.environ(?:\.get)?\(\s*["']([A-Z][A-Z0-9_]*)["']""",
                source,
            )
        )
        names.update(
            re.findall(
                r"""os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]""",
                source,
            )
        )
    documentation = (ROOT / "docs" / "environment.md").read_text(encoding="utf-8")
    missing = sorted(name for name in names if f"`{name}`" not in documentation)
    assert not missing, f"undocumented environment variables: {missing}"


def test_value_free_environment_example_covers_documented_variables():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    documentation = (ROOT / "docs" / "environment.md").read_text(encoding="utf-8")
    assignments = {
        line[:-1]
        for line in example.splitlines()
        if line and not line.startswith("#") and line.endswith("=")
    }
    documented = set(re.findall(r"`([A-Z][A-Z0-9_]*)`", documentation))
    assert assignments == documented
    assert not [
        line
        for line in example.splitlines()
        if line and not line.startswith("#") and not line.endswith("=")
    ]


def test_railway_docker_contract_is_stable():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in dockerfile
    assert "COPY pyproject.toml README.md config.yaml ./" in dockerfile
    assert "/healthz" in dockerfile
    assert "os.environ.get('PORT', '8000')" in dockerfile
    # Exec form, not shell form. With `CMD swinglab serve ...` the container's
    # PID 1 is /bin/sh, which does not forward SIGTERM — so every Railway
    # redeploy SIGKILLed the app mid-analysis and left the SQLite WAL
    # uncheckpointed. `exec` inside the exec form keeps ${PORT} expansion while
    # handing PID 1 to the app itself.
    assert 'CMD ["sh", "-c", "exec swinglab serve' in dockerfile, (
        "Dockerfile CMD must stay in exec form so SIGTERM reaches the app."
    )
    assert (
        "--host 0.0.0.0 --port ${PORT:-8000} --sessions-dir /data/sessions"
    ) in dockerfile


def test_github_workflows_are_read_only_and_do_not_persist_credentials():
    workflow_dir = ROOT / ".github" / "workflows"
    for name in ("ci.yml", "security.yml", "stage-0b-backups.yml"):
        source = (workflow_dir / name).read_text(encoding="utf-8")
        assert re.search(r"(?m)^permissions:\n  contents: read$", source)
        assert "pull_request_target:" not in source
        assert not re.search(r"(?m)^\s+[a-z-]+:\s*write\s*$", source)

        checkout_count = source.count("uses: actions/checkout@")
        assert checkout_count
        assert source.count("persist-credentials: false") == checkout_count

        action_refs = re.findall(
            r"(?m)^\s*(?:-\s+)?uses:\s*[^@\s]+@([^\s#]+)",
            source,
        )
        assert action_refs
        assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
