"""problem+json errors (RFC 9457): the body of every non-2xx the API answers.

Raise `ProblemException(status, ProblemType.<slug>, title, detail=...)`; never FastAPI's
`HTTPException` or an ad-hoc error body, so the surface answers one error shape. A request that
fails validation answers `validation-error` with the pydantic errors projected to `loc`, `msg` and
`type`, so the caller's payload is never echoed back. An exception nothing maps answers `internal`
in fixed words; its traceback goes to the log.

`title` and `detail` are read by an agent through the MCP server, or by a skill's operator: the
detail says what to do next.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fieldnotes_contracts import Problem, ProblemType

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

INTERNAL_TITLE = "the Fieldnotes API hit an unexpected error"
INTERNAL_DETAIL = "retry the request; if it keeps failing, the API's log holds the traceback"


class ProblemException(Exception):
    def __init__(
        self,
        status: int,
        type_: ProblemType,
        title: str,
        detail: str | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        self.problem = Problem(
            type=str(type_), title=title, status=status, detail=detail, **(extensions or {})
        )
        super().__init__(title)


def unauthenticated() -> ProblemException:
    return ProblemException(
        401,
        ProblemType.unauthenticated,
        "missing or unknown bearer token",
        detail="send the token of a configured client as `Authorization: Bearer <token>`",
    )


def not_found(id_: str) -> ProblemException:
    return ProblemException(
        404,
        ProblemType.not_found,
        f"no observation {id_}",
        detail=(
            "it may have been merged into another observation; post again to find the one "
            "that holds it now"
        ),
    )


def not_ready() -> ProblemException:
    return ProblemException(
        503,
        ProblemType.not_ready,
        "the Fieldnotes API is still starting",
        detail="it is cloning the store or building its index; retry in a minute",
    )


async def problem_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemException)
    return JSONResponse(
        status_code=exc.problem.status,
        content=exc.problem.model_dump(exclude_none=True),
        media_type=PROBLEM_CONTENT_TYPE,
    )


async def request_validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = jsonable_encoder(
        [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    )
    problem = ProblemException(
        422,
        ProblemType.validation_error,
        "the request is not valid",
        detail="the errors list names each field at fault",
        extensions={"errors": errors},
    )
    return await problem_exception_handler(request, problem)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Registered on `Exception`, so it sits on Starlette's ServerErrorMiddleware, which sends
    this response and then re-raises: a TestClient needs `raise_server_exceptions=False` to read
    it."""
    logger.exception("unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return await problem_exception_handler(
        request, ProblemException(500, ProblemType.internal, INTERNAL_TITLE, INTERNAL_DETAIL)
    )
