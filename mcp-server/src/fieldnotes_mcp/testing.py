"""The suites' harness: the real MCP server, driven by a real MCP client over its Streamable-HTTP
transport, with only the API's HTTP boundary faked.

`FakeApi` stands in for the API behind an `httpx.MockTransport`: it records every request the
server's real `ApiClient` makes and answers with what the test set. `mcp_session` serves the app
in-process through an `httpx` ASGI transport, so a tool call crosses the bearer gate, the
transport, FastMCP's argument validation and the tool, as it does in the pod.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from starlette.applications import Starlette

from .api_client import ApiClient
from .server import build_app

API_URL = "http://api.test"
API_TOKEN = "api-token"
MCP_TOKEN = "mcp-token"
AUTHORIZED = {"Authorization": f"Bearer {MCP_TOKEN}"}

ID = "01K5H8ZQ3V6D9W2X4Y7B1C0E5F"
AT = "2026-09-19T10:00:00Z"

Responder = Callable[[httpx.Request], httpx.Response]


class FakeApi:
    """The API's HTTP boundary: records each request, answers with `respond`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.respond: Responder = lambda request: httpx.Response(500)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def answer(self, status: int, body: Any) -> None:
        self.respond = lambda request: httpx.Response(status, json=body)

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


def serve(api: FakeApi) -> Starlette:
    """The app the pod serves, its API client on `api`."""
    return build_app(ApiClient(API_URL, API_TOKEN, transport=httpx.MockTransport(api)), MCP_TOKEN)


def http_client(app: Starlette, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    """An HTTP client of `app`, served in-process: no port is bound."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://mcp.test", headers=headers
    )


@asynccontextmanager
async def mcp_session(
    api: FakeApi, headers: dict[str, str] = AUTHORIZED
) -> AsyncIterator[ClientSession]:
    """An MCP client session with the app, presenting `headers` on every request."""
    app = serve(api)
    async with http_client(app, headers) as http, app.router.lifespan_context(app):
        async with streamable_http_client("http://mcp.test/mcp", http_client=http) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call(api: FakeApi, tool: str, arguments: dict[str, Any]) -> CallToolResult:
    async with mcp_session(api) as session:
        return await session.call_tool(tool, arguments)


def error_text(result: CallToolResult) -> str:
    assert result.isError, "expected a tool error"
    return "".join(block.text for block in result.content if block.type == "text")


def candidate(id_: str = ID) -> dict[str, Any]:
    return {
        "id": id_,
        "area": "uv workspace",
        "canonical": "Sync with --all-packages, or the members are not installed.",
        "status": "closed",
        "outcome": "done",
        "pointer": "docs/setup.md",
        "reactions": ["📝 (1)", "👍 (2)"],
        "cosine": 0.93,
        "score": 0.9712,
        "match_class": "likely",
        "next_step": f'react(id="{id_}", emoji, text?, repo, session?) if ...',
    }


def observation(id_: str = ID) -> dict[str, Any]:
    return {
        "id": id_,
        "status": "open",
        "area": "uv workspace",
        "category": "hint",
        "repos": ["pvginkel/Example"],
        "created": AT,
        "last_updated": AT,
        "last_reviewed": None,
        "last_seen": AT,
        "canonical": "Sync with --all-packages, or the members are not installed.",
        "outcome": None,
        "reason": None,
        "card": None,
        "card_updated": None,
        "pointer": None,
        "reactions": [
            {
                "at": AT,
                "emoji": "📝",
                "repo": "pvginkel/Example",
                "session": None,
                "client": "mcp",
                "text": "Sync with --all-packages, or the members are not installed.",
            }
        ],
        "comments": [{"at": AT, "author": "reconciler", "text": "Checked against the docs."}],
    }
