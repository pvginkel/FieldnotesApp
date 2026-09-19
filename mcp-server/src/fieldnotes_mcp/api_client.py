"""The MCP server's client of the API: the only code in this server that speaks HTTP to it
(design, "This repo").

One method per tool, each one REST call that takes and returns the shared contract models. The
`mcp` client's bearer rides every request. An answer outside 2xx raises `ApiError` carrying the
API's problem+json; a transport failure, which produced no answer, raises one carrying a problem
made up here, `api-unreachable`, so the tools report both the same way.
"""

from __future__ import annotations

import httpx

from fieldnotes_contracts import (
    Observation,
    PostReply,
    PostRequest,
    Problem,
    ProblemType,
    ReactReply,
    ReactRequest,
)

# A post embeds its text and, when it creates, commits and pushes before it answers; the API bounds
# each model call and each git command at 60 s, and a write runs several git commands.
API_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)

API_UNREACHABLE = "api-unreachable"


class ApiError(Exception):
    """The API refused a call, or could not be reached. Carries the problem to report."""

    def __init__(self, problem: Problem) -> None:
        self.problem = problem
        super().__init__(f"{problem.status} {problem.type}: {problem.title}")


def _problem(response: httpx.Response) -> Problem:
    """The API's problem+json, or one made up from the status for a body that is not one."""
    try:
        return Problem.model_validate(response.json())
    except ValueError:
        return Problem(
            type=str(ProblemType.internal),
            title=f"the Fieldnotes API answered {response.status_code} {response.reason_phrase}",
            status=response.status_code,
        )


class ApiClient:
    def __init__(
        self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """`transport` replaces the network, in the suites."""
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=API_TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, body: PostRequest | ReactRequest | None = None
    ) -> httpx.Response:
        json = None if body is None else body.model_dump(mode="json")
        try:
            response = await self._http.request(method, path, json=json)
        except httpx.RequestError as exc:
            raise ApiError(
                Problem(
                    type=API_UNREACHABLE,
                    title="the Fieldnotes API could not be reached",
                    status=502,
                    detail=f"{type(exc).__name__}; the request may or may not have taken "
                    "effect, retry in a minute",
                )
            ) from exc
        if response.is_error:
            raise ApiError(_problem(response))
        return response

    async def post(self, request: PostRequest) -> PostReply:
        response = await self._request("POST", "/observations", request)
        return PostReply.model_validate(response.json())

    async def react(self, id_: str, request: ReactRequest) -> ReactReply:
        response = await self._request("POST", f"/observations/{id_}/reactions", request)
        return ReactReply.model_validate(response.json())

    async def get(self, id_: str) -> Observation:
        response = await self._request("GET", f"/observations/{id_}")
        return Observation.model_validate(response.json())
