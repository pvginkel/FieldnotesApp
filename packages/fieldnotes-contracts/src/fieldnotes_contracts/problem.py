"""RFC 9457 problem+json: the error body of every non-2xx the API answers.

`type` is a stable slug from a closed set; `title` and `detail` are prose for whoever reads the
error, an agent through the MCP server or an operator running a skill. Extension members (the
validation errors' `errors` list) ride along through `extra="allow"`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ProblemType(StrEnum):
    """The closed set of `type` slugs."""

    unauthenticated = "unauthenticated"
    not_found = "not-found"
    # The request is well-formed and names a known observation, but it is in the wrong state for
    # the operation: a board sync of an observation with no card.
    conflict = "conflict"
    validation_error = "validation-error"
    # The store is not cloned or the index not built yet; retry shortly.
    not_ready = "not-ready"
    models_unreachable = "models-unreachable"
    store_unreachable = "store-unreachable"
    board_unreachable = "board-unreachable"
    internal = "internal"


class Problem(BaseModel):
    """An RFC 9457 problem document. Extension members are allowed."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str | None = None
