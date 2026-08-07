"""Export the deterministic v1 OpenAPI contract without starting workers."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from swinglab.api import create_app
from swinglab.config import Config


def export_openapi(output: Path) -> None:
    """Write the canonical schema while releasing every app-owned resource."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="swinglab-openapi-") as temp_dir:
        app = create_app(
            Config(), Path(temp_dir), start_background_workers=False
        )
        try:
            schema = app.openapi()
            schema.pop("servers", None)
            with output.open("w", encoding="utf-8", newline="\n") as exported:
                exported.write(
                    json.dumps(schema, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
        finally:
            app.state.jobs.close()
            app.state.users.close()
            app.state.throttle.close()
            if app.state.mobile_keyed_throttle is not None:
                app.state.mobile_keyed_throttle.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export_openapi(args.output)


if __name__ == "__main__":
    main()
