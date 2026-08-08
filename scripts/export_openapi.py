"""Export the deterministic v1 OpenAPI contract without starting workers."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from swinglab.api import create_app
from swinglab.config import Config


def hoist_nested_defs(schema: dict[str, Any]) -> None:
    """Promote nested Pydantic ``$defs`` into ``components.schemas``.

    Inline ``model_json_schema()`` bodies emit ``#/$defs/...`` pointers that
    openapi-typescript (and other document-root resolvers) cannot follow when
    ``$defs`` lives under a path schema. Rewrite every ``#/$defs/`` string to
    ``#/components/schemas/`` after hoisting.
    """

    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            nested = node.pop("$defs", None)
            if isinstance(nested, dict):
                for name, definition in nested.items():
                    existing = schemas.get(name)
                    if existing is not None and existing != definition:
                        raise ValueError(
                            f"OpenAPI $defs conflict for schema name {name!r}"
                        )
                    schemas[name] = definition
                    walk(definition)
            for key, value in list(node.items()):
                if isinstance(value, str) and value.startswith("#/$defs/"):
                    node[key] = "#/components/schemas/" + value.removeprefix(
                        "#/$defs/"
                    )
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


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
            hoist_nested_defs(schema)
            with output.open("w", encoding="utf-8", newline="\n") as exported:
                exported.write(
                    json.dumps(schema, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
        finally:
            app.state.resumable_upload_manager.close()
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
