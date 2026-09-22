"""The FastAPI application (design, "Services").

Startup runs in the background: cloning the store and building the index can take minutes on a
cold cache, and `/healthz` has to answer meanwhile. Until it is done `/readyz` and every endpoint
that needs the index answer `503 not-ready`. A startup that fails is logged and turns `/healthz`
to 503, so the pod is restarted.

Every endpoint but the health checks and the webhooks takes a client's bearer token.

No `from __future__ import annotations` here: FastAPI resolves a string annotation in the module's
globals, and the dependency aliases are local to `create_app`.
"""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from prometheus_client import CONTENT_TYPE_LATEST

from fieldnotes_contracts import (
    ID_PATTERN,
    MAX_CANDIDATES,
    BoardSyncReply,
    HealthReply,
    HookReply,
    MatchReply,
    MatchRequest,
    NeighborsReply,
    Observation,
    PostReply,
    PostRequest,
    ProblemType,
    ReactReply,
    ReactRequest,
)

from .auth import bearer
from .board import Board, BoardError, HttpBoard
from .config import Settings
from .errors import (
    ProblemException,
    not_ready,
    problem_exception_handler,
    request_validation_exception_handler,
    unauthenticated,
    unhandled_exception_handler,
)
from .hooks import github_push_to, github_verified, youtrack_issue, youtrack_verified
from .index import EmbeddingCache, Index
from .matching import Matcher
from .metrics import Metrics
from .models import HttpModels, Models, ModelsError
from .observations import Clock, Observations
from .store import GitError, Store

logger = logging.getLogger(__name__)

# A cold start embeds the whole store in batches; one batch on the shared node can take seconds.
MODELS_TIMEOUT = 60.0
BOARD_TIMEOUT = 30.0

NEIGHBORS_DEFAULT = 5

ObservationId = Annotated[str, Path(pattern=ID_PATTERN, description="the observation's ULID")]


@dataclass
class Runtime:
    settings: Settings
    store: Store
    index: Index
    observations: Observations
    ready: bool = False
    failed: bool = False


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def create_app(
    settings: Settings,
    *,
    models: Models | None = None,
    board: Board | None = None,
    clock: Clock = _utcnow,
    environ: Mapping[str, str] | None = None,
) -> FastAPI:
    """The app. `models` replaces the models pod, `board` YouTrack and `clock` the time, in the
    suites."""
    clients: list[httpx.AsyncClient] = []
    if models is None:
        clients.append(httpx.AsyncClient(base_url=settings.models_url, timeout=MODELS_TIMEOUT))
        models = HttpModels(clients[-1])
    if board is None and settings.board is not None:
        clients.append(httpx.AsyncClient(base_url=settings.board.url, timeout=BOARD_TIMEOUT))
        board = HttpBoard(clients[-1], settings.board)
    outcomes = settings.board.outcomes if settings.board is not None else {}
    store = Store(settings.store, os.environ if environ is None else environ)
    index = Index(
        settings.store.root, EmbeddingCache(settings.cache_dir, settings.embed_model), models
    )
    matcher = Matcher(index, models, settings.match)
    metrics = Metrics(index, clock, settings.clients.names)
    observations = Observations(store, index, matcher, clock, board, outcomes, metrics)
    runtime = Runtime(settings, store, index, observations)

    async def start() -> None:
        try:
            await store.start(index.update)
        except Exception:
            logger.exception("startup failed: the store could not be cloned or indexed")
            runtime.failed = True
            return
        runtime.ready = True
        logger.info(
            "ready: %d observations; clients %s",
            len(index),
            ", ".join(settings.clients.names) or "none",
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        starting = asyncio.create_task(start(), name="startup")
        yield
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        await store.stop()
        for client in clients:
            await client.aclose()

    # The contract models are the surface's definition (design, "This repo"): no OpenAPI document.
    app = FastAPI(
        title="Fieldnotes API", lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None
    )
    app.state.runtime = runtime
    app.add_exception_handler(ProblemException, problem_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(ModelsError, _models_unreachable)
    app.add_exception_handler(GitError, _store_unreachable)
    app.add_exception_handler(BoardError, _board_unreachable)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.middleware("http")
    async def timed(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        metrics.requests.labels(
            request.method, route.path if route is not None else "unmatched", response.status_code
        ).observe(time.perf_counter() - started)
        return response

    def ready() -> Observations:
        if not runtime.ready:
            raise not_ready()
        return runtime.observations

    def client(authorization: Annotated[str | None, Header()] = None) -> str:
        token = bearer(authorization)
        name = settings.clients.resolve(token) if token is not None else None
        if name is None:
            raise unauthenticated()
        return name

    Ready = Annotated[Observations, Depends(ready)]
    Client = Annotated[str, Depends(client)]

    @app.get("/healthz")
    async def healthz() -> HealthReply:
        if runtime.failed:
            raise ProblemException(
                503,
                ProblemType.internal,
                "the Fieldnotes API failed to start",
                detail="the store could not be cloned or indexed; the API's log says why",
            )
        return HealthReply(status="ok")

    @app.get("/readyz")
    async def readyz() -> HealthReply:
        ready()
        return HealthReply(status="ok")

    @app.get("/metrics", response_class=Response)
    async def prometheus() -> Response:
        """The Prometheus exposition; unauthenticated like the health checks, and counts only."""
        return Response(metrics.exposition(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/observations", status_code=201)
    async def post_observation(
        request: PostRequest, response: Response, observations: Ready, name: Client
    ) -> PostReply:
        """FR-1: candidates (200), and nothing created; or the new observation's id (201)."""
        reply = await observations.post(request, name)
        if reply.id is None:
            response.status_code = 200
        return reply

    @app.post("/observations/{id}/reactions")
    async def react(
        id: ObservationId, request: ReactRequest, observations: Ready, name: Client
    ) -> ReactReply:
        """FR-4."""
        return await observations.react(id, request, name)

    @app.get("/observations/{id}")
    async def get_observation(id: ObservationId, observations: Ready, name: Client) -> Observation:
        """FR-5."""
        observation = observations.get(id)
        metrics.gets.labels(name).inc()
        return observation

    @app.get("/observations/{id}/neighbors")
    async def neighbors(
        id: ObservationId,
        observations: Ready,
        _: Client,
        k: Annotated[int, Query(ge=1, le=MAX_CANDIDATES)] = NEIGHBORS_DEFAULT,
    ) -> NeighborsReply:
        """The observations nearest this one, none dropped by threshold."""
        return await observations.neighbors(id, k)

    @app.post("/match")
    async def match(request: MatchRequest, observations: Ready, _: Client) -> MatchReply:
        """The match pipeline, writing nothing."""
        return await observations.match(request)

    @app.post("/hooks/github")
    async def github_hook(
        request: Request,
        x_hub_signature_256: Annotated[str | None, Header()] = None,
        x_github_event: Annotated[str | None, Header()] = None,
    ) -> HookReply:
        """FR-9, FR-20: a push to the store's main queues a pull and reindex; the relay gives
        each receiver four seconds, so nothing is pulled before the answer."""
        github = settings.github
        body = await request.body()
        if github is None or not github_verified(github.secret, body, x_hub_signature_256):
            raise ProblemException(
                401,
                ProblemType.unauthenticated,
                "the delivery's signature does not verify",
                detail="sign with the configured secret as X-Hub-Signature-256",
            )
        if not github_push_to(github, settings.store.branch, x_github_event, body):
            metrics.webhooks.labels("github", "ignored").inc()
            return HookReply(action="ignored")
        store.pull()
        metrics.webhooks.labels("github", "queued").inc()
        return HookReply(action="queued")

    @app.post("/observations/{id}/board-sync")
    async def board_sync(id: ObservationId, observations: Ready, _: Client) -> BoardSyncReply:
        """FR-21: apply the observation's card as the board has it now. The reconciler's board
        scan calls this for every observation with a card (FR-13)."""
        return await observations.board_sync(id)

    @app.post("/hooks/youtrack")
    async def youtrack_hook(request: Request) -> HookReply:
        """FR-20, FR-21: an event naming an issue that is some observation's card queues a board
        sync of it; every other event is ignored without a call to YouTrack. The Webhook Triggers
        app waits for each answer, so nothing is read or written before it, and it sends the
        delivery before YouTrack commits the change, so the read waits `settle` seconds."""
        hook = settings.youtrack_hook
        if hook is None or not youtrack_verified(hook, request.headers.get(hook.header)):
            raise ProblemException(
                401,
                ProblemType.unauthenticated,
                "the delivery's token does not verify",
                detail="send the configured webhook token in the configured header",
            )
        issue = youtrack_issue(await request.body())
        if issue is None:
            metrics.webhooks.labels("youtrack", "ignored").inc()
            return HookReply(action="ignored")
        ready()
        entry = index.by_card(issue)
        if entry is None:
            metrics.webhooks.labels("youtrack", "ignored").inc()
            return HookReply(action="ignored")
        observations.sync_later(entry.observation.id, hook.settle)
        metrics.webhooks.labels("youtrack", "queued").inc()
        return HookReply(action="queued")

    return app


async def _models_unreachable(request: Request, exc: Exception) -> Response:
    logger.warning("models pod: %s", exc)
    return await problem_exception_handler(
        request,
        ProblemException(
            502,
            ProblemType.models_unreachable,
            "the models pod did not answer",
            detail="matching needs the embedding model; retry in a minute",
        ),
    )


async def _board_unreachable(request: Request, exc: Exception) -> Response:
    logger.warning("board: %s", exc)
    return await problem_exception_handler(
        request,
        ProblemException(
            502,
            ProblemType.board_unreachable,
            "YouTrack could not be reached or refused the read",
            detail="nothing was written; retry in a minute",
        ),
    )


async def _store_unreachable(request: Request, exc: Exception) -> Response:
    logger.warning("store: %s", exc)
    return await problem_exception_handler(
        request,
        ProblemException(
            502,
            ProblemType.store_unreachable,
            "the store's remote could not be reached or refused the write",
            detail="nothing was written; retry in a minute",
        ),
    )
