"""The MCP server and its Streamable-HTTP app (design, "This repo").

FastMCP from the official `mcp` SDK, served at `/mcp`, as the KubeCoder MCP server is:

- Stateless: every tool call is a request of its own, so the server holds nothing between calls
  and needs no session affinity.
- Replies as an event stream (`json_response` off), which pings while a call is held, so a slow
  post never goes silent on the wire.
- DNS-rebinding protection off: it guards a server a browser on the same machine can reach, and
  this one's boundary is the bearer gate (`auth.py`), not the Host header.

`GET /healthz` and `GET /readyz` answer without a credential. Readiness is this server's alone:
the API is a container of the same pod, which is not ready until the API is.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from fieldnotes_contracts import HealthReply

from .api_client import ApiClient
from .auth import BearerAuthMiddleware
from .tools import register_tools

SERVER_NAME = "fieldnotes"
SERVER_INSTRUCTIONS = (
    "Fieldnotes: the operator's complaint box for friction agents run into while they work, "
    "shared across every project and curated. `post` what got in your way; when the store "
    "already holds it, `post` answers with what is known about it instead of creating a "
    "duplicate, and you `react` to the one it is. `get` reads one in full. There is no search: "
    "the store is temporary, what is reported gets fixed or documented and then leaves it. What "
    "comes back when your post matches is curated, and the operator's rulings are in it: trust it."
)
HEALTH_PATHS = frozenset({"/healthz", "/readyz"})


async def _ok(_request: Request) -> Response:
    return JSONResponse(HealthReply(status="ok").model_dump())


def build_server(api: ApiClient) -> FastMCP:
    mcp = FastMCP(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=False,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    register_tools(mcp, api)
    for path in sorted(HEALTH_PATHS):
        mcp.custom_route(path, methods=["GET"])(_ok)
    return mcp


def build_app(api: ApiClient, token: str) -> Starlette:
    """The app to serve: the MCP server behind the bearer gate, `token` the one agents present."""
    app = build_server(api).streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=token, public=HEALTH_PATHS)
    return app
