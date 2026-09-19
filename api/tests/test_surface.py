"""The REST surface, pinned by equality (docs/change-discipline.md, "The two surfaces are
contracts"): the routes, the wire models' fields and types, and the enums' values, the closed set
of problem slugs among them. The skills call this surface from another repo and the MCP server
maps onto it, so a change here is deliberate: it shows as a diff to this file, and the slice that
makes it says so in its close-out report."""

import types
import typing
from enum import StrEnum
from typing import Annotated, Literal, Union, get_args, get_origin

from fastapi.routing import APIRoute
from pydantic import BaseModel

import fieldnotes_contracts as contracts
from fieldnotes_api.app import create_app
from fieldnotes_api.config import load_settings

# (method, path): (request model, reply model, success status, query parameters)
ROUTES = {
    ("POST", "/observations"): ("PostRequest", "PostReply", 201, []),
    ("POST", "/observations/{id}/reactions"): ("ReactRequest", "ReactReply", 200, []),
    ("GET", "/observations/{id}"): (None, "Observation", 200, []),
    ("GET", "/observations/{id}/neighbors"): (None, "NeighborsReply", 200, ["k"]),
    ("POST", "/observations/{id}/board-sync"): (None, "BoardSyncReply", 200, []),
    ("POST", "/match"): ("MatchRequest", "MatchReply", 200, []),
    ("POST", "/hooks/github"): (None, "HookReply", 200, []),
    ("POST", "/hooks/youtrack"): (None, "HookReply", 200, []),
    ("GET", "/healthz"): (None, "HealthReply", 200, []),
    ("GET", "/readyz"): (None, "HealthReply", 200, []),
}

MODELS = {
    "PostRequest": {
        "area": "str",
        "category": "Category",
        "text": "str",
        "repo": "str",
        "session": "str | None = None",
        "force": "bool = False",
    },
    "PostReply": {"id": "str | None = None", "candidates": "list[Candidate] = []"},
    "Candidate": {
        "id": "str",
        "area": "str",
        "canonical": "str",
        "status": "Status",
        "outcome": "Outcome | None",
        "pointer": "str | None",
        "reactions": "list[str]",
        "cosine": "float",
        "score": "float",
        "match_class": "MatchClass | None",
        "next_step": "str",
    },
    "ReactRequest": {
        "emoji": "str",
        "text": "str | None = None",
        "repo": "str",
        "session": "str | None = None",
    },
    "ReactReply": {"id": "str", "reactions": "list[str]"},
    "Observation": {
        "id": "str",
        "status": "Status",
        "area": "str",
        "category": "Category",
        "repos": "list[str]",
        "created": "AwareDatetime",
        "last_updated": "AwareDatetime",
        "last_reviewed": "AwareDatetime | None",
        "last_seen": "AwareDatetime",
        "canonical": "str",
        "outcome": "Outcome | None",
        "reason": "str | None",
        "card": "str | None",
        "card_updated": "AwareDatetime | None",
        "pointer": "str | None",
        "reactions": "list[Reaction]",
        "comments": "list[Comment]",
    },
    "Reaction": {
        "at": "AwareDatetime",
        "emoji": "str",
        "repo": "str",
        "session": "str | None = None",
        "client": "str | None = None",
        "text": "str | None = None",
    },
    "Comment": {"at": "AwareDatetime", "author": "str", "text": "str"},
    "MatchRequest": {
        "text": "str",
        "area": "str | None = None",
        "k": "int = 3",
    },
    "MatchReply": {"candidates": "list[Candidate]"},
    "NeighborsReply": {"id": "str", "neighbors": "list[Candidate]"},
    "BoardSyncReply": {
        "id": "str",
        "card": "str",
        "changed": "bool",
        "status": "Status",
        "outcome": "Outcome | None",
        "pointer": "str | None",
        "card_updated": "AwareDatetime | None",
    },
    "HookReply": {"action": "Literal['queued', 'ignored']"},
    "HealthReply": {"status": "Literal['ok']"},
    "Problem": {"type": "str", "title": "str", "status": "int", "detail": "str | None = None"},
}

ENUMS = {
    "Category": ["hint", "idea", "friction"],
    "Status": ["open", "proposed", "raised", "closed"],
    "Outcome": ["done", "wont-do"],
    "MatchClass": ["likely", "related"],
    "ProblemType": [
        "unauthenticated",
        "not-found",
        "conflict",
        "validation-error",
        "not-ready",
        "models-unreachable",
        "store-unreachable",
        "board-unreachable",
        "internal",
    ],
}

CONSTANTS = {"POST_CANDIDATES": 3, "MAX_CANDIDATES": 12, "POST_EMOJI": "📝"}


def spell(annotation: object) -> str:
    """A type as the tables above write it."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return spell(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        return " | ".join(spell(arg) for arg in get_args(annotation))
    if origin is list:
        return f"list[{spell(get_args(annotation)[0])}]"
    if origin is Literal:
        return f"Literal[{', '.join(repr(arg) for arg in get_args(annotation))}]"
    if annotation is type(None):
        return "None"
    return typing.cast(type, annotation).__name__


def fields(model: type[BaseModel]) -> dict[str, str]:
    spelled = {}
    for name, field in model.model_fields.items():
        text = spell(field.annotation)
        if not field.is_required():
            text += f" = {field.default!r}"
        spelled[name] = text
    return spelled


def test_the_routes_are_pinned():
    app = create_app(load_settings({"FIELDNOTES_STORE_URL": "/nonexistent"}), models=object())
    routes = {}
    for route in app.routes:
        assert isinstance(route, APIRoute), f"{route.path} is not an API route"
        body = [param.field_info.annotation.__name__ for param in route.dependant.body_params]
        [method] = route.methods
        routes[(method, route.path)] = (
            body[0] if body else None,
            route.response_model.__name__,
            route.status_code or 200,
            [param.name for param in route.dependant.query_params],
        )
    assert routes == ROUTES


def test_the_wire_models_are_pinned():
    assert {name: fields(getattr(contracts, name)) for name in MODELS} == MODELS


def test_every_wire_model_is_pinned():
    exported = {
        name
        for name in contracts.__all__
        if isinstance(getattr(contracts, name), type)
        and issubclass(getattr(contracts, name), BaseModel)
    }
    assert exported == set(MODELS)


def test_the_enums_are_pinned():
    exported = {
        name
        for name in contracts.__all__
        if isinstance(getattr(contracts, name), type)
        and issubclass(getattr(contracts, name), StrEnum)
    }
    assert exported == set(ENUMS)
    assert {name: [m.value for m in getattr(contracts, name)] for name in ENUMS} == ENUMS


def test_the_constants_are_pinned():
    assert {name: getattr(contracts, name) for name in CONSTANTS} == CONSTANTS
