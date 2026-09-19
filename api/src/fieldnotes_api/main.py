"""`fieldnotes-api`: serve the API with the settings in the environment."""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import load_settings


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
