"""Pydantic wire models of the Fieldnotes REST surface, shared by the API and the MCP server."""

from __future__ import annotations

from .api import (
    AREA_MAX,
    EMOJI_MAX,
    MAX_CANDIDATES,
    POST_CANDIDATES,
    REPO_MAX,
    SESSION_MAX,
    TEXT_MAX,
    BoardSyncReply,
    Candidate,
    HealthReply,
    HookReply,
    MatchClass,
    MatchReply,
    MatchRequest,
    NeighborsReply,
    PostReply,
    PostRequest,
    ReactReply,
    ReactRequest,
)
from .observation import (
    POST_EMOJI,
    Category,
    Comment,
    Observation,
    Outcome,
    Reaction,
    Status,
)
from .problem import Problem, ProblemType

__all__ = [
    # api
    "AREA_MAX",
    "EMOJI_MAX",
    "MAX_CANDIDATES",
    "POST_CANDIDATES",
    "REPO_MAX",
    "SESSION_MAX",
    "TEXT_MAX",
    "BoardSyncReply",
    "Candidate",
    "HealthReply",
    "HookReply",
    "MatchClass",
    "MatchReply",
    "MatchRequest",
    "NeighborsReply",
    "PostReply",
    "PostRequest",
    "ReactReply",
    "ReactRequest",
    # observation
    "POST_EMOJI",
    "Category",
    "Comment",
    "Observation",
    "Outcome",
    "Reaction",
    "Status",
    # problem
    "Problem",
    "ProblemType",
]
