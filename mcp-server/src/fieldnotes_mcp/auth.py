"""The inbound bearer (NFR-4): agents present `Authorization: Bearer <token>`, compared with
`FIELDNOTES_MCP_TOKEN` in constant time. It is one static token for every agent, as on the
KubeCoder MCP server; the API behind this server tells its callers apart, not this gate.

The middleware wraps every path, so the paths that answer without a credential, the probes, are
handed to it where it is installed. A refusal is problem+json, like every error of the API's.
"""

from __future__ import annotations

import hmac

from starlette.types import ASGIApp, Receive, Scope, Send

from fieldnotes_contracts import Problem, ProblemType

_UNAUTHENTICATED = (
    Problem(
        type=str(ProblemType.unauthenticated),
        title="missing or unknown bearer token",
        status=401,
        detail="send the Fieldnotes MCP token as `Authorization: Bearer <token>`",
    )
    .model_dump_json(exclude_none=True)
    .encode()
)


def _presented(scope: Scope) -> bytes | None:
    """The token of the request's `Authorization: Bearer <token>` header, or None. Read as bytes:
    a caller can send any header bytes, and nothing needs decoding to compare them."""
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.partition(b" ")
            token = token.strip()
            return token if scheme.lower() == b"bearer" and token else None
    return None


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str, public: frozenset[str]) -> None:
        self._app = app
        self._token = token.encode()
        self._public = public

    def _authorized(self, scope: Scope) -> bool:
        presented = _presented(scope)
        return presented is not None and hmac.compare_digest(presented, self._token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._public or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _UNAUTHENTICATED})
