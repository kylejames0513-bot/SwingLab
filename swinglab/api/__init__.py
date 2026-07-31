"""Stable application/API entrypoint without eagerly importing web extras."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Config

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app(
    cfg: Config | None = None, sessions_dir: str | Path = "sessions"
) -> "FastAPI":
    """Create the existing FastAPI application through the API boundary."""
    from ..web.app import create_app as _create_app

    return _create_app(cfg=cfg, sessions_dir=sessions_dir)


__all__ = ["create_app"]
