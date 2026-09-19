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
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError

from fieldnotes_contracts import (
    MAX_CANDIDATES,
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
from .config import Settings
from .errors import (
    ProblemException,
    not_ready,
    problem_exception_handler,
    request_validation_exception_handler,
    unauthenticated,
    unhandled_exception_handler,
)
from .hooks import github_push_to, github_verified
from .index import EmbeddingCache, Index
from .matching import Matcher
from .models import HttpModels, Models, ModelsError
from .observations import Clock, Observations
from .store import GitError, Store
from .ulid import PATTERN

logger = logging.getLogger(__name__)

# A cold start embeds the whole store in batches; one batch on the shared node can take seconds.
MODELS_TIMEOUT = 60.0

NEIGHBORS_DEFAULT = 5

ObservationId = Annotated[str, Path(pattern=PATTERN, description="the observation's ULID")]


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
    clock: Clock = _utcnow,
    environ: Mapping[str, str] | None = None,
) -> FastAPI:
    """The app. `models` replaces the models pod and `clock` the time, in the suites."""
    http: httpx.AsyncClient | None = None
    if models is None:
        http = httpx.AsyncClient(base_url=settings.models_url, timeout=MODELS_TIMEOUT)
        models = HttpModels(http)
    store = Store(settings.store, os.environ if environ is None else environ)
    index = Index(
        settings.store.root, EmbeddingCache(settings.cache_dir, settings.embed_model), models
    )
    matcher = Matcher(index, models, settings.match)
    runtime = Runtime(settings, store, index, Observations(store, index, matcher, clock))

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
        if http is not None:
            await http.aclose()

    app = FastAPI(title="Fieldnotes API", lifespan=lifespan)
    app.state.runtime = runtime
    app.add_exception_handler(ProblemException, problem_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(ModelsError, _models_unreachable)
    app.add_exception_handler(GitError, _store_unreachable)
    app.add_exception_handler(Exception, unhandled_exception_handler)

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
    async def get_observation(id: ObservationId, observations: Ready, _: Client) -> Observation:
        """FR-5."""
        return observations.get(id)

    @app.get("/observations/{id}/neighbors")
    async def neighbors(
        id: ObservationId,
        observations: Ready,
        _: Client,
        k: Annotated[int, Query(ge=1, le=MAX_CANDIDATES)] = NEIGHBORS_DEFAULT,
    ) -> NeighborsReply:
        """The observations nearest this one, reranked, none dropped by threshold."""
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
            return HookReply(action="ignored")
        store.pull()
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
            detail="matching needs the embedding and rerank models; retry in a minute",
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
