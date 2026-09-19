"""The inbound bearer gate and the probes (NFR-4; design, "This repo")."""

import pytest

from fieldnotes_mcp.testing import AUTHORIZED, ID, MCP_TOKEN, call, http_client, serve

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream"}


async def test_the_token_admits_a_tool_call(api):
    api.answer(201, {"id": ID, "candidates": []})

    result = await call(api, "post", {"area": "a", "category": "idea", "text": "t", "repo": "o/r"})

    assert not result.isError
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer wrong-token",
        f"Basic {MCP_TOKEN}",
        "Bearer ",
        f"Bearer {MCP_TOKEN}x",
        "Bearer tökén".encode(),  # a caller can send any header bytes
    ],
)
async def test_anything_but_the_token_is_refused(api, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    async with http_client(serve(api), headers) as http:
        response = await http.post("/mcp", json=INITIALIZE, headers=ACCEPT)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["type"] == "unauthenticated"
    assert api.requests == []


async def test_the_scheme_is_case_insensitive(api):
    app = serve(api)
    headers = {"Authorization": f"bearer {MCP_TOKEN}"}
    async with http_client(app, headers) as http, app.router.lifespan_context(app):
        response = await http.post("/mcp", json=INITIALIZE, headers=ACCEPT)

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_the_probes_answer_without_a_credential_and_without_the_api(api, path):
    async with http_client(serve(api)) as http:
        response = await http.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert api.requests == []


async def test_any_other_path_needs_the_token(api):
    async with http_client(serve(api)) as http:
        assert (await http.get("/")).status_code == 401
    async with http_client(serve(api), AUTHORIZED) as http:
        assert (await http.get("/")).status_code == 404
