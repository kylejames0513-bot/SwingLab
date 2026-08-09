"""Export the app's OpenAPI document to docs/api/openapi-v1.json.

    python scripts/export_openapi.py

The output is deterministic — sorted keys, trailing newline, no timestamps or
environment-dependent values — so it can be committed and diffed, and so a
stale copy shows up as a diff rather than as churn.

What this document is, and is not: FastAPI derives it from the route
signatures. Every `/api/v1` operation now declares a 200 response schema from
`swinglab/web/api_models.py`, so `openapi-typescript` produces real bodies
rather than `unknown`.

One caveat worth knowing before trusting it. The handlers still return
`JSONResponse`, because that is where `Cache-Control: no-store` is applied,
and FastAPI does not validate a response it did not serialize itself. So the
declared schema is not enforced by the framework — it is enforced by
`tests/test_api_response_contract.py`, which validates real responses from a
real account with a real completed analysis. That distinction matters: the
schema is true because a test proves it each build, not because the route
declares it.

`mobile/src/api/types.ts` is still hand-written. It no longer has to be —
generating it from this document is now possible, and
`tests/test_openapi_export.py` continues to assert the two agree on which
routes exist.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs" / "api" / "openapi-v1.json"


def build_document() -> dict:
    """The OpenAPI document for a default-configuration app.

    A throwaway sessions directory keeps generation free of whatever the
    caller has on disk, and the placeholder secret is never used to sign
    anything — the app is built and discarded without serving a request.

    ``ignore_cleanup_errors`` is what makes this runnable on Windows. The app
    is discarded without being shut down, so its SQLite connections are still
    open when the context manager unlinks the scratch tree, and Windows
    refuses to delete an open file (POSIX does not, which is why this only
    ever failed on one platform). Without it the script raises
    ``PermissionError: [WinError 32]`` *after* the document is built but
    before it is written, so the export appears to fail while the stale file
    stays on disk — the worst version of this failure, because the leak it is
    meant to close survives it. The scratch tree is throwaway either way.
    """
    os.environ.setdefault("SWINGLAB_SECRET", "openapi-export-placeholder")

    from swinglab.config import Config
    from swinglab.web.app import create_app

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        app = create_app(
            Config(),
            sessions_dir=Path(scratch) / "sessions",
            start_shopify_sync_worker=False,
        )
        return app.openapi()


def serialize(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the file on disk is stale, without writing.",
    )
    args = parser.parse_args(argv)

    rendered = serialize(build_document())

    if args.check:
        if not args.output.exists():
            print(f"{args.output} is missing; run scripts/export_openapi.py")
            return 1
        if args.output.read_text(encoding="utf-8") != rendered:
            print(f"{args.output} is stale; run scripts/export_openapi.py")
            return 1
        print(f"{args.output} is up to date")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
