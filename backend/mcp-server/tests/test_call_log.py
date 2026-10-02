"""The MCP server's log: one INFO line per tool call and one per `POST /mcp` answered 400, neither
quoting what the caller sent, and neither changing a reply (slice 003, R5, R6)."""

import json
import logging
import re

import anyio
import httpx
import pytest

from fieldnotes_mcp.api_client import ApiClient
from fieldnotes_mcp.call_log import LoggedFastMCP
from fieldnotes_mcp.server import build_server
from fieldnotes_mcp.testing import (
    API_TOKEN,
    API_URL,
    AUTHORIZED,
    ID,
    call,
    candidate,
    error_text,
    http_client,
    observation,
    serve,
)

LOGGER = "fieldnotes_mcp.call_log"
TEXT = "Sync with --all-packages, or the members are not installed."
POST = {
    "area": "uv workspace",
    "category": "hint",
    "text": TEXT,
    "repo": "pvginkel/Example",
}
DURATION = r"duration=\d+\.\d{3}s"


@pytest.fixture(autouse=True)
def _info(caplog):
    caplog.set_level(logging.INFO)


def _lines(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == LOGGER]


async def test_a_created_post_is_one_line_without_its_text(api, caplog):
    api.answer(201, {"id": ID, "candidates": []})

    await call(api, "post", POST)

    [line] = _lines(caplog)
    assert re.fullmatch(
        rf"tool=post repo=pvginkel/Example outcome=created {DURATION}", line
    )
    assert TEXT not in caplog.text


async def test_a_matched_post_is_logged_as_matched(api, caplog):
    api.answer(200, {"id": None, "candidates": [candidate()]})

    await call(api, "post", POST)

    [line] = _lines(caplog)
    assert re.fullmatch(
        rf"tool=post repo=pvginkel/Example outcome=matched {DURATION}", line
    )


@pytest.mark.parametrize(
    ("tool", "arguments", "reply", "expected"),
    [
        (
            "react",
            {"id": ID, "emoji": "👍", "text": TEXT, "repo": "a/b"},
            {"id": ID, "reactions": ["👍 (1)"]},
            "tool=react repo=a/b outcome=reacted",
        ),
        ("get", {"id": ID}, observation(), "tool=get outcome=read"),
    ],
)
async def test_react_and_get_are_logged(api, caplog, tool, arguments, reply, expected):
    api.answer(200, reply)

    await call(api, tool, arguments)

    [line] = _lines(caplog)
    assert re.fullmatch(rf"{expected} {DURATION}", line)
    assert TEXT not in caplog.text


async def test_a_problem_from_the_api_is_logged_with_its_type_beside_the_warning(
    api, caplog
):
    api.answer(
        404,
        {"type": "not-found", "title": f"no observation {ID}", "status": 404},
    )

    await call(api, "get", {"id": ID})

    [line] = _lines(caplog)
    assert re.fullmatch(
        rf"tool=get outcome=not-found {DURATION} reason=Error executing tool get: "
        rf"no observation {ID} \[not-found, 404\]",
        line,
    )
    assert ("fieldnotes_mcp.tools", logging.WARNING) in {
        (record.name, record.levelno) for record in caplog.records
    }


async def test_an_unreachable_api_is_logged_beside_the_warning(api, caplog):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api.respond = refuse

    await call(api, "post", POST)

    [line] = _lines(caplog)
    assert line.startswith("tool=post repo=pvginkel/Example outcome=api-unreachable ")
    assert (
        "reason=Error executing tool post: the Fieldnotes API could not be reached"
        in line
    )
    assert ("fieldnotes_mcp.tools", logging.WARNING) in {
        (record.name, record.levelno) for record in caplog.records
    }
    assert TEXT not in caplog.text


@pytest.mark.parametrize(
    ("override", "refused", "reason"),
    [
        # 2026-10-01 18:43:39: an `area` over the limit.
        ({"area": "Q" * 201}, "QQQQ", "area String should have at most 200 characters"),
        (
            {"category": "bug"},
            "bug'",
            "category Input should be 'hint', 'idea' or 'friction'",
        ),
        # A missing parameter's error quotes the arguments given, truncated to the text's end.
        ({"repo": None}, "not installed", "repo Field required"),
    ],
)
async def test_arguments_refused_before_the_tool_runs_are_logged_without_their_values(
    api, caplog, override, refused, reason
):
    arguments = {key: value for key, value in {**POST, **override}.items() if value}

    result = await call(api, "post", arguments)

    # The reply is FastMCP's, value and all; only the log leaves it out.
    assert refused in error_text(result)
    assert api.requests == []
    [line] = _lines(caplog)
    assert re.fullmatch(
        rf"tool=post (repo=pvginkel/Example )?outcome=invalid-arguments {DURATION} "
        rf"reason=Error executing tool post: 1 validation error for postArguments "
        rf"{re.escape(reason)} \[type=\w+, input_type=\w+\]",
        line,
    )
    assert refused not in caplog.text


async def test_an_unknown_tool_is_logged(api, caplog):
    result = await call(api, "search", {"repo": "a/b", "query": TEXT})

    assert error_text(result) == "Unknown tool: search"
    [line] = _lines(caplog)
    assert re.fullmatch(
        rf"tool=search repo=a/b outcome=unknown-tool {DURATION} reason=Unknown tool: search",
        line,
    )
    assert TEXT not in caplog.text


async def test_a_cancelled_call_is_logged(caplog):
    # A client that goes away mid-call: the transport closes and cancels the call in flight.
    server = LoggedFastMCP()
    entered = anyio.Event()

    @server.tool()
    async def hold(repo: str) -> str:
        entered.set()
        await anyio.sleep_forever()
        return repo

    async with anyio.create_task_group() as group:
        group.start_soon(server.call_tool, "hold", {"repo": "a/b"})
        await entered.wait()
        group.cancel_scope.cancel()

    [line] = _lines(caplog)
    assert re.fullmatch(rf"tool=hold repo=a/b outcome=cancelled {DURATION}", line)


JSON = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
BATCH = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "post", "arguments": POST},
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]


async def _post(app, body: bytes, headers: dict[str, str]) -> httpx.Response:
    async with http_client(app, AUTHORIZED) as http, app.router.lifespan_context(app):
        return await http.post("/mcp", content=body, headers={**JSON, **headers})


@pytest.mark.parametrize(
    ("body", "headers", "expected"),
    [
        (
            b"{not json",
            {},
            r"protocol-version=none method=unparsed reason=Parse error: Expecting .+",
        ),
        (
            json.dumps(BATCH).encode(),
            {},
            r"protocol-version=none method=batch\[tools/call,notifications/initialized\] "
            r"reason=Validation error: 4 validation errors for JSONRPCMessage JSONRPCRequest "
            r"Input should be a valid dictionary or instance of JSONRPCRequest "
            r"\[type=model_type, input_type=list\] .+",
        ),
        (
            json.dumps(TOOLS_LIST).encode(),
            {"MCP-Protocol-Version": "2024-01-01"},
            r"protocol-version=2024-01-01 method=tools/list reason=Bad Request: Unsupported "
            r"protocol version: 2024-01-01\. Supported versions: .+",
        ),
    ],
)
async def test_each_400_is_logged_and_answered_as_the_sdk_answers_it(
    api, caplog, body, headers, expected
):
    response = await _post(serve(api), body, headers)
    bare = build_server(
        ApiClient(API_URL, API_TOKEN, transport=httpx.MockTransport(api))
    ).streamable_http_app()
    sdk_response = await _post(bare, body, headers)

    assert response.status_code == sdk_response.status_code == 400
    assert response.content == sdk_response.content
    [line] = _lines(caplog)
    assert re.fullmatch(rf"POST /mcp answered 400: {expected}", line)
    assert "input_value" not in line
    assert TEXT not in line


async def test_a_post_answered_otherwise_leaves_no_line(api, caplog):
    response = await _post(serve(api), json.dumps(TOOLS_LIST).encode(), {})

    assert response.status_code == 200
    assert _lines(caplog) == []
