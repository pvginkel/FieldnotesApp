"""`fieldnotes-mcp`: serve the MCP server with the settings in the environment."""

from __future__ import annotations

import logging

import uvicorn

from .api_client import ApiClient
from .config import load_settings
from .server import build_app


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(
        build_app(ApiClient(settings.api_url, settings.api_token), settings.mcp_token),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
