"""Privacy-preserving Uvicorn access-log configuration."""

from __future__ import annotations

import copy
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG


class RedactAccessLogQueryFilter(logging.Filter):
    """Remove query parameters from Uvicorn's structured access-log args."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True

        full_path = args[2]
        if not isinstance(full_path, str) or "?" not in full_path:
            return True

        sanitized = list(args)
        sanitized[2] = full_path.partition("?")[0]
        record.args = tuple(sanitized)
        return True


def access_log_config() -> dict[str, Any]:
    """Return a private copy of Uvicorn's config with query redaction."""

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["redact_query_string"] = {
        "()": "swinglab.web.access_log.RedactAccessLogQueryFilter",
    }
    access_handler = config["handlers"]["access"]
    access_handler["filters"] = [
        *access_handler.get("filters", []),
        "redact_query_string",
    ]
    return config
