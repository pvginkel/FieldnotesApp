"""problem+json errors (RFC 9457): the body of every non-2xx the API answers.

Raise `ProblemException(status, ProblemType.<slug>, title, detail=...)`; never Flask's
`abort` or an ad-hoc error body, so the surface answers one error shape. A request that
fails validation answers `validation-error` with the pydantic errors projected to `loc`, `msg` and
`type`, so the caller's payload is never echoed back. An exception nothing maps answers `internal`
in fixed words; its traceback goes to the log.

`title` and `detail` are read by an agent through the MCP server, or by a skill's operator: the
detail says what to do next.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Response
from pydantic import ValidationError

from fieldnotes_contracts import Problem, ProblemType

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

INTERNAL_TITLE = "the Fieldnotes API hit an unexpected error"
INTERNAL_DETAIL = (
    "retry the request; if it keeps failing, the API's log holds the traceback"
)


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
            type=str(type_),
            title=title,
            status=status,
            detail=detail,
            **(extensions or {}),
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


def store_unreachable() -> ProblemException:
    return ProblemException(
        502,
        ProblemType.store_unreachable,
        "the store's remote could not be reached or refused the write",
        detail="nothing was written; retry in a minute",
    )


def problem_response(exc: ProblemException) -> Response:
    return Response(
        exc.problem.model_dump_json(exclude_none=True),
        status=exc.problem.status,
        content_type=PROBLEM_CONTENT_TYPE,
    )


def validation_problem(exc: ValidationError, *location: str) -> ProblemException:
    """A request that fails its model: the pydantic errors projected to `loc`, `msg` and `type`,
    each `loc` led by where the value was (`body`; `path`, `id`; `query`, `k`), so the payload is
    never echoed back."""
    errors = [
        {"loc": [*location, *e["loc"]], "msg": e["msg"], "type": e["type"]}
        for e in exc.errors()
    ]
    return ProblemException(
        422,
        ProblemType.validation_error,
        "the request is not valid",
        detail="the errors list names each field at fault",
        extensions={"errors": errors},
    )


def internal() -> ProblemException:
    return ProblemException(500, ProblemType.internal, INTERNAL_TITLE, INTERNAL_DETAIL)
