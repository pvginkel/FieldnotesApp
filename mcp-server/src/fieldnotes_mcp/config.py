"""The MCP server's settings, read once at startup from `FIELDNOTES_*` environment variables.

| Variable | Default | What |
| --- | --- | --- |
| `FIELDNOTES_API_URL` | `http://localhost:8080` | the API, a container of the same pod |
| `FIELDNOTES_API_TOKEN` | required | the bearer of the API's `mcp` client (secret) |
| `FIELDNOTES_MCP_TOKEN` | required | the bearer agents present to this server (secret) |
| `FIELDNOTES_MCP_HOST`, `_PORT` | `0.0.0.0`, 8081 | where the server listens |
| `FIELDNOTES_LOG_LEVEL` | `INFO` | |

Tokens are never in code or config files, only in environment variables materialised from
secrets. Both are required: the server is useless without the API's, and without its own it would
admit every caller (NFR-4). A missing or malformed value fails startup and names its variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

PREFIX = "FIELDNOTES_"

DEFAULT_API_URL = "http://localhost:8080"
# The API listens on 8080 in the same pod, so this server takes the next port.
DEFAULT_MCP_PORT = 8081


class SettingsError(RuntimeError):
    """A variable is missing or malformed. The message names it."""


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_token: str
    mcp_token: str
    host: str
    port: int
    log_level: str


def _optional(environ: Mapping[str, str], name: str) -> str | None:
    """The variable's value, or None when it is unset or blank."""
    return environ.get(PREFIX + name, "").strip() or None


def _get(environ: Mapping[str, str], name: str, default: str) -> str:
    return _optional(environ, name) or default


def _required(environ: Mapping[str, str], name: str) -> str:
    value = _optional(environ, name)
    if value is None:
        raise SettingsError(f"{PREFIX}{name} is not set")
    return value


def _port(environ: Mapping[str, str]) -> int:
    value = _get(environ, "MCP_PORT", str(DEFAULT_MCP_PORT))
    try:
        return int(value)
    except ValueError:
        raise SettingsError(f"{PREFIX}MCP_PORT is not a number: {value!r}") from None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    environ = os.environ if environ is None else environ
    return Settings(
        api_url=_get(environ, "API_URL", DEFAULT_API_URL),
        api_token=_required(environ, "API_TOKEN"),
        mcp_token=_required(environ, "MCP_TOKEN"),
        host=_get(environ, "MCP_HOST", "0.0.0.0"),
        port=_port(environ),
        log_level=_get(environ, "LOG_LEVEL", "INFO").upper(),
    )
