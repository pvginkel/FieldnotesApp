"""The three tools through a real MCP client, the API faked at its HTTP boundary (FR-1, FR-4..FR-7).

Each tool is one call to the API: the request it makes is checked field by field, and the API's
reply comes back as the tool's structured result, unchanged. What the API refuses, and an API that
cannot be reached, come back as tool errors that name the problem and what to do next.
"""

import httpx
import pytest

from fieldnotes_mcp.testing import (
    API_TOKEN,
    ID,
    call,
    candidate,
    error_text,
    mcp_session,
    observation,
)

# FR-6: exactly these tools, no search. Each: its parameters in order, the required ones, and the
# contract model its result is (the API's surface test pins those models' fields).
TOOLS = {
    "post": (
        ["area", "category", "text", "repo", "session", "force"],
        ["area", "category", "text", "repo"],
        "PostReply",
    ),
    "react": (["id", "emoji", "text", "repo", "session"], ["id", "emoji", "repo"], "ReactReply"),
    "get": (["id"], ["id"], "Observation"),
}

POST = {
    "area": "uv workspace",
    "category": "hint",
    "text": "Sync with --all-packages, or the members are not installed.",
    "repo": "pvginkel/Example",
}


async def test_the_tool_surface_is_pinned(api):
    async with mcp_session(api) as session:
        tools = (await session.list_tools()).tools
    surface = {
        tool.name: (
            list(tool.inputSchema["properties"]),
            tool.inputSchema.get("required", []),
            tool.outputSchema["title"],
        )
        for tool in tools
    }
    assert surface == TOOLS
    assert all(tool.description for tool in tools)
    assert api.requests == []


async def test_post_creates_when_nothing_matches(api):
    api.answer(201, {"id": ID, "candidates": []})

    result = await call(api, "post", {**POST, "session": "session-1"})

    assert not result.isError
    assert result.structuredContent == {"id": ID, "candidates": []}
    [request] = api.requests
    assert (request.method, request.url.path) == ("POST", "/observations")
    assert request.headers["authorization"] == f"Bearer {API_TOKEN}"
    assert api.body() == {**POST, "session": "session-1", "force": False}


async def test_post_answers_the_candidates_as_the_api_gives_them(api):
    # FR-1, FR-2: nothing is created; the candidates, with their next step, reach the agent whole.
    api.answer(200, {"id": None, "candidates": [candidate()]})

    result = await call(api, "post", POST)

    assert not result.isError
    assert result.structuredContent == {"id": None, "candidates": [candidate()]}


async def test_post_with_force_asks_the_api_to_create(api):
    api.answer(201, {"id": ID, "candidates": []})

    await call(api, "post", {**POST, "force": True})

    assert api.body() == {**POST, "session": None, "force": True}


async def test_post_strips_what_it_sends(api):
    api.answer(201, {"id": ID, "candidates": []})

    await call(api, "post", {**POST, "area": "  uv workspace\n"})

    assert api.body()["area"] == "uv workspace"


@pytest.mark.parametrize(
    "override",
    [
        {"category": "bug"},  # FR-7: product bugs are not observations
        {"text": "   "},
        {"repo": ""},
        {"area": "x" * 201},
    ],
)
async def test_post_refuses_what_the_contract_refuses_without_calling_the_api(api, override):
    result = await call(api, "post", {**POST, **override})

    assert result.isError
    assert api.requests == []


async def test_react_appends_a_reaction(api):
    api.answer(200, {"id": ID, "reactions": ["👍 (3)", "📝 (1)"]})

    result = await call(
        api, "react", {"id": ID, "emoji": "👍", "text": "Also on Python 3.14.", "repo": "a/b"}
    )

    assert not result.isError
    assert result.structuredContent == {"id": ID, "reactions": ["👍 (3)", "📝 (1)"]}
    [request] = api.requests
    assert (request.method, request.url.path) == ("POST", f"/observations/{ID}/reactions")
    assert api.body() == {
        "emoji": "👍",
        "text": "Also on Python 3.14.",
        "repo": "a/b",
        "session": None,
    }


async def test_react_without_text(api):
    api.answer(200, {"id": ID, "reactions": ["👎 (1)", "📝 (1)"]})

    await call(api, "react", {"id": ID, "emoji": "👎", "repo": "a/b"})

    assert api.body()["text"] is None


async def test_get_answers_the_whole_observation(api):
    # FR-5: every field, the reactions and the comments included.
    api.answer(200, observation())

    result = await call(api, "get", {"id": ID})

    assert not result.isError
    assert result.structuredContent == observation()
    [request] = api.requests
    assert (request.method, request.url.path) == ("GET", f"/observations/{ID}")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get", {"id": "01k5h8zq3v6d9w2x4y7b1c0e5f"}),  # lowercase is not the id
        ("get", {"id": "../match"}),
        ("react", {"id": ID[:-1], "emoji": "👍", "repo": "a/b"}),
    ],
)
async def test_a_malformed_id_is_refused_without_calling_the_api(api, tool, arguments):
    result = await call(api, tool, arguments)

    assert result.isError
    assert api.requests == []


async def test_a_problem_from_the_api_is_a_tool_error_with_what_to_do_next(api):
    api.answer(
        404,
        {
            "type": "not-found",
            "title": f"no observation {ID}",
            "status": 404,
            "detail": "it may have been merged into another observation; post again",
        },
    )

    result = await call(api, "get", {"id": ID})

    assert error_text(result).endswith(
        f"no observation {ID} [not-found, 404]: it may have been merged into another "
        "observation; post again"
    )


async def test_a_validation_problem_names_each_field_at_fault(api):
    api.answer(
        422,
        {
            "type": "validation-error",
            "title": "the request is not valid",
            "status": 422,
            "detail": "the errors list names each field at fault",
            "errors": [{"loc": ["body", "repo"], "msg": "too long", "type": "string_too_long"}],
        },
    )

    result = await call(api, "post", POST)

    assert error_text(result).endswith(
        "the request is not valid [validation-error, 422]: the errors list names each field at "
        "fault; body.repo: too long"
    )


async def test_an_answer_that_is_not_a_problem_is_still_a_tool_error(api):
    api.respond = lambda request: httpx.Response(502, text="<html>Bad Gateway</html>")

    result = await call(api, "get", {"id": ID})

    assert "the Fieldnotes API answered 502 Bad Gateway [internal, 502]" in error_text(result)


async def test_an_unreachable_api_is_a_tool_error(api):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api.respond = refuse

    result = await call(api, "post", POST)

    text = error_text(result)
    assert "the Fieldnotes API could not be reached [api-unreachable, 502]" in text
    assert "retry in a minute" in text
