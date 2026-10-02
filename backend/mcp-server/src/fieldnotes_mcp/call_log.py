"""What the server logs of the calls it answers, at INFO: one line per tool call, and one per
`POST /mcp` the SDK answers 400 (slice 003). Both only observe: no reply, status or tool behaviour
depends on them.

A tool call's line carries the tool, the repo where the call has one, the outcome, the duration
and, for a call that ended in an error, the reason the caller was given. It is logged where FastMCP
dispatches the call, so a call refused before any tool body runs, on its arguments or on an unknown
tool name, leaves its line too; the SDK logs neither.

The SDK logs none of its 400s either. Their line carries the request's `mcp-protocol-version`
header, its JSON-RPC method and the reason from the 400's JSON-RPC error body.

No line quotes what a caller sent: pydantic quotes the refused value in a validation error's text
(`input_value=…`), and that is removed from every reason logged.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from typing import Any, cast

from anyio import get_cancelled_exc_class
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ContentBlock
from pydantic import ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .api_client import ApiError

logger = logging.getLogger(__name__)

# pydantic-core renders each error on one line as `[type=…, input_value=…, input_type=…]`, the
# value as its repr, which holds no line break; matching greedily to the line's last
# `, input_type=` takes the whole value, whatever it contains.
_INPUT_VALUE = re.compile(r"input_value=.*, input_type=")
_DOCS_LINK = re.compile(r"For further information visit \S+")

_DONE = {"react": "reacted", "get": "read"}


def _unquoted(message: str) -> str:
    """`message` on one line, without the values pydantic quotes or the links it adds."""
    return " ".join(
        _DOCS_LINK.sub("", _INPUT_VALUE.sub("input_type=", message)).split()
    )


def _error_type(exc: ToolError) -> str:
    cause = exc.__cause__
    # FastMCP raises the unknown tool's error itself; every error out of a tool's run it raises
    # from the error that ended the run.
    if cause is None:
        return "unknown-tool"
    if isinstance(cause.__cause__, ApiError):
        return cause.__cause__.problem.type
    if isinstance(cause, ValidationError):
        return "invalid-arguments"
    return type(cause).__name__


def _log_call(
    tool: str,
    arguments: dict[str, Any],
    started: float,
    outcome: str,
    reason: str | None = None,
) -> None:
    fields = [f"tool={tool}"]
    if "repo" in arguments:
        fields.append(f"repo={arguments['repo']}")
    fields += [f"outcome={outcome}", f"duration={time.perf_counter() - started:.3f}s"]
    if reason is not None:
        fields.append(f"reason={reason}")
    logger.info(" ".join(fields))


class LoggedFastMCP(FastMCP):
    """FastMCP, logging each tool call it dispatches."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        started = time.perf_counter()
        try:
            result = await super().call_tool(name, arguments)
        except ToolError as exc:
            _log_call(name, arguments, started, _error_type(exc), _unquoted(str(exc)))
            raise
        except get_cancelled_exc_class():
            _log_call(name, arguments, started, "cancelled")
            raise
        # FastMCP answers `(content, structured)` for a tool with a result model, which all three
        # have, whatever its annotation says.
        _, reply = cast(tuple[Any, dict[str, Any]], result)
        if name == "post":
            outcome = "matched" if reply["id"] is None else "created"
        else:
            outcome = _DONE[name]
        _log_call(name, arguments, started, outcome)
        return result


def _method(body: bytes) -> str:
    """The JSON-RPC method of a request body: `none` for a message without one, `unparsed` for a
    body that is not JSON, and a batch's methods in brackets."""
    try:
        message = json.loads(body)
    except ValueError:
        return "unparsed"
    if isinstance(message, list):
        return f"batch[{','.join(_method_of(item) for item in message)}]"
    return _method_of(message)


def _method_of(message: Any) -> str:
    method = message.get("method") if isinstance(message, dict) else None
    return "none" if method is None else str(method)


class BadRequestLogMiddleware:
    """Logs each POST to `path` answered 400, from a copy of the request and response bodies as
    they pass; both pass on unchanged."""

    def __init__(self, app: ASGIApp, path: str) -> None:
        self._app = app
        self._path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != self._path
        ):
            await self._app(scope, receive, send)
            return
        request_body = bytearray()
        response_body = bytearray()
        status = 0

        async def receive_copied() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                request_body.extend(message.get("body", b""))
            return message

        async def send_observed(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body" and status == 400:
                response_body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    self._log(scope, bytes(request_body), bytes(response_body))
            await send(message)

        await self._app(scope, receive_copied, send_observed)

    @staticmethod
    def _log(scope: Scope, request_body: bytes, response_body: bytes) -> None:
        version = dict(scope["headers"]).get(b"mcp-protocol-version")
        logger.info(
            "POST %s answered 400: protocol-version=%s method=%s reason=%s",
            scope["path"],
            "none" if version is None else version.decode("latin-1"),
            _method(request_body),
            _unquoted(json.loads(response_body)["error"]["message"]),
        )
